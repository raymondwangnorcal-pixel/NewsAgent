from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, time as clock_time, timedelta
from collections.abc import Callable
from typing import Protocol

from news_agent.fetch import USER_AGENT, _ssl_context
from news_agent.time import briefing_now, briefing_timezone, briefing_today


logger = logging.getLogger(__name__)
REGULAR_NYSE_CLOSE = clock_time(16, 15)


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


def _nth_weekday(year: int, month: int, weekday: int, occurrence: int) -> date:
    value = date(year, month, 1)
    return value + timedelta(days=(weekday - value.weekday()) % 7 + 7 * (occurrence - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    value = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
    return value - timedelta(days=(value.weekday() - weekday) % 7)


def _easter_sunday(year: int) -> date:
    """Return Gregorian Easter without an external market-calendar dependency."""
    century = year // 100
    remainder = year % 100
    correction = (century - century // 4 - (8 * century + 13) // 25 + 19 * remainder + 15) % 30
    adjustment = (remainder + remainder // 4 + correction + 2 - century + century // 4) % 7
    month = 3 + (correction + adjustment + 40) // 44
    day = correction + adjustment + 28 - 31 * (month // 4)
    return date(year, month, day)


def _observed_fixed_holiday(year: int, month: int, day: int) -> date:
    value = date(year, month, day)
    if value.weekday() == 5:
        return value - timedelta(days=1)
    if value.weekday() == 6:
        return value + timedelta(days=1)
    return value


def is_nyse_trading_day(value: date) -> bool:
    """Cover the regular NYSE holiday calendar used by the Watchlist's U.S. listings."""
    if value.weekday() >= 5:
        return False
    year = value.year
    holidays = {
        _observed_fixed_holiday(year, 1, 1),
        _nth_weekday(year, 1, 0, 3),
        _nth_weekday(year, 2, 0, 3),
        _easter_sunday(year) - timedelta(days=2),
        _last_weekday(year, 5, 0),
        _observed_fixed_holiday(year, 6, 19),
        _observed_fixed_holiday(year, 7, 4),
        _nth_weekday(year, 9, 0, 1),
        _nth_weekday(year, 11, 3, 4),
        _observed_fixed_holiday(year, 12, 25),
    }
    return value not in holidays


def expected_quote_close_date(as_of: datetime | None = None) -> date:
    """Return the latest completed regular NYSE session for a New York run time."""
    now = as_of or briefing_now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=briefing_timezone())
    else:
        now = now.astimezone(briefing_timezone())
    candidate = now.date()
    if now.time() < REGULAR_NYSE_CLOSE:
        candidate -= timedelta(days=1)
    while not is_nyse_trading_day(candidate):
        candidate -= timedelta(days=1)
    return candidate


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
    expected_close_date: date | None = None,
) -> EndOfDayQuote | None:
    resolved = providers or (TiingoQuoteProvider(), EodhdQuoteProvider())
    if not any(getattr(provider, "token", True) for provider in resolved):
        return None
    expected = (expected_close_date or expected_quote_close_date()).isoformat()
    resolved_deadline = deadline if deadline is not None else time.monotonic() + retry_seconds
    while True:
        for provider in resolved:
            quote = provider.fetch(ticker)
            if quote is None:
                continue
            if quote.close_date == expected:
                return quote
            logger.warning(
                "watchlist quote rejected: ticker=%s provider=%s close_date=%s expected_close_date=%s",
                ticker, quote.provider, quote.close_date, expected,
            )
        if time.monotonic() >= resolved_deadline:
            return None
        sleeper(min(5.0, max(0.0, resolved_deadline - time.monotonic())))


def fetch_quotes_with_shared_deadline(
    tickers: tuple[str, ...],
    retry_seconds: float = 300.0,
    fetcher: QuoteFetcher = fetch_quote_with_fallback,
    expected_close_date: date | None = None,
) -> dict[str, EndOfDayQuote | None]:
    """Fetch independent ticker quotes concurrently under one wall-clock deadline."""
    if not tickers:
        return {}
    deadline = time.monotonic() + retry_seconds
    expected = expected_close_date or expected_quote_close_date()
    with ThreadPoolExecutor(max_workers=len(tickers), thread_name_prefix="watchlist-quote") as executor:
        futures = {
            ticker: executor.submit(fetcher, ticker, deadline=deadline, expected_close_date=expected)
            for ticker in tickers
        }
        results: dict[str, EndOfDayQuote | None] = {}
        for ticker, future in futures.items():
            try:
                results[ticker] = future.result()
            except Exception:
                results[ticker] = None
    return results
