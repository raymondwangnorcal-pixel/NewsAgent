# Plan: Consistent Culture + Media Briefings

**Status:** Implemented 2026-07-21 — seven-run rollout observation remains  
**Repository:** `/Users/raymondwang/PersonalProjects/NewsAgent`  
**Baseline:** `python3 -m pytest -q` → `183 passed`

## Implementation result

- Regression suite: `202 passed`.
- Live feed validation: Hollywood Reporter passed with 80% evidence-gate coverage; Deadline and Billboard passed with 100%; Polygon passed with 90%. All four had valid timestamps, 100% nonempty/nonduplicate feed text, matching extraction policies, and a successful article extraction.
- Isolated OpenAI-off dry run: 84 fetched Culture-hinted articles, 15 evidence-qualified Culture-hinted classification candidates, 14 deterministic Culture assignments, and 4 selected Culture stories after hard caps.
- Isolated classify-only dry run: 87 fetched Culture-hinted articles, 15 evidence-qualified Culture-hinted classification candidates, 12 model-assigned Culture stories, 4 selected Culture stories, 0 LLM drafts, and 22 explicit `fallback_disabled` drafts.
- No send was performed. The required seven daily rollout observations remain an operational follow-up because one implementation session cannot establish a seven-run median.

## Goal

Produce four to six evidence-rich, genuinely important Culture + Media stories on normal news days in both model-backed and deterministic runs, without weakening the existing evidence threshold, inventing a second category taxonomy, or allowing sports and one publisher to dominate the section.

## Success criteria

The implementation is complete when all of these conditions hold:

1. Every category receives a fair opportunity to enter enrichment and classification before globally dominant topics consume the available capacity.
2. Classification candidates have `evidence_score >= minimum_story_evidence_score` before they consume reserved or global classification slots.
3. `--openai-mode classify-only` runs the ambiguous-content judge and category classifier but never calls OpenAI drafting.
4. `--openai-mode off` makes no OpenAI calls and resolves multi-category feed-tag ties deterministically, using source type before category balance.
5. Culture selection prefers at least three distinct Culture lanes, never exceeds two stories from one publisher or three stories from one lane, and never admits a story below the evidence threshold merely to fill the section.
6. A single bounded backfill pass may classify additional already-qualified candidates, but it never performs a second page-enrichment pass or reclusters partially enriched data.
7. `--show-diagnostics` distinguishes feed-hint metrics from true post-classification category metrics.
8. A seven-run live validation produces a median of at least four published Culture stories, zero below-threshold Culture stories, and no run with more than three sports stories.

## Non-goals

- Do not guarantee six Culture stories when fewer than four evidence-qualified stories exist.
- Do not lower `minimum_story_evidence_score` below `1.2` for Culture.
- Do not add title-keyword category classification; `docs/category-guidelines.md` remains the only editorial category policy.
- Do not run a second page-enrichment or quality-gate pass during backfill.
- Do not depend on Google News article URLs resolving successfully. Aggregator entries remain discovery evidence unless a supported resolver is added separately.
- Do not change alert behavior in `src/news_agent/alerts.py`.

## Review resolutions

