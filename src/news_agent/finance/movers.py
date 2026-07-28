from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol

from news_agent.finance.explanations import explain_movers
from news_agent.finance.providers.stooq import MarketQuote, StooqMarketDataProvider
from news_agent.models import Article, MarketMover
from news_agent.watchlist import WatchlistEntry, match_watchlist_text
from news_agent.time import briefing_today


DEFAULT_MOVER_CACHE_PATH = Path("data/market_movers_cache.json")


class MarketDataProvider(Protocol):
    def fetch_quotes(self, symbols: dict[str, str]) -> dict[str, MarketQuote]:
        ...


@dataclass(frozen=True)
class AssetDefinition:
    symbol: str
    name: str
    asset_type: str
    provider_symbol: str
    threshold: float
    importance: float


DEFAULT_UNIVERSE: tuple[AssetDefinition, ...] = (
    AssetDefinition("SPY", "S&P 500 ETF", "major_index_etf", "spy.us", 1.0, 1.0),
    AssetDefinition("QQQ", "Nasdaq 100 ETF", "major_index_etf", "qqq.us", 1.0, 1.0),
    AssetDefinition("DIA", "Dow Industrials ETF", "major_index_etf", "dia.us", 1.0, 0.85),
    AssetDefinition("IWM", "Russell 2000 ETF", "major_index_etf", "iwm.us", 1.0, 0.75),
    AssetDefinition("AAPL", "Apple", "mega_cap_stock", "aapl.us", 3.0, 1.0),
    AssetDefinition("MSFT", "Microsoft", "mega_cap_stock", "msft.us", 3.0, 1.0),
    AssetDefinition("NVDA", "Nvidia", "mega_cap_stock", "nvda.us", 3.0, 1.0),
    AssetDefinition("TSLA", "Tesla", "mega_cap_stock", "tsla.us", 3.0, 0.95),
    AssetDefinition("AMZN", "Amazon", "mega_cap_stock", "amzn.us", 3.0, 0.95),
    AssetDefinition("META", "Meta", "mega_cap_stock", "meta.us", 3.0, 0.9),
    AssetDefinition("GOOGL", "Alphabet", "mega_cap_stock", "googl.us", 3.0, 0.9),
    AssetDefinition("AVGO", "Broadcom", "mega_cap_stock", "avgo.us", 3.0, 0.75),
    AssetDefinition("AMD", "AMD", "volatile_stock", "amd.us", 4.0, 0.7),
    AssetDefinition("NFLX", "Netflix", "volatile_stock", "nflx.us", 4.0, 0.65),
    AssetDefinition("XLK", "Technology Select Sector SPDR", "sector_etf", "xlk.us", 4.0, 0.65),
    AssetDefinition("XLF", "Financial Select Sector SPDR", "sector_etf", "xlf.us", 4.0, 0.6),
    AssetDefinition("XLE", "Energy Select Sector SPDR", "sector_etf", "xle.us", 4.0, 0.6),
    AssetDefinition("XBI", "Biotech ETF", "sector_etf", "xbi.us", 4.0, 0.5),
    AssetDefinition("SMH", "Semiconductor ETF", "sector_etf", "smh.us", 4.0, 0.7),
    AssetDefinition("ARKK", "ARK Innovation ETF", "sector_etf", "arkk.us", 4.0, 0.45),
    AssetDefinition("BTC", "Bitcoin", "crypto", "btcusd", 5.0, 0.85),
    AssetDefinition("ETH", "Ethereum", "crypto", "ethusd", 5.0, 0.75),
    AssetDefinition("USO", "Oil proxy", "commodity_proxy", "uso.us", 4.0, 0.5),
    AssetDefinition("GLD", "Gold proxy", "commodity_proxy", "gld.us", 4.0, 0.45),
    AssetDefinition("UUP", "U.S. dollar proxy", "macro_proxy", "uup.us", 2.0, 0.45),
    AssetDefinition("TLT", "Long Treasury proxy", "rate_proxy", "tlt.us", 2.0, 0.5),
)


