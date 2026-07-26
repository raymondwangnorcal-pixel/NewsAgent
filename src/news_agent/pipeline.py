from __future__ import annotations

import asyncio
import statistics
from dataclasses import dataclass, field, replace
from pathlib import Path
from collections import Counter

from news_agent.alerts import (
    DEFAULT_ALERT_CONFIG_PATH,
    DEFAULT_ALERT_HISTORY_PATH,
    NewsAlert,
    apply_alert_cooldown,
    generate_alerts,
    load_alert_config,
)
from news_agent.classify import (
    CATEGORY_NAMES,
    classify_clusters,
    default_category_assignments_path,
    write_category_assignments,
)
from news_agent.cluster import cluster_articles
from news_agent.compress import CompressionRunResult, compress_paragraphs_result
from news_agent.compression_audit import (
    DEFAULT_COMPRESSION_AUDIT_DIR,
    cleanup_compression_audits,
    default_compression_audit_path,
    write_compression_audit,
)
from news_agent.config import load_config
from news_agent.draft import DraftCandidate, DraftRunResult, draft_paragraphs_result
from news_agent.enrichment import enrich_clusters, select_enrichment_clusters
from news_agent.evidence import apply_cluster_evidence_scores, rank_articles_by_evidence
from news_agent.fetch import fetch_all_feeds
from news_agent.history import DEFAULT_HISTORY_PATH, apply_history, save_story_history
from news_agent.models import (
    AgentConfig,
    Article,
    BriefingParagraph,
    BriefingSection,
    CategoryAssignment,
    CategorySelectionLimit,
    OpenAICapabilities,
    OpenAICostConfig,
    OpenAIMode,
    QualityGateConfig,
    PipelineDiagnostics,
    StoryCluster,
)
from news_agent.openai_budget import OpenAIBudget
from news_agent.quality_gate import (
    apply_quality_gate,
    default_quality_gate_rejections_path,
    judge_ambiguous_articles,
    write_quality_gate_rejections,
)
from news_agent.scoring import (
    CultureSelectionState,
    SourceCapState,
    apply_importance,
    can_add_culture,
    importance_from_total_score,
    record_culture_selection,
    score_clusters,
)
from news_agent.skipped_log import SkippedStory, build_skipped_stories, default_skipped_path, write_skipped_log
from news_agent.source_balance import source_distribution_label
from news_agent.stocks import build_stock_snapshot
from news_agent.watchlist import DEFAULT_WATCHLIST_PATH, WatchlistEntry, load_watchlist


# Materially larger than the ~30 stories that actually publish (5 categories x 6),
# so importance-ranking (category-agnostic) doesn't starve a naturally-lower-scoring
# category before classification even runs -- e.g. Culture+Media during a week
# dominated by conflict/finance-market-reaction stories.
GLOBAL_CLASSIFICATION_POOL_SIZE = 30
CATEGORY_CLASSIFICATION_RESERVE = 10
MAX_CLASSIFICATION_POOL_SIZE = 80

FINANCE_LEAD_TICKER_COUNT = 7
def story_identity(cluster: StoryCluster) -> str:
    if cluster.key:
        return cluster.key
    if cluster.urls:
        return cluster.urls[0].split("?", 1)[0]
    return cluster.title.casefold()


def cluster_feed_hints(cluster: StoryCluster) -> tuple[str, ...]:
    hints = {category for article in cluster.articles for category in article.feed_categories}
    return tuple(category for category in CATEGORY_NAMES if category in hints)


def _empty_category_counts(include_unclassified: bool = False) -> dict[str, int]:
    counts = {category: 0 for category in CATEGORY_NAMES}
    if include_unclassified:
        counts["unclassified"] = 0
    return counts


def count_articles_by_feed_hint(articles: list[Article]) -> dict[str, int]:
    counts = _empty_category_counts()
    for article in articles:
        for category in CATEGORY_NAMES:
            counts[category] += int(category in article.feed_categories)
    return counts


def count_clusters_by_feed_hint(clusters: list[StoryCluster]) -> dict[str, int]:
    counts = _empty_category_counts()
    for cluster in clusters:
        for category in cluster_feed_hints(cluster):
            counts[category] += 1
    return counts


def count_clusters_by_category(clusters: list[StoryCluster]) -> dict[str, int]:
    counts = _empty_category_counts(include_unclassified=True)
    for cluster in clusters:
        key = cluster.category if cluster.category in CATEGORY_NAMES else "unclassified"
        counts[key] += 1
    return counts


@dataclass(frozen=True)
class PipelineContext:
    category_clusters: dict[str, list[StoryCluster]]
    stock_snapshot: object
    all_clusters: list[StoryCluster]
    quality_gate_rejections: tuple[tuple[Article, str], ...] = ()
    quality_gate_log_path: Path | None = None
    category_assignments: dict[str, CategoryAssignment] = field(default_factory=dict)
    category_assignments_log_path: Path | None = None
    diagnostics: PipelineDiagnostics = field(default_factory=PipelineDiagnostics)


