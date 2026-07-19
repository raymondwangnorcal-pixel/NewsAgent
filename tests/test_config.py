from __future__ import annotations

from pathlib import Path

from news_agent.config import DEFAULT_CONFIG_PATH, load_config
from news_agent.models import QualityGateConfig


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
