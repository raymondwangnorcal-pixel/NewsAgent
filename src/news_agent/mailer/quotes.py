from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import timedelta
from collections.abc import Callable
from typing import Protocol

from news_agent.fetch import USER_AGENT, _ssl_context
from news_agent.time import briefing_today


@dataclass(frozen=True)
class EndOfDayQuote:
    ticker: str
    close_date: str
    close_price: float
    previous_close: float
    provider: str

    @property
    def percent_change(self) -> float:
        return ((self.close_price - self.previous_close) / self.previous_close) * 100 if self.previous_close else 0.0


class QuoteProvider(Protocol):
    name: str

    def fetch(self, ticker: str) -> EndOfDayQuote | None:
        ...


QuoteFetcher = Callable[..., EndOfDayQuote | None]


def _get_json(url: str, headers: dict[str, str]) -> object:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **headers})
    with urllib.request.urlopen(request, timeout=12, context=_ssl_context()) as response:
        return json.loads(response.read().decode("utf-8"))


class TiingoQuoteProvider:
    name = "Tiingo"

    def __init__(self, token: str | None = None) -> None:
        self.token = token or os.getenv("TIINGO_API_KEY", "").strip()

    def fetch(self, ticker: str) -> EndOfDayQuote | None:
        if not self.token:
            return None
        end = briefing_today()
        start = end - timedelta(days=10)
        url = f"https://api.tiingo.com/tiingo/daily/{urllib.parse.quote(ticker)}/prices?" + urllib.parse.urlencode(
            {"startDate": start.isoformat(), "endDate": end.isoformat()}
        )
        try:
            payload = _get_json(url, {"Authorization": f"Token {self.token}"})
        except (TimeoutError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError):
            return None
        if not isinstance(payload, list) or not payload:
            return None
        current = payload[-1]
        previous = payload[-2] if len(payload) > 1 else None
        if not isinstance(current, dict) or not isinstance(previous, dict):
            return None
        try:
            return EndOfDayQuote(
                ticker=ticker,
                close_date=str(current["date"])[:10],
                close_price=float(current["close"]),
                previous_close=float(previous["close"]),
                provider=self.name,
            )
        except (KeyError, TypeError, ValueError):
            return None


class EodhdQuoteProvider:
    name = "EODHD"

    def __init__(self, token: str | None = None) -> None:
        self.token = token or os.getenv("EODHD_API_KEY", "").strip()

    def fetch(self, ticker: str) -> EndOfDayQuote | None:
        if not self.token:
            return None
        end = briefing_today()
        start = end - timedelta(days=10)
        url = f"https://eodhd.com/api/eod/{urllib.parse.quote(ticker)}.US?" + urllib.parse.urlencode(
            {"api_token": self.token, "fmt": "json", "from": start.isoformat(), "to": end.isoformat()}
        )
        try:
            payload = _get_json(url, {})
        except (TimeoutError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError):
            return None
        if not isinstance(payload, list) or len(payload) < 2:
            return None
        current, previous = payload[-1], payload[-2]
        if not isinstance(current, dict) or not isinstance(previous, dict):
            return None
        try:
            return EndOfDayQuote(
                ticker=ticker,
                close_date=str(current["date"]),
                close_price=float(current["close"]),
                previous_close=float(previous["close"]),
                provider=self.name,
            )
        except (KeyError, TypeError, ValueError):
            return None


def validate_quote_provider_configuration() -> None:
    missing = [
        name
        for name in ("TIINGO_API_KEY", "EODHD_API_KEY")
        if not os.getenv(name, "").strip()
    ]
    if missing:
        raise ValueError(f"Native email Watchlist requires {', '.join(missing)} in local .env.")


def fetch_quote_with_fallback(
    ticker: str,
    providers: tuple[QuoteProvider, ...] | None = None,
    retry_seconds: float = 300.0,
    sleeper: Callable[[float], None] = time.sleep,
    deadline: float | None = None,
) -> EndOfDayQuote | None:
    resolved = providers or (TiingoQuoteProvider(), EodhdQuoteProvider())
    if not any(getattr(provider, "token", True) for provider in resolved):
        return None
    resolved_deadline = deadline if deadline is not None else time.monotonic() + retry_seconds
    while True:
        for provider in resolved:
            quote = provider.fetch(ticker)
            if quote is not None:
                return quote
        if time.monotonic() >= resolved_deadline:
            return None
        sleeper(min(5.0, max(0.0, resolved_deadline - time.monotonic())))


def fetch_quotes_with_shared_deadline(
    tickers: tuple[str, ...],
    retry_seconds: float = 300.0,
    fetcher: QuoteFetcher = fetch_quote_with_fallback,
) -> dict[str, EndOfDayQuote | None]:
    """Fetch independent ticker quotes concurrently under one wall-clock deadline."""
    if not tickers:
        return {}
    deadline = time.monotonic() + retry_seconds
    with ThreadPoolExecutor(max_workers=len(tickers), thread_name_prefix="watchlist-quote") as executor:
        futures = {
            ticker: executor.submit(fetcher, ticker, deadline=deadline)
            for ticker in tickers
        }
        results: dict[str, EndOfDayQuote | None] = {}
        for ticker, future in futures.items():
            try:
                results[ticker] = future.result()
            except Exception:
                results[ticker] = None
    return results
