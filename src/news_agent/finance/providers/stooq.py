from __future__ import annotations

import csv
import io
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from news_agent.fetch import USER_AGENT, _ssl_context


@dataclass(frozen=True)
class MarketQuote:
    symbol: str
    latest_price: float
    previous_close: float
    volume: int | None = None
    as_of: str = ""


class StooqMarketDataProvider:
    endpoint = "https://stooq.com/q/l/"

    def fetch_quotes(self, symbols: dict[str, str]) -> dict[str, MarketQuote]:
        if not symbols:
            return {}
        query = ",".join(symbols.values())
        url = self.endpoint + "?" + urllib.parse.urlencode({"s": query, "f": "sd2t2ohlcv", "h": "", "e": "csv"})
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=10, context=_ssl_context()) as response:
                body = response.read().decode("utf-8", errors="replace")
        except (TimeoutError, urllib.error.URLError, urllib.error.HTTPError):
            return {}
        return parse_stooq_csv(body, symbols)


def parse_stooq_csv(body: str, symbols: dict[str, str]) -> dict[str, MarketQuote]:
    provider_to_symbol = {provider_symbol.lower(): symbol for symbol, provider_symbol in symbols.items()}
    quotes: dict[str, MarketQuote] = {}
    for row in csv.DictReader(io.StringIO(body)):
        symbol = provider_to_symbol.get(str(row.get("Symbol", "")).lower())
        if not symbol:
            continue
        latest = parse_float(row.get("Close"))
        previous = parse_float(row.get("Open"))
        if latest is None or previous in (None, 0):
            continue
        as_of = " ".join(part for part in (row.get("Date"), row.get("Time")) if part and part != "N/D")
        quotes[symbol] = MarketQuote(
            symbol=symbol,
            latest_price=latest,
            previous_close=previous,
            volume=parse_int(row.get("Volume")),
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
