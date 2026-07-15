# Plan: content-quality-gate
Created: 2026-07-15 | Branch: main | Tier: 1 (Focused — code-explorer, arch-researcher, test-analyzer)

## Summary

Redesign the in-progress `quality_gate.py` from a hardcoded, hard-reject regex filter into a
config-driven, soft-scoring content-quality layer that feeds a penalty into the existing cluster
ranking instead of silently dropping articles. Narrow hard rejection to near-certain junk only
(empty summary, exact title/summary duplicate). Add a batched LLM fallback for regex-ambiguous
cases, a `--quality-report` CLI flag for source-level rejection tracking, and a golden-set test
suite pulled from real production rejection logs already on disk. Land the unrelated `scoring.py`/
`summarize.py` accuracy fixes (word-boundary keyword matching, opinion-piece detection, war-idiom
fix) as a separate commit, outside this plan.

## Decisions

Recorded across `ceo-review` (SCOPE EXPANSION) and `eng-review`:

1. **Reframe accepted, extended scope**: soft scoring + config-driven thresholds + closed feedback
   loop (must-haves) plus source-level rejection tracking, golden-set tests, and LLM fallback for
   ambiguous cases (all four extras approved). The bundled `scoring.py`/`summarize.py` fixes ship
   as a separate commit, not part of this plan's diff.
2. **Ambiguous-band definition** (eng-review Q1): a single triggered regex heuristic = ambiguous
   (routes to LLM if enabled); 2+ triggered heuristics = confidently `clear_bad` (soft-penalized
   directly, no LLM call needed).
3. **LLM call gating** (eng-review Q2): runs whenever `openai_mode != "off"`, no new independent
   flag — reuses the existing mode switch already on every CLI invocation.
4. **Source-level tracking location** (eng-review Q3): a new CLI subcommand-style flag
   (`--quality-report`), off the hot path of every briefing run, matching the existing
   `--alerts`/`--test-telegram` early-return pattern in `cli.py`.
5. **Field naming**: the existing `StoryCluster.quality_score` is a source-reputation signal
   (`source_balance.cluster_quality_score`), unrelated to article content — the new signal is
   named `content_quality_penalty` on both `Article` and `StoryCluster` to avoid confusion (see
   ADR-0001).
6. **Compounding-penalty risk** (surfaced by arch-researcher): a single-source, low-content-quality
   cluster can now be hit by three independent mechanisms — the existing single-source
   `total_score -= 1.5` penalty, the existing `quality_score < 0.55` skip check, and the new
   `content_quality_penalty` subtraction. Mitigated by capping `content_quality_penalty` at 2.5
   and requiring a dedicated test (Task H2) proving legitimate single-source breaking news isn't
   over-suppressed.
7. **Source-report scope, v1**: `--quality-report` reports raw hard-reject counts by source across
   the retained daily JSON logs, not a true rejection *rate* — computing a rate requires logging
   total per-source fetched-article counts, which don't currently exist anywhere. Explicitly out
   of scope for this plan (see Out of Scope); flagged here so it isn't mistaken for an oversight.

## Architecture

See ADR-0001 (soft scoring), ADR-0002 (config-driven thresholds), ADR-0003 (LLM fallback) in
`docs/adr/`. Key structural points confirmed by research agents:

- All 23 existing `Article(...)` construction sites use keyword arguments — adding
  `content_quality_penalty: float = 0.0` (defaulted) is safe everywhere, no positional-arg
  breakage. No `Article` equality/hash dependency exists anywhere in the codebase, so the new
  field participating in the auto-generated `__eq__`/`__hash__` is a non-issue.
- `fetch.py`'s `parse_feed()` guarantees non-empty `title`/`url` but **not** non-empty `summary` —
  the hard-reject "empty summary" check must handle this real input shape, not assume it can't
  happen.
- Per-article scoring must happen **before** `cluster_articles()` (articles are frozen; clustering
  consumes already-scored articles). Cluster-level MIN-aggregation must happen **after**
  clustering — the natural fit is inside `score_clusters()`'s existing per-cluster loop
  (`scoring.py`), alongside where `cluster.quality_score = cluster_quality_score(cluster)` is
  already computed today.
- `[quality_gate]` TOML section must follow the `settings`-block flat-table parsing pattern in
  `config.py`, not the `categories` dict-of-subsections pattern (they parse structurally
  differently).