def load_cache(path: Path = DEFAULT_MOVER_CACHE_PATH) -> dict[str, dict[str, object]]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def save_cache(quotes: dict[str, MarketQuote], path: Path = DEFAULT_MOVER_CACHE_PATH, today: date | None = None) -> None:
    cache_date = (today or briefing_today()).isoformat()
    existing = load_cache(path)
    for symbol, quote in quotes.items():
        existing[symbol] = {
            "date": cache_date,
            "latest_price": quote.latest_price,
            "previous_close": quote.previous_close,
            "volume": quote.volume,
            "as_of": quote.as_of,
        }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(existing, indent=2, sort_keys=True), encoding="utf-8")


def cached_quotes(
    universe: tuple[AssetDefinition, ...],
    path: Path = DEFAULT_MOVER_CACHE_PATH,
    today: date | None = None,
) -> dict[str, MarketQuote]:
    cache_date = (today or briefing_today()).isoformat()
    data = load_cache(path)
    quotes: dict[str, MarketQuote] = {}
    for asset in universe:
        row = data.get(asset.symbol)
        if not isinstance(row, dict) or row.get("date") != cache_date:
            continue
        try:
            quotes[asset.symbol] = MarketQuote(
                symbol=asset.symbol,
                latest_price=float(row["latest_price"]),
                previous_close=float(row["previous_close"]),
                volume=int(row["volume"]) if row.get("volume") is not None else None,
                as_of=str(row.get("as_of", "")),
            )
        except (KeyError, TypeError, ValueError):
            continue
    return quotes


def fetch_quotes_with_cache(
    universe: tuple[AssetDefinition, ...] = DEFAULT_UNIVERSE,
    provider: MarketDataProvider | None = None,
    cache_path: Path = DEFAULT_MOVER_CACHE_PATH,
) -> dict[str, MarketQuote]:
    cached = cached_quotes(universe, cache_path)
    missing = {asset.symbol: asset.provider_symbol for asset in universe if asset.symbol not in cached}
    if not missing:
        return cached
    provider = provider or StooqMarketDataProvider()
    fetched = provider.fetch_quotes(missing)
    if fetched:
        save_cache(fetched, cache_path)
    return cached | fetched


def quote_to_mover(
    asset: AssetDefinition,
    quote: MarketQuote,
    watchlist_entries: tuple[WatchlistEntry, ...] = (),
) -> MarketMover | None:
    change = quote.latest_price - quote.previous_close
    percent_change = (change / quote.previous_close) * 100
    if abs(percent_change) < asset.threshold:
        return None
    watchlist_matches = match_watchlist_text(f"{asset.symbol} {asset.name}", watchlist_entries) if watchlist_entries else ()
    return MarketMover(
        symbol=asset.symbol,
        name=asset.name,
        asset_type=asset.asset_type,
        latest_price=quote.latest_price,
        previous_close=quote.previous_close,
        absolute_change=change,
        percent_change=percent_change,
        volume=quote.volume,
        as_of=quote.as_of,
        importance=asset.importance,
        threshold=asset.threshold,
        watchlist_matches=watchlist_matches,
    )


def detect_market_movers(
    articles: list[Article] | None = None,
    watchlist_entries: tuple[WatchlistEntry, ...] = (),
    universe: tuple[AssetDefinition, ...] = DEFAULT_UNIVERSE,
    provider: MarketDataProvider | None = None,
    cache_path: Path = DEFAULT_MOVER_CACHE_PATH,
) -> tuple[MarketMover, ...]:
    quotes = fetch_quotes_with_cache(universe, provider=provider, cache_path=cache_path)
    by_symbol = {asset.symbol: asset for asset in universe}
    movers = [
        mover
        for symbol, quote in quotes.items()
        if symbol in by_symbol
        for mover in (quote_to_mover(by_symbol[symbol], quote, watchlist_entries),)
        if mover is not None
    ]
    explained = explain_movers(tuple(movers), articles or [])
    explained = tuple(
        mover
        for mover in explained
        if mover.reason_confidence != "low"
        or abs(mover.percent_change) >= mover.threshold * 1.5
        or mover.watchlist_matches
    )
    return tuple(
        sorted(
            explained,
            key=lambda item: (
                abs(item.percent_change),
                item.importance,
                bool(item.watchlist_matches),
                item.reason_confidence == "high",
            ),
            reverse=True,
        )
    )
