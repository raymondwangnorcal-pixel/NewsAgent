from __future__ import annotations

import os
import tomllib
from pathlib import Path

from news_agent.models import (
    AgentConfig,
    CategoryConfig,
    FeedConfig,
    FormattingConfig,
    QualityGateConfig,
    SourceTierConfig,
)


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "sources.toml"


def parse_bool(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> AgentConfig:
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    settings = raw.get("settings", {})
    quality_gate_settings = raw.get("quality_gate", {})
    feeds = tuple(
        FeedConfig(
            name=item["name"],
            url=item["url"],
            reputation=float(item.get("reputation", 0.7)),
            categories=tuple(item.get("categories", ())),
            source_type=item.get("source_type", "general"),
            region=item.get("region", "global"),
            quality_weight=float(item.get("quality_weight", item.get("reputation", 0.7))),
            political_leaning=item.get("political_leaning", ""),
        )
        for item in raw.get("feeds", [])
    )
    categories = {
        name: CategoryConfig(name=name, label=item["label"])
        for name, item in raw.get("categories", {}).items()
    }
    source_tiers = {
        name: SourceTierConfig(
            primary=item["primary"],
            secondary=item["secondary"],
            specialist=item["specialist"],
        )
        for name, item in raw.get("source_tiers", {}).items()
    }
    return AgentConfig(
        feeds=feeds,
        categories=categories,
        lookback_hours=int(os.getenv("BRIEFING_LOOKBACK_HOURS", settings.get("lookback_hours", 30))),
        max_articles=int(os.getenv("BRIEFING_MAX_ARTICLES", settings.get("max_articles", 240))),
        formatting=FormattingConfig(
            max_chars_per_message_sms=int(
                os.getenv("BRIEFING_MAX_CHARS_PER_MESSAGE_SMS", settings.get("max_chars_per_message_sms", 1400))
            ),
            max_stories_per_category_sms=int(
                os.getenv(
                    "BRIEFING_MAX_STORIES_PER_CATEGORY_SMS",
                    settings.get("max_stories_per_category_sms", 5),
                )
            ),
            max_sources_per_story=int(
                os.getenv("BRIEFING_MAX_SOURCES_PER_STORY", settings.get("max_sources_per_story", 3))
            ),
            include_links_sms=parse_bool(
                os.getenv("BRIEFING_INCLUDE_LINKS_SMS", settings.get("include_links_sms")),
                default=False,
            ),
            include_links_telegram=parse_bool(
                os.getenv("BRIEFING_INCLUDE_LINKS_TELEGRAM", settings.get("include_links_telegram")),
                default=True,
            ),
        ),
        quality_gate=QualityGateConfig(
            min_summary_chars=int(
                os.getenv(
                    "BRIEFING_QUALITY_GATE_MIN_SUMMARY_CHARS",
                    quality_gate_settings.get("min_summary_chars", 80),
                )
            ),
            summary_duplicate_threshold=float(
                os.getenv(
                    "BRIEFING_QUALITY_GATE_SUMMARY_DUPLICATE_THRESHOLD",
                    quality_gate_settings.get("summary_duplicate_threshold", 0.85),
                )
            ),
            ambiguous_penalty_weight=float(
                os.getenv(
                    "BRIEFING_QUALITY_GATE_AMBIGUOUS_PENALTY_WEIGHT",
                    quality_gate_settings.get("ambiguous_penalty_weight", 0.4),
                )
            ),
            clear_bad_penalty_weight=float(
                os.getenv(
                    "BRIEFING_QUALITY_GATE_CLEAR_BAD_PENALTY_WEIGHT",
                    quality_gate_settings.get("clear_bad_penalty_weight", 1.5),
                )
            ),
            max_content_quality_penalty=float(
                os.getenv(
                    "BRIEFING_QUALITY_GATE_MAX_CONTENT_QUALITY_PENALTY",
                    quality_gate_settings.get("max_content_quality_penalty", 2.5),
                )
            ),
            low_content_quality_skip_threshold=float(
                os.getenv(
                    "BRIEFING_QUALITY_GATE_LOW_CONTENT_QUALITY_SKIP_THRESHOLD",
                    quality_gate_settings.get("low_content_quality_skip_threshold", 1.0),
                )
            ),
        ),
        source_tiers=source_tiers,
    )
