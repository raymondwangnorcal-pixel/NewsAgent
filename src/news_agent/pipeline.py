from __future__ import annotations

import asyncio
from typing import Literal

from news_agent.cluster import cluster_articles
from news_agent.config import load_config
from news_agent.fetch import fetch_all_feeds
from news_agent.models import AgentConfig, BriefingText, StoryCluster
from news_agent.scoring import score_clusters, top_for_category, top_overall
from news_agent.summarize import (
    generate_briefings_with_openai,
    generate_fallback_briefings,
    generate_polished_briefings_with_openai,
)
from news_agent.stocks import build_stock_snapshot


OpenAIMode = Literal["full", "polish", "off"]

CATEGORY_LIMITS = {
    "business_tech": 6,
    "domestic": 6,
    "global": 6,
    "culture": 6,
    "finance": 6,
}


async def collect_and_rank(config: AgentConfig | None = None) -> dict[str, list[StoryCluster]]:
    category_clusters, _ = await collect_context(config)
    return category_clusters


async def collect_context(config: AgentConfig | None = None):
    config = config or load_config()
    articles = await fetch_all_feeds(config.feeds, config.lookback_hours, config.max_articles)
    clusters = score_clusters(cluster_articles(articles), config)
    category_clusters = {
        category: top_for_category(clusters, category, limit)
        for category, limit in CATEGORY_LIMITS.items()
    }
    category_clusters["overall"] = top_overall(clusters, 10)
    stock_snapshot = await build_stock_snapshot(articles)
    return category_clusters, stock_snapshot


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
) -> list[BriefingText]:
    config = config or load_config()
    mode = resolve_openai_mode(use_openai=use_openai, openai_mode=openai_mode)
    category_clusters, stock_snapshot = await collect_context(config)

    if mode == "full":
        return generate_briefings_with_openai(category_clusters, config, stock_snapshot)

    draft_briefings = generate_fallback_briefings(category_clusters, config, stock_snapshot)
    if mode == "polish":
        return generate_polished_briefings_with_openai(draft_briefings)
    if mode == "off":
        return draft_briefings

    raise ValueError(f"Unsupported OpenAI mode: {mode}")


def build_briefings_sync(
    use_openai: bool | None = None,
    config: AgentConfig | None = None,
    openai_mode: OpenAIMode | None = None,
) -> list[BriefingText]:
    return asyncio.run(build_briefings(use_openai=use_openai, config=config, openai_mode=openai_mode))
