# Plan: Diagnose and Reduce the "Uncategorized" Skip Bucket

**Status:** Draft — diagnostics-first, fix deferred to data
**Date:** 2026-07-25
**Related:** `docs/ToAddress/Uncategorized-issue.dm` (diagnosis this plan implements)
**Reviewed by:** one bounded `gpt-5.6-terra` API call (2,689 input / 971 output
tokens, $0.0213) against this draft, prompted to find concrete issues rather
than restate the plan. All 8 points raised are resolved below (see *Review
resolutions*).

## Review resolutions

| Review comment | Resolution in this plan |
| --- | --- |
| Aggregate counters can't distinguish capacity loss from a low-ranked or false-positive watchlist match | Step 1 now persists a per-cluster audit record (id, evidence score, rank, source count, matched term, exclusion stage), not just totals |
| Fresh live dry runs aren't a controlled comparison against the cited 7-day sample (content, ordering, and `--ignore-history` all vary run to run) | Step 2 now replays the same persisted cluster inputs from the sampled days instead of relying on fresh live runs as the primary evidence |
| A large rejected-watchlist count doesn't prove those stories are salvageable, and Fix 3.1 can't fabricate corroboration it doesn't have | Step 2 now requires score-component distributions, not just counts, before Fix 3.1 is considered; Fix 3.1's design section states explicitly what it does and does not guarantee |
| Pool absence was being inferred, not verified, as capacity exclusion | `select_classification_candidates()` now returns an explicit exclusion reason per cluster (Step 1) instead of inferring capacity loss from list absence |
| Fix 3.2's interaction with the existing category reserve was unspecified (additive vs. carved, tie-break, double-counting a dual-eligible cluster) | Fix 3.2 now specifies an explicit carve-out, tie-break, and single-reservation rule |
| A cost-impact test can't prove real-dollar compliance without coupling to the actual budget estimator | The test is now specified against `OpenAIBudget`/`conservative_request_cost_usd` directly, not just a slot-count assertion |
| Broad watchlist terms (`AI`, `IPOs`, `inflation`) risk consuming reserved capacity on incidental mentions | Step 1 records the matched term per cluster; Step 2's decision gate now includes a term-precision review before any term is allowed to consume reserved capacity |
| The live-validation criterion blended distinct pipeline stages into one pass/fail check | Step 4 now validates gate-pass, pool-admission, classification-result, and final-selection as four separate, independently reported outcomes |

## Problem recap

Across a 7-day sample of `data/skipped_stories_*.json`, the `uncategorized`
bucket (clusters whose `category` was never assigned) accounted for 561
skipped clusters — more than every real category combined (culture 35,
finance 27, global 15, domestic 15, business_tech 9). Within it:

| Reason | Count |
| --- | ---: |
| insufficient story context | 379 |
| no reliable source confirmation | 172 |
| category already full | 6 |
| low content quality | 4 |

109 of the 561 (≈19%) carry a nonempty `watchlist_match` (`AI`, `NVDA`,
`AAPL`, `IPOs`, `inflation`, ...), and every one of them shows `importance: 0`
because importance is only computed post-classification — these stories were
never scored by the system that is supposed to protect stories the user
explicitly asked to track.

Two independent, ordering-driven pipeline gaps produce "uncategorized":

- **Gap A — evidence gate precedes classification.** `apply_evidence_gate()`
  (`pipeline.py:621`) runs on the full cluster list and sets `skip_reason =
  "insufficient story context"` for `evidence_score < minimum_story_evidence_score`
  (`1.2`) before any cluster is considered for classification.
- **Gap B — the classification pool is capacity-capped, not quality-gated.**
  `select_classification_candidates()` (`pipeline.py:622`) only ever admits up
  to `MAX_CLASSIFICATION_POOL_SIZE = 80` clusters (`GLOBAL_CLASSIFICATION_POOL_SIZE
  = 30` + `CATEGORY_CLASSIFICATION_RESERVE = 10` × 5 categories). A cluster that
  clears the evidence gate but doesn't rank into that 80 never gets a category,
  never gets an importance score, and falls through to `skip_reason()`'s
  generic `source_count <= 1 and impact_score < 3.0 → "no reliable source
  confirmation"` check regardless of whether it was ever actually evaluated
  for corroboration.

