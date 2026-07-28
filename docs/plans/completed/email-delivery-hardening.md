# Email Delivery Hardening Plan

**Status:** Completed — 2026-07-26
**Depends on:** `docs/plans/email-restructuring.md` (V1 implementation, landed)
**Blocks:** any further live Gmail send

## Summary

Two pieces of work, in order:

1. **Fix SMTP connection-level error handling.** Connection failures currently
   escape `send_email()` and crash the command, leaving delivery rows stranded
   in `sending`. Must land before the next live send.
2. **A focused Watchlist test pass**, plus bounded concurrent quote and news
   retrieval so the Watchlist can finish inside the scheduled delivery window.

**Scheduling decision:** Gmail runs independently of Telegram. A local
LaunchAgent starts email attempts at 8:20, 8:25, 8:30, and 8:35 AM in
`America/New_York`; the application rejects automated attempts outside that
window. It retries only definite pre-DATA failures. An `indeterminate`
recipient is never retried automatically and requires confirmed manual resend.

**Quote-time decision:** the five-minute quote-retrieval budget applies to the
whole Watchlist section, not each ticker. The three ticker fetches run
concurrently under one shared monotonic deadline, each retaining the
Tiingo-primary/EODHD-fallback sequence and dated-cache fallback.

**News-time decision:** targeted Google News discovery and publisher
enrichment run concurrently for the three tickers under one four-minute
deadline. A ticker that does not finish renders its quote row with an explicit
`News search unavailable` status; it never delays the full daily edition.

## Part 1 — SMTP connection errors must be recorded, not raised

### Root cause

`src/news_agent/mailer/smtp.py:56-60` catches only:

```python
except (TimeoutError, socket.timeout, smtplib.SMTPServerDisconnected) as exc:
except smtplib.SMTPException as exc:
```

Connection establishment failures are neither. They are `OSError` subclasses
raised by `factory(...)` at line 39 and `smtp.starttls(...)` at line 42:

| Failure | Exception | Currently |
| --- | --- | --- |
| DNS resolution | `socket.gaierror` | uncaught |
| Connection refused | `ConnectionRefusedError` | uncaught |
| TLS handshake | `ssl.SSLError` | uncaught |
| Certificate rejected | `ssl.SSLCertVerificationError` | uncaught |
| Network unreachable | `OSError` | uncaught |

### Blast radius

The exception escapes `send_email()`, then the recipient loop in
`EmailService.send_edition()` (`service.py:92`), then the `with
self.store.lock()` block. Consequences:

- The recipient's `deliveries` row stays `sending` — written at
  `service.py:91`, never updated.
- Remaining recipients are never attempted and get no row at all.
- The rolled-up edition state reflects a delivery that is not in flight.
- `--email-status` reports `sending`, which is false.
- The CLI exits with a traceback.

The file lock itself is fine — `state.py:99` releases it in a `finally`, so no
stale lock is left behind.

### Fix

**1a. Stable error codes.** Add `classify_smtp_error(exc) -> str` to `smtp.py`:

| Exception | Code |
| --- | --- |
| `socket.gaierror` | `dns_failure` |
| `ConnectionRefusedError` | `connection_refused` |
| `ssl.SSLCertVerificationError` | `tls_certificate_invalid` |
| `ssl.SSLError` | `tls_handshake_failed` |
| `TimeoutError` | `timeout` |
| `smtplib.SMTPAuthenticationError` | `auth_failed` |
| `smtplib.SMTPServerDisconnected` | `server_disconnected` |
| other `smtplib.SMTPException` | `smtp_<classname>` |
| other `OSError` | `network_error` |

Today the code is `type(exc).__name__.lower()`, which surfaces `gaierror` in
`--email-status`. Stable codes make status output readable and greppable.

**1b. Collapse to one handler keyed on `data_started`.** Replace both `except`
clauses with:

```python
except OSError as exc:
    state = "indeterminate" if data_started else "failed"
    return RecipientOutcome(recipient, state, classify_smtp_error(exc))
```

A single `OSError` clause is sufficient — verified against the interpreter:

- `smtplib.SMTPException` is itself a subclass of `OSError`, so the existing
  second clause is subsumed. No separate `smtplib` catch is needed.
- `OSError` also subsumes `socket.gaierror`, `ConnectionRefusedError`,
  `ssl.SSLError`, and `TimeoutError`, covering every row in the table above.
- `socket.timeout` is an alias of `TimeoutError` on the project's
  `requires-python = ">=3.11"`, so that catch is redundant too.

This is both broader and more correct than the current two-branch version:
- It fixes an existing misclassification: a `ConnectionResetError` *after*
  DATA is written is an `OSError` but not a timeout, so today it would fall to
  the second clause and be recorded `failed`. Per D12 in the restructuring
  plan, any exception inside the DATA window is `indeterminate`.
- Explicit non-250 responses from `mail()`, `rcpt()`, and `data()` keep their
  existing early returns. A server that definitively rejects is `failed`, not
  `indeterminate`; only exceptions during the DATA window are ambiguous.

**1c. Last-resort guard in the service loop.** In
`EmailService.send_edition()`, wrap the call so state integrity does not depend
on `send_email()` never raising:

