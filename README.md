# Morning News Agent

A scheduled AI briefing agent that gathers signals from reputable news sources every morning, ranks stories by frequency and expected impact, and sends five concise briefing messages:

1. Business and technology
2. Domestic U.S. news
3. Global news
4. Culture, social, and media trends
5. Financial news

The agent is intentionally pipeline-shaped instead of chat-shaped:

```text
RSS/news inputs -> rich feed parsing -> preliminary clustering/ranking
-> bounded policy-controlled article enrichment -> evidence scoring and context gate
-> final clustering/category scoring -> watchlist/source/history checks
-> stock mentions + market mover detection
-> OpenAI structured briefing or deterministic fallback -> NotificationSender
-> Telegram now, SMS later
```

## Quick Start

```bash
cd /Users/raymondwang/PersonalProjects/NewsAgent
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[providers]"
cp .env.example .env
```

Fill in `.env`, then run:

```bash
news-briefing --dry-run
news-briefing --dry-run --no-openai --show-skipped
news-briefing --dry-run --format console --brief
news-briefing --dry-run --openai-mode full
news-briefing --test-telegram
news-briefing --send
news-briefing --send --no-openai
news-briefing --dry-run --to email
news-briefing --dry-run --to email --email-parity
```

Use `--dry-run` until the content looks right. It prints the five messages instead of sending them.
Native email dry runs also write the complete formatted newsletter, including the Watchlist,
to `preview.html` for browser inspection.

## Environment

Required for AI summarization:

```text
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-5.6-terra
```

`OPENAI_MODEL` is the runtime model setting for every OpenAI stage. Keep it aligned
with the priced model in `config/sources.toml` before changing it.

Quality judging, classification/importance, drafting, and compression use the
standard `gpt-5.6-terra` rates configured in `config/sources.toml`. They share
one $1.00 ceiling for the entire run. Before each API batch, the pipeline
reserves enough room for its maximum response; if that batch would exceed the
remaining allowance, it uses the stage's deterministic fallback instead. With
`--show-diagnostics`, the CLI reports total usage and cost, a per-stage cost
breakdown, and whether the shared budget was exhausted.

Required for Phase 1 Telegram delivery:

```text
BRIEFING_DELIVERY_CHANNEL=telegram
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
TELEGRAM_CHAT_IDS=...
```

Optional:

```text
BRIEFING_LOOKBACK_HOURS=30
BRIEFING_MAX_ARTICLES=240
BRIEFING_TIMEZONE=America/New_York
BRIEFING_MEGA_CAP_TICKERS=AAPL,MSFT,NVDA,TSLA,AMZN,META,GOOGL
BRIEFING_CA_BUNDLE=/etc/ssl/cert.pem
BRIEFING_COMPRESSION=true
BRIEFING_MAX_CHARS_PER_MESSAGE_SMS=1400
BRIEFING_MAX_STORIES_PER_CATEGORY_SMS=5
BRIEFING_MAX_SOURCES_PER_STORY=3
BRIEFING_INCLUDE_LINKS_SMS=false
BRIEFING_INCLUDE_LINKS_TELEGRAM=false
```

Required for Gmail email delivery (keep these only in the local `.env`):

```text
GMAIL_SMTP_HOST=smtp.gmail.com
GMAIL_SMTP_PORT=587
GMAIL_SMTP_USERNAME=sender@gmail.com
GMAIL_SMTP_APP_PASSWORD=...
EMAIL_FROM=sender@gmail.com
EMAIL_TO=first@example.com,second@example.com
SEC_CONTACT_EMAIL=newsagent-contact@gmail.com
TIINGO_API_KEY=...
EODHD_API_KEY=...
NEWSLETTER_SHOW_WATCHLIST=true
```

Use `news-briefing --dry-run --to email --email-parity` to test the temporary
Gmail-only delivery baseline. It deliberately renders the Telegram digest
unchanged and does not require market-data keys. It is a temporary bridge;
native email delivery is `news-briefing --send --to email` after both quote
provider keys and `SEC_CONTACT_EMAIL` are configured. Native email runs always
retrieve and evaluate the Watchlist. Set `NEWSLETTER_SHOW_WATCHLIST=false` to
omit the Watchlist from HTML and plain-text output without disabling that
background processing; unset it or set it to `true` to restore the section.
Gate A evaluation starts disabled, so normal
editions initially include the exact notice `Watchlist evaluation disabled.`;
that notice does not mean Watchlist retrieval is disabled.

To send a fresh, same-day Gmail test revision without changing briefing
history or replacing the original edition, run:

```bash
news-briefing --send --to email --email-rebuild-today --confirm
```

Each invocation creates a separately tracked `Test resend` revision. It is
manual-only, has a `[TEST]` subject, and cannot affect production Watchlist
suppression or Gate A metrics. To resend a stored edition byte-for-byte instead,
use `news-briefing --email-resend EDITION_ID --confirm`.

