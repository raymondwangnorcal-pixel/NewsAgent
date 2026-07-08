from __future__ import annotations

import math
from datetime import datetime, timezone

from news_agent.models import AgentConfig, StoryCluster


MARKET_TERMS = {
    "stock", "stocks", "shares", "market", "nasdaq", "dow", "s&p", "treasury",
    "yield", "fed", "inflation", "rate", "earnings", "guidance", "crypto", "bitcoin",
}
SOCIAL_IMPACT_TERMS = {
    "election", "court", "ban", "strike", "war", "ceasefire", "dead", "killed",
    "evacuation", "health", "school", "hospital", "policy", "tariff", "sanction",
}
FORWARD_LOOKING_TERMS = {
    "expected", "plans", "will", "could", "next", "ahead", "upcoming", "deadline",
    "vote", "hearing", "trial", "earnings", "launch", "ipo",
}


def _term_hits(text: str, terms: tuple[str, ...] | set[str]) -> int:
    lowered = text.lower()
    return sum(1 for term in terms if term in lowered)


def score_clusters(clusters: list[StoryCluster], config: AgentConfig) -> list[StoryCluster]:
    now = datetime.now(timezone.utc)
    for cluster in clusters:
        source_count = len(cluster.sources)
        reputation = sum(article.reputation for article in cluster.articles) / max(len(cluster.articles), 1)
        cluster.frequency_score = min(4.0, math.log2(source_count + 1) * 1.7)

        age_hours = max((now - cluster.latest_published_at).total_seconds() / 3600, 0.0)
        cluster.recency_score = max(0.2, 2.0 - (age_hours / 18.0))

        text = cluster.merged_text.lower()
        broad_impact = _term_hits(text, MARKET_TERMS) + _term_hits(text, SOCIAL_IMPACT_TERMS)
        forward_looking = _term_hits(text, FORWARD_LOOKING_TERMS)
        cluster.impact_score = min(5.0, 1.0 + broad_impact * 0.55 + forward_looking * 0.35)

        category_scores: dict[str, float] = {}
        feed_category_votes: dict[str, float] = {}
        for article in cluster.articles:
            for category in article.feed_categories:
                feed_category_votes[category] = feed_category_votes.get(category, 0.0) + article.reputation

        for name, category in config.categories.items():
            keyword_hits = _term_hits(text, category.keywords)
            impact_hits = _term_hits(text, category.impact_terms)
            feed_vote = feed_category_votes.get(name, 0.0)
            category_scores[name] = feed_vote + keyword_hits * 0.75 + impact_hits * 0.9

        cluster.category_scores = category_scores
        cluster.total_score = (
            cluster.frequency_score * 2.0
            + cluster.impact_score * 2.4
            + cluster.recency_score
            + reputation
        )
        if source_count >= 2:
            cluster.total_score += 1.25
        elif cluster.impact_score < 3.0:
            cluster.total_score -= 1.5
    clusters.sort(key=lambda item: item.total_score, reverse=True)
    return clusters


def top_for_category(clusters: list[StoryCluster], category: str, limit: int) -> list[StoryCluster]:
    eligible = [
        cluster
        for cluster in clusters
        if cluster.category_scores.get(category, 0.0) > 0
    ]
    eligible.sort(
        key=lambda item: (item.category_scores.get(category, 0.0) + item.total_score, item.total_score),
        reverse=True,
    )
    return eligible[:limit]


def top_overall(clusters: list[StoryCluster], limit: int) -> list[StoryCluster]:
    return clusters[:limit]
