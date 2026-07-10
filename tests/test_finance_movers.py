from __future__ import annotations

from datetime import datetime, timezone

from news_agent.finance.movers import AssetDefinition, detect_market_movers
from news_agent.finance.providers.stooq import MarketQuote, parse_stooq_csv
from news_agent.models import Article


class FakeProvider:
    def __init__(self, quotes: dict[str, MarketQuote]) -> None:
        self.quotes = quotes

    def fetch_quotes(self, symbols: dict[str, str]) -> dict[str, MarketQuote]:
        return {symbol: quote for symbol, quote in self.quotes.items() if symbol in symbols}


def article(title: str, source: str = "Reuters", summary: str = "") -> Article:
    return Article(
        title=title,
        url=f"https://example.com/{hash(title)}",
        source=source,
        published_at=datetime.now(timezone.utc),
        summary=summary,
        reputation=1.0,
    )


def test_parse_stooq_csv_maps_rows_to_quotes() -> None:
    csv_body = "Symbol,Date,Time,Open,High,Low,Close,Volume\nnvda.us,2026-07-10,15:00:00,100,112,99,110,12345\n"

    quotes = parse_stooq_csv(csv_body, {"NVDA": "nvda.us"})

    assert quotes["NVDA"].latest_price == 110
    assert quotes["NVDA"].previous_close == 100
    assert quotes["NVDA"].volume == 12345


def test_detect_market_mover_attaches_high_confidence_earnings_reason(tmp_path) -> None:
    universe = (AssetDefinition("NVDA", "Nvidia", "mega_cap_stock", "nvda.us", 3.0, 1.0),)
    provider = FakeProvider({"NVDA": MarketQuote("NVDA", latest_price=110, previous_close=100)})
    articles = [
        article("Nvidia shares jump after earnings beat", "Reuters", "Nvidia raised guidance after earnings."),
        article("NVDA rises as earnings and guidance top estimates", "CNBC", "Investors reacted to guidance."),
    ]

    movers = detect_market_movers(articles, universe=universe, provider=provider, cache_path=tmp_path / "cache.json")

    assert movers[0].symbol == "NVDA"
    assert movers[0].reason_confidence == "high"
    assert movers[0].reason_sources == ("Reuters", "CNBC")


def test_detect_market_mover_handles_unexplained_large_move(tmp_path) -> None:
    universe = (AssetDefinition("TSLA", "Tesla", "mega_cap_stock", "tsla.us", 3.0, 1.0),)
    provider = FakeProvider({"TSLA": MarketQuote("TSLA", latest_price=108, previous_close=100)})

    movers = detect_market_movers([], universe=universe, provider=provider, cache_path=tmp_path / "cache.json")

    assert movers[0].reason_confidence == "low"
    assert "catalyst was not clear" in movers[0].move_reason


def test_explanation_ignores_false_match_without_causal_signal(tmp_path) -> None:
    universe = (AssetDefinition("AMD", "AMD", "volatile_stock", "amd.us", 4.0, 1.0),)
    provider = FakeProvider({"AMD": MarketQuote("AMD", latest_price=106, previous_close=100)})
    articles = [article("AMD appears in a local school acronym", "Local", "No company news was reported.")]

    movers = detect_market_movers(articles, universe=universe, provider=provider, cache_path=tmp_path / "cache.json")

    assert movers[0].reason_confidence == "low"
    assert movers[0].reason_sources == ("Local",)
