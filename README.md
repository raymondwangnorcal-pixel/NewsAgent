# Morning News Agent

A scheduled AI briefing agent that gathers signals from reputable news sources every morning, ranks stories by frequency and expected impact, and sends five concise briefing messages:

1. Business and technology
2. Domestic U.S. news
3. Global news
4. Culture, social, and media trends
5. Financial news

The agent is intentionally pipeline-shaped instead of chat-shaped:

```text
RSS/news inputs -> article normalization -> duplicate clustering -> category scoring
-> watchlist/source/history checks -> stock mentions + market mover detection
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
news-briefing --dry-run --openai-mode polish
news-briefing --test-telegram
news-briefing --send
news-briefing --send --no-openai
```

Use `--dry-run` until the content looks right. It prints the five messages instead of sending them.

## Environment

Required for AI summarization:

```text
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-5.4-mini
```

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
BRIEFING_MAX_CHARS_PER_MESSAGE_SMS=1400
BRIEFING_MAX_STORIES_PER_CATEGORY_SMS=5
BRIEFING_MAX_SOURCES_PER_STORY=3
BRIEFING_INCLUDE_LINKS_SMS=false
BRIEFING_INCLUDE_LINKS_TELEGRAM=true
```

Useful CLI options:

```bash
news-briefing --watchlist config/watchlist.json
news-briefing --history-path data/story_history.json
news-briefing --ignore-history
news-briefing --show-skipped
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

For a lower-cost OpenAI-backed briefing, use polish mode. It builds a local
fallback draft first, then sends only that compact draft to OpenAI for final
rewriting:

```bash
news-briefing --send --openai-mode polish
```

OpenAI modes:

- `full`: send ranked article context to OpenAI for the full briefing
- `polish`: build the local fallback draft, then send only the draft to OpenAI
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
- The agent does not scrape paywalled article bodies. It uses headlines, summaries, timestamps, and source names from configured feeds.
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