| Review comment | Resolution in this plan |
| --- | --- |
| Feed tags exist on articles, not clusters | Add `cluster_feed_hints()`, defined as the union of all nonempty `Article.feed_categories`. A dual-tag cluster intentionally contributes coverage to both hint queues but enters the final candidate pool only once. |
| Classification union cap and trim order were undefined | Use an exact upper bound of 80: 30 global candidates plus up to 10 reserved candidates for each of five categories. Global candidates enter first; category deficits are filled round-robin in `CATEGORY_NAMES` order. No arbitrary trimming occurs. |
| Evidence gate ran after candidate selection | Filter classification eligibility by `evidence_score >= config.enrichment.minimum_story_evidence_score` before global or reserved admission. The later evidence gate remains as a defensive publication check. |
| Enrichment should reserve clusters, not attempts | Reserve eight clusters per feed-hint category plus the top 20 global clusters, deduplicated. Schedule one directly permitted article per selected cluster before scheduling any second article. |
| Google News can consume dead-end attempts | Aggregator-only URLs are not page-enrichment eligible. They consume no page budget unless a future explicit resolver returns a policy-covered destination. |
| `classify-only` could accidentally enable drafting | Resolve the CLI mode once into `OpenAICapabilities(judge_quality, classify, draft)`. Downstream functions receive booleans, never the three-state string. |
| Ambiguous quality judging was unspecified | `full` and `classify-only` run the ambiguous-content judge; `off` does not. |
| Balanced fallback could override source signal | On tied feed votes, filter by an explicit source-type mapping first. Category balance is used only if multiple tied categories remain after source-type filtering. |
| Culture impact keywords duplicate category logic | Do not ship Culture keyword scoring initially. Add it only behind a disabled flag if seven-run diagnostics show that quotas still leave Culture underrepresented before classification. |
| New sources need extraction policies and overlap | Add overlapping film/TV outlets plus distinct music and gaming outlets, each with an `article_text` extraction policy. Enable only feeds that pass the stated live quality check. |
| Culture lanes conflict with existing selector | Add a dedicated `top_for_culture()` selector that replaces, rather than wraps, `top_for_category()` for Culture. |
| Strict schema requires lane on every assignment | Make `culture_lane` a required string on every classification entry; it is empty for non-Culture assignments. |
| Backfill implied a second enrichment pass | Backfill draws only from already quality-passed and evidence-qualified final clusters. It performs classification and selection only. |
| Enrichment and pre-pool rejection metrics cannot use true categories | Name pre-classification metrics `*_by_feed_hint`, including evidence rejection; reserve `*_by_category` for post-classification values and include `unclassified`. |
| Diagnostics should precede tuning | Diagnostics are implementation checkpoint 1 and establish the before/after baseline. |

## Final design

### OpenAI capability matrix

Add the following mode behavior:

| CLI mode | Ambiguous-content judge | Category classifier | Paragraph drafting |
| --- | --- | --- | --- |
| `full` | on | on | on |
| `classify-only` | on | on | off |
| `off` | off | off | off |

`--no-openai` remains an alias for `off`. Add `--no-openai-drafting` as an alias for `classify-only`. Passing both aliases is a CLI error.

### Cluster feed hints

`cluster_feed_hints(cluster)` returns a tuple in canonical `CATEGORY_NAMES` order. A category is present when any article in the cluster carries that feed category. This is deliberately inclusive for quota allocation; it is not a final category decision.

For The Verge, `("business_tech", "culture")` therefore contributes to both reserve-coverage counts. The cluster is deduplicated by `story_identity()` and appears once in the classification request.

### Classification pool

Eligible clusters must satisfy all of the following before admission:

- `cluster.skip_reason` is empty;
- `cluster.evidence_score >= minimum_story_evidence_score`;
- the cluster has not already been selected by identity.

Pool construction:

1. Add the top 30 eligible clusters by `total_score`.
2. Count feed-hint coverage already supplied by those clusters.
3. Build one ranked queue per category from all remaining eligible clusters carrying that hint.
4. Iterate categories in `CATEGORY_NAMES` order, adding one cluster per deficient category per round.
5. Adding a dual-hint cluster increments coverage for every hint it carries.
6. Stop when every available category reaches 10 hint-covered candidates or no queue can progress.

The mathematical maximum is 80 unique clusters. The normal count is lower because global candidates and dual-hint clusters satisfy reserves.

### Enrichment pool and request scheduling

Add these defaults to `EnrichmentConfig` and `[enrichment]`:

```toml
max_clusters_per_run = 60
global_cluster_slots = 20
reserved_clusters_per_category = 8
max_articles_per_cluster = 2
max_pages_per_run = 50
```

Enrichment cluster selection uses preliminary clusters and the same union-based feed hints:

1. Add the top 20 preliminary clusters globally.
2. Fill each category to eight feed-hint-covered clusters round-robin.
3. Stop at 60 unique clusters.

Article request scheduling then proceeds in two passes:

1. Schedule the highest-evidence article with a direct `article_text` or `metadata_only` policy from every selected cluster.
2. If budget remains, schedule the second-highest directly permitted article from each cluster.
3. Stop at 50 unique URLs.

`news.google.com` URLs are excluded from both passes. A cluster with rich feed content but no permitted page remains eligible for classification based on that feed evidence and consumes zero page requests.

### Deterministic category assignment

Add this explicit mapping to `src/news_agent/classify.py`:

| `feed_source_type` | Preferred tied categories |
| --- | --- |
| `business` | `business_tech` |
| `tech` | `business_tech` |
| `domestic` | `domestic` |
| `global` | `global` |
| `culture` | `culture` |
| `finance` | `finance` |
| `mixed_tech_culture` | `business_tech`, `culture` |
| `general`, `aggregator` | no preference |

Change The Verge's configured `source_type` to `mixed_tech_culture`.

Fallback selection for each ranked cluster:

1. Count article feed-category votes.
2. Keep only categories tied for the maximum vote count.
3. Count source-type preferences only among those tied categories.
4. If one category has the strongest source-type support, select it.
5. Otherwise select the tied category with the fewest assignments in this fallback batch.
6. Break a remaining tie using `CATEGORY_NAMES` order.

This uses balance only when feed and source-type evidence remain tied. The input list is already deterministically ranked, so repeated processing of identical inputs produces identical results.

### Culture lanes

The allowed lanes are:

- `film_tv`
- `music`
- `sports`
- `gaming`
- `media_creators`
- `internet_culture`
- empty string for non-Culture or unknown-lane assignments

Add `culture_lane` to `CategoryAssignment` and `StoryCluster`. Add `culture_lane` to `FeedConfig` and copy it to `Article.feed_culture_lane` during feed parsing.

The strict classification schema requires `culture_lane` for every assignment. The prompt instructs the model to return an empty string for every non-Culture assignment and one allowed lane for Culture. Invalid combinations are normalized defensively:

- non-Culture plus nonempty lane → lane becomes empty;
- Culture plus invalid/empty lane → lane becomes `media_creators` only if feed-lane voting produces that result, otherwise empty.

Fallback lane assignment uses majority voting across `Article.feed_culture_lane`; ties use the lane order listed above.

### Culture selection

Create `top_for_culture(clusters, limit=6, minimum=4)` in `src/news_agent/scoring.py`. It replaces the generic selector for Culture.

Selection runs in three passes over evidence-qualified Culture clusters ranked by `total_score`:

1. Diversity pass: select at most one story per nonempty lane, respecting a hard maximum of two stories from one primary source.
2. Balanced fill: fill toward six while keeping at most two stories per lane and two per primary source.
3. Minimum fill: if fewer than four were selected, add the highest-ranked remaining qualified stories until four, relaxing the preferred lane limit from two to a hard maximum of three; never relax the source cap, hard lane cap, or evidence threshold.

This makes broad lane diversity a preference while the publisher cap and three-per-lane cap remain hard rules. There is no separate sports-specific cap because sports is already a lane. If four qualified stories cannot be selected without violating either hard cap, publish fewer than four and report the constraint.

### Controlled backfill

After initial classification and evidence gating, compute provisional category selections. For each category below four stories:

1. Consider final clusters outside the initial classification pool.
2. Require no skip reason, evidence score at least `config.enrichment.minimum_story_evidence_score`, and a matching feed hint.
3. Take at most the next 10 ranked candidates per underfilled category.
4. Deduplicate the union by story identity.
5. Run one additional classification call when classification capability is enabled; otherwise run the deterministic fallback.
6. Apply assignments and rerun category selection once.

Backfill does not fetch pages, rerun the quality gate, rebuild clusters, or lower thresholds. The run ends after this single pass even when a section remains underfilled.

### Diagnostics

Extend `PipelineDiagnostics` with these exact maps:

- `fetched_articles_by_feed_hint`
- `preliminary_clusters_by_feed_hint`
- `enrichment_clusters_by_feed_hint`
- `classification_pool_by_feed_hint`
- `history_suppressed_by_feed_hint`
- `classified_clusters_by_category`
- `insufficient_context_by_feed_hint`
- `backfill_candidates_by_category`
- `selected_stories_by_category`
- `underfilled_reason_by_category`

Every map contains all five category keys. Post-classification maps additionally permit `unclassified`. A dual-tag cluster increments both applicable feed-hint counters, including rejection counts, but only one classified-category counter.

`underfilled_reason_by_category` values are one of:

- empty string when at least four stories publish;
- `not_enough_evidence_qualified_candidates`;
- `classification_moved_candidates_elsewhere`;
- `history_suppressed_candidates`;
- `source_diversity_cap`.

