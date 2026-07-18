# ADR-0002: Config-driven quality-gate thresholds

## Status

Accepted

## Context

`quality_gate.py` currently hardcodes its thresholds as module constants
(`MIN_SUMMARY_CHARS = 80`, `SUMMARY_DUPLICATE_THRESHOLD = 0.85`, plus the regex pattern lists).
Every other tunable in this codebase — category keywords, feed reputation, formatting limits —
is config-driven through `AgentConfig`/`config/sources.toml`, loaded by `config.py` with
`BRIEFING_*` environment-variable overrides. Tuning the quality gate currently means editing code.

## Decision

- Add `QualityGateConfig` to `models.py`, following the `FormattingConfig` pattern (a frozen
  dataclass with sensible defaults), holding: `min_summary_chars`, `summary_duplicate_threshold`,
  the per-signal penalty weights, `clear_bad_penalty_weight`, `max_content_quality_penalty`, and
  `low_content_quality_skip_threshold`.
- Add `quality_gate: QualityGateConfig = field(default_factory=QualityGateConfig)` to
  `AgentConfig` — a `default_factory`, not a required field, so every existing test that
  constructs `AgentConfig(...)` directly (tests/test_pipeline.py, tests/test_scoring.py) keeps
  working unmodified.
- Parse a new `[quality_gate]` TOML section in `config.py` following the **`settings`-block
  pattern** (`raw.get("settings", {})`, flat table, `.get(key, default)` per field) — not the
  `categories` dict-comprehension pattern, which is for repeated `[categories.x]` subsections and
  doesn't fit a single flat table.
- Add `BRIEFING_QUALITY_GATE_*` environment-variable overrides for each threshold, mirroring the
  existing `BRIEFING_LOOKBACK_HOURS` / `BRIEFING_MAX_ARTICLES` convention, so the config surface
  stays consistent rather than quality-gate being the one exception.
- Section is optional: if `[quality_gate]` is absent from `sources.toml`, `QualityGateConfig()`
  defaults apply — no breaking change for the existing config file.

## Consequences

- Thresholds are tunable without a code change, closing the "hardcoded regex heuristics" gap
  ceo-review flagged.
- Consistent with the rest of the config surface (env override + TOML default), rather than a
  special case.
