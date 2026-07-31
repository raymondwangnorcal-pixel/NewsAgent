from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from news_agent.models import Article, DuplicateGateConfig, StoryCluster


STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has", "have",
    "in", "into", "is", "it", "its", "of", "on", "or", "over", "says", "the", "to",
    "with", "after", "amid", "new", "news", "live", "updates", "how", "why", "what",
    "breaking", "latest", "analysis", "briefing", "report", "reports", "says", "said",
    "seen", "watch", "here", "today", "yesterday", "tomorrow",
}
TOKEN_RE = re.compile(r"[a-z0-9]+")
PUNCTUATION_RE = re.compile(r"[^a-z0-9\s]")
TICKER_RE = re.compile(r"(?:\$|\b(?:NASDAQ|NYSE|AMEX|NYSEARCA)\s*:?\s*)?([A-Z][A-Z0-9.-]{1,5})\b")
CAPITAL_PHRASE_RE = re.compile(r"\b[A-Z][A-Za-z0-9&.-]*(?:\s+[A-Z][A-Za-z0-9&.-]*){0,3}\b")
SOURCE_SPLIT_RE = re.compile(r"\s+(?:-|–|—|\|)\s+")
EVENT_TERMS = {
    "acquisition",
    "antitrust",
    "approval",
    "ban",
    "bankruptcy",
    "ceasefire",
    "charges",
    "cuts",
    "deal",
    "downgrade",
    "earnings",
    "election",
    "forecast",
    "guidance",
    "inflation",
    "ipo",
    "lawsuit",
    "launch",
    "layoffs",
    "merger",
    "probe",
    "recall",
    "rates",
    "regulation",
    "sanctions",
    "strike",
    "tariff",
    "upgrade",
    "war",
}
ENTITY_STOPWORDS = {
    "AP",
    "BBC",
    "CEO",
    "CNN",
    "ETF",
    "EU",
    "Fed",
    "Friday",
    "Monday",
    "Nasdaq",
    "Reuters",
    "Saturday",
    "Sunday",
    "Thursday",
    "Tuesday",
    "Wednesday",
}
GENERIC_GATE_ENTITIES = frozenset(
    {
        "AI",
        "AP",
        "BBC",
        "CEO",
        "CFO",
        "CNN",
        "COO",
        "CTO",
        "ETF",
        "EU",
        "GDP",
        "IPO",
        "REUTERS",
        "UK",
        "US",
        "USA",
    }
)
GENERIC_GATE_ENTITY_WORDS = STOPWORDS | {
    "according",
    "all",
    "another",
    "april",
    "aug",
    "august",
    "both",
    "but",
    "december",
    "during",
    "feb",
    "february",
    "following",
    "january",
    "july",
    "june",
    "march",
    "may",
    "months",
    "nov",
    "november",
    "now",
    "october",
    "once",
    "only",
    "people",
    "she",
    "some",
    "sept",
    "september",
    "subject",
    "target",
    "that",
    "there",
    "they",
    "this",
    "when",
    "whether",
    "while",
    "will",
    "would",
}
RETROSPECTIVE_TITLE_MARKERS = (
    "timeline",
    "explainer",
    "explained",
    "everything we know",
    "what to know",
    "what we know",
    "a look at",
    "recap",
)


@dataclass(frozen=True)
class DuplicateGatePair:
    left: StoryCluster
    right: StoryCluster
    title_jaccard: float
    shared_entities: tuple[str, ...]
    shared_event_terms: tuple[str, ...]
    hours_apart: float


def strip_source_names(title: str, source: str = "") -> str:
    parts = SOURCE_SPLIT_RE.split(title)
    if len(parts) > 1 and (not source or parts[-1].strip().casefold() == source.casefold()):
        return " ".join(parts[:-1]).strip()
    if source:
        return re.sub(rf"\b{re.escape(source)}\b", " ", title, flags=re.IGNORECASE)
    return title


def normalize_title(title: str, source: str = "") -> str:
    value = strip_source_names(title, source).lower()
    value = PUNCTUATION_RE.sub(" ", value)
    tokens = [token for token in TOKEN_RE.findall(value) if token not in STOPWORDS]
    return " ".join(tokens)


