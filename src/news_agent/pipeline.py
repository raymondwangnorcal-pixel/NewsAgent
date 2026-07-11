from __future__ import annotations

import asyncio
from dataclasses import dataclass
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
from news_agent.cluster import cluster_articles
from news_agent.config import load_config
from news_agent.fetch import fetch_all_feeds
from news_agent.history import DEFAULT_HISTORY_PATH, apply_history, save_story_history
from news_agent.models import AgentConfig, BriefingText, StoryCluster
from news_agent.scoring import score_clusters, top_for_category
from news_agent.skipped_log import SkippedStory, build_skipped_stories, default_skipped_path, write_skipped_log
from news_agent.source_balance import source_distribution_label
from news_agent.summarize import (
    generate_briefings_with_openai,
    generate_fallback_briefings,
    generate_polished_briefings_with_openai,
)
from news_agent.stocks import build_stock_snapshot
from news_agent.watchlist import DEFAULT_WATCHLIST_PATH, WatchlistEntry, load_watchlist


OpenAIMode = Literal["full", "polish", "off"]

CATEGORY_LIMITS = {
    "business_tech": 6,
    "domestic": 6,
    "global": 6,
    "culture": 6,
    "finance": 6,
}


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


@dataclass(frozen=True)
class BriefingBuildResult:
    briefings: list[BriefingText]
    skipped_stories: list[SkippedStory]
    skipped_log_path: Path
    source_debug_lines: tuple[str, ...]


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


async def collect_pipeline_context(
    config: AgentConfig | None = None,
    watchlist_entries: tuple[WatchlistEntry, ...] = (),
    history_path: Path = DEFAULT_HISTORY_PATH,
    ignore_history: bool = False,
) -> PipelineContext:
    config = config or load_config()
    articles = await fetch_all_feeds(config.feeds, config.lookback_hours, config.max_articles)
    clusters = score_clusters(cluster_articles(articles), config, watchlist_entries=watchlist_entries)
    apply_history(clusters, history_path, ignore_history=ignore_history)
    clusters.sort(key=lambda item: item.total_score, reverse=True)
    category_clusters = select_unique_category_clusters(clusters)
    stock_snapshot = await build_stock_snapshot(articles, watchlist_entries)
    return PipelineContext(category_clusters=category_clusters, stock_snapshot=stock_snapshot, all_clusters=clusters)


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
) -> list[BriefingText]:
    result = await build_briefing_result(
        use_openai=use_openai,
        config=config,
        openai_mode=openai_mode,
        watchlist_path=watchlist_path,
        history_path=history_path,
        ignore_history=ignore_history,
        persist_history=persist_history,
        skipped_log_path=skipped_log_path,
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
    used_story_ids: set[str] = set()
    category_clusters: dict[str, list[StoryCluster]] = {}
    for category, limit in CATEGORY_LIMITS.items():
        available = [cluster for cluster in clusters if story_identity(cluster) not in used_story_ids]
        selected = top_for_category(available, category, limit)
        category_clusters[category] = selected
        used_story_ids.update(story_identity(cluster) for cluster in selected)

    return category_clusters


def source_debug_lines(category_clusters: dict[str, list[StoryCluster]]) -> tuple[str, ...]:
    lines: list[str] = []
    for category, clusters in category_clusters.items():
        if not clusters:
            continue
        distribution = "; ".join(source_distribution_label(cluster) for cluster in clusters if cluster.sources)
        if distribution:
            lines.append(f"{category}: {distribution}")
    return tuple(lines)


async def build_briefing_result(
    use_openai: bool | None = None,
    config: AgentConfig | None = None,
    openai_mode: OpenAIMode | None = None,
    watchlist_path: Path | None = DEFAULT_WATCHLIST_PATH,
    history_path: Path = DEFAULT_HISTORY_PATH,
    ignore_history: bool = False,
    skipped_log_path: Path | None = None,
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
    )

    if mode == "full":
        briefings = generate_briefings_with_openai(context.category_clusters, config, context.stock_snapshot)
    else:
        draft_briefings = generate_fallback_briefings(context.category_clusters, config, context.stock_snapshot)
        if mode == "polish":
            briefings = generate_polished_briefings_with_openai(draft_briefings)
        elif mode == "off":
            briefings = draft_briefings
        else:
            raise ValueError(f"Unsupported OpenAI mode: {mode}")

    selected = selected_clusters(context.category_clusters)
    if persist_history and not ignore_history:
        save_story_history(selected, history_path)
    skipped = build_skipped_stories(context.all_clusters, selected)
    resolved_skipped_log_path = skipped_log_path or default_skipped_path()
    write_skipped_log(skipped, resolved_skipped_log_path)
    return BriefingBuildResult(
        briefings=briefings,
        skipped_stories=skipped,
        skipped_log_path=resolved_skipped_log_path,
        source_debug_lines=source_debug_lines(context.category_clusters),
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
) -> list[BriefingText]:
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
