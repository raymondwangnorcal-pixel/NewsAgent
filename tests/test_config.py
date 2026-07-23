from __future__ import annotations

from pathlib import Path

import pytest

from news_agent.config import DEFAULT_CONFIG_PATH, load_config
from news_agent.models import EnrichmentConfig, ExtractionPolicyConfig, QualityGateConfig


def test_load_config_defaults_quality_gate_when_section_absent() -> None:
    config = load_config(DEFAULT_CONFIG_PATH)

    assert config.quality_gate == QualityGateConfig()


def test_load_config_parses_explicit_quality_gate_section(tmp_path: Path) -> None:
    toml_path = tmp_path / "sources.toml"
    toml_path.write_text(
        """
        [settings]
        lookback_hours = 30
        max_articles = 240

        [quality_gate]
        min_summary_chars = 120
        summary_duplicate_threshold = 0.9
        ambiguous_penalty_weight = 0.6
        clear_bad_penalty_weight = 2.0
        max_content_quality_penalty = 3.0
        low_content_quality_skip_threshold = 1.5
        """,
        encoding="utf-8",
    )

    config = load_config(toml_path)

    assert config.quality_gate == QualityGateConfig(
        min_summary_chars=120,
        summary_duplicate_threshold=0.9,
        ambiguous_penalty_weight=0.6,
        clear_bad_penalty_weight=2.0,
        max_content_quality_penalty=3.0,
        low_content_quality_skip_threshold=1.5,
    )


def test_load_config_parses_enrichment_and_extraction_policies(tmp_path: Path) -> None:
    toml_path = tmp_path / "sources.toml"
    toml_path.write_text(
        """
        [enrichment]
        enabled = true
        max_pages_per_run = 12
        minimum_story_evidence_score = 2.25

        [[extraction_policies]]
        id = "example"
        allowed_domains = ["Example.COM"]
        policy = "article_text"
        """,
        encoding="utf-8",
    )

    loaded = load_config(toml_path).enrichment

    assert loaded.max_pages_per_run == 12
    assert loaded.minimum_story_evidence_score == 2.25
    assert loaded.policies == (ExtractionPolicyConfig("example", ("example.com",), "article_text"),)


def test_default_config_culture_feeds_have_lanes_and_policies() -> None:
    config = load_config(DEFAULT_CONFIG_PATH)
    culture_feeds = [feed for feed in config.feeds if "culture" in feed.categories and feed.source_type != "aggregator"]
    policy_domains = {domain for policy in config.enrichment.policies for domain in policy.allowed_domains}

    assert all(feed.culture_lane for feed in culture_feeds)
    assert {"film_tv", "music", "sports", "gaming"}.issubset({feed.culture_lane for feed in culture_feeds})
    for feed in culture_feeds:
        domain = feed.url.split("/", 3)[2].removeprefix("www.")
        assert domain in policy_domains


def test_default_config_has_overlapping_film_tv_sources() -> None:
    config = load_config(DEFAULT_CONFIG_PATH)

    assert sum(feed.culture_lane == "film_tv" for feed in config.feeds) >= 3


def test_default_config_uses_structured_selection_limits() -> None:
    config = load_config(DEFAULT_CONFIG_PATH)

    assert not hasattr(config, "max_culture_stories")
    assert config.category_selection_limits["culture"].floor == 2
    assert config.category_selection_limits["culture"].ceiling == 5
    assert config.category_selection_limits["culture"].big_day_max == 6
    assert config.importance.deck_target == 25


def test_default_config_has_requested_fetch_reserves() -> None:
    config = load_config(DEFAULT_CONFIG_PATH)

    assert config.category_fetch_reserves == {
        "business_tech": 40,
        "domestic": 40,
        "global": 40,
        "finance": 40,
        "culture": 30,
    }


def _write_config_variant(tmp_path: Path, old: str, new: str) -> Path:
    path = tmp_path / "sources.toml"
    text = DEFAULT_CONFIG_PATH.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("logistic_steepness = 0.30", "logistic_steepness = 0"),
        ("llm_weight = 0.65", "llm_weight = 1.1"),
        ("clamp_down = 25.0", "clamp_down = -1"),
        ("big_day_importance_threshold = 70.0", "big_day_importance_threshold = 101"),
        ("big_day_source_cap = 3", "big_day_source_cap = 1"),
    ],
)
def test_config_rejects_invalid_importance_ranges(tmp_path: Path, old: str, new: str) -> None:
    with pytest.raises(ValueError):
        load_config(_write_config_variant(tmp_path, old, new))


def test_config_rejects_invalid_selection_limit_order(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        load_config(_write_config_variant(tmp_path, "floor = 3\nceiling = 5", "floor = 7\nceiling = 5"))


def test_config_rejects_floor_sum_above_deck_target(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="floors"):
        load_config(_write_config_variant(tmp_path, "deck_target = 25", "deck_target = 10"))


def test_config_rejects_deck_target_above_normal_capacity(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="capacity"):
        load_config(_write_config_variant(tmp_path, "deck_target = 25", "deck_target = 26"))


def test_sms_story_limit_allows_big_day_sixth_story() -> None:
    assert load_config(DEFAULT_CONFIG_PATH).formatting.max_stories_per_category_sms == 6
