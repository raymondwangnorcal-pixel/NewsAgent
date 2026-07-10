from __future__ import annotations

from datetime import datetime, timedelta, timezone

from news_agent.cluster import cluster_articles, normalize_title
from news_agent.models import Article


def article(title: str, source: str = "Source", hours_ago: int = 0) -> Article:
    return Article(
        title=title,
        url=f"https://example.com/{hash(title)}",
        source=source,
        published_at=datetime.now(timezone.utc) - timedelta(hours=hours_ago),
    )


def test_cluster_articles_groups_similar_headlines() -> None:
    clusters = cluster_articles(
        [
            article("Fed signals rate cuts as inflation cools", "A"),
            article("Inflation cools as Fed signals possible rate cuts", "B"),
            article("Streaming service launches a new comedy slate", "C"),
        ]
    )

    assert len(clusters) == 2
    assert max(len(cluster.articles) for cluster in clusters) == 2


def test_normalize_title_removes_source_punctuation_and_filler() -> None:
    assert normalize_title("LIVE: Fed signals rate cuts, Reuters", "Reuters") == "fed signals rate cuts"


def test_cluster_articles_groups_same_event_across_outlets() -> None:
    clusters = cluster_articles(
        [
            article("Nvidia shares jump after earnings beat and higher forecast", "Reuters"),
            article("Nvidia stock rises as profit forecast tops estimates", "CNBC"),
            article("Apple growers face heat damage across Washington", "AP"),
        ]
    )

    grouped = [cluster for cluster in clusters if "Nvidia" in cluster.title]

    assert len(grouped) == 1
    assert len(grouped[0].articles) == 2
    assert grouped[0].source_count == 2


def test_cluster_articles_keeps_same_company_different_events_separate() -> None:
    clusters = cluster_articles(
        [
            article("Tesla shares rise after earnings beat", "Reuters"),
            article("Tesla recalls vehicles after safety probe", "AP"),
        ]
    )

    assert len(clusters) == 2


def test_cluster_articles_keeps_unrelated_similar_keywords_separate() -> None:
    clusters = cluster_articles(
        [
            article("Apple launches new iPhone with AI tools", "The Verge"),
            article("Apple growers face crop losses after extreme heat", "AP"),
        ]
    )

    assert len(clusters) == 2
