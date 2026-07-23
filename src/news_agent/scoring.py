from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from news_agent.models import AgentConfig, CategoryAssignment, ImportanceConfig, StoryCluster
from news_agent.source_balance import cluster_quality_score, source_balance_score
from news_agent.watchlist import WatchlistEntry, match_cluster_watchlist, watchlist_score


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

_TERM_PATTERN_CACHE: dict[str, re.Pattern[str]] = {}


@dataclass
class SourceCapState:
    counts: dict[tuple[str, str], int] = field(default_factory=dict)

    def held(self, category: str, source: str) -> int:
        return self.counts.get((category, source), 0)

    def record(self, category: str, source: str) -> None:
        self.counts[(category, source)] = self.held(category, source) + 1


@dataclass
class CultureSelectionState:
    source_caps: SourceCapState = field(default_factory=SourceCapState)
    lane_counts: dict[str, int] = field(default_factory=dict)


def _primary_source(cluster: StoryCluster) -> str:
    return cluster.sources[0] if cluster.sources else "unknown"


def can_add_culture(cluster: StoryCluster, state: CultureSelectionState, lane_cap: int) -> bool:
    return (
        state.source_caps.held("culture", _primary_source(cluster)) < 2
        and state.lane_counts.get(cluster.culture_lane, 0) < lane_cap
    )


def record_culture_selection(cluster: StoryCluster, state: CultureSelectionState) -> None:
    state.source_caps.record("culture", _primary_source(cluster))
    state.lane_counts[cluster.culture_lane] = state.lane_counts.get(cluster.culture_lane, 0) + 1


def top_for_culture(
    clusters: list[StoryCluster],
    limit: int = 6,
    minimum: int = 4,
    minimum_evidence_score: float = 1.2,
) -> list[StoryCluster]:
    """Compatibility wrapper using the selector's shared Culture constraints."""
    eligible = sorted(
        (
            cluster for cluster in clusters
            if cluster.category == "culture" and not cluster.skip_reason
            and cluster.evidence_score >= minimum_evidence_score
        ),
        key=lambda item: (-item.importance, -item.total_score),
    )
    selected: list[StoryCluster] = []
    state = CultureSelectionState()
    selected_ids: set[str] = set()
    for lane_cap, target in ((1, limit), (2, limit), (3, min(minimum, limit))):
        for cluster in eligible:
            if len(selected) >= target:
                break
            if cluster.key not in selected_ids and can_add_culture(cluster, state, lane_cap):
                selected.append(cluster)
                selected_ids.add(cluster.key)
                record_culture_selection(cluster, state)
    return selected


def _term_pattern(term: str) -> re.Pattern[str]:
    pattern = _TERM_PATTERN_CACHE.get(term)
    if pattern is None:
        # Word-boundary match, not a bare substring check: a naive `"ai" in text` matches
        # inside "daily", "said", "maintain", "explain", "certain", and dozens of other
        # ordinary words, which was silently shoving unrelated articles into whatever
        # category owns a short keyword like "ai" or "us ". \b keeps short keywords honest.
        pattern = re.compile(rf"\b{re.escape(term.strip())}\b")
        _TERM_PATTERN_CACHE[term] = pattern
    return pattern


def _term_hits(text: str, terms: tuple[str, ...] | set[str]) -> int:
    lowered = text.lower()
    return sum(1 for term in terms if _term_pattern(term).search(lowered))


def score_clusters(
    clusters: list[StoryCluster],
    config: AgentConfig,
    watchlist_entries: tuple[WatchlistEntry, ...] = (),
) -> list[StoryCluster]:
    now = datetime.now(timezone.utc)
    for cluster in clusters:
        source_count = len(cluster.sources)
        reputation = sum(article.reputation for article in cluster.articles) / max(len(cluster.articles), 1)
        cluster.quality_score = cluster_quality_score(cluster)
        cluster.content_quality_penalty = sum(
            article.content_quality_penalty for article in cluster.articles
        ) / max(len(cluster.articles), 1)
        cluster.source_balance_score = source_balance_score(cluster)
        cluster.frequency_score = min(4.0, math.log2(source_count + 1) * 1.7)

        age_hours = max((now - cluster.latest_published_at).total_seconds() / 3600, 0.0)
        cluster.recency_score = max(0.2, 2.0 - (age_hours / 18.0))

        text = cluster.merged_text.lower()
        broad_impact = _term_hits(text, MARKET_TERMS) + _term_hits(text, SOCIAL_IMPACT_TERMS)
        forward_looking = _term_hits(text, FORWARD_LOOKING_TERMS)
        cluster.impact_score = min(5.0, 1.0 + broad_impact * 0.55 + forward_looking * 0.35)

        # Category assignment is not computed here -- it's a category-agnostic
        # importance score only. See news_agent.classify for guideline-driven
        # (LLM) category assignment, which sets cluster.category downstream.
        cluster.watchlist_matches = match_cluster_watchlist(cluster, watchlist_entries) if watchlist_entries else ()
        raw_watchlist_score = watchlist_score(cluster.watchlist_matches, watchlist_entries)
        if source_count >= 2 or cluster.impact_score >= 3.0 or cluster.quality_score >= 0.8:
            cluster.watchlist_score = raw_watchlist_score
        else:
            cluster.watchlist_score = min(0.1, raw_watchlist_score)

        cluster.total_score = (
            cluster.frequency_score * 2.0
            + cluster.impact_score * 2.4
            + cluster.recency_score
            + reputation
            + cluster.quality_score
            + cluster.source_balance_score
            + cluster.watchlist_score
            + min(2.0, cluster.evidence_score * 0.4)
            - cluster.content_quality_penalty
        )
        if source_count >= 2:
            cluster.total_score += 1.25
        elif cluster.impact_score < 3.0:
            cluster.total_score -= 1.5
    clusters.sort(key=lambda item: item.total_score, reverse=True)
    return clusters


def importance_from_total_score(total_score: float, config: ImportanceConfig) -> float:
    """Map the legacy score onto a stable 0-100 deterministic importance scale."""
    exponent = -config.logistic_steepness * (total_score - config.logistic_midpoint)
    return 100.0 / (1.0 + math.exp(max(-700.0, min(700.0, exponent))))


def apply_importance(
    clusters: list[StoryCluster],
    assignments: dict[str, CategoryAssignment],
    config: ImportanceConfig,
) -> None:
    for cluster in clusters:
        if not config.enabled:
            cluster.importance = 0.0
            continue
        deterministic = importance_from_total_score(cluster.total_score, config)
        assignment = assignments.get(cluster.key)
        llm_value = assignment.llm_importance if assignment is not None else None
        if llm_value is None:
            cluster.importance = deterministic
            continue
        blended = deterministic * (1.0 - config.llm_weight) + llm_value * config.llm_weight
        lower = max(0.0, deterministic - config.clamp_down)
        upper = min(100.0, deterministic + config.clamp_up)
        cluster.importance = max(lower, min(upper, blended))