def tokenize(text: str) -> set[str]:
    return {token for token in TOKEN_RE.findall(text.lower()) if len(token) > 2 and token not in STOPWORDS}


def story_key(article: Article) -> str:
    tokens = sorted(tokenize(normalize_title(article.title, article.source)))
    return " ".join(tokens[:8]) or article.title.lower()


def jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def extract_entities(text: str) -> set[str]:
    entities: set[str] = set()
    for match in TICKER_RE.findall(text):
        if match.isupper() and 1 < len(match) <= 6:
            entities.add(match.replace(".", "-").upper())
    for phrase in CAPITAL_PHRASE_RE.findall(text):
        cleaned = " ".join(phrase.split()).strip()
        if cleaned and cleaned not in ENTITY_STOPWORDS and len(cleaned) > 2:
            entities.add(cleaned.casefold())
    return entities


def event_terms(text: str) -> set[str]:
    tokens = tokenize(text)
    return tokens & EVENT_TERMS


def article_tokens(article: Article) -> set[str]:
    return tokenize(normalize_title(article.title, article.source))


def cluster_tokens(cluster: StoryCluster) -> set[str]:
    tokens: set[str] = set()
    for article in cluster.articles:
        tokens |= article_tokens(article)
    return tokens


def cluster_entities(cluster: StoryCluster) -> set[str]:
    entities: set[str] = set()
    for article in cluster.articles:
        entities |= extract_entities(f"{article.title} {article.summary}")
    return entities


def cluster_event_terms(cluster: StoryCluster) -> set[str]:
    terms: set[str] = set()
    for article in cluster.articles:
        terms |= event_terms(f"{article.title} {article.summary}")
    return terms


def hours_apart(left: datetime, right: datetime) -> float:
    if left.tzinfo is None:
        left = left.replace(tzinfo=timezone.utc)
    if right.tzinfo is None:
        right = right.replace(tzinfo=timezone.utc)
    return abs((left - right).total_seconds()) / 3600


def time_proximity_score(article: Article, cluster: StoryCluster) -> float:
    hours = hours_apart(article.published_at, cluster.latest_published_at)
    if hours <= 3:
        return 1.0
    if hours <= 12:
        return 0.7
    if hours <= 24:
        return 0.35
    return 0.0


def different_development(article: Article, cluster: StoryCluster, title_score: float) -> bool:
    shared_entities = extract_entities(f"{article.title} {article.summary}") & cluster_entities(cluster)
    if not shared_entities or title_score >= 0.32:
        return False
    article_events = event_terms(f"{article.title} {article.summary}")
    cluster_events = cluster_event_terms(cluster)
    if article_events and cluster_events and article_events.isdisjoint(cluster_events):
        return True
    return False


def clusters_are_different_developments(
    left: StoryCluster,
    right: StoryCluster,
    title_score: float,
) -> bool:
    if not (cluster_entities(left) & cluster_entities(right)) or title_score >= 0.32:
        return False
    left_events = cluster_event_terms(left)
    right_events = cluster_event_terms(right)
    return bool(left_events and right_events and left_events.isdisjoint(right_events))


def specific_shared_entities(left: StoryCluster, right: StoryCluster) -> set[str]:
    left_entities = cluster_entities(left)
    right_entities = cluster_entities(right)
    shared = left_entities & right_entities
    exact_entities = {
        entity
        for entity in shared
        if not (
            (tokens := {token.upper() for token in TOKEN_RE.findall(entity.casefold())})
            and (
                tokens <= GENERIC_GATE_ENTITIES
                or all(token.casefold() in GENERIC_GATE_ENTITY_WORDS for token in tokens)
                or all(len(token) == 1 for token in tokens)
            )
        )
    }
    left_tokens = {
        token.casefold()
        for entity in left_entities
        for token in TOKEN_RE.findall(entity)
        if len(token) >= 3 and any(character.isdigit() for character in token)
    }
    right_tokens = {
        token.casefold()
        for entity in right_entities
        for token in TOKEN_RE.findall(entity)
        if len(token) >= 3 and any(character.isdigit() for character in token)
    }
    return exact_entities or (left_tokens & right_tokens)


