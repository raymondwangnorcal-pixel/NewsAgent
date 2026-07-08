from __future__ import annotations

import os
import tomllib
from pathlib import Path

from news_agent.models import AgentConfig, CategoryConfig, FeedConfig


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "sources.toml"


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> AgentConfig:
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    settings = raw.get("settings", {})
    feeds = tuple(
        FeedConfig(
            name=item["name"],
            url=item["url"],
            reputation=float(item.get("reputation", 0.7)),
            categories=tuple(item.get("categories", ())),
        )
        for item in raw.get("feeds", [])
    )
    categories = {
        name: CategoryConfig(
            name=name,
            label=item["label"],
            keywords=tuple(keyword.lower() for keyword in item.get("keywords", ())),
            impact_terms=tuple(term.lower() for term in item.get("impact_terms", ())),
        )
        for name, item in raw.get("categories", {}).items()
    }
    return AgentConfig(
        feeds=feeds,
        categories=categories,
        lookback_hours=int(os.getenv("BRIEFING_LOOKBACK_HOURS", settings.get("lookback_hours", 30))),
        max_articles=int(os.getenv("BRIEFING_MAX_ARTICLES", settings.get("max_articles", 240))),
    )