## Goal

Determine, with instrumented evidence rather than log-aggregate inference,
how much of the 561-row bucket is genuinely thin content (correctly excluded)
versus genuinely viable, watchlist-relevant, or evidence-qualified stories
excluded purely by pipeline ordering or pool capacity — then apply the
narrowest fix the data supports.

## Non-goals

- Do not lower `minimum_story_evidence_score` blindly before diagnostics
  confirm over-rejection; evidence quality is a hard gate for a reason.
- Do not guarantee every watchlist-matched cluster publishes — a reserved
  classification slot is an opportunity to be scored, not a promise to select.
- Do not touch `docs/plans/PreSourceUP.md` / `source-restructure-synthesized.md`
  / `source-system-restructure.md` — all three operate downstream of
  `apply_category_assignments()`, which none of these 561 clusters reach; they
  are not an alternative fix for this problem.
- Do not change the shared `$1.00`-per-run OpenAI cost ceiling; any pool-size
  increase must be evaluated against it, not exempted from it.

## Design

### Step 1 — Diagnostics before any behavioral change

Aggregate counters alone cannot distinguish "excluded by real capacity limit"
from "excluded because the watchlist match was a low-ranked or incidental
substring hit" — that distinction requires per-cluster detail, not totals.
This step adds both.

**1a. Explicit exclusion reason, not inferred absence.** Change
`select_classification_candidates()` to return, alongside the selected
`candidates` list, a `dict[str, str]` mapping every non-selected cluster's
`story_identity()` to one of:

- `"below_global_rank"` — did not rank into the global-30;
- `"category_reserve_exhausted"` — its category's 10-slot reserve queue was
  already full when it was reached;
- `"deduplicated"` — an identical `story_identity()` was already selected.

This replaces inferring capacity exclusion from list-absence with a verified,
per-cluster reason, and is a precondition for Step 2's decision gate, not
just a diagnostic nicety.

**1b. Per-cluster audit record.** Add a lightweight, non-`PipelineDiagnostics`
audit write (matching the existing `category_assignments_*.json` /
`skipped_stories_*.json` daily-file convention, not a new schema system) for
every cluster that is either evidence-gate-rejected or classification-pool-
excluded and carries a nonempty `watchlist_matches`:

```json
{
  "story_identity": "...",
  "headline": "...",
  "matched_watchlist_terms": ["AI"],
  "evidence_score": 0.9,
  "total_score": 6.2,
  "global_rank": 47,
  "source_count": 1,
  "exclusion_stage": "evidence_gate" ,
  "exclusion_reason": "insufficient_story_context"
}
```

`exclusion_stage` is one of `evidence_gate` or `classification_pool`;
`exclusion_reason` is `"insufficient_story_context"` for the former and one
of the three 1a reason codes for the latter. This is what lets Step 2 tell
"this watchlist story was thin" apart from "this watchlist story lost a rank
race" apart from "`AI` matched incidentally inside an unrelated headline."

**1c. Aggregate counters**, derived from the same boundary, still land in
`PipelineDiagnostics` for `--show-diagnostics` (all defaulted, following the
existing frozen-dataclass convention):

```python
pre_classification_cluster_count: int = 0
evidence_gate_rejected_count: int = 0
evidence_gate_rejected_watchlist_count: int = 0
classification_pool_excluded_count: int = 0
classification_pool_excluded_watchlist_count: int = 0
classification_pool_excluded_by_reason: dict[str, int] = field(default_factory=dict)
```

Surface these under the existing `Classified results` diagnostics heading,
next to the category-health maps already added by the
culture-briefing-consistency work (`fetched_articles_by_feed_hint`,
`classification_pool_by_feed_hint`, `insufficient_context_by_feed_hint`).

**This step ships regardless of what Step 2 decides.** It has no effect on
selection, cost, or output — it only makes the two gaps separately visible,
with enough per-cluster detail to actually test Step 2's hypotheses rather
than merely count them.

### Step 2 — Decision gate (data, not guesswork)

