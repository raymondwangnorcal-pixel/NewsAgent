from __future__ import annotations

from collections import Counter

from news_agent.models import StoryCluster


QUALITY_SOURCE_HINTS = {
    "ap": 1.0,
    "associated press": 1.0,
    "bbc": 0.95,
    "bloomberg": 1.0,
    "cnbc": 0.9,
    "financial times": 1.0,
    "ft": 1.0,
    "npr": 0.9,
    "nytimes": 0.95,
    "new york times": 0.95,
    "reuters": 1.0,
    "the wall street journal": 0.95,
    "wall street journal": 0.95,
    "washington post": 0.9,
    "wsj": 0.95,
}


def source_quality(source: str, fallback: float = 0.7) -> float:
    lowered = source.lower()
    for hint, score in QUALITY_SOURCE_HINTS.items():
        if hint in lowered:
            return score
    return fallback


def cluster_quality_score(cluster: StoryCluster) -> float:
    if not cluster.articles:
        return 0.0
    scores = [
        max(article.reputation, source_quality(article.source, article.reputation))
        for article in cluster.articles
    ]
    return sum(scores) / len(scores)


def source_distribution(cluster: StoryCluster) -> dict[str, int]:
    return dict(Counter(article.source for article in cluster.articles))


def source_balance_score(cluster: StoryCluster) -> float:
    unique_sources = len(cluster.sources)
    if unique_sources >= 4:
        return 1.0
    if unique_sources >= 2:
        return 0.6
    if cluster.articles and cluster_quality_score(cluster) >= 0.9:
        return 0.2
    return -0.5


def source_distribution_label(cluster: StoryCluster) -> str:
    distribution = source_distribution(cluster)
    return ", ".join(f"{source}: {count}" for source, count in sorted(distribution.items()))