def duplicate_gate_candidates(
    clusters: list[StoryCluster],
    config: DuplicateGateConfig,
) -> list[DuplicateGatePair]:
    pairs: list[DuplicateGatePair] = []
    for left_index, left in enumerate(clusters):
        for right in clusters[left_index + 1 :]:
            elapsed_hours = hours_apart(left.latest_published_at, right.latest_published_at)
            if elapsed_hours > config.candidate_window_hours:
                continue
            shared_entities = specific_shared_entities(left, right)
            if not shared_entities:
                continue
            title_score = jaccard(cluster_tokens(left), cluster_tokens(right))
            shared_events = cluster_event_terms(left) & cluster_event_terms(right)
            if (
                title_score < config.candidate_title_jaccard_threshold
                and not shared_events
            ):
                continue
            if clusters_are_different_developments(left, right, title_score):
                continue
            pairs.append(
                DuplicateGatePair(
                    left=left,
                    right=right,
                    title_jaccard=title_score,
                    shared_entities=tuple(sorted(shared_entities)),
                    shared_event_terms=tuple(sorted(shared_events)),
                    hours_apart=elapsed_hours,
                )
            )
    return sorted(
        pairs,
        key=lambda pair: (
            -len(pair.shared_entities),
            -pair.title_jaccard,
            *sorted((story_key(pair.left.articles[0]), story_key(pair.right.articles[0]))),
        ),
    )


def article_cluster_similarity(article: Article, cluster: StoryCluster) -> float:
    title_score = jaccard(article_tokens(article), cluster_tokens(cluster))
    entity_score = jaccard(extract_entities(f"{article.title} {article.summary}"), cluster_entities(cluster))
    event_score = jaccard(event_terms(f"{article.title} {article.summary}"), cluster_event_terms(cluster))
    time_score = time_proximity_score(article, cluster)
    if different_development(article, cluster, title_score):
        return min(title_score, 0.25)
    return title_score * 0.55 + entity_score * 0.2 + event_score * 0.15 + time_score * 0.1


def choose_canonical_headline(articles: list[Article]) -> str:
    if not articles:
        return ""
    ranked = sorted(
        articles,
        key=lambda item: (
            item.reputation,
            -int(any(word in item.title.lower() for word in ("live", "updates", "latest"))),
            min(len(item.title), 120),
        ),
        reverse=True,
    )
    return ranked[0].title


def is_retrospective_title(title: str) -> bool:
    lowered = title.lower()
    return any(marker in lowered for marker in RETROSPECTIVE_TITLE_MARKERS)


def choose_merged_headline(articles: list[Article]) -> str:
    if not articles:
        return ""
    ranked = sorted(
        articles,
        key=lambda item: (
            not is_retrospective_title(item.title),
            item.reputation,
            item.published_at,
        ),
        reverse=True,
    )
    return ranked[0].title


def refresh_cluster(cluster: StoryCluster) -> None:
    cluster.title = choose_canonical_headline(cluster.articles) or cluster.title
    cluster.key = story_key(max(cluster.articles, key=lambda item: item.reputation))


def cluster_articles(articles: list[Article], threshold: float = 0.43) -> list[StoryCluster]:
    clusters: list[StoryCluster] = []

    for article in articles:
        best_cluster: StoryCluster | None = None
        best_score = 0.0
        for cluster in clusters:
            score = article_cluster_similarity(article, cluster)
            if score > best_score:
                best_score = score
                best_cluster = cluster

        if best_cluster is not None and best_score >= threshold:
            best_cluster.articles.append(article)
            refresh_cluster(best_cluster)
        else:
            key = story_key(article)
            cluster = StoryCluster(key=key, title=article.title, articles=[article])
            clusters.append(cluster)

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
            refresh_cluster(existing)
            for url in canonical_urls:
                by_url[url] = existing
    return merged