**Replay, don't just re-run live.** A fresh live dry run is not a controlled
comparison against the 7-day sample this plan is diagnosing: fetched
content, clustering, ordering, and (in `full` mode) model output all vary
run to run, and `--ignore-history` itself changes what survives history
suppression. Before drawing any conclusion, add a `--replay-clusters <path>`
dry-run mode that loads a frozen snapshot of already-fetched/enriched/scored
clusters (captured once from a live run and persisted to a fixture file)
and runs only the evidence-gate → classification-pool → classify stages
against that fixed input. This makes Step 1's counters and audit records
comparable across repeated invocations, which a fresh fetch every time
cannot guarantee.

Use two data sources together:

1. **Primary — replayed fixture runs.** Capture 3–5 frozen cluster snapshots
   (one per sampled day already on disk under `data/skipped_stories_*.json`,
   reconstructed from the same articles where possible) and replay them
   under both `--openai-mode off` and `--openai-mode full` to get
   repeatable, comparable counts and audit records.
2. **Secondary — fresh live runs**, still collected (`--ignore-history
   --show-diagnostics`, no `--send`) as a confirmatory check that the
   replayed pattern still holds under live conditions, but never as the sole
   basis for a decision.

**Before concluding a term is "genuinely thin," inspect the distribution, not
just the count.** A large `evidence_gate_rejected_watchlist_count` does not
by itself show those stories are safely salvageable — it shows how many were
rejected, not whether their evidence scores clustered just below `1.2`
(plausibly recoverable) or far below it (genuinely thin). Pull the
`evidence_score` and `source_count` distribution from the 1b audit records
for every watchlist-tagged rejection before selecting Fix 3.1. Separately,
because Fix 3.1 only decides whether a cluster *enters* classification —
it cannot manufacture corroboration a thin story doesn't have — any cluster
it admits must still clear every downstream gate (classification,
`no reliable source confirmation`, importance) unchanged; Fix 3.1 buys a
chance to be evaluated, not a promise to publish.

**Term-precision review, before any term is trusted.** Broad watchlist terms
(`AI`, `IPOs`, `inflation`) can match incidentally inside unrelated headlines.
Before Step 3 reserves capacity or relaxes a threshold for any term, sample
the 1b audit records for that term and manually confirm the match is
topically relevant, not an incidental substring hit. A term with a high
false-positive rate in the sample is excluded from Fix 3.1/3.2 eligibility
until the watchlist-matching logic itself is tightened (separate, unscoped
follow-up) — reserving capacity for a noisy term would just consume the
reserve on irrelevant stories instead of fixing anything.

Decide the fix based on the replayed data:

| Observed pattern (replayed, term-precision-filtered) | Interpretation | Candidate fix |
| --- | --- | --- |
| `evidence_gate_rejected_watchlist_count` is the larger term, stays nonzero across replays, and evidence scores cluster just under the threshold | Watchlist stories are marginally, not categorically, thin at the evidence layer | Fix 3.1 only (do not touch the pool cap) |
| `classification_pool_excluded_watchlist_count` is the larger term and `classification_pool_excluded_by_reason` shows `below_global_rank` / `category_reserve_exhausted` dominating | Evidence-qualified watchlist stories are losing a capacity race they should not have to run | Fix 3.2 (reserve, not raise the global cap) |
| Both are small relative to total watchlist-matched fetch volume, or dominated by low-precision term matches | The 561-row bucket is mostly genuinely thin, non-watchlist content, or a matching-precision problem, not a selection-capacity problem | No selection-logic change; close this plan with the diagnostics as the deliverable, and open a separate matching-precision follow-up if warranted |

Do not implement Step 3 before this data exists. This mirrors the "diagnostics
precede tuning" ordering already established in
`docs/plans/completed/culture-briefing-consistency.md`.

### Step 3 — Candidate fixes (implement only the one(s) Step 2's data selects)

