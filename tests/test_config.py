from __future__ import annotations

from pathlib import Path

import pytest

from news_agent.config import DEFAULT_CONFIG_PATH, load_config
from news_agent.models import (
    BriefingParagraph,
    CompressionConfig,
    DraftingConfig,
    DuplicateGateConfig,
    EnrichmentConfig,
    ExtractionPolicyConfig,
    OpenAICostConfig,
    QualityGateConfig,
)


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


def test_default_compression_config_matches_locked_values() -> None:
    config = CompressionConfig()

    assert config.enabled is False
    assert config.model == "gpt-5.6-terra"
    assert config.min_words_to_compress == 40
    assert config.min_words_floor == 20
    assert config.compress_fallback_drafts is False
    assert config.guard_entities is True
    assert config.max_output_tokens_per_batch == 1200
    assert not hasattr(config, "target_words")


def test_default_drafting_config_matches_locked_model_and_output_cap() -> None:
    config = DraftingConfig()

    assert config.model == "gpt-5.6-terra"
    assert config.max_output_tokens_per_batch == 6000


def test_default_duplicate_gate_config_matches_locked_values() -> None:
    config = load_config(DEFAULT_CONFIG_PATH).duplicate_gate

    assert config == DuplicateGateConfig()


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        (
            "candidate_title_jaccard_threshold = 0.20",
            "candidate_title_jaccard_threshold = 1.1",
            "duplicate_gate.candidate_title_jaccard_threshold",
        ),
        (
            'reasoning_effort = "medium"',
            'reasoning_effort = "minimal"',
            "duplicate_gate.reasoning_effort",
        ),
        (
            "max_component_size = 4",
            "max_component_size = 1",
            "duplicate_gate.max_component_size",
        ),
    ],
)
def test_config_rejects_invalid_duplicate_gate_settings(
    tmp_path: Path,
    old: str,
    new: str,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        load_config(_write_config_variant(tmp_path, old, new))


def test_default_openai_cost_config_has_verified_prices_and_one_dollar_cap() -> None:
    config = OpenAICostConfig()

    assert config.enabled is True
    assert config.model == "gpt-5.6-terra"
    assert config.max_cost_usd_per_run == 1.00
    assert config.input_cost_usd_per_million_tokens == 2.50
    assert config.output_cost_usd_per_million_tokens == 15.00


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (
            '[drafting]\nmodel = "gpt-5.6-terra"',
            '[drafting]\nmodel = "gpt-5.6-sol"',
        ),
        ("max_output_tokens_per_batch = 6000", "max_output_tokens_per_batch = 0"),
    ],
)
def test_config_rejects_invalid_drafting_settings(
    tmp_path: Path,
    old: str,
    new: str,
) -> None:
    with pytest.raises(ValueError):
        load_config(_write_config_variant(tmp_path, old, new))


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ('[openai_costs]\nenabled = true', '[openai_costs]\nenabled = false'),
        (
            'enabled = true\nmodel = "gpt-5.6-terra"\nmax_cost_usd_per_run = 1.00',
            'enabled = true\nmodel = "gpt-5.6-sol"\nmax_cost_usd_per_run = 1.00',
        ),
        ("max_cost_usd_per_run = 1.00", "max_cost_usd_per_run = 0.0"),
        (
            "input_cost_usd_per_million_tokens = 2.5\n"
            "output_cost_usd_per_million_tokens = 15.0",
            "input_cost_usd_per_million_tokens = 0.0\n"
            "output_cost_usd_per_million_tokens = 15.0",
        ),
    ],
)
def test_config_rejects_invalid_openai_cost_settings(
    tmp_path: Path,
    old: str,
    new: str,
) -> None:
    with pytest.raises(ValueError):
        load_config(_write_config_variant(tmp_path, old, new))


def test_checked_in_config_enables_all_openai_costs_with_one_shared_cap() -> None:
    config = load_config(DEFAULT_CONFIG_PATH)

    assert config.openai_costs.enabled is True
    assert config.openai_costs.max_cost_usd_per_run == 1.0
    assert config.openai_costs.input_cost_usd_per_million_tokens == 2.5
    assert config.openai_costs.output_cost_usd_per_million_tokens == 15.0
    assert config.compression.enabled is True


def test_briefing_paragraph_compression_fields_default() -> None:
    paragraph = BriefingParagraph(
        story_id="story",
        category="culture",
        paragraph="Original.",
        sources=("Example",),
    )

    assert paragraph.full_paragraph == ""
    assert paragraph.compression_status == ""
    assert paragraph.compression_ratio == 0.0


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (
            '[compression]\nenabled = true\nmodel = "gpt-5.6-terra"',
            '[compression]\nenabled = true\nmodel = "gpt-5.6-sol"',
        ),
        ("min_words_to_compress = 40", "min_words_to_compress = 10"),
        ("min_words_floor = 20", "min_words_floor = -1"),
        ("max_output_tokens_per_batch = 1200", "max_output_tokens_per_batch = 0"),
        ("compress_fallback_drafts = false", "compress_fallback_drafts = true"),
        ("guard_entities = true", "guard_entities = false"),
    ],
)
def test_config_rejects_invalid_compression_ranges(tmp_path: Path, old: str, new: str) -> None:
    with pytest.raises(ValueError):
        load_config(_write_config_variant(tmp_path, old, new))


def test_live_openai_requires_nonzero_token_prices(tmp_path: Path) -> None:
    path = _write_config_variant(
        tmp_path,
        "input_cost_usd_per_million_tokens = 2.5\n"
        "output_cost_usd_per_million_tokens = 15.0",
        "input_cost_usd_per_million_tokens = 0.0\n"
        "output_cost_usd_per_million_tokens = 0.0",
    )

    with pytest.raises(ValueError, match="positive"):
        load_config(path)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1", True),
        ("true", True),
        ("yes", True),
        ("0", False),
        ("false", False),
        ("no", False),
        ("unexpected", True),
    ],
)
def test_env_var_toggles_compression(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
    expected: bool,
) -> None:
    monkeypatch.setenv("BRIEFING_COMPRESSION", value)

    assert load_config(DEFAULT_CONFIG_PATH).compression.enabled is expected


def test_explicit_compression_override_has_precedence_over_env_and_toml(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BRIEFING_COMPRESSION", "true")

    loaded = load_config(DEFAULT_CONFIG_PATH, compression_enabled_override=False)

    assert loaded.compression.enabled is False
