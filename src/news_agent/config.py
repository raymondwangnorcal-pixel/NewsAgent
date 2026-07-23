from __future__ import annotations

import os
import tomllib
from pathlib import Path

from news_agent.models import (
    AgentConfig, CategoryConfig, CategorySelectionLimit, DEFAULT_CATEGORY_FETCH_RESERVES,
    DEFAULT_CATEGORY_SELECTION_LIMITS, EnrichmentConfig, ExtractionPolicyConfig, FeedConfig,
    FormattingConfig, ImportanceConfig, QualityGateConfig,
)


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "sources.toml"
ALLOWED_CULTURE_LANES = {"", "film_tv", "music", "sports", "gaming", "media_creators", "internet_culture"}


def parse_bool(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def parse_culture_lane(value: object) -> str:
    lane = str(value or "")
    if lane not in ALLOWED_CULTURE_LANES:
        raise ValueError(f"Unsupported culture_lane: {lane}")
    return lane


def parse_nonnegative_int(value: object, setting_name: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise ValueError(f"{setting_name} must be non-negative")
    return parsed


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> AgentConfig:
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    settings = raw.get("settings", {})
    quality_gate_settings = raw.get("quality_gate", {})
    enrichment_settings = raw.get("enrichment", {})
    fetch_reserve_settings = raw.get("fetch_reserves", DEFAULT_CATEGORY_FETCH_RESERVES)
    importance_settings = raw.get("importance", {})
    selection_settings = raw.get("selection", {})
    selection_limit_settings = raw.get("selection_limits", {})
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
            culture_lane=parse_culture_lane(item.get("culture_lane", "")),  # type: ignore[arg-type]
        )
        for item in raw.get("feeds", [])
    )
    categories = {
        name: CategoryConfig(name=name, label=item["label"])
        for name, item in raw.get("categories", {}).items()
    }
    max_articles = int(os.getenv("BRIEFING_MAX_ARTICLES", settings.get("max_articles", 240)))
    category_fetch_reserves = {
        category: parse_nonnegative_int(fetch_reserve_settings.get(category, 0), f"fetch_reserves.{category}")
        for category in DEFAULT_CATEGORY_FETCH_RESERVES
    }
    if sum(category_fetch_reserves.values()) > max_articles:
        raise ValueError("category fetch reserves cannot exceed max_articles")
    importance = ImportanceConfig(
        enabled=parse_bool(importance_settings.get("enabled"), default=True),
        logistic_midpoint=float(importance_settings.get("logistic_midpoint", 12.0)),
        logistic_steepness=float(importance_settings.get("logistic_steepness", 0.30)),
        llm_weight=float(importance_settings.get("llm_weight", 0.65)),
        clamp_down=float(importance_settings.get("clamp_down", 25.0)),
        clamp_up=float(importance_settings.get("clamp_up", 100.0)),
        deck_target=int(importance_settings.get("deck_target", 25)),
        big_day_importance_threshold=float(importance_settings.get("big_day_importance_threshold", 70.0)),
        big_day_requires_corroboration=parse_bool(
            importance_settings.get("big_day_requires_corroboration"), default=True
        ),
        calibration_version=str(
            importance_settings.get("calibration_version", "total-score-v1-2026-07-21")
        ),
    )
    limits = {
        category: CategorySelectionLimit(
            floor=int(selection_limit_settings.get(category, {}).get("floor", default.floor)),
            ceiling=int(selection_limit_settings.get(category, {}).get("ceiling", default.ceiling)),
            big_day_max=int(selection_limit_settings.get(category, {}).get("big_day_max", default.big_day_max)),
        )
        for category, default in DEFAULT_CATEGORY_SELECTION_LIMITS.items()
    }
    if set(selection_limit_settings) not in (set(), set(DEFAULT_CATEGORY_SELECTION_LIMITS)):
        raise ValueError("selection_limits must contain exactly the configured categories")
    if importance.logistic_steepness <= 0:
        raise ValueError("importance.logistic_steepness must be positive")
    if not 0 <= importance.llm_weight <= 1:
        raise ValueError("importance.llm_weight must be between 0 and 1")
    if not 0 <= importance.clamp_down <= 100 or not 0 <= importance.clamp_up <= 100:
        raise ValueError("importance clamps must be between 0 and 100")
    if not 0 <= importance.big_day_importance_threshold <= 100:
        raise ValueError("importance.big_day_importance_threshold must be between 0 and 100")
    if importance.deck_target < 1:
        raise ValueError("importance.deck_target must be positive")
    for category, limit in limits.items():
        if limit.floor < 0 or not limit.floor <= limit.ceiling <= limit.big_day_max:
            raise ValueError(f"invalid selection limits for {category}")
    if sum(limit.floor for limit in limits.values()) > importance.deck_target:
        raise ValueError("selection floors cannot exceed deck target")
    if importance.deck_target > sum(limit.ceiling for limit in limits.values()):
        raise ValueError("deck target cannot exceed normal category ceiling capacity")
    max_per_source = int(selection_settings.get("max_per_source_per_category", 2))
    big_day_source_cap = int(selection_settings.get("big_day_source_cap", 3))
    if max_per_source < 1 or big_day_source_cap < max_per_source:
        raise ValueError("selection source caps are invalid")
    return AgentConfig(
        feeds=feeds,
        categories=categories,
        lookback_hours=int(os.getenv("BRIEFING_LOOKBACK_HOURS", settings.get("lookback_hours", 30))),
        max_articles=max_articles,
        category_fetch_reserves=category_fetch_reserves,
        importance=importance,
        category_selection_limits=limits,
        max_per_source_per_category=max_per_source,
        big_day_source_cap=big_day_source_cap,
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
                default=False,
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
        enrichment=EnrichmentConfig(
            enabled=parse_bool(enrichment_settings.get("enabled"), default=True),
            max_clusters_per_run=int(enrichment_settings.get("max_clusters_per_run", 60)),
            global_cluster_slots=int(enrichment_settings.get("global_cluster_slots", 20)),
            reserved_clusters_per_category=int(enrichment_settings.get("reserved_clusters_per_category", 8)),
            max_articles_per_cluster=int(enrichment_settings.get("max_articles_per_cluster", 2)),
            max_pages_per_run=int(enrichment_settings.get("max_pages_per_run", 50)),
            request_timeout_seconds=float(enrichment_settings.get("request_timeout_seconds", 8)),
            max_response_bytes=int(enrichment_settings.get("max_response_bytes", 2_000_000)),
            max_extracted_chars=int(enrichment_settings.get("max_extracted_chars", 6_000)),
            minimum_extracted_chars=int(enrichment_settings.get("minimum_extracted_chars", 300)),
            minimum_story_evidence_score=float(enrichment_settings.get("minimum_story_evidence_score", 1.2)),
            policies=tuple(
                ExtractionPolicyConfig(
                    id=str(item["id"]),
                    allowed_domains=tuple(str(domain).casefold() for domain in item.get("allowed_domains", ())),
                    policy=item.get("policy", "disabled"),
                )
                for item in raw.get("extraction_policies", ())
            ),
        ),
    )
