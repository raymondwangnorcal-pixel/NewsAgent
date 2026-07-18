from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal

from news_agent.alerts import (
    DEFAULT_ALERT_CONFIG_PATH,
    DEFAULT_ALERT_HISTORY_PATH,
    NewsAlert,
    apply_alert_cooldown,
    generate_alerts,
    load_alert_config,
)
from news_agent.classify import (
    classify_clusters,
    default_category_assignments_path,
    write_category_assignments,
)
from news_agent.cluster import cluster_articles
from news_agent.config import load_config
from news_agent.draft import DraftCandidate, draft_paragraphs
from news_agent.fetch import fetch_all_feeds
from news_agent.history import DEFAULT_HISTORY_PATH, apply_history, save_story_history
from news_agent.models import (
    AgentConfig,
    Article,
    BriefingParagraph,
    BriefingSection,
    CategoryAssignment,
    QualityGateConfig,
    StoryCluster,
)
from news_agent.quality_gate import (
    apply_quality_gate,
    default_quality_gate_rejections_path,
    judge_ambiguous_articles,
    write_quality_gate_rejections,
)
from news_agent.scoring import score_clusters, top_for_category
from news_agent.skipped_log import SkippedStory, build_skipped_stories, default_skipped_path, write_skipped_log
from news_agent.source_balance import cluster_source_attributions, resolve_source_name, source_distribution_label
from news_agent.stocks import build_stock_snapshot
from news_agent.watchlist import DEFAULT_WATCHLIST_PATH, WatchlistEntry, load_watchlist


OpenAIMode = Literal["full", "off"]

CATEGORY_LIMITS = {
    "business_tech": 6,
    "domestic": 6,
    "global": 6,
    "culture": 6,
    "finance": 6,
}

# Materially larger than the ~30 stories that actually publish (5 categories x 6),
# so importance-ranking (category-agnostic) doesn't starve a naturally-lower-scoring
# category before classification even runs -- e.g. Culture+Media during a week
# dominated by conflict/finance-market-reaction stories.
CLASSIFICATION_POOL_SIZE = 50

FINANCE_LEAD_TICKER_COUNT = 7
PRIMARY_SOURCE_TIER_BOOST = 0.75
SECONDARY_SOURCE_TIER_BOOST = 0.5


def story_identity(cluster: StoryCluster) -> str:
    if cluster.key:
        return cluster.key
    if cluster.urls:
        return cluster.urls[0].split("?", 1)[0]
    return cluster.title.casefold()


@dataclass(frozen=True)
class PipelineContext:
    category_clusters: dict[str, list[StoryCluster]]
    stock_snapshot: object
    all_clusters: list[StoryCluster]
    quality_gate_rejections: tuple[tuple[Article, str], ...] = ()
    quality_gate_log_path: Path | None = None
    category_assignments: dict[str, CategoryAssignment] = field(default_factory=dict)
    category_assignments_log_path: Path | None = None


@dataclass(frozen=True)
class BriefingBuildResult:
    briefings: list[BriefingSection]
    skipped_stories: list[SkippedStory]
    skipped_log_path: Path
    source_debug_lines: tuple[str, ...]
    quality_gate_rejections: tuple[tuple[Article, str], ...] = ()
    quality_gate_log_path: Path | None = None
    category_assignments_log_path: Path | None = None


@dataclass(frozen=True)
class AlertBuildResult:
    alerts: list[NewsAlert]
    alert_history_path: Path


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
) -> list[Article]:
    verdicts = judge_ambiguous_articles(ambiguous_articles)
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
    pool_size: int = CLASSIFICATION_POOL_SIZE,
) -> list[StoryCluster]:
    """Bounded, category-agnostic top-N by importance score. Sized well above the
    number of stories that will actually publish so a dominant topic can't crowd
    out every candidate for a naturally-lower-scoring category before the
    guideline-driven classifier ever gets a chance to weigh in."""
    eligible = [cluster for cluster in clusters if not cluster.skip_reason]
    ranked = sorted(eligible, key=lambda item: item.total_score, reverse=True)
    return ranked[:pool_size]


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


def apply_source_tier_scoring(candidates: list[StoryCluster], config: AgentConfig) -> None:
    """Apply category-aware source boosts and annotate corroboration integrity.

    Uncertain body-similarity matches deliberately retain the article's display
    outlet as an independent identity. Only explicit publisher/title-credit
    evidence can collapse syndicated copies into the same source.
    """
    for cluster in candidates:
        attributions = cluster_source_attributions(cluster)
        counted_identities = {
            attribution.resolved_source if attribution.confidence == "confirmed" else attribution.display_source
            for attribution in attributions
        }
        cluster.corroboration_status = "confirmed" if len(counted_identities) >= 2 else "single_source"
        cluster.source_attributions = tuple(
            {
                "display_source": attribution.display_source,
                "resolved_source": attribution.resolved_source,
                "confidence": attribution.confidence,
                "signal": attribution.signal,
            }
            for attribution in attributions
        )

        tier = config.source_tiers.get(cluster.category)
        roles: list[str] = []
        specialist_urls: list[str] = []
        if tier is not None:
            tier_sources = {
                "primary": resolve_source_name(tier.primary) or tier.primary,
                "secondary": resolve_source_name(tier.secondary) or tier.secondary,
                "specialist": resolve_source_name(tier.specialist) or tier.specialist,
            }
            confirmed_sources = {
                attribution.resolved_source for attribution in attributions if attribution.confidence == "confirmed"
            }
            for role, source in tier_sources.items():
                if source in confirmed_sources:
                    roles.append(role)
            for article, attribution in zip(cluster.articles, attributions):
                if attribution.resolved_source == tier_sources["specialist"] and attribution.confidence == "confirmed":
                    specialist_urls.append(article.url)

            if "primary" in roles:
                cluster.total_score += PRIMARY_SOURCE_TIER_BOOST
            if "secondary" in roles:
                cluster.total_score += SECONDARY_SOURCE_TIER_BOOST

        cluster.source_roles = tuple(roles)
        cluster.specialist_article_urls = tuple(dict.fromkeys(specialist_urls))
        if cluster.source_count >= 2 and len(counted_identities) == 1:
            cluster.skip_reason = "single wire-syndicated source only"


