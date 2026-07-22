from __future__ import annotations

from pathlib import Path

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


def test_default_config_hard_limits_culture_to_three_stories() -> None:
    config = load_config(DEFAULT_CONFIG_PATH)

    assert config.max_culture_stories == 3
