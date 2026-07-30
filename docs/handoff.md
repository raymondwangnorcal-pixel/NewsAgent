# NewsAgent Handoff

Last updated: 2026-07-30T21:41:00Z

## Current Goal

Restore useful Gmail Watchlist stories. The current implementation fetches Google News RSS candidates, but rejects their opaque redirect URLs before enrichment, so the email renders quote rows without stories. The approved repair is Tiingo News first and EODHD News only when Tiingo fails or returns no usable records, preserving direct publisher links and verifying facts before any summary is produced. The plan is at `docs/plans/watchlist-retrieval-reliability.md` (status: Approved for implementation).

## Accomplished This Session

No implementation work was performed this session; it consisted solely of a state audit requested via `/handoff`. The audit verified the prior handoff's claims against the working tree and confirmed they remain accurate: the Watchlist repair is still unimplemented (`src/news_agent/mailer/watchlist_news.py` is unchanged since 2026-07-26 and still builds only a `GOOGLE_NEWS_BASE` feed), and `TIINGO`/`EODHD` identifiers appear only in `src/news_agent/mailer/quotes.py` as *quote* providers — there is no news-provider code, config, or test anywhere in `src/`, `tests/`, or `config/`. The full test suite was re-run and passes at `370 passed`, matching the previous checkpoint. The previous session's `docs/handoff.md` was found untracked, so it had never been committed; this revision is committed.

## Outstanding Tasks

1. Implement the approved Watchlist retrieval repair in `src/news_agent/mailer/watchlist_news.py`: the `WatchlistDiscovery` dataclass, `provider_symbol()` EODHD mapping, Tiingo-first / EODHD-fallback fetches, direct-URL normalization and dedup, diagnostics, and the two decided fallback messages (`Summary unavailable: <headline>` and `Article push failed`).
2. Add focused tests for the new retrieval path and run a no-send Gmail dry run to confirm Watchlist stories, direct publisher hyperlinks, and unchanged quote-only behavior for a quiet ticker.
3. Decide whether to commit the existing broad, uncommitted email/deduplication work and the generated runtime data after validating it. Do not discard or reset it.

## Recommended Next Task

Implement Step 1 of `docs/plans/watchlist-retrieval-reliability.md` in `src/news_agent/mailer/watchlist_news.py` (provider-news records and ticker mapping), after running the plan's two pre-condition checks.

## Git / Remote State

Branch: `main`, tracking `origin/main`. HEAD is `38831a2 Refactor NewsAgent architecture`.

Remote freshness: verified. `git fetch --quiet origin` succeeded on 2026-07-30; `git rev-list --left-right --count origin/main...HEAD` reports `0 0`, so there are no unpushed local commits and nothing to pull.

Working tree: dirty, and intentionally so. 26 tracked files are modified — source (`src/news_agent/{cli,cluster,config,draft,formatting,models,openai_client,pipeline}.py`, the `mailer/` package), their tests, `README.md`, `config/sources.toml`, `docs/plans/email-restructuring.md`, and generated runtime data. Untracked additions include `src/news_agent/duplicate_gate.py`, `tests/test_duplicate_gate.py`, `tests/test_openai_client.py`, `docs/plans/duplicate-event-deduplication.md`, `docs/plans/watchlist-retrieval-reliability.md`, dated `data/` records, and eleven `data/compression_audits/` files. Preserve all of it until reviewed and committed intentionally.

Handoff commit: c8ee061 (`docs: update handoff (2026-07-30)`; this sha line was corrected by an immediate follow-up commit)

## Validation

- Full test suite: `PYTHONPATH=src pytest -q` → `370 passed in 0.41s` (run 2026-07-30). Note that `.venv/bin/python` has no `pytest` installed; use the system `pytest` on `PATH` (3.14 / pytest 9.0.3) with `PYTHONPATH=src`.
- Repository-state audit: `git status --short --branch`, `git fetch --quiet origin`, `git rev-list --left-right --count origin/main...HEAD`, `git check-ignore -v docs/handoff.md` (not ignored), and a rebase/merge/detached-HEAD check (none in progress).
- Static confirmation that the Watchlist repair is absent: `grep -rniE "tiingo|eodhd" src tests config` matches only `src/news_agent/mailer/quotes.py`.
- Not run this session: the no-send Gmail dry run and the plan's credential/configuration pre-condition checks. No live provider calls were made.
- Carried forward from earlier sessions: `git diff --check` passed after the duplicate-event work; a live no-send Gmail build reported 3 eligible duplicate pairs, 1 merged pair, 0 rejected pairs, estimated OpenAI cost `$0.220062`, and sent no email; Watchlist diagnosis found Google News candidates for AAPL, BN, COST, CURI, ETHB, META, NET, NVO, and SHOP, with all 45 sampled candidates rejected as `redirect_not_permitted`.

## Risks / Decisions

- Tiingo News is the chosen primary retrieval source. EODHD News is fallback-only because its news calls cost five API calls. Google RSS cannot support a substantive summary by itself and may remain only as optional discovery/observability.
- For a provider-verified article whose publisher text cannot be extracted, show the verified headline labeled `Summary unavailable`; never invent prose from the headline or provider description. If neither provider yields a usable article, show exactly `Article push failed`. Valid recent records with no investor-relevant event render quotes only.
- Retain the 10-ticker Watchlist cap, shared News/quote deadlines, the $1 per-run OpenAI cap, the $0.25 Watchlist reserve, Gmail-only newsletter behavior, and Telegram independence.
- Credentials for Tiingo and EODHD live in `.env`. Never print, log, or document their values, and never log request URLs containing tokens.
- Keep potential duplicate stories unless the duplicate gate confidently merges them; merged stories show source diversity and up to five links. Empty non-finance sections disappear.
- The worktree is user-owned and holds substantial uncommitted work. Avoid destructive Git operations (`reset`, `checkout --`, `stash`, `clean`).

## Archive Decision

Safe to archive: No

Reason: the requested Watchlist retrieval repair is unimplemented, its tests and email dry run are outstanding, and the repository carries substantial uncommitted source, test, and runtime-data changes.

Next action: implement and validate the approved Watchlist retrieval repair, then review and intentionally commit the existing email/deduplication work.
