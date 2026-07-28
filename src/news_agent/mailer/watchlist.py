from __future__ import annotations

import json
import re
from pathlib import Path

from news_agent.mailer.models import EmailWatchlistEntry


DEFAULT_EMAIL_WATCHLIST_PATH = Path(__file__).resolve().parents[3] / "config" / "email_watchlist.json"
DEFAULT_GENERAL_WATCHLIST_PATH = Path(__file__).resolve().parents[3] / "config" / "watchlist.json"
TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.-]{0,9}$")
ALLOWED_INSTRUMENT_TYPES = {"stock", "adr", "etf"}


def load_email_watchlist(path: Path = DEFAULT_EMAIL_WATCHLIST_PATH) -> tuple[EmailWatchlistEntry, ...]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Email watchlist file is missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Email watchlist is not valid JSON: {path}") from exc
    items = raw.get("items", raw) if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        raise ValueError("Email watchlist must contain an items array.")
    if not 1 <= len(items) <= 10:
        raise ValueError("Email watchlist must contain between one and ten entries.")
    entries: list[EmailWatchlistEntry] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Every email watchlist entry must be an object.")
        ticker = str(item.get("ticker", "")).strip().upper()
        display_name = str(item.get("display_name") or item.get("name") or "").strip()
        instrument_type = str(item.get("instrument_type", "")).strip().lower()
        if not TICKER_RE.fullmatch(ticker):
            raise ValueError(f"Invalid email watchlist ticker: {ticker!r}")
        if ticker in seen:
            raise ValueError(f"Duplicate email watchlist ticker: {ticker}")
        if not display_name:
            raise ValueError(f"Email watchlist entry {ticker} needs display_name.")
        if instrument_type not in ALLOWED_INSTRUMENT_TYPES:
            raise ValueError(f"Email watchlist entry {ticker} needs instrument_type stock, adr, or etf.")
        aliases = tuple(str(value).strip() for value in item.get("aliases", ()) if str(value).strip())
        entries.append(EmailWatchlistEntry(ticker, display_name, instrument_type, aliases))
        seen.add(ticker)
    return tuple(entries)


def validate_shared_watchlist_consistency(
    email_path: Path = DEFAULT_EMAIL_WATCHLIST_PATH,
    general_path: Path = DEFAULT_GENERAL_WATCHLIST_PATH,
) -> None:
    """Reject conflicting aliases for tickers intentionally shared by both lists."""
    email_entries = load_email_watchlist(email_path)
    try:
        raw_general = json.loads(general_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"General watchlist file is missing: {general_path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"General watchlist is not valid JSON: {general_path}") from exc
    items = raw_general.get("items", raw_general) if isinstance(raw_general, dict) else raw_general
    if not isinstance(items, list):
        raise ValueError("General watchlist must contain an items array.")
    general = {
        str(item.get("ticker", "")).strip().upper(): tuple(str(alias).strip() for alias in item.get("aliases", ()) if str(alias).strip())
        for item in items
        if isinstance(item, dict) and str(item.get("ticker", "")).strip()
    }
    for entry in email_entries:
        if entry.ticker in general and entry.aliases != general[entry.ticker]:
            raise ValueError(
                f"Ticker {entry.ticker} has different aliases in email_watchlist.json and watchlist.json."
            )