**Fix 3.1 — Watchlist-aware evidence-gate exception, not a threshold change.**
Do not lower `minimum_story_evidence_score` globally (it protects every
category, not just watchlist stories). Instead, add a narrower,
separately-configured floor `watchlist_minimum_story_evidence_score` (default
equal to `minimum_story_evidence_score`, i.e. a no-op until deliberately
tuned) that `apply_evidence_gate()` checks only for clusters with a nonempty
`watchlist_matches` restricted to terms that passed Step 2's precision
review. This keeps the general evidence bar untouched and makes any
relaxation explicit, scoped, and auditable — never a blanket loosening.
**What this does not do:** admitting a cluster past this floor does not
change its `source_count`, `impact_score`, or classification outcome — a
thin single-source story that clears the lowered evidence floor can still be
correctly caught by `no reliable source confirmation` or fail classification
on its own merits downstream. Fix 3.1 only widens who gets evaluated, never
who gets published.

**Fix 3.2 — Reserve classification-pool slots for evidence-qualified watchlist
clusters.** Mirror the existing per-category reserve mechanism
(`CATEGORY_CLASSIFICATION_RESERVE`), with the ordering and overlap rules the
initial draft left unspecified now made explicit:

- **Carved out of, not additive to, the existing 80-item ceiling.** Introduce
  `WATCHLIST_CLASSIFICATION_RESERVE` (proposed default: 10) and reduce
  `GLOBAL_CLASSIFICATION_POOL_SIZE` from 30 to 20, so
  `MAX_CLASSIFICATION_POOL_SIZE` stays exactly 80 (`20 + 10 + 10×5`). This
  keeps the pool-size/cost envelope unchanged instead of silently growing it;
  if Step 2's replay data shows 80 is itself too tight, that is a separate,
  explicitly justified decision, not a side effect of adding a watchlist
  carve-out.
- **Runs last, after the global fill and every category reserve.** The
  watchlist reserve only ever draws from clusters neither the global-20 pass
  nor any category's 10-slot reserve already selected — it fills unused
  capacity, it does not compete with or preempt category coverage.
- **Single-reservation rule.** A cluster that is both category-hint-eligible
  and watchlist-matched is claimed by whichever pass reaches it first
  (global, then category round-robin, then watchlist); once claimed by
  `story_identity()`, it is not reserved again. This prevents a dual-eligible
  cluster from being double-counted against both reserves' diagnostics.
- **Tie-break.** Candidates within the watchlist reserve are ranked by
  `total_score` descending, then `story_identity()` ascending — the same
  deterministic tie-break convention used everywhere else in `pipeline.py`.
- **Term eligibility.** Only watchlist terms that passed Step 2's
  precision review may draw from this reserve; a term still under review is
  excluded from `cluster_feed_hints`-style eligibility for this reserve until
  reviewed, so a noisy broad term cannot silently consume the 10 slots on
  incidental matches.

This must be validated against the existing classification-cost budget
(`$1.00`/run, shared across judge/classify/draft/compress) before being
locked in — see the cost-impact test below, which couples directly to the
budget estimator rather than only checking the reserve's slot count.

Both fixes are independent and can ship separately; Step 2's data determines
whether one, both, or neither is warranted.

## Implementation steps

### Step 0 — baseline
```bash
python3 -m pytest -q
git diff --check
```

### Step 1 — diagnostics
**Files:** `src/news_agent/models.py`, `src/news_agent/pipeline.py`,
`src/news_agent/cli.py`, `tests/test_pipeline.py`, `tests/test_cli.py`.

**Tests:**
- `test_pre_classification_cluster_count_matches_input_length`
- `test_evidence_gate_rejected_counts_split_by_watchlist_match`
- `test_classification_pool_excluded_counts_split_by_watchlist_match`
- `test_select_classification_candidates_returns_explicit_exclusion_reason_per_cluster`
- `test_exclusion_reason_distinguishes_rank_reserve_and_dedup`
- `test_audit_record_captures_matched_term_evidence_score_rank_and_stage`
- `test_diagnostics_new_fields_default_to_zero_with_partial_kwargs`
- `test_cli_prints_uncategorized_gap_diagnostics`

**Verify:**
```bash
python3 -m pytest tests/test_pipeline.py tests/test_cli.py -q
```
**Commit checkpoint:** `feat(diagnostics): split evidence-gate and pool-capacity exclusion counts`

