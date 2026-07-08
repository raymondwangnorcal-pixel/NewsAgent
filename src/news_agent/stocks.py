from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict

from news_agent.fetch import USER_AGENT, _ssl_context
from news_agent.models import Article, StockMention, StockQuote, StockSnapshot


DEFAULT_MEGA_CAPS = ("AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "META", "GOOGL")
TICKER_STOPWORDS = {
    "AI", "API", "CEO", "CFO", "COO", "CTO", "DOJ", "ETF", "EU", "FDA", "FTC",
    "GDP", "IPO", "MLB", "NBA", "NATO", "NFL", "SEC", "TV", "UK", "UN", "US",
    "USA", "USD", "VC",
}
EXPLICIT_TICKER_PATTERNS = (
    re.compile(r"\$([A-Z][A-Z0-9.]{0,5})\b"),
    re.compile(r"\b(?:NASDAQ|NYSE|AMEX|NYSEARCA)\s*:\s*([A-Z][A-Z0-9.]{0,5})\b"),
    re.compile(r"\(([A-Z][A-Z0-9.]{0,5})\)"),
    re.compile(r"\b([A-Z][A-Z0-9.]{0,5})\s+(?:stock|shares)\b"),
)
COMPANY_ALIASES = {
    "alphabet": "GOOGL",
    "amazon": "AMZN",
    "apple": "AAPL",
    "google": "GOOGL",
    "meta": "META",
    "microsoft": "MSFT",
    "nvidia": "NVDA",
    "tesla": "TSLA",
}


def mega_cap_tickers() -> tuple[str, ...]:
    configured = os.getenv("BRIEFING_MEGA_CAP_TICKERS")
    if not configured:
        return DEFAULT_MEGA_CAPS
    tickers = tuple(symbol.strip().upper() for symbol in configured.split(",") if symbol.strip())
    return tickers or DEFAULT_MEGA_CAPS


def extract_tickers_from_headline(headline: str) -> set[str]:
    symbols: set[str] = set()
    for pattern in EXPLICIT_TICKER_PATTERNS:
        for match in pattern.findall(headline):
            symbol = normalize_symbol(match)
            if symbol and symbol not in TICKER_STOPWORDS:
                symbols.add(symbol)

    lowered = headline.lower()
    for alias, symbol in COMPANY_ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}\b", lowered):
            symbols.add(symbol)
    return symbols


def normalize_symbol(raw: str) -> str:
    symbol = raw.strip().upper().replace(".", "-")
    if not re.fullmatch(r"[A-Z][A-Z0-9-]{0,5}", symbol):
        return ""
    return symbol


def collect_stock_mentions(articles: list[Article]) -> tuple[StockMention, ...]:
    headlines: dict[str, list[str]] = defaultdict(list)
    sources: dict[str, set[str]] = defaultdict(set)
    for article in articles:
        for symbol in extract_tickers_from_headline(article.title):
            headlines[symbol].append(article.title)
            sources[symbol].add(article.source)

    mentions = [
        StockMention(
            symbol=symbol,
            mention_count=len(items),
            headlines=tuple(items[:5]),
            sources=tuple(sorted(sources[symbol])[:5]),
        )
        for symbol, items in headlines.items()
    ]
    mentions.sort(key=lambda item: (item.mention_count, len(item.sources), item.symbol), reverse=True)
    return tuple(mentions)


def stooq_symbol(symbol: str) -> str:
    return f"{symbol.lower().replace('-', '.')}.us"


def fetch_yahoo_quotes(symbols: list[str]) -> dict[str, StockQuote]:
    quotes: dict[str, StockQuote] = {}
    for symbol in symbols:
        quote = fetch_yahoo_quote(symbol)
        if quote is not None:
            quotes[symbol] = quote
    return quotes