@dataclass(frozen=True)
class BriefingBuildResult:
    briefings: list[BriefingSection]
    skipped_stories: list[SkippedStory]
    skipped_log_path: Path
    source_debug_lines: tuple[str, ...]
    quality_gate_rejections: tuple[tuple[Article, str], ...] = ()
    quality_gate_log_path: Path | None = None
    category_assignments_log_path: Path | None = None
    compression_audit_path: Path | None = None
    diagnostics: PipelineDiagnostics = field(default_factory=PipelineDiagnostics)


@dataclass(frozen=True)
class AlertBuildResult:
    alerts: list[NewsAlert]
    alert_history_path: Path


@dataclass(frozen=True)
class SelectionResult:
    category_clusters: dict[str, list[StoryCluster]]
    floor_selected_by_category: dict[str, int]
    remainder_selected_by_category: dict[str, int]
    big_day_selected_by_category: dict[str, int]
    source_cap_relaxed_by_category: dict[str, int]

    @property
    def selected_count(self) -> int:
        return sum(len(items) for items in self.category_clusters.values())


def _selection_key(cluster: StoryCluster) -> tuple[float, float, float, str]:
    return (
        -cluster.importance,
        -cluster.total_score,
        -cluster.latest_published_at.timestamp(),
        story_identity(cluster),
    )


def _primary_source(cluster: StoryCluster) -> str:
    return cluster.sources[0] if cluster.sources else "unknown"


def select_importance_deck(
    clusters: list[StoryCluster],
    config: AgentConfig,
    minimum_evidence_score: float = 0.0,
) -> SelectionResult:
    """Allocate a hard-sized deck through category floors, remainder, then big-day slots."""
    eligible = sorted(
        (
            cluster for cluster in clusters
            if cluster.category in CATEGORY_NAMES
            and not cluster.skip_reason
            and cluster.evidence_score >= minimum_evidence_score
        ),
        key=_selection_key,
    )
    selected = {category: [] for category in CATEGORY_NAMES}
    selected_ids: set[str] = set()
    source_state = SourceCapState()
    culture_state = CultureSelectionState(source_caps=source_state)
    floor_counts = _empty_category_counts()
    remainder_counts = _empty_category_counts()
    big_day_counts = _empty_category_counts()
    source_relaxations = _empty_category_counts()

    def add(cluster: StoryCluster, phase_counts: dict[str, int], relaxed: bool = False) -> None:
        category = cluster.category
        selected[category].append(cluster)
        selected_ids.add(story_identity(cluster))
        if category == "culture":
            record_culture_selection(cluster, culture_state)
        else:
            source_state.record(category, _primary_source(cluster))
        phase_counts[category] += 1
        if relaxed:
            source_relaxations[category] += 1

    # Phase 1: guarantee each category's configured editorial floor. Culture gets
    # a lane-diversity pass first; non-Culture source caps relax only if necessary.
    for category in CATEGORY_NAMES:
        limit = config.category_selection_limits[category]
        category_items = [cluster for cluster in eligible if cluster.category == category]
        if category == "culture":
            for lane_cap in (1, 2):
                for cluster in category_items:
                    if len(selected[category]) >= limit.floor:
                        break
                    if story_identity(cluster) not in selected_ids and can_add_culture(cluster, culture_state, lane_cap):
                        add(cluster, floor_counts)
                if len(selected[category]) >= limit.floor:
                    break
            continue
        for cluster in category_items:
            if len(selected[category]) >= limit.floor:
                break
            source = _primary_source(cluster)
            if source_state.held(category, source) < config.max_per_source_per_category:
                add(cluster, floor_counts)
        if len(selected[category]) < limit.floor:
            for cluster in category_items:
                if len(selected[category]) >= limit.floor:
                    break
                if story_identity(cluster) not in selected_ids:
                    add(cluster, floor_counts, relaxed=True)

    # Phase 2: rank all remaining stories together. Over-cap non-Culture stories
    # are deferred, then admitted only if the deck would otherwise remain short.
    deferred: list[StoryCluster] = []
    for cluster in eligible:
        if len(selected_ids) >= config.importance.deck_target:
            break
        identity = story_identity(cluster)
        category = cluster.category
        limit = config.category_selection_limits[category]
        if identity in selected_ids or len(selected[category]) >= limit.ceiling:
            continue
        if category == "culture":
            if can_add_culture(cluster, culture_state, lane_cap=2):
                add(cluster, remainder_counts)
            continue
        if source_state.held(category, _primary_source(cluster)) >= config.max_per_source_per_category:
            deferred.append(cluster)
            continue
        add(cluster, remainder_counts)
    for cluster in deferred:
        if len(selected_ids) >= config.importance.deck_target:
            break
        identity = story_identity(cluster)
        category = cluster.category
        if identity in selected_ids or len(selected[category]) >= config.category_selection_limits[category].ceiling:
            continue
        add(cluster, remainder_counts, relaxed=True)

    # Phase 3: a category may take a sixth story only on a genuinely consequential
    # day. LLM-only elevation requires multiple sources, and source/lane hard caps
    # remain in force.
    for cluster in eligible:
        if len(selected_ids) >= config.importance.deck_target:
            break
        identity = story_identity(cluster)
        category = cluster.category
        limit = config.category_selection_limits[category]
        if (
            identity in selected_ids
            or len(selected[category]) >= limit.big_day_max
            or cluster.importance < config.importance.big_day_importance_threshold
        ):
            continue
        deterministic = importance_from_total_score(cluster.total_score, config.importance)
        if (
            config.importance.big_day_requires_corroboration
            and deterministic < config.importance.big_day_importance_threshold
            and cluster.source_count < 2
        ):
            continue
        if category == "culture":
            if not can_add_culture(cluster, culture_state, lane_cap=3):
                continue
        elif source_state.held(category, _primary_source(cluster)) >= config.big_day_source_cap:
            continue
        add(cluster, big_day_counts)

    # Keep the pre-presentation total-score order through drafting so changing
    # display rank cannot perturb the ordering of the OpenAI prompt batch.
    drafting_order = {
        category: sorted(items, key=lambda cluster: cluster.total_score, reverse=True)
        for category, items in selected.items()
    }
    return SelectionResult(drafting_order, floor_counts, remainder_counts, big_day_counts, source_relaxations)