`print_diagnostics()` prints pre-classification feed-hint metrics under a `Feed-hint pipeline` heading and true category results under `Classified results`; it never labels hint counts as final categories.

### Source expansion

Add the following proposed direct feeds only after each endpoint passes the precondition check below:

| Source | Feed URL | Feed categories | Source type | Culture lane | Reputation |
| --- | --- | --- | --- | --- | ---: |
| The Hollywood Reporter | `https://www.hollywoodreporter.com/feed/` | `culture` | `culture` | `film_tv` | 0.80 |
| Deadline | `https://deadline.com/feed/` | `culture` | `culture` | `film_tv` | 0.80 |
| Billboard | `https://www.billboard.com/feed/` | `culture` | `culture` | `music` | 0.80 |
| Polygon | `https://www.polygon.com/rss/index.xml` | `culture` | `culture` | `gaming` | 0.75 |

Existing lanes:

- ESPN Top Headlines → `sports`
- Variety → `film_tv`
- The Verge → `gaming`
- Google News Culture → empty lane; discovery-only and never page-enriched

Add matching `article_text` extraction policies for `hollywoodreporter.com`, `deadline.com`, `billboard.com`, and `polygon.com`.

An endpoint is enabled only if a 30-hour live sample satisfies all of these conditions:

- HTTP response and XML parsing succeed;
- at least five recent entries are returned;
- at least 60% of entries have nonempty, non-title-duplicate `best_available_text`;
- timestamps parse into the configured lookback window;
- one permitted article page produces either `extracted` or at least 300 characters of rich feed content.

If an endpoint fails, leave it out of `sources.toml`; do not replace it with a broad Google News query in this change.

### Culture impact scoring decision

Do not add Culture keyword bonuses in the initial rollout. After seven live runs, calculate:

- median `classification_pool_by_feed_hint.culture`;
- median `classified_clusters_by_category.culture`;
- median `selected_stories_by_category.culture`.

Only open a follow-up scoring change if the median Culture classification-pool count remains below 10 after quotas. Candidate scarcity before classification is the only accepted trigger. This avoids adding an uncalibrated keyword boost when quota and source fixes already solve the problem.

## Implementation steps

### Step 0 — Preserve the working tree and confirm baseline

**Read-only commands:**

```bash
cd /Users/raymondwang/PersonalProjects/NewsAgent
git status --short
python3 -m pytest -q
git diff --check
```

**Expected:** `183 passed`; no whitespace errors. Existing unrelated modifications, especially `data/story_history.json` and date-stamped audit files, remain untouched.

### Step 1 — Add category-health diagnostics before behavior changes

**Files:**

- `/Users/raymondwang/PersonalProjects/NewsAgent/src/news_agent/models.py`
- `/Users/raymondwang/PersonalProjects/NewsAgent/src/news_agent/pipeline.py`
- `/Users/raymondwang/PersonalProjects/NewsAgent/src/news_agent/cli.py`
- `/Users/raymondwang/PersonalProjects/NewsAgent/tests/test_pipeline.py`
- `/Users/raymondwang/PersonalProjects/NewsAgent/tests/test_cli.py`

Add the diagnostic maps defined above with `field(default_factory=dict)`. Add pure helpers `cluster_feed_hints()`, `count_clusters_by_feed_hint()`, and `count_clusters_by_category()`. Populate metrics at fetch, preliminary-cluster, enrichment-selection, history, classification, evidence-gate, backfill, and final-selection boundaries.

Tests:

- `test_cluster_feed_hints_uses_union_and_canonical_order`
- `test_cluster_feed_hints_counts_dual_tag_cluster_in_both_hint_buckets`
- `test_category_diagnostics_keep_unclassified_separate`
- `test_cli_diagnostics_labels_feed_hints_separately_from_categories`

**Verify:**

```bash
python3 -m pytest tests/test_pipeline.py tests/test_cli.py -q
```

**Expected:** all targeted tests pass and existing CLI output remains unchanged unless `--show-diagnostics` is present.

**Commit checkpoint:** `feat(diagnostics): report category pipeline health`

### Step 2 — Split OpenAI mode into explicit capabilities

**Files:**

