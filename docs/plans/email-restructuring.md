# Email V1 Restructuring Plan

**Status:** Approved for implementation
**Canonical location:** this file
**Supersedes:** the email portions of `docs/Goal/mobile-split-email.md`

## Summary

Add a new email delivery service without changing the existing Telegram/SMS
notification protocol. Milestone 1 proves Gmail delivery using the unchanged
Telegram-format briefing purely as a baseline smoke test. Milestone 2 adds the
email-native format and a personalized newsletter for up to three configured,
valid U.S.-listed stocks, ADRs, or ETFs.

The plan reuses existing enrichment, extraction-policy, budget, and history
machinery rather than building parallel implementations inside the new package.
Where a decision narrows scope, the resulting limitation is recorded explicitly
under [Accepted Limitations](#accepted-limitations) rather than left implied.

## Milestones

### Milestone 1 — Delivery baseline

Goal: prove Gmail SMTP delivery, recipient handling, MIME construction, and
delivery-state persistence. Editorial quality is explicitly not the goal.

- Send one Gmail message per configured `EMAIL_TO` recipient.
- The plain-text body is the exact existing Telegram header and formatted
  section text joined into one digest, byte for byte.
- The HTML part is a semantic equivalent of that same text. No email-specific
  editorial changes.
- **This output inherits Telegram's 3600-character cap and its whole-story
  dropping behavior, including any `+ N more stories omitted for length`
  lines.** That is intended and temporary. Milestone 1 output is a delivery
  proof, not the email product, and must not be evaluated as one.
- No Watchlist section, no quote rows, no email-specific retrieval.
- **Temporary implementation bridge:** `--to email --email-parity` (or
  `--to both --email-parity`) keeps this Gmail-only smoke-test path available
  after Milestone 2. Remove it once native newsletter delivery has been
  verified with both quote-provider keys.

### Milestone 2 — Personalized newsletter

- Add an `email` `FormatMode` to `src/news_agent/formatting.py` with
  `max_chars = None`, `story_limit = None`, and links enabled. This removes the
  Milestone 1 truncation and story-dropping behavior.
- Render the minimal newsletter template: shared sections, then a Watchlist
  section with one quote row per ticker, concise top-one/two-event summaries,
  `why it matters`, original-publisher links, and the informational-only
  footer.
- Mark retrieval failures explicitly. When source text or LLM summarization
  fails, display the cited headline as "Summary unavailable."

## Decisions

Each decision below is binding for V1. The rationale is recorded because
several of them deliberately reject a more general solution.

### D1 — History stays shared; catch-up scope is narrowed

Keep the single shared `data/story_history.json` and leave
`apply_history()` / `save_story_history()` behavior unchanged.

Define email catch-up as covering **only editions whose Gmail send definitely
failed before SMTP accepted message data**, recovered from the SQLite canonical
edition record. An `indeterminate` edition may have been received and is never
carried forward automatically. Do not attempt to recover stories that a
Telegram-only run already consumed.

Rationale: `apply_history()` runs at `pipeline.py:617`, on clusters, before
selection and formatting, and applies a score penalty rather than a filter.
`save_story_history(selected)` runs once at `pipeline.py:889`, gated only by
`persist_history=args.send`. Per-channel history therefore requires either two
history files and two builds — losing the build-once property — or
channel-aware suppression threaded through the whole pipeline. Neither is worth
doing during a restructure of the same file.

For `--to both` partial failure: persist story history when the Telegram send is
accepted, regardless of the email outcome. Email recovers its own gap from the
edition record.

Future upgrade path, if email becomes the primary channel: per-channel history
paths (both functions already take a `path` argument) plus a cached
fetch/enrich stage so the second build is cheap. Out of scope for V1.

### D2 — Milestone 1 is strict byte-for-byte Telegram parity

The Milestone 1 plain-text body must equal the existing Telegram output
exactly, truncation included. It exists to isolate delivery from editorial
concerns: if the email is wrong, the cause is the mail path, not the format.

The `email` `FormatMode` arrives in Milestone 2 and is not permitted in
Milestone 1.

### D3 — Reserve $0.25 of the run budget for Watchlist

Add `watchlist_reserve_usd = 0.25` to `[openai_costs]` in `config/sources.toml`
and to `OpenAICostConfig` in `src/news_agent/models.py`.

Add a `reserved_usd` field to `OpenAIBudget`:

- General stages (judging, drafting, compression) check
  `remaining_usd - reserved_usd`.
- Watchlist summarization checks `remaining_usd`.

Expose the live budget object on the briefing result (`BriefingResult
.openai_budget`) rather than reconstructing it from diagnostics totals.

Rationale: `OpenAIBudget` is constructed inside `build_briefing_result` at
`pipeline.py:846` and consumed by the general pipeline first. Without a
reservation, a heavy news day leaves nothing for Watchlist, and the flagship
personalized section silently degrades to "Summary unavailable" for every
ticker.

### D4 — Google News RSS is discovery-only for arbitrary U.S. tickers

V1 must allow a user to replace the example selections in
`config/email_watchlist.json` with any valid U.S.-listed common stock, ADR, or
ETF. Each entry contains a canonical ticker, display name, instrument type, and
optional aliases. At startup, validate that the ticker is supported as the
declared U.S.-listed instrument type by the quote provider; reject invalid or
unsupported configuration with an actionable error.

For each configured ticker, issue one targeted Google News RSS query combining
the display name, ticker, and aliases, for example
`("Apple Inc." OR AAPL) when:1d`. Google News is discovery only: its redirect
URL is never rendered, cited, or summarized.

Resolve every candidate through the existing safe redirect and domain-policy
logic, then admit it only when its final original-publisher domain matches an
existing `[[extraction_policies]]` allowlist entry. Reuse
`enrich_article()` and `policy_for_url()` from
`src/news_agent/enrichment.py` for page retrieval. Do not reimplement the SSRF
guard (`_is_public_http_url`), `_SafeRedirectHandler`, or domain allowlist
check.

The rendered citation and link always identify the resolved original publisher.
Candidates with an unsafe, unresolved, or unallowlisted final URL are rejected
and logged. A search that produces no qualifying article is not an error: the
email still shows the ticker's quote row and no Watchlist summary. A search or
redirect failure is displayed as a search-unavailable status rather than being
presented as no news.

Rationale: direct top-story publisher feeds cannot reliably discover news for
an arbitrary user-selected company. Google News provides broad ticker discovery
while the existing redirect, allowlist, extraction, and citation rules retain
the source-integrity boundary.

### D5 — Source roles reuse `[[extraction_policies]]`; no new tier config

Do not introduce a `Tier 1` / `Tier 2` configuration surface. Add an optional
field to the existing `[[extraction_policies]]` entries:

```toml
source_role = "primary"   # or "publisher"; defaults to "publisher"
```

Watchlist admission reads that field. `allowed_domains` remains the single
publisher allowlist.

`metadata_only` publishers (currently `nytimes.com`, `washingtonpost.com`) may
be cited but never summarized. Rendering "Summary unavailable" for them is
correct policy behavior, not a defect. When an `article_text` publisher covers
the same event, prefer it.

Freeze the `agent/source-restructure` branch and
`docs/plans/source-system-restructure.md` until email V1 ships. Email V1 with
three tickers depends on nothing from that work, and two concurrent refactors
of `pipeline.py` is unacceptable risk.

### D6 — Package name is `mailer`

The new package is `src/news_agent/mailer/`, not `src/news_agent/email/`.
Naming it `email` shadows the stdlib package the module itself imports for MIME
construction. Absolute imports make it technically work; it remains a trap for
readers and tooling.

### D7 — One delivery-target flag

Use one new explicit delivery-target flag for the V1 email paths:

```
--to {email,telegram,both}
```

- Default resolves from `BRIEFING_DELIVERY_CHANNEL`.
- `both` means email plus Telegram in V1.
- Retain `--channel sms` as a deprecated SMS alias and preserve a configured
  `BRIEFING_DELIVERY_CHANNEL=sms` default. SMS is not a `--to` value because
  V1 `both` deliberately excludes it.
- Reject commands that specify both `--to` and `--channel`; `argparse` choices
  alone cannot enforce that cross-flag conflict.
- Legacy `--channel telegram` maps to `--to telegram` and prints a deprecation
  warning to stderr. Legacy SMS invocations continue through the existing SMS
  sender with the same warning.
- `--format` is no longer derived from the target unless passed explicitly.
  `resolve_format_mode()` returns `console` for dry runs and the target's
  native format otherwise.
- `--alerts` remains Telegram/SMS only. Email alerts are a separate product
  decision, out of scope for V1.

Rationale: a new `--telegram` flag collides with the existing
`--channel telegram` both conceptually and in the argparse namespace.

### D8 — One timezone helper, used everywhere

Add `briefing_now()` and `briefing_today()` reading `BRIEFING_TIMEZONE` via
stdlib `zoneinfo`. Replace every `date.today()` call, including
`formatting.header_title()` and `notifications/factory.send_briefing_messages()`.

The `America/New_York` 8:20 AM threshold, the local-date guard, and every
rendered date must resolve through these helpers.

Catch-up editions carry **today's** date in the header and subject, with an
inline `From yesterday's edition` marker on carried-over stories. Do not
backdate the header.

### D9 — SQLite state, location, and locking

- Path: `data/email_state.db`. Add `data/*.db` to `.gitignore` (currently only
  `db.sqlite3` is ignored, and `data/` is committed).
- Tables: `editions`, `edition_stories`, `deliveries` (one row per recipient
  per edition), `quote_cache`.
- Schema versioning via `PRAGMA user_version` and an upgrade block. No
  migration framework.
- Process lock: `flock` on a PID file, with an `os.kill(pid, 0)` liveness
  check. Do not use a timestamp-based staleness heuristic.

Rationale on the lock: the LaunchAgent starts the four daily Gmail attempts. A stale lock left
by a crashed run would otherwise block sending indefinitely and silently. A PID
liveness check self-heals on the next tick.

### D10 — Zero new runtime dependencies

`pyproject.toml` currently declares only `certifi`. Keep it that way.

- SMTP and MIME: stdlib `smtplib` and `email.mime`.
- Tiingo and EODHD adapters: JSON over `urllib`, following the existing
  `post_telegram_form()` pattern in `notifications/telegram.py`.
- Watchlist config file: `config/email_watchlist.json`, **not** YAML. The
  project has no YAML dependency — `parse_simple_yaml_watchlist()` is a
  hand-rolled subset parser. JSON uses the stdlib parser and matches the
  existing `config/watchlist.json` format.

### D11 — Two watchlist files, guarded by a consistency test

Keep `config/email_watchlist.json` separate from `config/watchlist.json`. V1
accepts up to ten valid U.S.-listed stock, ADR, or ETF entries in the email
file; the checked-in selections are examples and may be substituted. Do not
merge the files: adding an email-only ticker to the shared file would change
general-briefing watchlist matching, which this plan must not do.

Add a test asserting that any ticker present in both files has identical ticker
and alias definitions. This contains drift without coupling the channels.

### D12 — `indeterminate` is the post-DATA gap only

Delivery states: `prepared`, `sending`, `smtp_accepted`, `failed`,
`indeterminate`.

- `indeterminate`: an exception raised after `SMTP.data()` has written the
  message body but before a `250` response is read (timeout, connection reset).
- `failed`: everything else, including all explicit 4xx/5xx responses and any
  failure before DATA. Safe to retry automatically.
- Only an edition in the definite pre-DATA `failed` state may contribute its
  articles to a later catch-up email. `sending` and `indeterminate` mean the
  edition may have been received and are never carried forward automatically.
- Resend command for an indeterminate edition:
  `news-briefing --email-resend <edition-id> --confirm`. Never automatic.

The mailer does **not** implement `NotificationSender`. That protocol's
`send_message(recipient, message) -> None` cannot report per-recipient
outcomes. Give the mailer its own
`send_edition(...) -> list[RecipientOutcome]`. The existing factory and
protocol are untouched, as required.

### D13 — Keep the first-accepted watermark; add visibility

Advance the email article-window watermark after the first SMTP-accepted
recipient. Record per-recipient outcomes in `deliveries`.

Add:

- `news-briefing --email-status`, printing recent editions with per-recipient
  outcomes.
- A stderr warning at send time for any recipient not reaching
  `smtp_accepted`.

### D14 — Capture the golden file before refactoring

Before any change to `pipeline.py`, record a full run's `formatted_messages`
from a frozen fixture set and commit it as a golden file. Refactor afterward
and diff against it.

The ordering is the point. A golden file recorded after the refactor only
captures whatever the refactor broke. This is the safety net for the highest
risk change in the plan — restructuring a 1047-line module around a persisted
canonical edition.

### D15 — Documentation and configuration cleanup

- Add `**Status:** Superseded by docs/plans/email-restructuring.md` to
  `docs/Goal/mobile-split-email.md`, and remove its link to
  `folk-style-pivot.md`, which was deleted in commit `748972f`.
- Resolve `include_links_telegram`: it is `false` in `config/sources.toml` and
  `true` in `.env`, where the environment wins. Set the intended value in
  `config/sources.toml` and delete the `.env` override so the config file
  describes actual behavior.

## Key Changes

- Add `config/email_watchlist.json` for up to ten valid U.S.-listed stock,
  ADR, or ETF selections. It may initially use `AAPL`/Apple, NVO/Novo Nordisk,
  and META/Meta Platforms as examples, but a user may substitute any validated
  U.S.-listed instrument. Do not modify `config/watchlist.json`, which
  continues to drive existing briefing and alert behavior (D11).
- Add `src/news_agent/mailer/` (D6) to own:
  - Gmail SMTP configuration and multipart HTML/plain-text delivery.
  - Email composition and the minimal newsletter template.
  - SQLite-backed canonical edition, delivery state, article-window watermark,
    quote cache, and process lock (D9).
  - Watchlist discovery through targeted Google News RSS queries, followed by
    safe redirect resolution, original-publisher allowlist admission,
    extraction via `enrich_article()`, materiality selection, and source-linked
    summaries (D4, D5).
  - Tiingo Free primary and EODHD Free backup quote adapters (D10).
- Preserve the notification factory and `NotificationSender` protocol
  unchanged. Telegram/SMS retain their existing delivery implementation (D12).
- Refactor the briefing run around a persisted canonical edition: build the
  shared general briefing once, preserve selected-story and source provenance,
  then project it independently to Telegram and email.
- Add `--to {email,telegram,both}` with legacy `--channel` compatibility (D7).
  `both` means email plus Telegram; `--channel sms` remains a deprecated SMS
  alias. Reject a command that combines `--to` with `--channel`. `--to email
  --dry-run` renders the exact multipart content without writing delivery state;
  `--to both --dry-run` renders both channel projections.
- Add `--email-status` (D13) and `--email-resend <edition-id> --confirm` (D12).
- Route all date and time resolution through `briefing_now()` /
  `briefing_today()` (D8).
- Reserve `watchlist_reserve_usd` from the run budget (D3). When the
  reservation is exhausted, preserve the article as a cited "Summary
  unavailable" headline rather than exceed the cap.
- Retry primary/backup quote retrieval for five minutes; use the dated cached
  close only when both fail.
- When a prior email edition is in the definite pre-DATA `failed` state, combine
  its unsent general content with the current edition, capped at five general
  stories plus the normal three Watchlist quote rows. Never carry forward a
  `sending` or `indeterminate` edition. Mark remaining older content as omitted.
- Add a macOS `launchd` LaunchAgent that runs Gmail-only attempts at 8:20,
  8:25, 8:30, and 8:35 AM `America/New_York`. The application enforces that
  retry window and local-date guard, retries only definite pre-DATA recipient
  failures, and never automatically retries an indeterminate delivery.

## Accepted Limitations

Recorded deliberately. Do not treat these as defects during implementation.

1. **Milestone 1 emails are truncated.** They inherit Telegram's 3600-character
   cap and drop whole stories. Resolved in Milestone 2 (D2).
2. **Telegram-only days are not caught up in email.** If a run delivers to
   Telegram only, those stories are marked seen for all channels and will not
   resurface in a later email. Email catch-up covers only prepared-but-
   undelivered editions (D1).
3. **`metadata_only` publishers never produce summaries.** NYT and WaPo
   Watchlist items render as cited headlines with "Summary unavailable" (D5).
4. **A permanently failing second recipient loses editions silently.** The
   watermark advances on first acceptance. `EMAIL_TO` is a single address
   today; revisit if that changes (D13).
5. **Google News is discovery-only.** It broadens coverage for arbitrary
   U.S.-listed tickers, but a result still needs a safely resolved,
   allowlisted original publisher before it can appear in the email (D4).

## Test Plan

Ordered. Item 1 precedes any change to `pipeline.py`.

1. **Golden file, captured first (D14).** Record and commit full
   `formatted_messages` output from a frozen fixture set. After the
   canonical-edition refactor, assert byte-identical Telegram output.
2. Unit-test Gmail configuration validation, comma-separated recipient parsing,
   MIME construction, SMTP success/rejection/timeout behavior, and absence of
   secret leakage in logs and errors.
3. Snapshot-test Milestone 1 parity: the email plain-text body exactly matches
   the concatenated Telegram header and section messages, truncation included.
4. Test `--to` resolution for all three targets, `both` as email plus Telegram,
   legacy `--channel telegram` and `--channel sms` behavior with deprecation
   warnings, rejection of `--to` combined with `--channel`, `--format`
   independence, and one-build/two-delivery behavior.
5. Test SQLite uniqueness constraints, `flock` acquisition and stale-PID
   recovery, dry-run non-mutation, delivery-state transitions, indeterminate
   resend and carry-forward protection, recipient partial failure, and watermark
   advancement.
6. Test `config/email_watchlist.json` validation for arbitrary valid
   U.S.-listed stock, ADR, and ETF entries, rejection of unsupported or
   mismatched instrument types, the three-ticker limit, and the cross-file
   consistency assertion against `config/watchlist.json` (D11).
7. Test Google News query construction and redirect safety, Watchlist publisher
   admission via `source_role`, `metadata_only` citation-without-summary
   behavior, deduplication, material-event selection, budget-reservation
   fallback, summary fallback, and citation rendering.
8. Test the `watchlist_reserve_usd` boundary: general stages cannot consume the
   reservation, and Watchlist can.
9. Test Tiingo/EODHD primary/backup behavior, the five-minute retry boundary,
   dated cached fallback, and weekend/holiday quote display.
10. Test `briefing_today()` against a fixed `BRIEFING_TIMEZONE`, including the
    8:20–8:35 retry window, the local-date guard, and catch-up header dating (D8).
11. Verify manually, in order: a live `--to email --dry-run`, a static SMTP
    test, a two-recipient parity send, a missed-edition catch-up preview, and a
    scheduled `launchd` dry run before enabling the live daily job.

## Assumptions

- Gmail credentials remain only in the local uncommitted `.env`:
  `GMAIL_SMTP_HOST`, `GMAIL_SMTP_PORT`, `GMAIL_SMTP_USERNAME`,
  `GMAIL_SMTP_APP_PASSWORD`, `EMAIL_FROM`, and comma-separated `EMAIL_TO`.
  All six are already present.
- Free-provider API keys are supplied locally as `TIINGO_API_KEY` and
  `EODHD_API_KEY`; startup fails clearly if quote delivery is enabled without
  them.
- The email watermark represents the last SMTP-accepted email window. Telegram
  retains independent delivery tracking; story history remains shared (D1).
- No new runtime dependency is added to `pyproject.toml` (D10).
- The `agent/source-restructure` branch stays frozen until V1 ships (D5).
- Website, auth, mobile channels, paid providers, and multi-user
  profile/watchlist infrastructure remain out of scope for V1.