```python
try:
    outcome = send_email(...)
except Exception as exc:  # noqa: BLE001 - state integrity is the point
    outcome = RecipientOutcome(recipient, "failed", f"unhandled_{type(exc).__name__.lower()}")
self.store.record_delivery(edition.edition_id, outcome)
```

The loop continues, so a first-recipient failure no longer silently skips the
rest. The existing stderr warning at `service.py:102-107` already reports any
non-accepted recipient.

**1d. Non-zero exit on total failure.** `cli.py` currently prints
`Sent email to 0 recipient(s).` and exits `0` when every recipient fails. Exit
non-zero and print each recipient's error code when no recipient reached
`smtp_accepted`. A launchd job that fails silently is worse than one that
fails loudly.

**1e. Preflight configuration check.** Validate `GMAIL_SMTP_HOST` is non-empty
and `GMAIL_SMTP_PORT` is `465` or `587` before preparing the edition. A typo
should fail fast with a clear message rather than producing one `dns_failure`
per recipient after an edition has already been written to SQLite.

### Part 1 tests

All use the existing `FakeSMTP` pattern in `tests/test_mailer.py` — no network.

1. Factory raises `socket.gaierror` → `failed` / `dns_failure`, no exception
   escapes.
2. Factory raises `ConnectionRefusedError` → `failed` / `connection_refused`.
3. `starttls` raises `ssl.SSLError` → `failed` / `tls_handshake_failed`.
4. Connection reset after DATA → `indeterminate`, not `failed`.
5. Connection reset before DATA → `failed` (retry-safe).
6. Two recipients, first raises `gaierror` → both rows recorded, second still
   attempted.
7. After a connection failure, `--email-status` shows `failed`, never
   `sending`.
8. Total failure → CLI exits non-zero.
9. Regression: `test_smtp_sender_reports_acceptance` still passes.

## Part 2 — Focused Watchlist test pass

Current `tests/test_mailer.py` covers parity text, recipient dedup, SMTP
acceptance, edition reuse, the watermark rule, the three-entry limit, alias
consistency, and the 8:20–8:35 scheduler guard. None of the Watchlist behavior below is
covered. Tests only — no production changes unless a test exposes a defect.

### Quote adapters (`mailer/quotes.py`)

- Tiingo success → `provider == "Tiingo"`, correct `percent_change`.
- Tiingo returns `None` → EODHD fallback used, `provider == "EODHD"`.
- Both return `None` → `fetch_quote_with_fallback` returns `None`, and
  `render_newsletter` falls back to `store.cached_quote` and renders the dated
  cached close.
- Retry boundary: inject `retry_seconds=0` and a fake `sleeper`; assert one
  full provider pass then return, no unbounded loop.
- Missing keys → `validate_quote_provider_configuration()` raises naming the
  missing variables.
- Weekend/holiday: payload whose last entry is Friday renders Friday's date.
- Malformed payloads (non-list, single element, missing `close`) → `None`
  rather than an exception.

### Watchlist retrieval (`mailer/watchlist_news.py`)

- Feed error → `discover_watchlist_articles` returns `search_unavailable`, and
  the rendered section shows the unavailable label rather than an empty block.
- Candidate enriching to `failed` or `blocked` → excluded.
- Candidate whose resolved domain has no extraction policy
  (`policy_for_url` → `None`) → excluded.
- `primary` role articles sort ahead of `publisher` role.
- Duplicate resolved `final_url` → deduplicated.
- More than `WATCHLIST_MAX_CANDIDATES` candidates → only the first five are
  enriched.

### Editorial behavior (`summarize_watchlist`, `mailer/render.py`)

- Material event → summary, `why it matters`, and citation links present.
- `material=false` → quote row only, no summary block.
- No article reaches `enrichment_status == "extracted"` → headline plus
  "Summary unavailable."
- LLM outcome is `None`, e.g. the watchlist reserve is exhausted → "Summary
  unavailable."
- Malformed JSON from the model → "Summary unavailable."
- `source_urls` matching no article → citations fall back to all summarizable
  articles (`watchlist_news.py:134`). Pin the current behavior.

### Delivery

- `--to email --dry-run` writes no `editions`, `deliveries`, or `quote_cache`
  rows. The dry-run path already passes `persist_quotes=False`
  (`cli.py:241`); the test locks it in.
- Native send to two recipients records both outcomes.

## Sequencing

Part 1 lands and its tests pass before any live send. The entire point is that
a connection failure during the live run gets recorded rather than crashing
mid-edition.

1. Implement Part 1 (1a–1e) and its nine tests.
2. Add the Part 2 tests. Fix anything they expose.
3. Full suite green.
4. `--to email --dry-run --email-parity` — parity render, no state written.
5. `--to email --dry-run` — full newsletter with the configured Tiingo and
   EODHD keys. Inspect: quote rows present and dated, each ticker shows either
   a summary or an explicit unavailable label, links resolve to allowlisted
   publishers.
6. `--to email --send` — one real Gmail edition.
7. `--email-status` — confirm `smtp_accepted` for every recipient.

## Out of scope

- Any change to Watchlist retrieval strategy. D4 in the restructuring plan
  approved targeted Google News RSS discovery; retaining it is outside this
  hardening pass.
- Any change to the notification factory or `NotificationSender`.