- `/Users/raymondwang/PersonalProjects/NewsAgent/src/news_agent/models.py`
- `/Users/raymondwang/PersonalProjects/NewsAgent/src/news_agent/cli.py`
- `/Users/raymondwang/PersonalProjects/NewsAgent/src/news_agent/pipeline.py`
- `/Users/raymondwang/PersonalProjects/NewsAgent/src/news_agent/classify.py`
- `/Users/raymondwang/PersonalProjects/NewsAgent/src/news_agent/draft.py`
- `/Users/raymondwang/PersonalProjects/NewsAgent/tests/test_cli.py`
- `/Users/raymondwang/PersonalProjects/NewsAgent/tests/test_pipeline.py`
- `/Users/raymondwang/PersonalProjects/NewsAgent/tests/test_classify.py`
- `/Users/raymondwang/PersonalProjects/NewsAgent/tests/test_draft.py`

Add `OpenAIMode = Literal["full", "classify-only", "off"]` and immutable `OpenAICapabilities`. Replace downstream string checks with `use_openai: bool`. Keep mode resolution only in the CLI/pipeline boundary and implement the capability matrix exactly as documented.

Tests:

- `test_full_mode_enables_quality_classification_and_drafting`
- `test_classify_only_enables_quality_and_classification_but_not_drafting`
- `test_off_mode_disables_all_openai_calls`
- `test_no_openai_drafting_alias_selects_classify_only`
- `test_conflicting_openai_aliases_are_rejected`

**Verify:**

```bash
python3 -m pytest tests/test_cli.py tests/test_pipeline.py tests/test_classify.py tests/test_draft.py tests/test_quality_gate.py -q
```

**Commit checkpoint:** `feat(pipeline): separate OpenAI classification and drafting modes`

### Step 3 — Reserve evidence-qualified classification candidates

**Files:**

- `/Users/raymondwang/PersonalProjects/NewsAgent/src/news_agent/pipeline.py`
- `/Users/raymondwang/PersonalProjects/NewsAgent/tests/test_pipeline.py`

Replace `select_classification_candidates()` with the exact global-first, round-robin reserve algorithm defined above. Constants are `GLOBAL_CLASSIFICATION_POOL_SIZE = 30`, `CATEGORY_CLASSIFICATION_RESERVE = 10`, and `MAX_CLASSIFICATION_POOL_SIZE = 80`.

Tests:

- `test_classification_pool_filters_below_threshold_before_reserving`
- `test_classification_pool_includes_ten_available_culture_hints_despite_finance_dominance`
- `test_classification_pool_dual_hint_cluster_counts_for_both_but_appears_once`
- `test_classification_pool_global_first_then_round_robin_order`
- `test_classification_pool_never_exceeds_eighty`

**Verify:**

```bash
python3 -m pytest tests/test_pipeline.py -q
```

**Commit checkpoint:** `feat(selection): reserve evidence-qualified category candidates`

### Step 4 — Reserve enrichment clusters and stop spending on aggregator dead ends

**Files:**

- `/Users/raymondwang/PersonalProjects/NewsAgent/src/news_agent/models.py`
- `/Users/raymondwang/PersonalProjects/NewsAgent/src/news_agent/config.py`
- `/Users/raymondwang/PersonalProjects/NewsAgent/src/news_agent/enrichment.py`
- `/Users/raymondwang/PersonalProjects/NewsAgent/config/sources.toml`
- `/Users/raymondwang/PersonalProjects/NewsAgent/tests/test_config.py`
- `/Users/raymondwang/PersonalProjects/NewsAgent/tests/test_enrichment.py`

Add the enrichment quota fields and implement the cluster selection and two-pass direct-policy article scheduler exactly as defined above. Remove Google News from page-attempt eligibility; keep its feed content available.

Tests:

- `test_enrichment_pool_reserves_eight_available_culture_clusters`
- `test_enrichment_pool_dual_hint_cluster_is_deduplicated`
- `test_enrichment_scheduler_gives_each_cluster_one_attempt_before_seconds`
- `test_enrichment_scheduler_skips_aggregator_only_urls_without_spending_budget`
- `test_enrichment_scheduler_prefers_policy_covered_article_over_aggregator_article`
- `test_enrichment_scheduler_respects_fifty_page_limit`

**Verify:**

```bash
python3 -m pytest tests/test_config.py tests/test_enrichment.py -q
```