def order_paragraphs_for_presentation(
    paragraphs: list[BriefingParagraph],
    category_clusters: dict[str, list[StoryCluster]],
) -> list[BriefingParagraph]:
    """Apply importance display order after drafting without changing draft inputs."""
    category_order = {category: index for index, category in enumerate(CATEGORY_NAMES)}
    importance_rank = {
        (category, cluster.key): rank
        for category, clusters in category_clusters.items()
        for rank, cluster in enumerate(sorted(clusters, key=_selection_key))
    }
    indexed = list(enumerate(paragraphs))
    indexed.sort(
        key=lambda item: (
            category_order.get(item[1].category, len(category_order)),
            importance_rank.get((item[1].category, item[1].story_id), len(paragraphs)),
            item[0],
        )
    )
    return [paragraph for _, paragraph in indexed]


async def collect_and_rank(config: AgentConfig | None = None) -> dict[str, list[StoryCluster]]:
    context = await collect_pipeline_context(config)
    return context.category_clusters


async def collect_context(config: AgentConfig | None = None):
    context = await collect_pipeline_context(config)
    return context.category_clusters, context.stock_snapshot


def _resolve_quality_gate_config(config: object) -> QualityGateConfig:
    # `config` isn't always a real AgentConfig — some tests pass an opaque `object()`
    # sentinel while monkeypatching away everything that would otherwise touch it. Fall
    # back to defaults rather than assuming `config.quality_gate` exists.
    return getattr(config, "quality_gate", None) or QualityGateConfig()


def _apply_ambiguous_verdicts(
    survivors: list[Article],
    ambiguous_articles: list[Article],
    quality_gate_config: QualityGateConfig,
    openai_budget: OpenAIBudget | None = None,
) -> list[Article]:
    verdicts = judge_ambiguous_articles(ambiguous_articles, budget=openai_budget)
    if not verdicts:
        return survivors

    junk_penalty = min(quality_gate_config.clear_bad_penalty_weight, quality_gate_config.max_content_quality_penalty)
    replacements: dict[int, Article] = {}
    for article in ambiguous_articles:
        verdict = verdicts.get(article.url)
        if verdict is None:
            # No verdict for this URL (chunk failure or omitted) — keep the original
            # regex-only ambiguous-tier penalty.
            continue
        penalty = 0.0 if verdict == "good" else junk_penalty
        replacements[id(article)] = replace(article, content_quality_penalty=penalty)

    if not replacements:
        return survivors
    return [replacements.get(id(article), article) for article in survivors]


def select_classification_candidates(
    clusters: list[StoryCluster],
    pool_size: int | None = None,
    minimum_evidence_score: float = 0.0,
) -> list[StoryCluster]:
    """Global-first classification pool with evidence-gated category reserves."""
    eligible = [
        cluster for cluster in clusters
        if not cluster.skip_reason and cluster.evidence_score >= minimum_evidence_score
    ]
    ranked = sorted(eligible, key=lambda item: item.total_score, reverse=True)
    if pool_size is not None:
        return ranked[:pool_size]
    selected = ranked[:GLOBAL_CLASSIFICATION_POOL_SIZE]
    selected_ids = {story_identity(cluster) for cluster in selected}
    coverage = Counter(category for cluster in selected for category in cluster_feed_hints(cluster))
    queues = {
        category: [cluster for cluster in ranked if category in cluster_feed_hints(cluster)]
        for category in CATEGORY_NAMES
    }
    positions = {category: 0 for category in CATEGORY_NAMES}
    while len(selected) < MAX_CLASSIFICATION_POOL_SIZE:
        progressed = False
        for category in CATEGORY_NAMES:
            if coverage[category] >= CATEGORY_CLASSIFICATION_RESERVE:
                continue
            queue = queues[category]
            while positions[category] < len(queue) and story_identity(queue[positions[category]]) in selected_ids:
                positions[category] += 1
            if positions[category] >= len(queue):
                continue
            cluster = queue[positions[category]]
            positions[category] += 1
            selected.append(cluster)
            selected_ids.add(story_identity(cluster))
            coverage.update(cluster_feed_hints(cluster))
            progressed = True
        if not progressed:
            break
    return selected


