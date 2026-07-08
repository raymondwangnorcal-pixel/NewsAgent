from __future__ import annotations

import asyncio

from news_agent.cluster import cluster_articles
from news_agent.config import load_config
from news_agent.fetch import fetch_all_feeds
from news_agent.models import AgentConfig, BriefingText, StoryCluster
from news_agent.scoring import score_clusters, top_for_category, top_overall
from news_agent.summarize import generate_briefings_with_openai, generate_fallback_briefings
from news_agent.stocks import build_stock_snapshot


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


async def build_briefings(use_openai: bool = True, config: AgentConfig | None = None) -> list[BriefingText]:
    config = config or load_config()
    category_clusters, stock_snapshot = await collect_context(config)
    if use_openai:
        return generate_briefings_with_openai(category_clusters, config, stock_snapshot)
    return generate_fallback_briefings(category_clusters, config, stock_snapshot)


def build_briefings_sync(use_openai: bool = True, config: AgentConfig | None = None) -> list[BriefingText]:
    return asyncio.run(build_briefings(use_openai=use_openai, config=config))
