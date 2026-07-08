from __future__ import annotations

import re
from collections import defaultdict

from news_agent.models import Article, StoryCluster


STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has", "have",
    "in", "into", "is", "it", "its", "of", "on", "or", "over", "says", "the", "to",
    "with", "after", "amid", "new", "news", "live", "updates", "how", "why", "what",
}
TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> set[str]:
    return {token for token in TOKEN_RE.findall(text.lower()) if len(token) > 2 and token not in STOPWORDS}


def story_key(article: Article) -> str:
    tokens = sorted(tokenize(article.title))
    return " ".join(tokens[:8]) or article.title.lower()


def jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def cluster_articles(articles: list[Article], threshold: float = 0.34) -> list[StoryCluster]:
    clusters: list[StoryCluster] = []
    token_cache: dict[str, set[str]] = {}

    for article in articles:
        article_tokens = tokenize(article.title)
        best_cluster: StoryCluster | None = None
        best_score = 0.0
        for cluster in clusters:
            cluster_tokens = token_cache[cluster.key]
            score = jaccard(article_tokens, cluster_tokens)
            if score > best_score:
                best_score = score
                best_cluster = cluster

        if best_cluster is not None and best_score >= threshold:
            best_cluster.articles.append(article)
            token_cache[best_cluster.key] |= article_tokens
        else:
            key = story_key(article)
            cluster = StoryCluster(key=key, title=article.title, articles=[article])
            clusters.append(cluster)
            token_cache[key] = set(article_tokens)

    return merge_url_duplicates(clusters)


def merge_url_duplicates(clusters: list[StoryCluster]) -> list[StoryCluster]:
    by_url: dict[str, StoryCluster] = {}
    merged: list[StoryCluster] = []
    for cluster in clusters:
        canonical_urls = {article.url.split("?")[0] for article in cluster.articles}
        existing = next((by_url[url] for url in canonical_urls if url in by_url), None)
        if existing is None:
            merged.append(cluster)
            for url in canonical_urls:
                by_url[url] = cluster
        else:
            existing.articles.extend(cluster.articles)
            for url in canonical_urls:
                by_url[url] = existing
    return merged


def group_by_category(clusters: list[StoryCluster]) -> dict[str, list[StoryCluster]]:
    grouped: dict[str, list[StoryCluster]] = defaultdict(list)
    for cluster in clusters:
        if not cluster.category_scores:
            continue
        category = max(cluster.category_scores, key=cluster.category_scores.get)
        grouped[category].append(cluster)
    return dict(grouped)
