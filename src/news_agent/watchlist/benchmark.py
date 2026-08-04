from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from news_agent.watchlist.models import BenchmarkCandidate


def load_benchmark_candidates(path: Path, allowed_tickers: set[str]) -> tuple[BenchmarkCandidate, ...]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.casefold() == ".jsonl":
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    else:
        loaded = json.loads(text)
        rows = loaded.get("items", loaded) if isinstance(loaded, dict) else loaded
    if not isinstance(rows, list):
        raise ValueError("Benchmark import must contain a list of items.")
    candidates = tuple(_parse_candidate(row, allowed_tickers) for row in rows)
    identities = [(item.ticker, item.event_date, item.source_url) for item in candidates]
    if len(set(identities)) != len(identities):
        raise ValueError("Benchmark import contains duplicate ticker/date/source rows.")
    return candidates


def _parse_candidate(raw: Any, allowed_tickers: set[str]) -> BenchmarkCandidate:
    if not isinstance(raw, dict):
        raise ValueError("Every benchmark item must be an object.")
    ticker = str(raw.get("ticker", "")).strip().upper()
    if ticker not in allowed_tickers:
        raise ValueError(f"Unsupported benchmark ticker: {ticker}")
    source_url = str(raw.get("source_url", "")).strip()
    parsed = urlparse(source_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError(f"Benchmark source_url must be HTTPS for {ticker}.")
    headline = str(raw.get("headline", "")).strip()
    rationale = str(raw.get("materiality_rationale", "")).strip()
    provenance = str(raw.get("provenance", "")).strip()
    if not headline or not rationale or not provenance:
        raise ValueError(f"Benchmark item {ticker} needs headline, materiality_rationale, and provenance.")
    try:
        event_date = date.fromisoformat(str(raw.get("event_date", "")))
    except ValueError as exc:
        raise ValueError(f"Benchmark item {ticker} needs an ISO event_date.") from exc
    return BenchmarkCandidate(ticker, event_date, source_url, headline, rationale, provenance)