**Commit checkpoint:** `feat(enrichment): reserve category coverage within page budget`

### Step 5 — Make deterministic classification signal-first and balanced on true ties

**Files:**

- `/Users/raymondwang/PersonalProjects/NewsAgent/src/news_agent/classify.py`
- `/Users/raymondwang/PersonalProjects/NewsAgent/config/sources.toml`
- `/Users/raymondwang/PersonalProjects/NewsAgent/tests/test_classify.py`

Add the source-type mapping, change The Verge to `mixed_tech_culture`, and make fallback classification stateful within one ranked batch only for the final balance tie-break. Do not inspect title keywords.

Tests:

- `test_fallback_majority_feed_vote_beats_balance`
- `test_fallback_source_type_breaks_equal_feed_vote`
- `test_fallback_balance_breaks_only_remaining_true_tie`
- `test_fallback_category_order_breaks_equal_balance`
- `test_fallback_identical_ranked_inputs_are_repeatable`
- `test_fallback_does_not_classify_untagged_cluster_from_title_keywords`

**Verify:**

```bash
python3 -m pytest tests/test_classify.py -q
```

**Commit checkpoint:** `fix(classification): balance deterministic category ties`

### Step 6 — Add Culture lanes and the dedicated selector

**Files:**

- `/Users/raymondwang/PersonalProjects/NewsAgent/src/news_agent/models.py`
- `/Users/raymondwang/PersonalProjects/NewsAgent/src/news_agent/config.py`
- `/Users/raymondwang/PersonalProjects/NewsAgent/src/news_agent/fetch.py`
- `/Users/raymondwang/PersonalProjects/NewsAgent/src/news_agent/classify.py`
- `/Users/raymondwang/PersonalProjects/NewsAgent/src/news_agent/pipeline.py`
- `/Users/raymondwang/PersonalProjects/NewsAgent/src/news_agent/scoring.py`
- `/Users/raymondwang/PersonalProjects/NewsAgent/config/sources.toml`
- `/Users/raymondwang/PersonalProjects/NewsAgent/tests/test_config.py`
- `/Users/raymondwang/PersonalProjects/NewsAgent/tests/test_fetch.py`
- `/Users/raymondwang/PersonalProjects/NewsAgent/tests/test_classify.py`
- `/Users/raymondwang/PersonalProjects/NewsAgent/tests/test_pipeline.py`
- `/Users/raymondwang/PersonalProjects/NewsAgent/tests/test_scoring.py`

Add lane fields, strict-schema behavior, fallback voting, assignment propagation, and `top_for_culture()` exactly as defined above. `select_unique_category_clusters()` calls `top_for_culture()` only for Culture and retains `top_for_category()` for the other four categories.

Tests:

- `test_config_parses_feed_culture_lane`
- `test_fetch_copies_feed_culture_lane_to_article`
- `test_strict_classifier_schema_requires_lane_for_every_assignment`
- `test_non_culture_assignment_clears_lane`
- `test_fallback_lane_uses_feed_lane_vote`
- `test_culture_selector_prefers_three_distinct_lanes`
- `test_culture_selector_hard_caps_primary_source_at_two`
- `test_culture_selector_relaxes_lane_preference_only_to_reach_four`
- `test_culture_selector_never_selects_below_evidence_threshold`

**Verify:**

```bash
python3 -m pytest tests/test_config.py tests/test_fetch.py tests/test_classify.py tests/test_pipeline.py tests/test_scoring.py -q
```

**Commit checkpoint:** `feat(culture): diversify briefing stories by editorial lane`

### Step 7 — Add one-pass evidence-qualified backfill

**Files:**

- `/Users/raymondwang/PersonalProjects/NewsAgent/src/news_agent/pipeline.py`
- `/Users/raymondwang/PersonalProjects/NewsAgent/tests/test_pipeline.py`

Add `select_backfill_candidates()` and one post-classification pass using the rules above. Backfill must call neither `enrich_clusters()` nor `cluster_articles()`.

Tests:

- `test_backfill_runs_once_for_category_below_four`
- `test_backfill_uses_only_matching_feed_hints`
- `test_backfill_excludes_below_threshold_and_history_suppressed_clusters`
- `test_backfill_deduplicates_candidates_shared_by_underfilled_categories`
- `test_backfill_does_not_call_enrichment_or_reclustering`
- `test_backfill_stops_after_one_pass_when_section_remains_underfilled`