### Step 2 — live diagnostic runs (no code change)
Three to five isolated dry runs (`--ignore-history --show-diagnostics`, no
`--send`), both `off` and `full` OpenAI modes, recording the five counters
per run. This is a data-collection checkpoint, not a commit.

### Step 3 — implement the data-selected fix(es)
**Files (Fix 3.1):** `src/news_agent/models.py`, `src/news_agent/config.py`,
`config/sources.toml`, `src/news_agent/pipeline.py`, `tests/test_config.py`,
`tests/test_pipeline.py`.

**Files (Fix 3.2):** `src/news_agent/pipeline.py`, `tests/test_pipeline.py`.

**Tests (as applicable):**
- `test_watchlist_evidence_floor_defaults_equal_to_general_floor`
- `test_watchlist_evidence_floor_does_not_relax_non_watchlist_clusters`
- `test_watchlist_admitted_cluster_still_fails_downstream_source_confirmation_check` — proves Fix 3.1 widens evaluation, not publication
- `test_watchlist_classification_reserve_admits_qualified_watchlist_cluster`
- `test_watchlist_reserve_never_exceeds_configured_size`
- `test_watchlist_reserve_runs_only_after_global_and_category_fills`
- `test_dual_eligible_cluster_claimed_once_not_double_reserved`
- `test_watchlist_reserve_excludes_terms_not_yet_precision_reviewed`
- `test_pool_size_unchanged_at_eighty_after_carve_out` — `20 + 10 + 10×5 == 80`
- `test_watchlist_reserve_admission_checked_against_openai_budget_estimator` — couples to `OpenAIBudget.can_start()` / `conservative_request_cost_usd()` directly, not just a slot-count assertion

**Verify:**
```bash
python3 -m pytest -q
git diff --check
```

### Step 4 — live validation

A single blended "reaches classification with nonzero importance" check
conflates independent pipeline stages — a correctly classified story can
legitimately score zero importance, and an evidence-rejected story was never
eligible to reach that point at all. Validate each stage separately, using
the same previously-lost watchlist examples (`AI`/Chinese-AI-model story,
`AAPL`/Apple Music pricing story, `IPOs`/Holtec filing) in an isolated dry
run (`--openai-mode full --ignore-history --show-diagnostics`, no `--send`):

1. **Evidence-gate pass/fail** — does the cluster now clear
   `apply_evidence_gate()` (Fix 3.1 clusters only)?
2. **Classification-pool admission** — does the cluster now appear in
   `candidates`, with `classification_pool_excluded_by_reason` showing no
   entry for it?
3. **Classification result** — does `classify_clusters()` assign it a real
   category and an `llm_importance` (not `None`)?
4. **Final selection outcome** — is it selected, or excluded for a specific,
   inspectable reason (floor/ceiling, source cap, lane cap)?

Report all four outcomes per example, not a single pass/fail. A cluster that
fails at stage 1 should never be checked at stage 3 as if stage 1 passed.

## Rollback

- Step 1 diagnostics are additive and read-only; revert is a plain code
  revert with no data migration.
- Fix 3.1 rolls back by setting `watchlist_minimum_story_evidence_score` back
  to the general floor (config-only, no code change needed).
- Fix 3.2 rolls back by setting `WATCHLIST_CLASSIFICATION_RESERVE = 0`.

## Open questions for review

1. Is a fixed reserve size (10) the right shape for Fix 3.2, or should it
   scale with the number of configured watchlist entries?
2. Should the watchlist evidence floor (Fix 3.1) be a single scalar, or
   per-entry (some watchlist terms may warrant a lower bar than others,
   especially once term-precision review shows some terms are far noisier
   than others)?
3. ~~Does reserving classification-pool slots for watchlist clusters risk
   crowding out a legitimate category reserve?~~ Resolved in Fix 3.2: the
   watchlist reserve runs only after the global and category-reserve passes
   and is carved out of the unchanged 80-item ceiling, so it can only consume
   capacity neither pass already claimed.
4. The term-precision review (Step 2) is a manual sampling step with no
   defined sample size or reviewer other than the user — should it be
   formalized (e.g. a fixed number of matches per term, a written rubric) or
   left as an ad hoc check before each term is enabled?
