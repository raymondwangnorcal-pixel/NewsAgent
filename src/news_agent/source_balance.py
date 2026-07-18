from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import re

from news_agent.cluster import jaccard, tokenize
from news_agent.models import Article, StoryCluster


SOURCE_ALIASES: dict[str, tuple[str, float]] = {
    "ap": ("Associated Press", 1.0),
    "ap news": ("Associated Press", 1.0),
    "associated press": ("Associated Press", 1.0),
    "bbc": ("BBC News", 0.95),
    "bbc news": ("BBC News", 0.95),
    "bbc world": ("BBC News", 0.95),
    "bloomberg": ("Bloomberg", 1.0),
    "cnbc": ("CNBC", 0.9),
    "financial times": ("Financial Times", 1.0),
    "ft": ("Financial Times", 1.0),
    "npr": ("NPR", 0.9),
    "nytimes": ("The New York Times", 0.95),
    "new york times": ("The New York Times", 0.95),
    "reuters": ("Reuters", 1.0),
    "the wall street journal": ("The Wall Street Journal", 0.95),
    "wall street journal": ("The Wall Street Journal", 0.95),
    "washington post": ("The Washington Post", 0.9),
    "wsj": ("The Wall Street Journal", 0.95),
    "variety": ("Variety", 0.85),
    "hollywood reporter": ("The Hollywood Reporter", 0.85),
    "billboard": ("Billboard", 0.85),
}

_ALIAS_PATTERNS = tuple(
    (re.compile(rf"(?<!\w){re.escape(alias)}(?!\w)", re.IGNORECASE), canonical, quality)
    for alias, (canonical, quality) in sorted(SOURCE_ALIASES.items(), key=lambda item: len(item[0]), reverse=True)
)
_TITLE_CREDIT_RE = re.compile(r"(?:\s[-–—|]\s|\s\()(?P<credit>Reuters|AP|Associated Press)\)?\s*$", re.IGNORECASE)
WIRE_SOURCES = {"Reuters", "Associated Press"}
BODY_SIMILARITY_THRESHOLD = 0.8


@dataclass(frozen=True)
class SourceAttribution:
    display_source: str
    resolved_source: str
    confidence: str
    signal: str


def resolve_source_name(source: str) -> str | None:
    for pattern, canonical, _quality in _ALIAS_PATTERNS:
        if pattern.search(source):
            return canonical
    return None


def source_quality(source: str, fallback: float = 0.7) -> float:
    for pattern, _canonical, score in _ALIAS_PATTERNS:
        if pattern.search(source):
            return score
    return fallback


def _explicit_title_credit(title: str) -> str | None:
    match = _TITLE_CREDIT_RE.search(title)
    return resolve_source_name(match.group("credit")) if match else None


def resolve_source_attribution(
    article: Article,
    cluster_articles: list[Article] | tuple[Article, ...],
) -> SourceAttribution:
    canonical = resolve_source_name(article.source)
    if canonical is not None:
        return SourceAttribution(article.source, canonical, "confirmed", "publisher")

    credited = _explicit_title_credit(article.title)
    if credited is not None:
        return SourceAttribution(article.source, credited, "confirmed", "title_credit")

    article_body_tokens = tokenize(article.summary)
    if article_body_tokens:
        for peer in cluster_articles:
            if peer is article:
                continue
            peer_source = resolve_source_name(peer.source) or _explicit_title_credit(peer.title)
            if peer_source not in WIRE_SOURCES:
                continue
            if jaccard(article_body_tokens, tokenize(peer.summary)) >= BODY_SIMILARITY_THRESHOLD:
                return SourceAttribution(article.source, peer_source, "uncertain", "body_similarity")

    return SourceAttribution(article.source, article.source, "independent", "publisher")


def cluster_source_attributions(cluster: StoryCluster) -> tuple[SourceAttribution, ...]:
    return tuple(resolve_source_attribution(article, cluster.articles) for article in cluster.articles)


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