**Verify:**

```bash
python3 -m pytest tests/test_pipeline.py -q
```

**Commit checkpoint:** `feat(selection): backfill underfilled categories once`

### Step 8 — Validate and add direct Culture feeds

**Files:**

- `/Users/raymondwang/PersonalProjects/NewsAgent/config/sources.toml`
- `/Users/raymondwang/PersonalProjects/NewsAgent/tests/test_config.py`
- `/Users/raymondwang/PersonalProjects/NewsAgent/tests/test_fetch.py`

Run these exact checks from the repository root:

```bash
PYTHONPATH=src .venv/bin/python -c "from news_agent.fetch import fetch_feed; from news_agent.models import FeedConfig; f=FeedConfig(name='The Hollywood Reporter',url='https://www.hollywoodreporter.com/feed/',reputation=0.8,categories=('culture',),source_type='culture'); a=fetch_feed(f); print(len(a), sum(bool(x.best_available_text) for x in a), sum(x.best_available_text.casefold() != x.title.casefold() for x in a))"
PYTHONPATH=src .venv/bin/python -c "from news_agent.fetch import fetch_feed; from news_agent.models import FeedConfig; f=FeedConfig(name='Deadline',url='https://deadline.com/feed/',reputation=0.8,categories=('culture',),source_type='culture'); a=fetch_feed(f); print(len(a), sum(bool(x.best_available_text) for x in a), sum(x.best_available_text.casefold() != x.title.casefold() for x in a))"
PYTHONPATH=src .venv/bin/python -c "from news_agent.fetch import fetch_feed; from news_agent.models import FeedConfig; f=FeedConfig(name='Billboard',url='https://www.billboard.com/feed/',reputation=0.8,categories=('culture',),source_type='culture'); a=fetch_feed(f); print(len(a), sum(bool(x.best_available_text) for x in a), sum(x.best_available_text.casefold() != x.title.casefold() for x in a))"
PYTHONPATH=src .venv/bin/python -c "from news_agent.fetch import fetch_feed; from news_agent.models import FeedConfig; f=FeedConfig(name='Polygon',url='https://www.polygon.com/rss/index.xml',reputation=0.75,categories=('culture',),source_type='culture'); a=fetch_feed(f); print(len(a), sum(bool(x.best_available_text) for x in a), sum(x.best_available_text.casefold() != x.title.casefold() for x in a))"
```

Each command prints `total nonempty nonduplicate`. Require `total >= 5`, `nonempty / total >= 0.60`, and `nonduplicate / total >= 0.60`. Then inspect parsed timestamps and one article-page enrichment in a temporary test harness. Enable only endpoints satisfying every source-expansion criterion. Add the matching extraction policy in the same commit as each feed; a feed without a policy must not be merged.

Tests:

- `test_default_config_culture_feeds_have_valid_lanes`
- `test_every_direct_culture_feed_domain_has_extraction_policy`
- `test_culture_has_overlapping_film_tv_sources`
- `test_culture_has_music_gaming_and_sports_discovery`

**Verify:**

```bash
python3 -m pytest tests/test_config.py tests/test_fetch.py -q
```

**Commit checkpoint:** `feat(sources): broaden direct Culture reporting coverage`

### Step 9 — Full regression and live shadow verification

Run without sending and without writing repository audit/history files:

```bash
python3 -m pytest -q
git diff --check
```

Create a fixed temporary working directory, then run three isolated smoke tests. Each Python command explicitly loads the repository `.env`, while relative audit logs are written beneath `/private/tmp/news-agent-culture-shadow`:

```bash
mkdir -p /private/tmp/news-agent-culture-shadow
cd /private/tmp/news-agent-culture-shadow
/Users/raymondwang/PersonalProjects/NewsAgent/.venv/bin/python -c "from pathlib import Path; from news_agent.env import load_dotenv; load_dotenv(Path('/Users/raymondwang/PersonalProjects/NewsAgent/.env')); from news_agent.cli import main; main(['--config','/Users/raymondwang/PersonalProjects/NewsAgent/config/sources.toml','--watchlist','/Users/raymondwang/PersonalProjects/NewsAgent/config/watchlist.json','--history-path','/private/tmp/news-agent-culture-shadow/full-history.json','--dry-run','--openai-mode','full','--ignore-history','--show-diagnostics'])"
/Users/raymondwang/PersonalProjects/NewsAgent/.venv/bin/python -c "from pathlib import Path; from news_agent.env import load_dotenv; load_dotenv(Path('/Users/raymondwang/PersonalProjects/NewsAgent/.env')); from news_agent.cli import main; main(['--config','/Users/raymondwang/PersonalProjects/NewsAgent/config/sources.toml','--watchlist','/Users/raymondwang/PersonalProjects/NewsAgent/config/watchlist.json','--history-path','/private/tmp/news-agent-culture-shadow/classify-only-history.json','--dry-run','--openai-mode','classify-only','--ignore-history','--show-diagnostics'])"
/Users/raymondwang/PersonalProjects/NewsAgent/.venv/bin/python -c "from pathlib import Path; from news_agent.env import load_dotenv; load_dotenv(Path('/Users/raymondwang/PersonalProjects/NewsAgent/.env')); from news_agent.cli import main; main(['--config','/Users/raymondwang/PersonalProjects/NewsAgent/config/sources.toml','--watchlist','/Users/raymondwang/PersonalProjects/NewsAgent/config/watchlist.json','--history-path','/private/tmp/news-agent-culture-shadow/off-history.json','--dry-run','--openai-mode','off','--ignore-history','--show-diagnostics'])"
```

Expected for all modes:

- no story below evidence score `1.2` publishes;
- Culture has at least four stories when diagnostics show at least four evidence-qualified candidates;
- no publisher contributes more than two Culture stories;
- diagnostics clearly separate feed hints from classified categories.

Expected for `classify-only`:

- category assignments are model-generated;
- every paragraph has `draft_status == "fallback_disabled"`;
- no OpenAI drafting request occurs.

Expected for `off`:

- no OpenAI request occurs;
- deterministic results are stable for identical fixture inputs.

**Commit checkpoint:** `test(culture): verify consistent category coverage`

## Rollout

1. Merge Steps 1–7 with the new behavior enabled and source additions limited to feeds that passed validation.
2. Run daily with `--openai-mode classify-only --show-diagnostics` for seven sends; this isolates category quality from generative drafting quality while retaining low-cost editorial classification.
3. Record the nine diagnostic maps for every run.
4. After seven runs, accept the rollout if median Culture output is at least four, no below-threshold story published, no publisher exceeded two Culture slots, and no run exceeded three stories in one lane during minimum-fill relaxation.
5. If Culture remains underfilled because `classification_pool_by_feed_hint.culture < 10`, open the contingent Culture-impact-scoring follow-up. If the pool is healthy but classification moves stories elsewhere, revise sources or category guidelines instead of manipulating scores.

## Rollback

- Every behavioral phase has its own commit checkpoint. Revert the newest checkpoint first; no database or persistent schema migration exists.
- Disabling new direct feeds requires only removing their `[[feeds]]` blocks; retain their extraction policies safely or remove both in the same revert.
- If quota selection increases runtime or cost unexpectedly, set `reserved_clusters_per_category = 0` and restore `max_clusters_per_run = 40` while keeping diagnostics active.
- If `classify-only` behaves incorrectly, use `--openai-mode full` or `--openai-mode off`; neither depends on data migration.
- Do not roll back by deleting or resetting user-owned `data/*.json` changes.

## Final verification checklist

- [ ] Full suite passes from the repository root.
- [ ] All three OpenAI modes satisfy their capability matrix.
- [ ] Evidence filtering precedes classification admission.
- [ ] Classification pool includes ten Culture-hinted candidates when ten qualified candidates exist.
- [ ] Enrichment pool includes eight Culture-hinted clusters when eight exist.
- [ ] Aggregator-only URLs consume zero page attempts.
- [ ] Culture lane is required in strict classifier output and empty for non-Culture assignments.
- [ ] Culture selector caps each publisher at two and prefers at least three lanes.
- [ ] Backfill runs once and performs no enrichment/reclustering.
- [ ] Diagnostics label feed hints and true categories separately.
- [ ] New Culture feeds and extraction policies are paired.
- [ ] Seven-run rollout metrics meet the success criteria before considering Culture keyword scoring.
