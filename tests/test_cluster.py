from __future__ import annotations

from datetime import datetime, timezone

from news_agent.cluster import cluster_articles
from news_agent.models import Article


def article(title: str, source: str = "Source") -> Article:
    return Article(title=title, url=f"https://example.com/{hash(title)}", source=source, published_at=datetime.now(timezone.utc))


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