def apply_category_assignments(
    candidates: list[StoryCluster],
    assignments: dict[str, CategoryAssignment],
) -> None:
    """Mutates cluster.category in place for every candidate that has an
    assignment. Candidates classify_clusters didn't cover (shouldn't happen --
    it always fills gaps with the degraded fallback -- but defensive here)
    are left at their default empty category, which excludes them from every
    top_for_category result without crashing anything."""
    for cluster in candidates:
        assignment = assignments.get(cluster.key)
        if assignment is not None:
            cluster.category = assignment.category
            cluster.culture_lane = assignment.culture_lane if assignment.category == "culture" else ""


def apply_evidence_gate(clusters: list[StoryCluster], minimum_score: float) -> None:
    for cluster in clusters:
        if not cluster.skip_reason and cluster.evidence_score < minimum_score:
            cluster.skip_reason = "insufficient story context"


def select_backfill_candidates(
    clusters: list[StoryCluster],
    initial_candidates: list[StoryCluster],
    underfilled_categories: list[str],
    minimum_evidence_score: float,
    per_category_limit: int = 10,
) -> list[StoryCluster]:
    initial_ids = {story_identity(cluster) for cluster in initial_candidates}
    selected: list[StoryCluster] = []
    selected_ids: set[str] = set()
    ranked = sorted(clusters, key=lambda item: item.total_score, reverse=True)
    for category in underfilled_categories:
        category_candidates = [
            cluster for cluster in ranked
            if story_identity(cluster) not in initial_ids
            and not cluster.skip_reason
            and cluster.evidence_score >= minimum_evidence_score
            and category in cluster_feed_hints(cluster)
        ][:per_category_limit]
        for cluster in category_candidates:
            identity = story_identity(cluster)
            if identity in selected_ids:
                continue
            selected.append(cluster)
            selected_ids.add(identity)
    return selected


def underfilled_categories(
    selected: dict[str, list[StoryCluster]],
    limits: dict[str, CategorySelectionLimit],
) -> list[str]:
    return [
        category
        for category in CATEGORY_NAMES
        if len(selected[category]) < limits[category].floor
    ]


def underfilled_reasons_by_category(
    clusters: list[StoryCluster],
    selected: dict[str, list[StoryCluster]],
    history_suppressed: list[StoryCluster],
    minimum_evidence_score: float,
    limits: dict[str, CategorySelectionLimit],
) -> dict[str, str]:
    reasons: dict[str, str] = {}
    for category in CATEGORY_NAMES:
        target = limits[category].floor
        if len(selected[category]) >= target:
            reasons[category] = ""
            continue
        eligible_hints = [
            cluster for cluster in clusters
            if category in cluster_feed_hints(cluster) and cluster.evidence_score >= minimum_evidence_score
        ]
        if category == "culture" and len([cluster for cluster in eligible_hints if cluster.category == category]) >= target:
            reasons[category] = "source_diversity_cap"
        elif any(category in cluster_feed_hints(cluster) for cluster in history_suppressed):
            reasons[category] = "history_suppressed_candidates"
        elif len(eligible_hints) >= 4:
            reasons[category] = "classification_moved_candidates_elsewhere"
        else:
            reasons[category] = "not_enough_evidence_qualified_candidates"
    return reasons


def importance_summary_by_category(clusters: list[StoryCluster]) -> dict[str, dict[str, float | int]]:
    summaries: dict[str, dict[str, float | int]] = {}
    for category in CATEGORY_NAMES:
        values = [cluster.importance for cluster in clusters if cluster.category == category and not cluster.skip_reason]
        summaries[category] = {
            "count": len(values),
            "min": round(min(values), 2) if values else 0.0,
            "median": round(statistics.median(values), 2) if values else 0.0,
            "max": round(max(values), 2) if values else 0.0,
        }
    return summaries


def deck_underfilled_reason(result: SelectionResult, clusters: list[StoryCluster], config: AgentConfig) -> str:
    if result.selected_count >= config.importance.deck_target:
        return ""
    remaining = [
        cluster for cluster in clusters
        if cluster.category in CATEGORY_NAMES and not cluster.skip_reason
        and story_identity(cluster) not in {
            story_identity(item) for items in result.category_clusters.values() for item in items
        }
    ]
    if not remaining:
        return "not_enough_qualified_candidates"
    big_day_eligible = [
        cluster for cluster in remaining
        if cluster.importance >= config.importance.big_day_importance_threshold
    ]
    if not big_day_eligible:
        return "importance_below_big_day_threshold"
    culture_sources = Counter(_primary_source(cluster) for cluster in result.category_clusters["culture"])
    culture_lanes = Counter(cluster.culture_lane for cluster in result.category_clusters["culture"])
    if any(
        cluster.category == "culture"
        and (culture_sources[_primary_source(cluster)] >= 2 or culture_lanes[cluster.culture_lane] >= 3)
        for cluster in big_day_eligible
    ):
        return "culture_diversity_caps"
    return "not_enough_qualified_candidates"


def _flatten_cluster_articles(clusters: list[StoryCluster]) -> list[Article]:
    seen: set[int] = set()
    articles: list[Article] = []
    for cluster in clusters:
        for article in cluster.articles:
            if id(article) not in seen:
                articles.append(article)
                seen.add(id(article))
    return articles