Watchlist evaluation is opt-in. After the test suite and a full no-send run pass
for the same implementation version, activate it explicitly:

```bash
news-briefing --dry-run --to email --show-diagnostics \
  --activate-watchlist-gate --tests-passed \
  --implementation-version VERSION --confirm
```

Independent benchmark events can be imported and reviewed locally with
`--watchlist-benchmark-import FILE` and `--review-watchlist-benchmark`.
Use `--review-watchlist-relationships` for ambiguity review and
`--review-watchlist-evaluations` for rendered-event and large-move review.
If a fully measurable Gate A window fails, the regular newsletter is replaced
by one administrative failure alert and scheduled work halts. Recovery requires
`news-briefing --restart-after-gate-failure --confirm`; that command performs a
no-send health check and never sends a newsletter itself.

To schedule native Telegram-plus-email delivery locally, copy
`scripts/com.newsagent.briefing.plist` to `~/Library/LaunchAgents/` and load it
with `launchctl load ~/Library/LaunchAgents/com.newsagent.briefing.plist`.
It runs Gmail-only attempts at 8:20, 8:25, 8:30, and 8:35 AM in
`BRIEFING_TIMEZONE`. SQLite prevents a second accepted current-date edition;
only definite pre-DATA failures are retried automatically.

Useful CLI options:

```bash
news-briefing --watchlist config/watchlist.json
news-briefing --history-path data/story_history.json
news-briefing --ignore-history
news-briefing --show-skipped
news-briefing --show-diagnostics
news-briefing --format sms
news-briefing --format telegram
news-briefing --format console
news-briefing --brief
news-briefing --alerts --dry-run
news-briefing --alerts --send
```

Phase 4 SMS placeholders, not required for Phase 1:

```text
BRIEFING_TO_NUMBER=
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_FROM_NUMBER=
```

## Phase 1 Telegram Setup

1. In Telegram, open a chat with `@BotFather`.
2. Send `/newbot` and follow the prompts.
3. Copy the bot token into your local `.env` as `TELEGRAM_BOT_TOKEN`.
4. Start a chat with your new bot and send it any message.
5. Get your chat ID by opening this URL in a browser, replacing `BOT_TOKEN_HERE` with your token:

```text
https://api.telegram.org/botBOT_TOKEN_HERE/getUpdates
```

6. Find the `chat.id` value in the response and put it in `.env` as `TELEGRAM_CHAT_ID`.
7. Test delivery:

```bash
news-briefing --test-telegram
```

To send individual copies to more than one Telegram chat, have each person open
the bot link and press **Start**, then refresh `getUpdates` and add their
`chat.id` values to `TELEGRAM_CHAT_IDS`:

```text
TELEGRAM_CHAT_IDS=8325088675,8748244551
```

When `TELEGRAM_CHAT_IDS` is set, it is used instead of `TELEGRAM_CHAT_ID`.

8. Send the real OpenAI-generated briefing:

```bash
news-briefing --send
```

For a cheaper pipeline check that still sends to Telegram:

```bash
news-briefing --send --no-openai
```

To use OpenAI for quality judging and classification/importance while keeping
drafting and compression deterministic, use classify-only mode:

```bash
news-briefing --send --openai-mode classify-only
```

OpenAI modes:

- `full`: send ranked article context to OpenAI for the full briefing
- `classify-only`: use OpenAI for quality judging and classification/importance,
  then use deterministic fallback prose
- `off`: use deterministic fallback summaries only; same as `--no-openai`

Do not put the Telegram bot token in source code, tests, README examples, or committed files. It belongs only in local `.env` or a secret manager.

## Personal Watchlist

The default watchlist lives in `config/watchlist.json` and includes AI,
startups, venture capital, fintech, creator economy, gaming, education
technology, Columbia, NYC, Endless Studios, mega-cap tickers, BTC/ETH, IPOs,
interest rates, inflation, and the Federal Reserve.

Watchlist matches are checked against titles, summaries, snippets, explicit
tickers, and topic aliases. Matching stories receive a modest score boost and
show a `Watchlist:` tag in the briefing, but low-quality one-source stories are
not promoted solely because they match the watchlist.

## Story History And Skipped Logs

Story history is stored in `data/story_history.json` by default after a real
send. Repeated unchanged clusters are suppressed; meaningful updates can still
appear with an `Update:` note.

Skipped-story audit logs are written silently to:

```text
data/skipped_stories_YYYY-MM-DD.json
```

Use `--show-skipped` to print a readable source-distribution block and skipped
story table after the briefing.

## Message Formatting

Final SMS/Telegram text is rendered through `src/news_agent/formatting.py`, so
dry runs and sends use the same phone-friendly layout.

Format modes:

- `console`: default for `--dry-run`; prints separators, exact message bodies,
  character counts, estimated SMS segments, and omitted-story totals
- `telegram`: default for Telegram sends; readable spacing, compact source
  attribution, and room for longer messages
- `sms`: default for SMS sends; stricter message length, no links, fewer stories

Use `--brief` for ultra-compact one-line story items:

```bash
news-briefing --dry-run --no-openai --format console --brief
news-briefing --send --no-openai --format telegram --brief
```

## Breaking Alerts

Alert mode is separate from the morning briefing:

```bash
news-briefing --alerts --dry-run
news-briefing --alerts --send
```

Alerts are disabled by default in `config/alerts.json`. Enable them there and
adjust cooldowns or thresholds as needed. Alert history is stored in
`data/alert_history.json` to avoid repeated sends.

## Scheduling

### Local Cron

Run at 7:00 AM Eastern every weekday:

```cron
0 7 * * 1-5 cd /Users/raymondwang/Documents/Codex/2026-07-07/i-wa/outputs/morning-news-agent && . .venv/bin/activate && news-briefing --send >> logs/briefing.log 2>&1
```

### GitHub Actions

Copy `.github/workflows/morning-briefing.yml` into a private repository, add the environment values as GitHub Actions secrets, and adjust the cron if desired. GitHub cron uses UTC.

## Source Tuning

Edit `config/sources.toml` to change sources, add feeds, and adjust category keywords. The agent rewards:

- multiple reputable sources covering the same story
- market, policy, safety, geopolitical, or social impact
- stories likely to matter over the next few days
- recent publication times

It penalizes low-source-count items, niche one-offs, and duplicate variants.

## Finance Stock Snapshot

The fifth text also receives a stock snapshot with:

- tickers explicitly mentioned in that morning's headlines, such as `$NVDA`, `(AAPL)`, `NASDAQ: MSFT`, or `TSLA stock`
- known mega-cap company names mapped to tickers when they appear in headlines
- the mega-cap watchlist: `AAPL`, `MSFT`, `NVDA`, `TSLA`, `AMZN`, `META`, `GOOGL`
- quote/change data from Yahoo Finance's chart endpoint when reachable
- explained market movers from a Stooq-style no-key CSV provider when reachable

If quote data is unavailable, the briefing still includes the mention counts and sources.

The market mover detector checks major ETFs, mega-cap stocks, sector ETFs,
BTC/ETH, and simple oil/gold/dollar/rate proxies. It flags large moves, looks
for recent causal headlines, and includes only the strongest explained moves or
very large unexplained moves with cautious wording.

Free market data limitations:

- Stooq coverage varies by symbol and asset class, especially crypto and macro proxies.
- The provider currently uses available CSV fields, which may approximate previous close depending on the instrument.
- If the data endpoint is unavailable, mover detection degrades gracefully and the rest of the briefing still runs.

## Example Dry Run Shape

```text
==============================
TEXT 1/5: BUSINESS + TECH
==============================
🧠 BUSINESS + TECH — July 10

• Nvidia shares jump after earnings beat
  What happened: Nvidia raised guidance after strong AI chip demand.
  Why it matters: It reinforces investor confidence in the AI infrastructure trade.
  Sources: Reuters, CNBC

==============================
TEXT 5/5: FINANCE
==============================
💸 FINANCE — July 10

Market snapshot
• AAPL: 123.45 (+1.2%)
• NVDA: 200.00 (+5.6%)

Big movers
• NVDA +5.6% — earnings beat expectations.

Summary
Total messages: 5
Message 1: 432 chars, approx 3 SMS segments
Omitted stories: 0
```

## Notes

- Telegram messages are sent as one header plus five separate briefing messages. Long messages are split safely before sending.
- The agent reads rich RSS/Atom content and selectively extracts article text only from explicitly permitted domains. It does not bypass paywalls, authentication, or bot protection; blocked pages fall back to feed evidence.
- Likely finalists are enriched within configured request limits, then rescored. Stories below the minimum evidence threshold are excluded as `insufficient story context` rather than padded from a headline.
- Drafting failures are visible through `--show-diagnostics` and a stderr warning; every paragraph records whether it came from the model or a deterministic fallback.
- For best results, keep a mix of wire services, national outlets, finance outlets, tech outlets, international outlets, and culture/sports sources.

## Phase 4 SMS

Delivery is behind `NotificationSender`, so SMS can be restored without touching the news pipeline. The current Twilio adapter lives in `src/news_agent/notifications/sms.py`; channel selection lives in `src/news_agent/notifications/factory.py`.

To switch later, set:

```text
BRIEFING_DELIVERY_CHANNEL=sms
BRIEFING_TO_NUMBER=+15555550124
TWILIO_ACCOUNT_SID=...
TWILIO_AUTH_TOKEN=...
TWILIO_FROM_NUMBER=+15555550123
```
