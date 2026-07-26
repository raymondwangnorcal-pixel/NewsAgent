# Importance Scoring & Remainder Fill — Implementation Plan

**Status:** Revised and implementation-ready  
**Date:** 2026-07-21  
**Repository:** `/Users/raymondwang/PersonalProjects/NewsAgent`  
**Baseline:** `python3 -m pytest -q` → `209 passed`

## Goal

Add an auditable 0–100 story-importance score and use it to allocate a hard 25-story briefing deck: protected category floors first, a cross-category importance remainder second, and threshold-gated sixth stories only when another category cannot use its normal capacity.

## Locked decisions

1. **Deck target is a hard 25-story maximum.** No selection phase may publish story 26. The sum of per-category big-day maxima is 30, but that is only a set of individual caps; it is not a reachable deck size while `deck_target = 25`.
2. **Normal category ceiling is five.** A category can receive a sixth story only during big-day redistribution, only while the deck remains below 25, and only when that story has final importance at least 70.
3. **No global top-K condition.** The threshold and the hard deck target are sufficient; adding top-K would duplicate the same ranking signal.
4. **One bounded backfill pass remains.** It exists specifically to find additional candidates for categories below their floor. Cross-category remainder selection does not replace classification coverage.
5. **Missing LLM importance is `None`, not `0`.** Zero remains a valid editorial grade.
6. **Culture constraints remain hard.** Never exceed two stories from one primary publisher or three stories from one Culture lane, even if that leaves Culture below its floor or the deck below 25.
7. **Evidence and history gates remain hard.** Importance never revives a skipped or below-threshold cluster.
8. **Current `max_culture_stories = 3` is removed.** Structured floor/ceiling/max configuration becomes the only final-selection limit, and the separate environment override `BRIEFING_MAX_CULTURE_STORIES` is removed. This removal happens in **Step 3**, when the pre-change selector that reads it is deleted; Steps 1–2 add the new configuration *alongside* `max_culture_stories` so the shadow-run state (old selector still active, importance computed but unused) remains runnable.
9. **SMS formatting permits six stories.** Change `max_stories_per_category_sms` from 5 to 6 so an allocated big-day sixth story is not silently omitted at formatting time.
10. **Importance is computed on the classified cluster as it exists today.** Classifier `outlier_urls` continue to affect drafting evidence only; this change does not recluster or rescore after outlier identification.
11. **The importance clamp is asymmetric.** The LLM may lift final importance above the deterministic score up to `clamp_up` (default open, 100), but may not pull it more than `clamp_down` (25) below. A big-day sixth slot earned *only* through LLM lift additionally requires multi-source corroboration when `big_day_requires_corroboration` is true.
12. **A soft non-Culture per-source cap is retained.** The four non-Culture categories keep a soft per-source cap (2, relaxing to 3 on big days) that yields to protected floors and to otherwise-unfillable slots, preserving the diversity behavior of the removed `top_for_category()` deferral.

## Selection policy

| Category | Internal key | Floor | Normal ceiling | Big-day max |
| --- | --- | ---: | ---: | ---: |
| Business + Tech | `business_tech` | 3 | 5 | 6 |
| U.S. News | `domestic` | 3 | 5 | 6 |
| Global News | `global` | 3 | 5 | 6 |
| Finance | `finance` | 3 | 5 | 6 |
| Culture + Media | `culture` | 2 | 5 | 6 |

Floors total 14. Normal ceilings total 25. A floor means “select up to this count from qualified, classified candidates while honoring all hard constraints.” It is not a promise to weaken evidence, history, publisher, or Culture-lane rules.

### Qualified candidate

A cluster is qualified for every selection phase only when all of these are true:

- `cluster.category` is one of `CATEGORY_NAMES`;
- `cluster.skip_reason` is empty;
- `cluster.evidence_score >= config.enrichment.minimum_story_evidence_score`;
- its `story_identity()` has not already been selected;
- adding it would not violate the relevant category maximum;
- for Culture, adding it would not exceed two stories from its primary source or the phase-specific lane maximum.

### Deterministic ordering

Every global or per-category importance sort uses this exact key:

```python
(
    -cluster.importance,
    -cluster.total_score,
    -cluster.latest_published_at.timestamp(),
    story_identity(cluster),
)
```

This guarantees stable ordering when two clusters receive the same rounded importance.

### Phase 1 — protected floors

Process categories in canonical `CATEGORY_NAMES` order. Within each non-Culture category, select the highest-ranked qualified candidates until its floor is met or supply is exhausted.

For Culture’s floor of two:

1. Prefer the highest-ranked candidate from a lane not yet represented.
2. Respect the hard two-per-source cap.
3. Use a preferred lane cap of one during the first pass.
4. If two stories cannot be selected, run a second pass allowing two from one lane.
5. Never exceed the hard lane cap of three or source cap of two.

A **soft per-source cap** applies to the four non-Culture categories (see *Non-Culture source diversity* below): each primary source may hold at most `max_per_source_per_category` (default 2) stories in a category during Phases 1–2, relaxing to `big_day_source_cap` (default 3) in Phase 3. The cap is soft — it yields when honoring it would leave a protected floor unmet or a deck slot unfillable from any other source. This preserves the diversity guarantee the old `top_for_category()` deferral provided, now expressed inside the phase-based selector rather than as a separate algorithm.

### Phase 2 — cross-category remainder

Create one globally ordered list of every unselected qualified candidate. Walk it once until the deck reaches 25 or the list is exhausted:

- reject a candidate when its category already has five stories;
- for Culture, reject it when its source already has two selected stories or its lane already has two;
- for non-Culture categories, defer (do not yet select) a candidate when its primary source already holds `max_per_source_per_category` (2) stories in that category;
- otherwise select it and update category/source/lane state.

If the deck is still below 25 after this diversity-respecting pass and only deferred (over-cap) candidates remain, walk the deferred non-Culture candidates in the same importance order and select them until the deck reaches 25 or they are exhausted. This second pass is what makes the source cap *soft*: a dominant publisher exceeds two in a category only when no more diverse qualified story exists to fill the slot. Phase 2 never grants a sixth story.

### Phase 3 — big-day redistribution

Run only when phase 2 ends below 25. Walk the remaining candidates in the same deterministic order:

- require `cluster.importance >= 70`;
- require the category count to be below six;
- require the deck count to be below 25;
- for Culture, keep the source cap at two and relax the preferred lane cap from two to the hard cap of three;
- for non-Culture categories, relax the soft source cap from two to `big_day_source_cap` (3);
- when `big_day_requires_corroboration` is true (default), a candidate whose deterministic importance alone is below the threshold (`importance_det < big_day_importance_threshold`) — i.e. one that clears 70 only through LLM lift — must additionally have `source_count >= 2`, so a single-source, keyword-invisible story cannot seize the rare sixth slot on an LLM grade alone;
- select until the deck reaches 25 or no candidate qualifies.

This phase fills capacity released by categories with fewer than five qualified stories. It never expands the deck beyond 25.

### Underfilled reasons

Retain existing underfilled reasons and evaluate them against the new floor:

- `not_enough_evidence_qualified_candidates`
- `classification_moved_candidates_elsewhere`
- `history_suppressed_candidates`
- `source_diversity_cap`

Add `importance_below_big_day_threshold` only as a deck-level diagnostic explaining why phase 3 stopped below 25; do not use it as a per-category floor reason.

## Importance model

### Deterministic component

Use a fixed logistic transformation:

```python
importance_det = 100.0 / (1.0 + math.exp(-k * (total_score - m)))
```

Initial locked values:

- `m = 12.0`
- `k = 0.30`

These values are grounded in 739 current skipped-story audit rows: observed `total_score` range 5.50–23.29, median 7.61. Approximate mappings are:

| `total_score` | Deterministic importance |
| ---: | ---: |
| 7 | 18 |
| 12 | 50 |
| 15 | 71 |
| 20 | 92 |

Clamp the returned float to `[0.0, 100.0]`. The mapping is absolute only while the `total_score` formula remains unchanged. Any change to weights or terms in `score_clusters()` requires rerunning the calibration evaluation and updating a `calibration_version` string.

### LLM component

When classification capability is enabled, the existing classify response must include `importance` as an integer from 0 through 100.

Prompt anchors:

- 90–100: immediate, broad real-world consequence, such as war escalation, a major disaster, or market-moving national policy.
- 70–89: significant consequence to many readers, such as a major legal ruling or a tariff materially changing household costs.
- 40–69: notable but narrower consequence, such as consequential corporate earnings or regional policy.
- 20–39: routine or niche development, such as an ordinary product launch or scheduled entertainment release.
- 0–19: trivia, promotion, recap, minor celebrity activity, or low-consequence novelty.

The prompt must explicitly grade consequence rather than reader interest, virality, source prestige, or category scarcity. The same rubric applies to all categories.

### Hybrid combination

Represent the raw grade as `CategoryAssignment.llm_importance: int | None`. Fallback classification always sets it to `None`.

