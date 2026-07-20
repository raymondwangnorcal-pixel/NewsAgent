from __future__ import annotations

import re
from dataclasses import replace

from news_agent.cluster import jaccard, tokenize
from news_agent.models import Article, StoryCluster


NUMBER_RE = re.compile(r"(?:[$€£]\s*)?\b\d[\d,.]*(?:%|\s+(?:million|billion|trillion))?\b", re.IGNORECASE)
SENTENCE_RE = re.compile(r"[.!?](?:\s|$)")


def evidence_substance_score(article: Article) -> float:
    text = article.best_available_text.strip()
    if not text:
        return 0.0
    title_similarity = jaccard(tokenize(article.title), tokenize(text))
    numeric_details = min(3, len(NUMBER_RE.findall(text)))
    sentence_count = min(4, len(SENTENCE_RE.findall(text)))
    extraction_bonus = 1.0 if article.enrichment_status == "extracted" else 0.0
    rich_feed_bonus = 0.45 if article.enrichment_status == "feed_content" and len(text) >= 300 else 0.0
    return max(
        0.0,
        min(len(text), 1200) / 300
        + numeric_details * 0.35
        + sentence_count * 0.20
        + article.reputation
        + extraction_bonus
        + rich_feed_bonus
        - title_similarity * 2.0
        - article.content_quality_penalty,
    )


def score_article_evidence(article: Article) -> Article:
    return replace(article, evidence_score=evidence_substance_score(article))


def rank_articles_by_evidence(articles: list[Article] | tuple[Article, ...]) -> list[Article]:
    scored = [score_article_evidence(article) for article in articles]
    return sorted(scored, key=lambda item: (item.evidence_score, item.reputation, len(item.best_available_text)), reverse=True)


def apply_cluster_evidence_scores(clusters: list[StoryCluster]) -> list[StoryCluster]:
    for cluster in clusters:
        cluster.articles = rank_articles_by_evidence(cluster.articles)
        cluster.evidence_score = cluster.articles[0].evidence_score if cluster.articles else 0.0
    return clusters