async def collect_pipeline_context(
    config: AgentConfig | None = None,
    watchlist_entries: tuple[WatchlistEntry, ...] = (),
    history_path: Path = DEFAULT_HISTORY_PATH,
    ignore_history: bool = False,
    openai_mode: OpenAIMode | None = None,
    quality_gate_log_path: Path | None = None,
    category_assignments_log_path: Path | None = None,
) -> PipelineContext:
    config = config or load_config()
    resolved_mode = resolve_openai_mode(openai_mode=openai_mode)
    articles = await fetch_all_feeds(config.feeds, config.lookback_hours, config.max_articles)

    quality_gate_config = _resolve_quality_gate_config(config)
    survivors, hard_rejections, ambiguous_articles = apply_quality_gate(list(articles), quality_gate_config)

    if ambiguous_articles and resolved_mode != "off":
        survivors = _apply_ambiguous_verdicts(survivors, ambiguous_articles, quality_gate_config)

    resolved_quality_gate_log_path = quality_gate_log_path or default_quality_gate_rejections_path()
    write_quality_gate_rejections(hard_rejections, resolved_quality_gate_log_path)

    clusters = score_clusters(cluster_articles(survivors), config, watchlist_entries=watchlist_entries)
    apply_history(clusters, history_path, ignore_history=ignore_history)
    clusters.sort(key=lambda item: item.total_score, reverse=True)

    candidates = select_classification_candidates(clusters)
    assignments = classify_clusters(candidates, openai_mode=resolved_mode)
    apply_category_assignments(candidates, assignments)
    apply_source_tier_scoring(candidates, config)
    clusters.sort(key=lambda item: item.total_score, reverse=True)
    resolved_category_assignments_log_path = category_assignments_log_path or default_category_assignments_path()
    write_category_assignments(assignments, resolved_category_assignments_log_path, clusters=candidates)

    category_clusters = select_unique_category_clusters(clusters)
    stock_snapshot = await build_stock_snapshot(articles, watchlist_entries)
    return PipelineContext(
        category_clusters=category_clusters,
        stock_snapshot=stock_snapshot,
        all_clusters=clusters,
        quality_gate_rejections=tuple(hard_rejections),
        quality_gate_log_path=resolved_quality_gate_log_path,
        category_assignments=assignments,
        category_assignments_log_path=resolved_category_assignments_log_path,
    )


def resolve_openai_mode(use_openai: bool | None = None, openai_mode: OpenAIMode | None = None) -> OpenAIMode:
    if openai_mode is not None:
        return openai_mode
    if use_openai is False:
        return "off"
    return "full"


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


def select_unique_category_clusters(clusters: list[StoryCluster]) -> dict[str, list[StoryCluster]]:
    # Cross-category duplication is structurally prevented upstream: classify_clusters
    # assigns exactly one category per cluster, so there's no "first category to claim
    # it wins" tiebreak to do here anymore -- just cap each category independently.
    return {category: top_for_category(clusters, category, limit) for category, limit in CATEGORY_LIMITS.items()}


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
            candidates.append(
                DraftCandidate(
                    story_id=cluster.key,
                    category=category,
                    title=cluster.title,
                    articles=articles,
                    corroboration_status=cluster.corroboration_status,
                    specialist_article_urls=cluster.specialist_article_urls,
                )
            )
    return candidates


def build_briefing_sections(
    paragraphs: list[BriefingParagraph],
    config: AgentConfig,
    stock_snapshot: object,
) -> list[BriefingSection]:
    by_category: dict[str, list[BriefingParagraph]] = {name: [] for name in CATEGORY_LIMITS}
    for paragraph in paragraphs:
        by_category.setdefault(paragraph.category, []).append(paragraph)

    sections: list[BriefingSection] = []
    for category in CATEGORY_LIMITS:
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
    persist_history: bool = True,
) -> BriefingBuildResult:
    config = config or load_config()
    mode = resolve_openai_mode(use_openai=use_openai, openai_mode=openai_mode)
    watchlist_entries = load_watchlist(watchlist_path) if watchlist_path is not None else ()
    context = await collect_pipeline_context(
        config,
        watchlist_entries=watchlist_entries,
        history_path=history_path,
        ignore_history=ignore_history,
        openai_mode=mode,
        quality_gate_log_path=quality_gate_log_path,
        category_assignments_log_path=category_assignments_log_path,
    )

    draft_candidates = build_draft_candidates(context.category_clusters, context.category_assignments)
    paragraphs = draft_paragraphs(draft_candidates, openai_mode=mode)
    briefings = build_briefing_sections(paragraphs, config, context.stock_snapshot)

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