Use these locked rollout values:

- `llm_weight = 0.65`
- `clamp_down = 25.0`
- `clamp_up = 100.0`

```python
det = importance_from_total_score(cluster.total_score, config.importance)
if assignment is None or assignment.llm_importance is None:
    final = det
else:
    blended = round(
        config.importance.llm_weight * assignment.llm_importance
        + (1.0 - config.importance.llm_weight) * det
    )
    lower = det - config.importance.clamp_down
    upper = det + config.importance.clamp_up
    final = min(upper, max(lower, blended))
cluster.importance = min(100.0, max(0.0, float(final)))
```

An actual LLM grade of zero therefore remains distinguishable from an unavailable grade.

#### Asymmetric clamp — deterministic floor, open ceiling

The clamp is deliberately one-directional. The **downside** (`clamp_down = 25`) still protects against the LLM *under*-rating a strongly-corroborated story: final importance can never fall more than 25 points below deterministic. The **upside** is open by default (`clamp_up = 100`, so `det + clamp_up` never binds), letting a confident LLM grade lift a story whose importance the keyword-based deterministic score misses. This resolves the earlier concern that a low-deterministic story could never reach the top of the scale no matter how important the LLM judged it.

The 35% deterministic weight still tempers the top end: to reach the big-day threshold of 70, a low-deterministic story (e.g. `det ≈ 21`) needs an LLM grade near 97, so only near-certain LLM judgments carry that far, and the big-day corroboration guard (Phase 3) prevents a single-source hallucinated grade from converting that reach into an actual sixth story. `clamp_up` can be lowered (e.g. to 40) in a later review to re-tighten the ceiling without touching the downside guard.

## Feature flag and disabled behavior

`ImportanceConfig.enabled` is a permanent kill-switch for the importance *signal*, not for the selector. Once Step 3 lands, the three-phase selector is the only final-selection path; there is no second, parallel selector to fall back to.

- When `enabled = true`: importance is computed (deterministic, or hybrid when classification is available) and drives all ordering and the big-day threshold.
- When `enabled = false`: importance computation is skipped and every `cluster.importance` stays `0.0`. The deterministic tie-break key then reduces to ordering by `-total_score` (then recency, then `story_identity`), so Phase 1 floors and the Phase 2 remainder select purely by `total_score`. Because no cluster can reach the big-day threshold of 70 with importance `0.0`, Phase 3 is inert and no category receives a sixth story; the deck behaves as floors-plus-`total_score`-remainder capped at five per category.

This makes `enabled = false` a safe, self-consistent disable that depends on no removed configuration (`max_culture_stories`) and no removed code path (the pre-change category selector). Restoring the exact pre-change published output is done by git-reverting the commit series, not by the flag.

## Configuration contracts

### `src/news_agent/models.py`

Add immutable configuration models:

```python
@dataclass(frozen=True)
class CategorySelectionLimit:
    floor: int
    ceiling: int
    big_day_max: int


@dataclass(frozen=True)
class ImportanceConfig:
    enabled: bool = True
    logistic_midpoint: float = 12.0
    logistic_steepness: float = 0.30
    llm_weight: float = 0.65
    clamp_down: float = 25.0
    clamp_up: float = 100.0
    deck_target: int = 25
    big_day_importance_threshold: float = 70.0
    big_day_requires_corroboration: bool = True
    calibration_version: str = "total-score-v1-2026-07-21"
```

Add:

- `AgentConfig.importance: ImportanceConfig`;
- `AgentConfig.category_selection_limits: dict[CategoryName, CategorySelectionLimit]`;
- `AgentConfig.max_per_source_per_category: int = 2` — soft per-source cap for the four non-Culture categories in Phases 1–2;
- `AgentConfig.big_day_source_cap: int = 3` — the relaxed non-Culture per-source cap in Phase 3;
- `StoryCluster.importance: float = 0.0`;
- `CategoryAssignment.llm_importance: int | None = None`.

Do **not** remove `AgentConfig.max_culture_stories` in this step — the pre-change selector still reads it through the Step-2 shadow run. Its removal, and removal of `BRIEFING_MAX_CULTURE_STORIES`, is deferred to Step 3.

### `config/sources.toml`

Change `settings.max_stories_per_category_sms` to `6` and add these exact sections. Leave `settings.max_culture_stories` in place for now; it is removed in Step 3 alongside the old selector.