async def collect_pipeline_context(
    config: AgentConfig | None = None,
    watchlist_entries: tuple[WatchlistEntry, ...] = (),
    history_path: Path = DEFAULT_HISTORY_PATH,
    ignore_history: bool = False,
    openai_mode: OpenAIMode | None = None,
    quality_gate_log_path: Path | None = None,
    category_assignments_log_path: Path | None = None,
    openai_budget: OpenAIBudget | None = None,
) -> PipelineContext:
    config = config or load_config()
    budget = openai_budget or OpenAIBudget(
        getattr(config, "openai_costs", None) or OpenAICostConfig()
    )
    resolved_mode = resolve_openai_mode(openai_mode=openai_mode)
    capabilities = openai_capabilities(resolved_mode)
    articles = await fetch_all_feeds(
        config.feeds,
        config.lookback_hours,
        config.max_articles,
        config.category_fetch_reserves,
    )

    # Run a cheap title/feed-content pass first, then spend page requests only on
    # plausible finalists. Final quality decisions happen after enrichment.
    preliminary_articles = rank_articles_by_evidence(articles)
    preliminary_clusters = cluster_articles(preliminary_articles)
    apply_cluster_evidence_scores(preliminary_clusters)
    preliminary_clusters = score_clusters(preliminary_clusters, config, watchlist_entries=watchlist_entries)
    preliminary_clusters.sort(key=lambda item: item.total_score, reverse=True)
    enrichment_selection = select_enrichment_clusters(preliminary_clusters, config.enrichment)
    enriched_clusters, enrichment_stats = await asyncio.to_thread(
        enrich_clusters,
        preliminary_clusters,
        config.enrichment,
    )
    enriched_articles = _flatten_cluster_articles(enriched_clusters)

    quality_gate_config = _resolve_quality_gate_config(config)
    survivors, hard_rejections, ambiguous_articles = apply_quality_gate(enriched_articles, quality_gate_config)

    if ambiguous_articles and capabilities.judge_quality:
        survivors = _apply_ambiguous_verdicts(
            survivors,
            ambiguous_articles,
            quality_gate_config,
            budget,
        )

    resolved_quality_gate_log_path = quality_gate_log_path or default_quality_gate_rejections_path()
    write_quality_gate_rejections(hard_rejections, resolved_quality_gate_log_path)

    clusters = cluster_articles(survivors)
    apply_cluster_evidence_scores(clusters)
    clusters = score_clusters(clusters, config, watchlist_entries=watchlist_entries)
    apply_history(clusters, history_path, ignore_history=ignore_history)
    clusters.sort(key=lambda item: item.total_score, reverse=True)

    minimum_evidence = config.enrichment.minimum_story_evidence_score
    apply_evidence_gate(clusters, minimum_evidence)
    candidates = select_classification_candidates(clusters, minimum_evidence_score=minimum_evidence)
    assignments = classify_clusters(
        candidates,
        use_openai=capabilities.classify,
        budget=budget,
    )
    apply_category_assignments(candidates, assignments)
    apply_importance(candidates, assignments, config.importance)

    provisional_result = select_importance_deck(clusters, config, minimum_evidence)
    underfilled = underfilled_categories(provisional_result.category_clusters, config.category_selection_limits)
    backfill = select_backfill_candidates(clusters, candidates, underfilled, minimum_evidence)
    if backfill:
        backfill_assignments = classify_clusters(
            backfill,
            use_openai=capabilities.classify,
            budget=budget,
        )
        assignments.update(backfill_assignments)
        apply_category_assignments(backfill, backfill_assignments)
        apply_importance(backfill, backfill_assignments, config.importance)
    resolved_category_assignments_log_path = category_assignments_log_path or default_category_assignments_path()
    write_category_assignments(assignments, resolved_category_assignments_log_path)

    selection_result = select_importance_deck(clusters, config, minimum_evidence)
    category_clusters = selection_result.category_clusters
    history_suppressed = [cluster for cluster in clusters if "stale/repeated" in cluster.skip_reason]
    insufficient = [cluster for cluster in clusters if cluster.skip_reason == "insufficient story context"]
    selected_counts = {category: len(category_clusters[category]) for category in CATEGORY_NAMES}
    underfilled_reasons = underfilled_reasons_by_category(
        clusters,
        category_clusters,
        history_suppressed,
        minimum_evidence,
        config.category_selection_limits,
    )
    stock_snapshot = await build_stock_snapshot(articles, watchlist_entries)
    return PipelineContext(
        category_clusters=category_clusters,
        stock_snapshot=stock_snapshot,
        all_clusters=clusters,
        quality_gate_rejections=tuple(hard_rejections),
        quality_gate_log_path=resolved_quality_gate_log_path,
        category_assignments=assignments,
        category_assignments_log_path=resolved_category_assignments_log_path,
        diagnostics=PipelineDiagnostics(
            articles_fetched=len(articles),
            pages_attempted=enrichment_stats.pages_attempted,
            pages_extracted=enrichment_stats.pages_extracted,
            pages_blocked=enrichment_stats.pages_blocked,
            pages_failed=enrichment_stats.pages_failed,
            feed_content_articles=sum(bool(article.feed_content) for article in articles),
            fetched_articles_by_feed_hint=count_articles_by_feed_hint(articles),
            preliminary_clusters_by_feed_hint=count_clusters_by_feed_hint(preliminary_clusters),
            enrichment_clusters_by_feed_hint=count_clusters_by_feed_hint(enrichment_selection),
            classification_pool_by_feed_hint=count_clusters_by_feed_hint(candidates),
            history_suppressed_by_feed_hint=count_clusters_by_feed_hint(history_suppressed),
            insufficient_context_by_feed_hint=count_clusters_by_feed_hint(insufficient),
            classified_clusters_by_category=count_clusters_by_category(candidates + backfill),
            backfill_candidates_by_category={
                category: sum(category in cluster_feed_hints(cluster) for cluster in backfill)
                for category in CATEGORY_NAMES
            },
            selected_stories_by_category=selected_counts,
            underfilled_reason_by_category=underfilled_reasons,
            importance_by_category=importance_summary_by_category(candidates + backfill),
            floor_selected_by_category=selection_result.floor_selected_by_category,
            remainder_selected_by_category=selection_result.remainder_selected_by_category,
            big_day_selected_by_category=selection_result.big_day_selected_by_category,
            source_cap_relaxed_by_category=selection_result.source_cap_relaxed_by_category,
            deck_target=config.importance.deck_target,
            deck_selected=selection_result.selected_count,
            deck_underfilled_reason=deck_underfilled_reason(selection_result, clusters, config),
            openai_input_tokens=budget.input_tokens,
            openai_output_tokens=budget.output_tokens,
            openai_cost_usd=round(budget.cost_usd, 6),
            openai_budget_exhausted=budget.exhausted,
            openai_cost_by_stage={
                stage: round(cost, 6)
                for stage, cost in budget.cost_by_stage.items()
            },
            openai_stage_outcomes=budget.stage_outcomes(),
        ),
    )


