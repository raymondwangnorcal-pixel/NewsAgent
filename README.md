# Morning News Agent

A scheduled AI briefing agent that gathers signals from reputable news sources every morning, ranks stories by frequency and expected impact, and sends six concise briefing messages:

1. Business and technology
2. Domestic U.S. news
3. Global news
4. Culture, social, and media trends
5. Financial news
6. What matters most today

The agent is intentionally pipeline-shaped instead of chat-shaped:

```text
RSS/news inputs -> article normalization -> duplicate clustering -> category scoring
-> stock mention extraction + quote snapshot -> OpenAI structured briefing
-> NotificationSender -> Telegram now, SMS later
```

## Quick Start

```bash
cd /Users/raymondwang/Documents/Codex/2026-07-07/i-wa/outputs/morning-news-agent
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[providers]"
cp .env.example .env
```

Fill in `.env`, then run:

```bash
news-briefing --dry-run
news-briefing --test-telegram
news-briefing --send
news-briefing --send --no-openai
```

Use `--dry-run` until the content looks right. It prints the six messages instead of sending them.

## Environment

Required for AI summarization:

```text
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-5.5
```

Required for Phase 1 Telegram delivery:

```text
BRIEFING_DELIVERY_CHANNEL=telegram
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

Optional:

```text
BRIEFING_LOOKBACK_HOURS=30
BRIEFING_MAX_ARTICLES=240
BRIEFING_TIMEZONE=America/New_York
BRIEFING_MEGA_CAP_TICKERS=AAPL,MSFT,NVDA,TSLA,AMZN,META,GOOGL
BRIEFING_CA_BUNDLE=/etc/ssl/cert.pem
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
5. Get your chat ID by opening this URL in a browser, replacing `<token>` with your token:

```text
https://api.telegram.org/bot<token>/getUpdates
```

6. Find the `chat.id` value in the response and put it in `.env` as `TELEGRAM_CHAT_ID`.
7. Test delivery:

```bash
news-briefing --test-telegram
```

8. Send the real OpenAI-generated briefing:

```bash
news-briefing --send
```

For a cheaper pipeline check that still sends to Telegram:

```bash
news-briefing --send --no-openai
```

Do not put the Telegram bot token in source code, tests, README examples, or committed files. It belongs only in local `.env` or a secret manager.

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

If quote data is unavailable, the briefing still includes the mention counts and sources.

## Notes

- Telegram messages are sent as one header plus six separate briefing messages. Long messages are split safely before sending.
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
