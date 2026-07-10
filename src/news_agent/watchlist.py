from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from news_agent.models import StoryCluster


DEFAULT_WATCHLIST_PATH = Path(__file__).resolve().parents[2] / "config" / "watchlist.json"
WORD_RE = re.compile(r"[a-z0-9]+")
EXPLICIT_TICKER_RE = re.compile(r"(?:\$|\b(?:NASDAQ|NYSE|AMEX|NYSEARCA)\s*:?\s*)?([A-Z][A-Z0-9.-]{1,5})\b")


@dataclass(frozen=True)
class WatchlistEntry:
    label: str
    aliases: tuple[str, ...] = ()
    ticker: str = ""
    weight: float = 1.0

    @property
    def terms(self) -> tuple[str, ...]:
        return (self.label, *self.aliases)


DEFAULT_WATCHLIST_ENTRIES: tuple[WatchlistEntry, ...] = (
    WatchlistEntry("AI", ("artificial intelligence", "generative ai")),
    WatchlistEntry("startups", ("startup", "founder", "seed round")),
    WatchlistEntry("venture capital", ("vc", "funding round")),
    WatchlistEntry("fintech", ("financial technology",)),
    WatchlistEntry("creator economy", ("creator", "influencer")),
    WatchlistEntry("gaming", ("video games", "game studio")),
    WatchlistEntry("education technology", ("edtech", "education software")),
    WatchlistEntry("Columbia", ("Columbia University",)),
    WatchlistEntry("NYC", ("New York City", "Manhattan", "Brooklyn")),
    WatchlistEntry("Endless Studios", ("Endless",)),
    WatchlistEntry("AAPL", ("Apple",), ticker="AAPL"),
    WatchlistEntry("MSFT", ("Microsoft",), ticker="MSFT"),
    WatchlistEntry("NVDA", ("Nvidia",), ticker="NVDA"),
    WatchlistEntry("TSLA", ("Tesla",), ticker="TSLA"),
    WatchlistEntry("META", ("Meta", "Facebook"), ticker="META"),
    WatchlistEntry("GOOGL", ("Alphabet", "Google"), ticker="GOOGL"),
    WatchlistEntry("AMZN", ("Amazon",), ticker="AMZN"),
    WatchlistEntry("BTC", ("Bitcoin",), ticker="BTC"),
    WatchlistEntry("ETH", ("Ethereum",), ticker="ETH"),
    WatchlistEntry("IPOs", ("ipo", "public listing")),
    WatchlistEntry("interest rates", ("rates", "treasury yields")),
    WatchlistEntry("inflation", ("cpi", "prices")),
    WatchlistEntry("Federal Reserve", ("Fed", "FOMC")),
)


def load_watchlist(path: Path | None = None) -> tuple[WatchlistEntry, ...]:
    resolved = path or DEFAULT_WATCHLIST_PATH
    if not resolved.exists():
        return DEFAULT_WATCHLIST_ENTRIES

    text = resolved.read_text(encoding="utf-8")
    if resolved.suffix.lower() in {".yaml", ".yml"}:
        return parse_simple_yaml_watchlist(text)

    raw = json.loads(text)
    items = raw.get("items", raw) if isinstance(raw, dict) else raw
    return tuple(entry_from_mapping(item) for item in items)


def parse_simple_yaml_watchlist(text: str) -> tuple[WatchlistEntry, ...]:
    entries: list[WatchlistEntry] = []
    current: dict[str, Any] | None = None
    alias_mode = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("- name:"):
            if current:
                entries.append(entry_from_mapping(current))
            current = {"name": line.split(":", 1)[1].strip().strip('"')}
            alias_mode = False
        elif line.startswith("- ") and current is None:
            entries.append(WatchlistEntry(line[2:].strip().strip('"')))
        elif current is not None and line.startswith("aliases:"):
            alias_mode = True
            current.setdefault("aliases", [])
        elif current is not None and alias_mode and line.startswith("- "):
            current.setdefault("aliases", []).append(line[2:].strip().strip('"'))
        elif current is not None and ":" in line:
            key, value = line.split(":", 1)
            current[key.strip()] = value.strip().strip('"')
            alias_mode = False
    if current:
        entries.append(entry_from_mapping(current))
    return tuple(entries)


def entry_from_mapping(item: object) -> WatchlistEntry:
    if isinstance(item, str):
        return WatchlistEntry(item)
    if not isinstance(item, dict):
        raise ValueError(f"Invalid watchlist entry: {item!r}")
    label = str(item.get("label") or item.get("name") or "").strip()
    if not label:
        raise ValueError(f"Watchlist entry is missing a label/name: {item!r}")
    aliases = tuple(str(alias).strip() for alias in item.get("aliases", ()) if str(alias).strip())
    ticker = str(item.get("ticker", "")).strip().upper()
    weight = float(item.get("weight", 1.0))
    return WatchlistEntry(label=label, aliases=aliases, ticker=ticker, weight=weight)


def normalize_words(value: str) -> set[str]:
    return set(WORD_RE.findall(value.lower()))


def explicit_tickers(text: str) -> set[str]:
    symbols: set[str] = set()
    for match in re.finditer(r"\$([A-Z][A-Z0-9.-]{1,5})\b", text):
        symbols.add(match.group(1).replace(".", "-"))
    for match in re.finditer(r"\b(?:NASDAQ|NYSE|AMEX|NYSEARCA)\s*:?\s*([A-Z][A-Z0-9.-]{1,5})\b", text):
        symbols.add(match.group(1).replace(".", "-"))
    for match in re.finditer(r"\(([A-Z][A-Z0-9.-]{1,5})\)", text):
        symbols.add(match.group(1).replace(".", "-"))
    return symbols


def term_matches(term: str, text: str, words: set[str]) -> bool:
    normalized_term = " ".join(WORD_RE.findall(term.lower()))
    if not normalized_term:
        return False
    if len(normalized_term) <= 3 and normalized_term.isalpha():
        return re.search(rf"\b{re.escape(term)}\b", text, flags=re.IGNORECASE) is not None
    if re.search(rf"\b{re.escape(normalized_term)}\b", " ".join(WORD_RE.findall(text.lower()))):
        return True
    term_words = set(normalized_term.split())
    return len(term_words) > 1 and term_words.issubset(words)


def match_watchlist_text(text: str, entries: tuple[WatchlistEntry, ...]) -> tuple[str, ...]:
    words = normalize_words(text)
    tickers = explicit_tickers(text)
    matches: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        matched = False
        if entry.ticker and entry.ticker in tickers:
            matched = True
        if not matched:
            matched = any(term_matches(term, text, words) for term in entry.terms)
        if matched and entry.label not in seen:
            matches.append(entry.label)
            seen.add(entry.label)
    return tuple(matches)


def match_cluster_watchlist(
    cluster: StoryCluster,
    entries: tuple[WatchlistEntry, ...],
) -> tuple[str, ...]:
    text = "\n".join((cluster.title, cluster.representative_summary, cluster.merged_text))
    return match_watchlist_text(text, entries)


def watchlist_score(matches: tuple[str, ...], entries: tuple[WatchlistEntry, ...]) -> float:
    if not matches:
        return 0.0
    weights = {entry.label: entry.weight for entry in entries}
    return min(2.0, sum(weights.get(match, 1.0) for match in matches) * 0.35)