- No test in this repo currently mocks the OpenAI client — the LLM-judge function is the first.
  Follow the class-swap pattern already used for `FakeTelegramSender` in `tests/test_notifications.py`.

## Tasks

### Layer 1 — Foundation (config + data model)

- [ ] **Task A**: Add `QualityGateConfig` dataclass to `models.py` (fields: `min_summary_chars`,
  `summary_duplicate_threshold`, `ambiguous_penalty_weight`, `clear_bad_penalty_weight`,
  `max_content_quality_penalty`, matching current hardcoded values as defaults). Add
  `quality_gate: QualityGateConfig = field(default_factory=QualityGateConfig)` to `AgentConfig`.
  Add `content_quality_penalty: float = 0.0` to `Article` and to `StoryCluster`.
  **Acceptance**: existing `AgentConfig(...)` / `Article(...)` / `StoryCluster(...)` constructor
  calls across all test files continue to pass unmodified (no required-field breakage).
- [ ] **Task B**: Parse `[quality_gate]` TOML section in `config.py` (flat-table `.get()` pattern,
  matching `settings`), with `BRIEFING_QUALITY_GATE_MIN_SUMMARY_CHARS` /
  `BRIEFING_QUALITY_GATE_SUMMARY_DUPLICATE_THRESHOLD` / etc. env-var overrides, mirroring the
  existing `BRIEFING_LOOKBACK_HOURS` convention. Section optional — absent section falls back to
  `QualityGateConfig()` defaults.
  **Acceptance**: `load_config()` on the current `config/sources.toml` (no `[quality_gate]`
  section present) returns default thresholds unchanged; a test `sources.toml` fixture with an
  explicit `[quality_gate]` section overrides them.

### Layer 2 — Content-quality scoring (depends on Layer 1)

- [ ] **Task C**: Rewrite `quality_gate.py`'s core scoring function. Narrow hard-reject to: empty/
  whitespace-only summary, and summary-duplicates-title (Jaccard > threshold or exact match) —
  same detection logic as today, narrower trigger set. Bucket everything else by triggered-
  heuristic count (0 = `clear_good`, 1 = `ambiguous`, 2+ = `clear_bad`) and compute a soft
  `content_quality_penalty` per article, capped at `max_content_quality_penalty`. Return
  survivors (with `dataclasses.replace()`-applied penalties) + hard-rejections (unchanged log
  format) + the ambiguous-bucket list for Task D to consume.
  **Acceptance**: existing `test_quality_gate.py` hard-reject cases for empty/duplicate summaries
  still reject; former hard-reject cases for thin-summary/teaser/stock-tip-without-catalyst now
  survive with `content_quality_penalty > 0` instead of being dropped.
- [ ] **Task D**: Add `judge_ambiguous_articles(articles, model=None) -> dict[str, str]` (URL ->
  `"good"`/`"junk"`) using one batched `client.responses.create(...)` call with a strict
  `json_schema` response format, following `summarize.py`'s `_generate_structured_briefings`
  shape. Explicit `try/except` around the call — on any exception, return `{}` (caller falls back
  to the regex-only ambiguous-tier penalty). Wire into Task C's flow: only called when the
  ambiguous bucket is non-empty and `openai_mode != "off"`.
  **Acceptance**: mocked-success test confirms `"good"` verdicts zero out the penalty and
  `"junk"` verdicts raise it to clear-bad level; mocked-failure test confirms graceful degradation
  to the regex verdict with no exception propagating.

### Layer 3 — Scoring integration (depends on Layer 2)

- [ ] **Task E**: In `scoring.py`'s `score_clusters()` loop, compute
  `cluster.content_quality_penalty = min(a.content_quality_penalty for a in cluster.articles)`
  alongside the existing `cluster.quality_score` line. Subtract it from `total_score` as a new
  term, capped so it can't alone push a cluster below a sane floor.
  **Acceptance**: a cluster with one clean corroborating article and one teaser-headline article
  scores the same as a cluster with only the clean article (MIN-aggregation proven).
- [ ] **Task F**: Add a `"low content quality"` branch to `skip_reason()` in `skipped_log.py`,
  distinct from the existing `"low source quality"` branch, gated on `content_quality_penalty`
  exceeding a threshold. This is what surfaces the soft-penalty signal in the existing
  skipped-stories debug output (closing the feedback loop via the mechanism that already exists,
  per ADR-0001).
  **Acceptance**: `format_skipped_table()` output for a heavily-penalized-but-not-hard-rejected
  cluster shows `"low content quality"`, distinguishable from `"low source quality"`.