def resolve_openai_mode(use_openai: bool | None = None, openai_mode: OpenAIMode | None = None) -> OpenAIMode:
    if openai_mode is not None:
        return openai_mode
    if use_openai is False:
        return "off"
    return "full"


def openai_capabilities(mode: OpenAIMode) -> OpenAICapabilities:
    if mode == "full":
        return OpenAICapabilities(judge_quality=True, classify=True, draft=True)
    if mode == "classify-only":
        return OpenAICapabilities(judge_quality=True, classify=True, draft=False)
    return OpenAICapabilities(judge_quality=False, classify=False, draft=False)


async def build_briefings(
    use_openai: bool | None = None,
    config: AgentConfig | None = None,
    openai_mode: OpenAIMode | None = None,
    watchlist_path: Path | None = DEFAULT_WATCHLIST_PATH,
    history_path: Path = DEFAULT_HISTORY_PATH,
    ignore_history: bool = False,
    persist_history: bool = True,
    skipped_log_path: Path | None = None,
    quality_gate_log_path: Path | None = None,
    category_assignments_log_path: Path | None = None,
    compression_audit_dir: Path = DEFAULT_COMPRESSION_AUDIT_DIR,
) -> list[BriefingSection]:
    result = await build_briefing_result(
        use_openai=use_openai,
        config=config,
        openai_mode=openai_mode,
        watchlist_path=watchlist_path,
        history_path=history_path,
        ignore_history=ignore_history,
        persist_history=persist_history,
        skipped_log_path=skipped_log_path,
        quality_gate_log_path=quality_gate_log_path,
        category_assignments_log_path=category_assignments_log_path,
        compression_audit_dir=compression_audit_dir,
    )
    return result.briefings


def selected_clusters(category_clusters: dict[str, list[StoryCluster]]) -> list[StoryCluster]:
    selected: list[StoryCluster] = []
    seen: set[str] = set()
    for clusters in category_clusters.values():
        for cluster in clusters:
            identity = story_identity(cluster)
            if identity not in seen:
                selected.append(cluster)
                seen.add(identity)
    return selected


def source_debug_lines(category_clusters: dict[str, list[StoryCluster]]) -> tuple[str, ...]:
    lines: list[str] = []
    for category, clusters in category_clusters.items():
        if not clusters:
            continue
        distribution = "; ".join(source_distribution_label(cluster) for cluster in clusters if cluster.sources)
        if distribution:
            lines.append(f"{category}: {distribution}")
    return tuple(lines)


def _finance_lead_lines(stock_snapshot: object) -> tuple[str, ...]:
    """Real, non-LLM-generated market-quote lines for the finance section's
    lead-in -- kept structurally separate from drafted paragraphs (see
    BriefingSection.lead_lines) since a drafting model shouldn't be trusted
    to state today's exact price from memory."""
    mega_caps = getattr(stock_snapshot, "mega_caps", None)
    quote_for = getattr(stock_snapshot, "quote_for", None)
    if not mega_caps or quote_for is None:
        return ()
    return tuple(quote_for(symbol).compact() for symbol in mega_caps[:FINANCE_LEAD_TICKER_COUNT])


