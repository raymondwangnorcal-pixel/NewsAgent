# Email V1 Restructuring Plan

## Summary

This is the canonical durable implementation plan. Implement a new
email-specific service without changing the existing Telegram/SMS notification
protocol. The first milestone proves Gmail delivery with the unchanged
Telegram-format briefing; the second adds the personalized three-ticker
newsletter.

## Key Changes

- Add `config/email_watchlist.yaml` for `AAPL`/Apple, `NVO`/Novo Nordisk, and
  `META`/Meta Platforms. Do not modify the shared `config/watchlist.json`,
  which continues to drive existing briefing and alert behavior.
- Add `src/news_agent/email/` to own:
  - Gmail SMTP configuration and multipart HTML/plain-text delivery.
  - Email composition and the minimal newsletter template.
  - SQLite-backed canonical edition, email delivery state, article-window
    watermark, quote cache, and process lock.
  - Targeted Google News RSS retrieval, redirect resolution, original-publisher
    source-tier admission, extraction, materiality selection, source-linked
    summaries, and safe headline-only fallbacks.
  - Tiingo Free primary and EODHD Free backup quote adapters.
- Preserve the current notification factory and `NotificationSender` protocol.
  Telegram/SMS retain their existing delivery implementation.
- Refactor the briefing run around a persisted canonical edition: build the
  shared general briefing once, preserve selected-story/source provenance, then
  project it independently to Telegram and email. Channel-specific history and
  delivery state must not suppress email catch-up content.
- Add mutually exclusive delivery target flags:
  - `--email --send`: email only.
  - `--telegram --send`: Telegram only.
  - `--both --send`: build once, send the shared general briefing to Telegram
    and email, then append the email-only Watchlist section.
  - Keep legacy `--channel` invocations working unchanged.
  - `--email --dry-run` renders the exact multipart content without writing
    delivery state; `--both --dry-run` renders both channel projections.
- Parity milestone:
  - Send one Gmail message per configured `EMAIL_TO` recipient.
  - Plain text is the exact existing Telegram header and formatted message text
    joined into one digest.
  - HTML is a semantic equivalent of that same content; no email-specific
    editorial changes yet.
- Personalized milestone:
  - Use the minimal newsletter template: shared sections followed by a
    Watchlist section with one quote row per ticker, concise top-one/two-event
    summaries, `why it matters`, original-publisher links, and the
    informational-only footer.
  - Apply Tier 1 primary-source and Tier 2 approved-publisher policy only to
    Watchlist content.
  - Treat Google News URLs as discovery-only redirects. Resolve each candidate
    to its final original-publisher URL before source-tier validation, citation,
    or rendering; reject candidates whose resolved domains are not approved.
  - Mark retrieval failures explicitly; when source text or LLM summarization
    fails, display the cited headline as “Summary unavailable.”
  - Watchlist work shares the existing $1 total OpenAI budget for the run. When
    insufficient budget remains, preserve the article as a cited
    “Summary unavailable” headline rather than exceed the cap.
- Persist explicit delivery states: `prepared`, `sending`, `smtp_accepted`,
  `failed`, and `indeterminate`.
  - Retry primary/backup quote retrieval for five minutes; use the dated cached
    close only when both fail.
  - Do not automatically resend `indeterminate` Gmail attempts; allow an
    explicit warned resend command.
  - With multiple recipients, advance the email article-window watermark after
    the first SMTP-accepted recipient, as chosen. Record per-recipient outcomes
    so failed recipients remain diagnosable, but they do not block future
    editions.
- When a prior email edition was missed, combine its unsent general content
  with the current edition but cap the catch-up at five general stories plus
  the normal three Watchlist quote rows. Mark any remaining older content as
  omitted.
- Add a macOS `launchd` LaunchAgent that runs every 15 minutes. The application
  enforces the `America/New_York` 8:15 AM threshold and local-date guard, sends
  at most one current-date edition after wake, and never sends a prior-date
  catch-up.

## Test Plan

- Unit-test Gmail configuration validation, comma-separated recipients, MIME
  construction, SMTP success/rejection/timeout behavior, and no secret leakage.
- Snapshot-test parity: the email plain-text body exactly matches the existing
  Telegram-formatted header and section messages.
- Test all CLI targets, mutual exclusion, legacy `--channel` compatibility,
  and one-build/two-delivery behavior.
- Test SQLite uniqueness, file locking, dry-run non-mutation, delivery
  transitions, indeterminate-send protection, recipient partial failure, and
  watermark behavior.
- Test Watchlist YAML validation, three-ticker limit, RSS query construction,
  redirect resolution, source-tier admission, deduplication, material-event
  selection, unavailable-search labels, budget fallback, summary fallback, and
  citation rendering.
- Test Tiingo/EODHD primary/backup behavior, five-minute retry boundary, dated
  cached fallback, and weekend/holiday quote display.
- Verify a live `--email --dry-run`, static SMTP test, two-recipient parity
  send, missed-edition catch-up preview, and scheduled `launchd` dry-run before
  enabling the live daily job.

## Assumptions

- Gmail credentials remain only in the local uncommitted `.env`:
  `GMAIL_SMTP_HOST`, `GMAIL_SMTP_PORT`, `GMAIL_SMTP_USERNAME`,
  `GMAIL_SMTP_APP_PASSWORD`, `EMAIL_FROM`, and comma-separated `EMAIL_TO`.
- Free-provider API keys are supplied locally as `TIINGO_API_KEY` and
  `EODHD_API_KEY`; startup fails clearly if quote delivery is enabled without
  them.
- The email watermark represents the last SMTP-accepted email window, while
  Telegram retains independent history and delivery tracking.
- `docs/plans/email-restructuring.md` is the sole canonical plan location.
- Website, auth, mobile channels, paid providers, and multi-user
  profile/watchlist infrastructure remain explicitly out of scope for V1.