def fetch_yahoo_quote(symbol: str) -> StockQuote | None:
    url = "https://query1.finance.yahoo.com/v8/finance/chart/" + urllib.parse.quote(symbol) + "?" + urllib.parse.urlencode(
        {"range": "5d", "interval": "1d"}
    )
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(request, timeout=10, context=_ssl_context()) as response:
            body = response.read().decode("utf-8", errors="replace")
    except (TimeoutError, urllib.error.URLError, urllib.error.HTTPError):
        return None

    try:
        data = json.loads(body)
        result = data["chart"]["result"][0]
        meta = result["meta"]
    except (KeyError, TypeError, IndexError, json.JSONDecodeError):
        return None

    price = parse_float(meta.get("regularMarketPrice"))
    quote_rows = result.get("indicators", {}).get("quote", [{}])
    latest_row = quote_rows[0] if quote_rows else {}
    previous_close = (
        parse_float(meta.get("regularMarketPreviousClose"))
        or parse_float(meta.get("previousClose"))
        or previous_close_from_series(latest_row.get("close"))
        or parse_float(meta.get("chartPreviousClose"))
    )
    change_percent = None
    if price is not None and previous_close not in (None, 0):
        change_percent = ((price - previous_close) / previous_close) * 100

    open_price = latest_value(latest_row.get("open"))
    volume = parse_int(meta.get("regularMarketVolume")) or parse_int(latest_value(latest_row.get("volume")))
    as_of = str(meta.get("regularMarketTime", ""))

    return StockQuote(
        symbol=symbol,
        price=price,
        change_percent=change_percent,
        open_price=open_price,
        volume=volume,
        as_of=as_of,
        provider="Yahoo Finance",
    )


def latest_value(values: object) -> object:
    if not isinstance(values, list):
        return None
    for value in reversed(values):
        if value is not None:
            return value
    return None


def previous_close_from_series(values: object) -> float | None:
    if not isinstance(values, list):
        return None
    closes = [parse_float(value) for value in values]
    closes = [value for value in closes if value is not None]
    if len(closes) < 2:
        return None
    return closes[-2]


def fetch_stooq_quotes(symbols: list[str]) -> dict[str, StockQuote]:
    if not symbols:
        return {}
    lookup = {stooq_symbol(symbol): symbol for symbol in symbols}
    query = ",".join(lookup)
    url = "https://stooq.com/q/l/?" + urllib.parse.urlencode(
        {"s": query, "f": "sd2t2ohlcv", "h": "", "e": "csv"}
    )
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=10, context=_ssl_context()) as response:
            body = response.read().decode("utf-8", errors="replace")
    except (TimeoutError, urllib.error.URLError, urllib.error.HTTPError):
        return {}

    quotes: dict[str, StockQuote] = {}
    for row in csv.DictReader(io.StringIO(body)):
        symbol = lookup.get(row.get("Symbol", "").lower())
        if not symbol:
            continue
        price = parse_float(row.get("Close"))
        open_price = parse_float(row.get("Open"))
        change_percent = None
        if price is not None and open_price not in (None, 0):
            change_percent = ((price - open_price) / open_price) * 100
        volume = parse_int(row.get("Volume"))
        as_of = " ".join(part for part in (row.get("Date"), row.get("Time")) if part and part != "N/D")
        quotes[symbol] = StockQuote(
            symbol=symbol,
            price=price,
            change_percent=change_percent,
            open_price=open_price,
            volume=volume,
            as_of=as_of,
        )
    return quotes


def parse_float(value: str | None) -> float | None:
    if not value or value == "N/D":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def parse_int(value: str | None) -> int | None:
    if not value or value == "N/D":
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


async def build_stock_snapshot(articles: list[Article]) -> StockSnapshot:
    mentions = collect_stock_mentions(articles)
    mega_caps = mega_cap_tickers()
    mentioned_symbols = [mention.symbol for mention in mentions]
    quote_symbols = sorted(set(mega_caps) | set(mentioned_symbols))
    quotes = await asyncio.to_thread(fetch_yahoo_quotes, quote_symbols)
    return StockSnapshot(news_mentions=mentions, mega_caps=mega_caps, quotes=quotes)