def build_draft_candidates(
    category_clusters: dict[str, list[StoryCluster]],
    category_assignments: dict[str, CategoryAssignment],
) -> list[DraftCandidate]:
    candidates: list[DraftCandidate] = []
    for category, clusters in category_clusters.items():
        for cluster in clusters:
            assignment = category_assignments.get(cluster.key)
            outlier_urls = set(assignment.outlier_urls) if assignment else set()
            articles = tuple(article for article in cluster.articles if article.url not in outlier_urls) or tuple(
                cluster.articles
            )
            articles = tuple(rank_articles_by_evidence(articles))
            candidates.append(
                DraftCandidate(story_id=cluster.key, category=category, title=cluster.title, articles=articles)
            )
    return candidates


def build_briefing_sections(
    paragraphs: list[BriefingParagraph],
    config: AgentConfig,
    stock_snapshot: object,
) -> list[BriefingSection]:
    by_category: dict[str, list[BriefingParagraph]] = {name: [] for name in CATEGORY_NAMES}
    for paragraph in paragraphs:
        by_category.setdefault(paragraph.category, []).append(paragraph)

    sections: list[BriefingSection] = []
    for category in CATEGORY_NAMES:
        label = config.categories[category].label if category in config.categories else category
        lead_lines = _finance_lead_lines(stock_snapshot) if category == "finance" else ()
        sections.append(
            BriefingSection(
                category=category,
                label=label,
                paragraphs=tuple(by_category.get(category, ())),
                lead_lines=lead_lines,
            )
        )
    return sections


async def build_briefing_result(
    use_openai: bool | None = None,
    config: AgentConfig | None = None,
    openai_mode: OpenAIMode | None = None,
    watchlist_path: Path | None = DEFAULT_WATCHLIST_PATH,
    history_path: Path = DEFAULT_HISTORY_PATH,
    ignore_history: bool = False,
    skipped_log_path: Path | None = None,
    quality_gate_log_path: Path | None = None,
    category_assignments_log_path: Path | None = None,
    compression_audit_dir: Path = DEFAULT_COMPRESSION_AUDIT_DIR,
    persist_history: bool = True,
) -> BriefingBuildResult:
    config = config or load_config()
    mode = resolve_openai_mode(use_openai=use_openai, openai_mode=openai_mode)
    openai_budget = OpenAIBudget(config.openai_costs)
    watchlist_entries = load_watchlist(watchlist_path) if watchlist_path is not None else ()
    context = await collect_pipeline_context(
        config,
        watchlist_entries=watchlist_entries,
        history_path=history_path,
        ignore_history=ignore_history,
        openai_mode=mode,
        quality_gate_log_path=quality_gate_log_path,
        category_assignments_log_path=category_assignments_log_path,
        openai_budget=openai_budget,
    )

    draft_candidates = build_draft_candidates(context.category_clusters, context.category_assignments)
    capabilities = openai_capabilities(mode)
    drafting_result: DraftRunResult = draft_paragraphs_result(
        draft_candidates,
        use_openai=capabilities.draft,
        config=config.drafting,
        budget=openai_budget,
    )
    drafted_paragraphs = drafting_result.paragraphs
    compression_result: CompressionRunResult = compress_paragraphs_result(
        drafted_paragraphs,
        config.compression,
        use_openai=capabilities.draft,
        budget=openai_budget,
    )
    compressed_paragraphs = compression_result.paragraphs
    compression_audit_path: Path | None = None
    cleanup_compression_audits(compression_audit_dir)
    if config.compression.enabled and capabilities.draft:
        compression_audit_path = write_compression_audit(
            compressed_paragraphs,
            draft_candidates,
            compression_result,
            config.compression,
            path=default_compression_audit_path(compression_audit_dir),
        )
    presentation_paragraphs = order_paragraphs_for_presentation(compressed_paragraphs, context.category_clusters)
    briefings = build_briefing_sections(presentation_paragraphs, config, context.stock_snapshot)

    selected = selected_clusters(context.category_clusters)
    if persist_history and not ignore_history:
        save_story_history(selected, history_path)
    quality_gate_config = _resolve_quality_gate_config(config)
    skipped = build_skipped_stories(
        context.all_clusters,
        selected,
        low_content_quality_threshold=quality_gate_config.low_content_quality_skip_threshold,
    )
    resolved_skipped_log_path = skipped_log_path or default_skipped_path()
    write_skipped_log(skipped, resolved_skipped_log_path)
    return BriefingBuildResult(
        briefings=briefings,
        skipped_stories=skipped,
        skipped_log_path=resolved_skipped_log_path,
        source_debug_lines=source_debug_lines(context.category_clusters),
        quality_gate_rejections=context.quality_gate_rejections,
        quality_gate_log_path=context.quality_gate_log_path,
        category_assignments_log_path=context.category_assignments_log_path,
        compression_audit_path=compression_audit_path,
        diagnostics=replace(
            context.diagnostics,
            llm_drafts=sum(paragraph.draft_status == "llm" for paragraph in drafted_paragraphs),
            fallback_drafts=sum(paragraph.draft_status != "llm" for paragraph in drafted_paragraphs),
            fallback_story_ids=tuple(
                paragraph.story_id for paragraph in drafted_paragraphs if paragraph.draft_status != "llm"
            ),
            drafting_input_tokens=drafting_result.input_tokens,
            drafting_output_tokens=drafting_result.output_tokens,
            drafting_cost_usd=round(drafting_result.cost_usd, 6),
            drafting_budget_exhausted=drafting_result.budget_exhausted,
            compressed_count=sum(
                paragraph.compression_status == "compressed" for paragraph in compressed_paragraphs
            ),
            compression_status_counts=dict(
                Counter(paragraph.compression_status for paragraph in compressed_paragraphs)
            ),
            median_compression_ratio=round(
                statistics.median(
                    paragraph.compression_ratio
                    for paragraph in compressed_paragraphs
                    if paragraph.compression_status == "compressed"
                ),
                4,
            )
            if any(paragraph.compression_status == "compressed" for paragraph in compressed_paragraphs)
            else 0.0,
            guard_failures=sum(
                paragraph.compression_status == "kept_original_guard_failed"
                for paragraph in compressed_paragraphs
            ),
            entity_warnings=compression_result.entity_warnings,
            compression_cost_usd=round(compression_result.cost_usd, 6),
            compression_budget_exhausted=compression_result.budget_exhausted,
            openai_input_tokens=openai_budget.input_tokens,
            openai_output_tokens=openai_budget.output_tokens,
            openai_cost_usd=round(openai_budget.cost_usd, 6),
            openai_budget_exhausted=openai_budget.exhausted,
            openai_cost_by_stage={
                stage: round(cost, 6)
                for stage, cost in openai_budget.cost_by_stage.items()
            },
            openai_stage_outcomes=openai_budget.stage_outcomes(),
        ),
    )


