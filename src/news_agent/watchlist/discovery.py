from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from news_agent.models import Article, FeedConfig
from news_agent.watchlist.models import EntityMap, SourceState


YAHOO_FEED_URL = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={key}&region=US&lang=en-US"
EXCLUDED_HOSTS = frozenset({"fool.com", "247wallst.com", "marketbeat.com", "stocktwits.com", "trefis.com"})


@dataclass(frozen=True)
class DiscoveryResult:
    key: str
    state: SourceState
    articles: tuple[Article, ...] = ()
    error_code: str = ""


def yahoo_feed(key: str) -> FeedConfig:
    return FeedConfig(
        name=f"Yahoo Finance {key}",
        url=YAHOO_FEED_URL.format(key=key),
        reputation=0.7,
        categories=("finance",),
        source_type="finance",
        region="U.S.",
        quality_weight=0.7,
    )


def distinct_discovery_keys(entity_map: EntityMap) -> tuple[str, ...]:
    return tuple(sorted({key for entity in entity_map.tickers.values() for key in entity.discovery_keys}))


def fetch_distinct_yahoo_feeds(
    entity_map: EntityMap,
    fetcher: Callable[[FeedConfig], tuple[tuple[Article, ...], str]],
) -> dict[str, DiscoveryResult]:
    results: dict[str, DiscoveryResult] = {}
    for key in distinct_discovery_keys(entity_map):
        articles, error = fetcher(yahoo_feed(key))
        results[key] = DiscoveryResult(
            key,
            SourceState.FAILED if error else SourceState.OK,
            tuple(articles),
            error,
        )
    return results


def route_discovery_results(entity_map: EntityMap, results: dict[str, DiscoveryResult]) -> dict[str, tuple[Article, ...]]:
    routed: dict[str, tuple[Article, ...]] = {}
    for ticker, entity in entity_map.tickers.items():
        seen: set[str] = set()
        articles: list[Article] = []
        for key in entity.discovery_keys:
            for article in results.get(key, DiscoveryResult(key, SourceState.FAILED)).articles:
                identity = article.canonical_url or article.url
                if identity not in seen:
                    seen.add(identity)
                    articles.append(article)
        routed[ticker] = tuple(articles)
    return routed


def cache_key(source_id: str, discovery_key: str, briefing_date: date) -> tuple[str, str, str]:
    return source_id, discovery_key, briefing_date.isoformat()
