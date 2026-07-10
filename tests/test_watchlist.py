from __future__ import annotations

from news_agent.watchlist import WatchlistEntry, match_watchlist_text


def test_watchlist_matches_exact_ticker() -> None:
    entries = (WatchlistEntry("NVDA", ticker="NVDA", aliases=("Nvidia",)),)

    matches = match_watchlist_text("Nvidia (NVDA) rises after earnings.", entries)

    assert matches == ("NVDA",)


def test_watchlist_matches_fuzzy_topic_alias() -> None:
    entries = (WatchlistEntry("education technology", aliases=("edtech", "education software")),)

    matches = match_watchlist_text("A new edtech startup raised a seed round.", entries)

    assert matches == ("education technology",)