def build_briefings_sync(
    use_openai: bool | None = None,
    config: AgentConfig | None = None,
    openai_mode: OpenAIMode | None = None,
    watchlist_path: Path | None = DEFAULT_WATCHLIST_PATH,
    history_path: Path = DEFAULT_HISTORY_PATH,
    ignore_history: bool = False,
    persist_history: bool = True,
    skipped_log_path: Path | None = None,
    quality_gate_log_path: Path | None = None,
    category_assignments_log_path: Path | None = None,
    compression_audit_dir: Path = DEFAULT_COMPRESSION_AUDIT_DIR,
) -> list[BriefingSection]:
    return asyncio.run(
        build_briefings(
            use_openai=use_openai,
            config=config,
            openai_mode=openai_mode,
            watchlist_path=watchlist_path,
            history_path=history_path,
            ignore_history=ignore_history,
            persist_history=persist_history,
            skipped_log_path=skipped_log_path,
            quality_gate_log_path=quality_gate_log_path,
            category_assignments_log_path=category_assignments_log_path,
            compression_audit_dir=compression_audit_dir,
        )
    )


def build_briefing_result_sync(
    use_openai: bool | None = None,
    config: AgentConfig | None = None,
    openai_mode: OpenAIMode | None = None,
    watchlist_path: Path | None = DEFAULT_WATCHLIST_PATH,
    history_path: Path = DEFAULT_HISTORY_PATH,
    ignore_history: bool = False,
    skipped_log_path: Path | None = None,
    quality_gate_log_path: Path | None = None,
    category_assignments_log_path: Path | None = None,
    compression_audit_dir: Path = DEFAULT_COMPRESSION_AUDIT_DIR,
    persist_history: bool = True,
) -> BriefingBuildResult:
    return asyncio.run(
        build_briefing_result(
            use_openai=use_openai,
            config=config,
            openai_mode=openai_mode,
            watchlist_path=watchlist_path,
            history_path=history_path,
            ignore_history=ignore_history,
            skipped_log_path=skipped_log_path,
            quality_gate_log_path=quality_gate_log_path,
            category_assignments_log_path=category_assignments_log_path,
            compression_audit_dir=compression_audit_dir,
            persist_history=persist_history,
        )
    )


async def build_alert_result(
    config: AgentConfig | None = None,
    watchlist_path: Path | None = DEFAULT_WATCHLIST_PATH,
    alert_config_path: Path = DEFAULT_ALERT_CONFIG_PATH,
    alert_history_path: Path = DEFAULT_ALERT_HISTORY_PATH,
) -> AlertBuildResult:
    config = config or load_config()
    watchlist_entries = load_watchlist(watchlist_path) if watchlist_path is not None else ()
    context = await collect_pipeline_context(
        config,
        watchlist_entries=watchlist_entries,
        ignore_history=True,
    )
    alert_config = load_alert_config(alert_config_path)
    alerts = generate_alerts(context.all_clusters, context.stock_snapshot.market_movers, alert_config)
    alerts = apply_alert_cooldown(alerts, alert_history_path, alert_config.cooldown_minutes)
    return AlertBuildResult(alerts=alerts, alert_history_path=alert_history_path)


def build_alert_result_sync(
    config: AgentConfig | None = None,
    watchlist_path: Path | None = DEFAULT_WATCHLIST_PATH,
    alert_config_path: Path = DEFAULT_ALERT_CONFIG_PATH,
    alert_history_path: Path = DEFAULT_ALERT_HISTORY_PATH,
) -> AlertBuildResult:
    return asyncio.run(
        build_alert_result(
            config=config,
            watchlist_path=watchlist_path,
            alert_config_path=alert_config_path,
            alert_history_path=alert_history_path,
        )
    )