```toml
[importance]
enabled = true
logistic_midpoint = 12.0
logistic_steepness = 0.30
llm_weight = 0.65
clamp_down = 25.0
clamp_up = 100.0
deck_target = 25
big_day_importance_threshold = 70.0
big_day_requires_corroboration = true
calibration_version = "total-score-v1-2026-07-21"

[selection]
max_per_source_per_category = 2
big_day_source_cap = 3

[selection_limits.business_tech]
floor = 3
ceiling = 5
big_day_max = 6

[selection_limits.domestic]
floor = 3
ceiling = 5
big_day_max = 6

[selection_limits.global]
floor = 3
ceiling = 5
big_day_max = 6

[selection_limits.finance]
floor = 3
ceiling = 5
big_day_max = 6

[selection_limits.culture]
floor = 2
ceiling = 5
big_day_max = 6
```

Validation in `src/news_agent/config.py` must reject:

- `logistic_steepness <= 0`;
- `llm_weight` outside `[0, 1]`;
- `clamp_down` or `clamp_up` outside `[0, 100]`;
- `max_per_source_per_category < 1`, or `big_day_source_cap < max_per_source_per_category`;
- threshold outside `[0, 100]`;
- missing or extra category keys;
- negative floor/ceiling/max values;
- `floor > ceiling` or `ceiling > big_day_max`;
- `sum(floors) > deck_target`;
- `deck_target > sum(ceilings)`;
- `deck_target < 1`.

Removal of `BRIEFING_MAX_CULTURE_STORIES` parsing (and `settings.max_culture_stories`) is deferred to Step 3. No replacement environment override is added; selection policy is ultimately configured in TOML as one coherent unit.

## Classification and application order

The pipeline order is fixed:

1. Fetch, enrich, quality-gate, cluster, score, history-gate, and evidence-gate as today.
2. Build the initial classification pool.
3. Classify the initial pool.
4. Apply category assignments.
5. Apply deterministic/hybrid importance to the initial pool.
6. Run provisional three-phase selection.
7. Identify categories below their configured floor.
8. Select one bounded backfill union using existing rules: outside the initial pool, no skip reason, evidence-qualified, matching feed hint, at most 10 candidates per deficient category, deduplicated by `story_identity()`.
9. Run at most one additional classifier/fallback call for that union.
10. Apply backfill category assignments and importance.
11. Run the three-phase selector once more over all classified clusters.
12. Stop even if a floor or deck target remains underfilled.

Backfill selection itself remains ranked by `total_score`, because candidates do not have an LLM importance grade until after classification. No second enrichment, quality-gate, clustering, history, or evidence pass is permitted.

## Culture integration

Replace whole-list `top_for_culture()` calls inside final selection with stateful helpers in `src/news_agent/scoring.py`:

```python
@dataclass
class CultureSelectionState:
    source_counts: dict[str, int] = field(default_factory=dict)
    lane_counts: dict[str, int] = field(default_factory=dict)


def can_add_culture(
    cluster: StoryCluster,
    state: CultureSelectionState,
    lane_cap: int,
) -> bool:
    source = cluster.sources[0] if cluster.sources else "unknown"
    lane = cluster.culture_lane
    return state.source_counts.get(source, 0) < 2 and state.lane_counts.get(lane, 0) < lane_cap
```

Add a matching `record_culture_selection()` helper. Phase 1 uses lane caps one then two; phase 2 uses two; phase 3 uses three. Source cap two never changes.

Retain `top_for_culture()` as a compatibility wrapper around the same helpers until all direct tests migrate; do not maintain a second independent constraint algorithm. The non-Culture source cap (next section) reuses the same per-source counting; implement one shared `SourceCapState` rather than duplicating source tracking.

## Non-Culture source diversity

The four non-Culture categories keep a soft per-source cap, mirroring the diversity guarantee the removed `top_for_category()` deferral provided, but expressed as shared selection state rather than a separate algorithm.

Introduce one shared per-source counter, used by every phase and by Culture:

```python
@dataclass
class SourceCapState:
    counts: dict[tuple[str, str], int] = field(default_factory=dict)  # (category, source) -> count

    def held(self, category: str, source: str) -> int:
        return self.counts.get((category, source), 0)

    def record(self, category: str, source: str) -> None:
        self.counts[(category, source)] = self.held(category, source) + 1
```

A cluster's primary source is `cluster.sources[0]` (already reputation-ordered), matching the existing convention in `top_for_category()` and `top_for_culture()`.

### How it fits each phase