### Layer 4 — Pipeline wiring + regression safety (depends on Layer 3)

- [ ] **Task G**: Update `pipeline.py`'s `collect_pipeline_context()` to call the new Task C/D
  scoring flow before `cluster_articles()`, replacing the current `filter_low_quality_articles()`
  call. Keep writing hard-rejections to `quality_gate_rejections_{date}.json` (unchanged filename/
  format).
  **Acceptance**: `test_collect_pipeline_context_filters_articles_before_clustering` (existing)
  still passes for the narrowed hard-reject set; new test confirms soft-penalized articles reach
  `cluster_articles()` rather than being filtered out beforehand.
- [ ] **Task H1** (compounding-penalty regression test): a single-source cluster with maximum
  `content_quality_penalty` does not silently vanish from `all_clusters` — it's still present,
  just heavily downranked and/or `skip_reason`-tagged, distinguishing "soft-suppressed" from the
  old "hard-dropped, gone forever" behavior.
- [ ] **Task H2** (golden-set tests): pull confirmed examples from the on-disk
  `data/quality_gate_rejections_2026-07-12.json`, `data/quality_gate_rejections_2026-07-14.json`,
  `data/skipped_stories_2026-07-12.json`, `data/skipped_stories_2026-07-14.json` into
  parametrized fixtures in `tests/test_quality_gate.py`, asserting the redesigned scorer produces
  the same reject/keep verdict category (hard-reject vs soft-penalized-survivor) as the original
  logged outcome, where that distinction is still meaningful under the new narrower hard-reject
  set.

### Layer 5 — Observability (depends on Layer 4, additive/independent)

- [ ] **Task I**: New `quality_report.py` module: `aggregate_source_rejections(log_dir, days) ->
  dict[str, int]`, reading `quality_gate_rejections_*.json` files across the last N days, grouped
  by `source`, counting raw hard-reject occurrences (v1 — not a true rate, per Decision 7). Skip
  unreadable/corrupt files with a warning, don't crash.
  Add `--quality-report [--report-days N]` flag to `cli.py`, following the `--alerts`/
  `--test-telegram` early-return pattern (news pipeline must not run when this flag is passed).
  **Acceptance**: test mirrors `test_cli_test_telegram_skips_news_pipeline`'s
  monkeypatch-and-assert-pipeline-not-invoked pattern; a corrupt log file in the report window
  doesn't crash the command.

## Test Plan

- Unit: `test_quality_gate.py` — hard-reject boundary (narrowed set), soft-penalty bucketing
  (clear_good/ambiguous/clear_bad), LLM judge success/failure/degradation, golden-set fixtures
  (Task H2).
- Unit: `test_models.py` — new `QualityGateConfig` defaults, `Article`/`StoryCluster` new fields
  don't break existing construction.
- Unit: config — `[quality_gate]` section present/absent, env-var overrides.
- Integration: `test_scoring.py` — MIN-aggregation across cluster articles, `total_score`
  subtraction, compounding-penalty regression (Task H1).
- Integration: `test_skipped_log.py` — new `"low content quality"` skip reason distinguishable
  from `"low source quality"`.
- Integration: `test_pipeline.py` — `collect_pipeline_context` end-to-end with the new scoring
  flow; hard-reject log format/filename unchanged.
- CLI: `test_cli.py` — `--quality-report` early-return pattern, corrupt-log resilience.

## Integration Verification

After implementation, run a real (non-mocked) dry-run against the current `config/sources.toml`
feed list and confirm:
- `python -m news_agent --dry-run --show-skipped` still produces the quality-gate-rejections and
  skipped-stories debug tables, now showing both `"low source quality"` and
  `"low content quality"` reasons where applicable.
- `python -m news_agent --quality-report` runs without touching the news pipeline and produces a
  per-source rejection count table from the existing `data/quality_gate_rejections_*.json` files
  already on disk (2026-07-12, 2026-07-14).

## Out of Scope

- True rejection-*rate* reporting (requires new total-per-source-fetched-article logging at fetch
  time — not built here; v1 reports raw counts only).
- The unrelated `scoring.py` word-boundary keyword-matching fix and `summarize.py` opinion-piece/
  war-idiom fixes — land as a separate, independent commit outside this plan.
- Any UI/config surface beyond `sources.toml` + env vars (this is a single-user CLI tool; no admin
  UI for tuning thresholds).