- **Phase 1 (floors):** within a non-Culture category, first select qualified candidates whose primary source is under `max_per_source_per_category` (2), in importance order, until the floor is met. If the floor is still unmet because the only remaining qualified candidates are over-cap, run a second pass that ignores the cap until the floor is met or supply is exhausted. The cap therefore never blocks a protected floor.
- **Phase 2 (remainder):** the global walk defers over-cap non-Culture candidates; the second walk over the deferred pool (already specified in *Phase 2*) fills any remaining deck capacity. A publisher exceeds two in a category only when no more diverse qualified story exists.
- **Phase 3 (big-day):** the cap relaxes to `big_day_source_cap` (3), consistent with the old `max(2, limit // 2)` scaling at the six-story limit and with Culture's lane relaxation.

### Relationship to the rest of the program

- **Culture is unchanged.** Culture's own source cap (hard, two per source) and lane caps stay as specified; the non-Culture cap applies only to the four non-Culture categories. Both share one `SourceCapState`, so there is a single source-counting implementation, not two.
- **Hardness differs by design.** The Culture source cap is **hard** and may leave Culture below its floor (decision #6). The non-Culture cap is **soft** and yields to meet a floor or to fill an otherwise-empty slot — the exact behavior of the old `top_for_category()` deferred-append pass, preserved.
- **Importance still governs order.** The cap only changes *which* qualified candidate is taken when a source is saturated; the deterministic importance tie-break still decides ordering within the under-cap pass and the deferred pass.
- **Configuration.** `max_per_source_per_category` and `big_day_source_cap` live in the `[selection]` TOML block and are validated (see *Configuration contracts*). Setting `max_per_source_per_category` at or above the ceiling disables the cap, reproducing pure importance-only behavior if ever wanted.

Add a `source_cap_relaxed_by_category: dict[str, int]` diagnostic counting selections in each category that exceeded the soft cap, so over-concentration is visible in audits.

## Diagnostics and audit contracts

### `PipelineDiagnostics`

Add these exact fields:

```python
importance_by_category: dict[str, dict[str, float | int]] = field(default_factory=dict)
floor_selected_by_category: dict[str, int] = field(default_factory=dict)
remainder_selected_by_category: dict[str, int] = field(default_factory=dict)
big_day_selected_by_category: dict[str, int] = field(default_factory=dict)
source_cap_relaxed_by_category: dict[str, int] = field(default_factory=dict)
deck_target: int = 0
deck_selected: int = 0
deck_underfilled_reason: str = ""
```

Every new field **must** carry a default, because `PipelineDiagnostics` is a frozen dataclass whose fields are all default-constructed; existing code and tests build it with keyword subsets and would break if a required, default-less field were added. Add a regression test (`test_pipeline_diagnostics_construct_with_partial_kwargs`) that constructs it with no arguments and with a subset, asserting the new fields default to empty containers / zero / `""`.

`importance_by_category` covers every evidence-qualified classified candidate, not only selected stories. Every category key maps to:

```python
{"count": int, "min": float, "median": float, "max": float}
```

Use zeros for all four values when a category is empty. `deck_underfilled_reason` is empty at 25, otherwise one of:

- `not_enough_qualified_candidates`
- `culture_diversity_caps`
- `importance_below_big_day_threshold`

Print phase counts and importance distributions under the existing `Classified results` diagnostics heading.

### Assignment log

Persist `llm_importance` as an integer or JSON `null`. Readers must tolerate old rows without the field by treating them as `None`.

### Skipped-story log

Add `importance: float` to `SkippedStory`, its JSON payload, and console table. Sort skipped stories by `(importance, score)` descending so auditing matches final selection policy.

Do not modify `quality_report.py`; it aggregates quality-gate rejection files, which have no classified-cluster importance data. Importance reporting belongs in pipeline diagnostics and skipped-story audit logs.

## Reproducible calibration and evaluation

### Initial curve basis

The locked initial values `m = 12.0` and `k = 0.30` use the current audit distribution noted above. They are not re-fit during normal runs.

### Replay fixture

Create `/Users/raymondwang/PersonalProjects/NewsAgent/tests/fixtures/importance_selection_replay.json` containing 30 synthetic, fully specified clusters: six per category. Include title, category, total score, evidence score, primary source, Culture lane, raw LLM importance or `null`, and expected final importance band.

The fixture must include:

- one tariff/fuel-cost story expected in 70–89;
- one war-escalation story expected in 90–100;
- one major legal ruling expected in 70–89;
- one market-moving earnings story expected in 60–79;
- one ordinary product launch expected in 20–39;
- one scheduled movie release expected in 20–39;
- a legitimate LLM importance of zero;
- a missing LLM grade (`null`);
- a keyword-invisible story (low `total_score`, so low deterministic importance) with a near-certain LLM grade, expected to reach the big-day band through upside reach;
- a single-source, below-deterministic-threshold story with a high LLM grade, used to exercise the big-day corroboration guard both denied (one source) and admitted (two sources);
- a non-Culture category with three-plus qualified stories from one dominant publisher, to exercise the soft source cap, its floor-driven relaxation, and its remainder-driven relaxation;
- equal-final-importance candidates exercising every tie-break;
- Culture candidates that hit publisher and lane caps;
- at least one category with only two qualified candidates so phase 3 must redistribute released capacity.

This is a deterministic test fixture, not a live-news scrape. Review and commit it with the scoring change so future weight changes produce an explicit fixture diff.

### Acceptance criteria

Before enabling hybrid weight 0.65:

- deterministic importance is monotonic for every fixture row;
- every anchor example lands in its expected band after hybrid clamping;
- tariff/fuel-cost ranks above scheduled movie release;
- war escalation ranks above ordinary product launch;
- no LLM grade moves final importance more than `clamp_down` (25) points *below* deterministic;
- a high LLM grade lifts final importance above deterministic (bounded only by `clamp_up`), and the keyword-invisible-but-LLM-important fixture story reaches the big-day band;
- a single-source, below-threshold story is denied a sixth slot while `big_day_requires_corroboration` is true, and admitted once given a second source;
- the non-Culture soft source cap holds a dominant publisher to two per category when diverse candidates exist, and yields to meet a floor or fill an otherwise-empty slot;
- OpenAI-off and `llm_importance = None` produce exactly deterministic importance;
- the replay deck satisfies every floor that has feasible constrained supply;
- final deck count is at most 25;
- phase 3 only selects importance 70 or higher.

## Implementation steps

### Step 0 — establish baseline

From `/Users/raymondwang/PersonalProjects/NewsAgent`:

```bash
git status --short
python3 -m pytest -q
git diff --check
```

Expected test result at plan revision: `209 passed`. Preserve unrelated moves of `culture-briefing-plan-review.md`, `quality_gate_spec.md`, and `.DS_Store`; do not stage, delete, or rewrite them as part of this work.

### Step 1 — add configuration and model contracts

**Files:**

- `/Users/raymondwang/PersonalProjects/NewsAgent/src/news_agent/models.py`
- `/Users/raymondwang/PersonalProjects/NewsAgent/src/news_agent/config.py`
- `/Users/raymondwang/PersonalProjects/NewsAgent/config/sources.toml`
- `/Users/raymondwang/PersonalProjects/NewsAgent/tests/test_models.py`
- `/Users/raymondwang/PersonalProjects/NewsAgent/tests/test_config.py`

Add the exact dataclasses, fields, TOML blocks, and validation specified above, **in parallel with** the existing `max_culture_stories` setting (which is not removed until Step 3). Write failing validation/default tests first.

**Tests:**

- `test_default_importance_config_matches_locked_values`
- `test_default_selection_limits_match_policy`
- `test_config_rejects_invalid_importance_ranges`
- `test_config_rejects_invalid_selection_limit_order`
- `test_config_rejects_floor_sum_above_deck_target`
- `test_config_rejects_deck_target_above_normal_capacity`
- `test_sms_story_limit_allows_big_day_sixth_story`

**Verify:**

```bash
python3 -m pytest tests/test_models.py tests/test_config.py -q
```

**Commit checkpoint:** `feat(config): define importance and deck selection policy`

### Step 2 — add importance calculation and classification grade

**Files:**

- `/Users/raymondwang/PersonalProjects/NewsAgent/src/news_agent/classify.py`
- `/Users/raymondwang/PersonalProjects/NewsAgent/src/news_agent/scoring.py`
- `/Users/raymondwang/PersonalProjects/NewsAgent/tests/test_classify.py`
- `/Users/raymondwang/PersonalProjects/NewsAgent/tests/test_scoring.py`
- `/Users/raymondwang/PersonalProjects/NewsAgent/tests/fixtures/importance_selection_replay.json`

Add the strict-schema integer, prompt anchors, optional raw grade, logistic mapping, asymmetric hybrid clamp (`clamp_down` / `clamp_up`), assignment persistence, the shared `SourceCapState` plus Culture state helpers, and replay fixture specified above.

**Tests:**

- `test_importance_from_total_score_matches_locked_anchor_values`
- `test_importance_from_total_score_is_monotonic_and_bounded`
- `test_hybrid_importance_downside_clamp_protects_deterministic_floor`
- `test_hybrid_importance_upside_open_allows_llm_reach`
- `test_llm_zero_is_preserved_as_real_grade`
- `test_missing_llm_importance_uses_deterministic_only`
- `test_fallback_assignment_uses_none_importance`
- `test_classifier_schema_requires_bounded_importance`
- `test_assignment_log_reads_missing_importance_as_none`
- `test_culture_state_enforces_source_and_lane_caps`
- `test_source_cap_state_tracks_per_category_counts`

**Verify:**

```bash
python3 -m pytest tests/test_classify.py tests/test_scoring.py -q
```

**Commit checkpoint:** `feat(scoring): calculate calibrated hybrid importance`

### Step 3 — replace final selection with floor and remainder phases

**Files:**

- `/Users/raymondwang/PersonalProjects/NewsAgent/src/news_agent/pipeline.py`
- `/Users/raymondwang/PersonalProjects/NewsAgent/src/news_agent/models.py`
- `/Users/raymondwang/PersonalProjects/NewsAgent/src/news_agent/config.py`
- `/Users/raymondwang/PersonalProjects/NewsAgent/config/sources.toml`
- `/Users/raymondwang/PersonalProjects/NewsAgent/tests/test_pipeline.py`
- `/Users/raymondwang/PersonalProjects/NewsAgent/tests/test_config.py`

Add a pure selection result model containing selected clusters plus phase counts. Implement the three phases and exact tie-break ordering above, enforcing the soft non-Culture source cap (and its floor/remainder/big-day relaxations) and the big-day corroboration guard through the shared `SourceCapState`. Retain one backfill pass using the fixed application order. In the same step, **delete the pre-change selector** (`select_unique_category_clusters` / `top_for_category` / `underfilled_*` helpers and the `CATEGORY_LIMITS` constant it used) and remove the now-unused `AgentConfig.max_culture_stories`, `settings.max_culture_stories`, and `BRIEFING_MAX_CULTURE_STORIES` parsing. This is the point at which decision #8 takes effect, so the shadow-run state in Steps 1–2 stays runnable.

**Tests:**

- `test_max_culture_stories_setting_is_removed`
- `test_feasible_floors_are_selected_before_higher_importance_remainder`
- `test_floor_does_not_relax_evidence_history_or_culture_caps`
- `test_remainder_uses_global_importance_order`
- `test_equal_importance_uses_total_score_recency_and_identity_ties`
- `test_normal_phase_never_exceeds_five_per_category`
- `test_big_day_sixth_requires_released_capacity_and_importance_70`
- `test_big_day_corroboration_blocks_single_source_llm_promotion`
- `test_big_day_phase_never_exceeds_deck_target_25`
- `test_non_culture_soft_source_cap_limits_dominant_publisher`
- `test_soft_source_cap_yields_to_meet_floor`
- `test_soft_source_cap_yields_to_fill_remainder`
- `test_big_day_source_cap_relaxes_to_three`
- `test_culture_source_cap_unaffected_by_non_culture_cap`
- `test_culture_floor_prefers_distinct_lanes`
- `test_culture_source_cap_can_leave_floor_underfilled`
- `test_backfill_runs_once_only_for_floor_deficits`
- `test_backfill_importance_is_applied_before_final_selection`
- `test_selection_deduplicates_story_identity_across_phases`

**Verify:**

```bash
python3 -m pytest tests/test_pipeline.py tests/test_scoring.py -q
```

**Commit checkpoint:** `feat(selection): fill briefing remainder by importance`

### Step 4 — add diagnostics and skipped-log auditing

**Files:**

- `/Users/raymondwang/PersonalProjects/NewsAgent/src/news_agent/models.py`
- `/Users/raymondwang/PersonalProjects/NewsAgent/src/news_agent/pipeline.py`
- `/Users/raymondwang/PersonalProjects/NewsAgent/src/news_agent/cli.py`
- `/Users/raymondwang/PersonalProjects/NewsAgent/src/news_agent/skipped_log.py`
- `/Users/raymondwang/PersonalProjects/NewsAgent/tests/test_cli.py`
- `/Users/raymondwang/PersonalProjects/NewsAgent/tests/test_pipeline.py`
- `/Users/raymondwang/PersonalProjects/NewsAgent/tests/test_skipped_log.py`

Add the exact diagnostics and audit fields above. Do not route importance through `quality_report.py`.

**Tests:**

- `test_importance_diagnostics_cover_all_classified_qualified_candidates`
- `test_empty_category_importance_summary_is_zeroed`
- `test_phase_diagnostics_sum_to_selected_deck`
- `test_deck_underfilled_reason_reports_big_day_threshold`
- `test_cli_prints_floor_remainder_and_big_day_counts`
- `test_skipped_log_persists_and_sorts_by_importance`

**Verify:**

```bash
python3 -m pytest tests/test_cli.py tests/test_pipeline.py tests/test_skipped_log.py -q
```

**Commit checkpoint:** `feat(diagnostics): expose importance selection decisions`

### Step 5 — full regression and replay verification

Run:

```bash
python3 -m pytest -q
git diff --check
```

Expected: all tests pass; the exact count will exceed the 209-test baseline.

Run isolated dry runs from `/private/tmp/news-agent-importance-shadow` with absolute config, watchlist, audit, and history paths. First use `--openai-mode off --ignore-history --show-diagnostics`; then use `--openai-mode full --ignore-history --show-diagnostics`. Neither command may use `--send`.

Verify from diagnostics:

- deck count never exceeds 25;
- every selected story meets evidence threshold 1.2;
- OpenAI-off importance equals deterministic importance;
- full-mode grades never fall more than 25 points below deterministic, and upward lift stays within `clamp_up`;
- no non-Culture category shows more than two stories from one publisher unless a floor or an otherwise-empty slot forced the relaxation (check `source_cap_relaxed_by_category`);
- phase counts sum to the selected deck;
- every feasible floor is met;
- Culture never exceeds two stories from one source or three from one lane.

Do not require a live run to produce a sixth story; the replay fixture is the reproducible proof of big-day behavior.

**Commit checkpoint:** `test(importance): verify calibrated deck allocation`

## Rollout

1. Merge Steps 1–2 with `importance.enabled = true`. Because the selector replacement is Step 3, the existing selector is still active here: importance is computed and logged for every classified cluster but does not yet affect output. Shadow-check by comparing importance ordering against `total_score` ordering. (`max_culture_stories` and the old `CATEGORY_LIMITS` remain until Step 3 so this intermediate state runs.)
2. Enable the three-phase selector with `llm_weight = 0.0` for two dry runs and confirm deterministic floor/remainder behavior.
3. Set `llm_weight = 0.65` and run one full-mode dry run; inspect anchor stories and clamp diagnostics.
4. Enable production sends only after the replay fixture and all three shadow stages pass.
5. After seven production runs, review median selected count, floor misses, sixth-slot frequency, and per-category importance distributions. Any change to the `total_score` formula requires a new calibration version before deployment.

## Rollback

- Set `importance.enabled = false` to disable the importance signal: the three-phase selector still runs but orders by `total_score` and grants no sixth stories (see *Feature flag and disabled behavior*). This is the fast kill-switch, and it does not depend on the pre-change selector, which no longer exists after Step 3. To restore the exact pre-change published output, git-revert the commit series.
- If configuration parsing itself must be reverted, revert `feat(config): define importance and deck selection policy` together with later importance commits; there is no database migration.
- Assignment and skipped-story logs are append-compatible: old readers ignore new keys, and new readers treat absent `llm_importance` as `None`.
- Do not roll back by deleting history, quality-gate, assignment, or skipped-story audit files.

## Presentation and ordering (scope)

This change governs **selection** only — which stories enter the deck and how many per category. It does not change **presentation**:

- Within each category, stories keep their current display order.
- The deck keeps its current category ordering; there is no importance-ranked "top story of the day" lead.

Importance is available on every selected `StoryCluster` (and in diagnostics), so a later change could drive display order or a lead-story highlight — but that is deliberately out of scope here to keep this a single, reviewable selection change. The natural hook for a follow-up is briefing assembly in `pipeline.py` / `formatting.py`, which would sort selected clusters by the same deterministic importance tie-break key before rendering. Deferring it also avoids coupling the selection change to formatter/character-budget behavior (`FormatOptions`), which is easier to review separately.

*(Default taken: presentation reordering is out of scope for this change. Flip this section to in-scope if you want the lead-story highlight built now.)*

## Non-goals

- Do not change `[fetch_reserves]`, the 240-article ceiling, the 50-page enrichment budget, evidence threshold 1.2, or source extraction permissions.
- Do not add a separate OpenAI request; importance is one field in the existing classification response.
- Do not use per-run min-max normalization.
- Do not keyword-classify categories or use category scarcity to inflate importance.
- Do not guarantee floors by admitting unqualified stories.
- Do not reorder category sections or add a top-story highlight; within-category importance ordering is implemented separately (see `presentation-ordering-plan.md`).
