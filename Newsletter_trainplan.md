# Newsletter Training and Quality-Gate Review Plan

**Status:** Proposed; nothing here is implemented. This designs a general-news (five-category briefing) evaluation system that mirrors the already-implemented Watchlist review system, so newsletter relevance can be *measured* before it is *changed*.

**Non-goal, stated up front:** this system does not self-train. Labels feed measurement, locked regression fixtures, and human-approved quality-gate changes. No weight, threshold, or prompt is ever updated automatically from a label.

---

## 1. Why this exists

The general-news side of NewsAgent currently produces, per briefing day:

| Item | Volume | Where it lands today |
| --- | --- | --- |
| Sent stories | up to 25 (`deck_target`, `config/sources.toml:24`) | `editions` / `edition_stories` in `data/email_state.db`, keyed by **category message title only** (`mailer/service.py:56`, `:89`) |
| Skipped clusters | ~130–150 (measured 2026-07-31, 08-03, 08-04) | `data/skipped_stories_<date>.json`, rewritten each run |
| Hard quality-gate rejections | ~70–85 | `data/quality_gate_rejections_<date>.json` |
| Category assignments | ~40–80 | `data/category_assignments_<date>.json` |

Two blockers follow:

1. **Sent stories are not addressable.** `edition_stories.story_id` for general news is the category message title (`"Business and technology"`) — one row per *category*, not per story (`mailer/service.py:89`). There is nothing to attach a judgment to.
2. **Filtered candidates are not durable.** The JSON logs are per-day, overwritten, carry no stable identity or review status, and collapse very different filters into one string: `insufficient story context` is 75–80% of all skips (`skipped_log.py:34-52`).

The Watchlist already solved the equivalent problem: `watchlist_events` + `watchlist_diagnostics` persist what happened, `watchlist_adjudications` stores immutable write-once verdicts, `watchlist_benchmark_events` holds independently sourced ground truth that may not be produced by relabelling retrieved items, and `--review-watchlist-evaluations` / `--review-watchlist-benchmark` present items one at a time (`cli.py:612-690`). This plan builds the same three-part structure for general news.

---

## 2. Watchlist → Newsletter mapping

| Watchlist concept | Today | Newsletter analogue (this plan) |
| --- | --- | --- |
| `watchlist_events` (what rendered) | `mailer/state.py:203` | `newsletter_candidates` rows with `disposition='sent'` |
| `watchlist_diagnostics` (per ticker-day outcome) | `mailer/state.py:222` | `newsletter_candidates` rows with `disposition='filtered'` + `newsletter_runs` |
| `watchlist_adjudications` (immutable verdicts) | `mailer/state.py:283` | `newsletter_adjudications` |
| `watchlist_benchmark_events` (independent frame) | `mailer/state.py:269` | `newsletter_manual_examples` |
| `--review-watchlist-evaluations` | `cli.py:652` | `--review-newsletter-evaluations` |
| `--review-watchlist-benchmark` | `cli.py:612` | `--review-newsletter-examples` |
| `--watchlist-benchmark-import` | `cli.py:186` | `--newsletter-example-import` / `--newsletter-example-add` |
| Gate A metric aggregation (`_gate_metrics`) | `mailer/state.py:677` | `newsletter_metrics()` + `--newsletter-evaluation-report` |
| Retention purge | `mailer/state.py:1143` | newsletter clauses in the same pass; labels exempt |
| Production/test isolation | `edition_kind`, `persist_watchlist_state` (`mailer/service.py:84`) | `persist_review_state` on the same switch |

Deliberate divergences from the Watchlist design:

- **No email-visible surface.** The Watchlist adds count-only footers and a weekly progress notice (`gate_progress_notice`, `mailer/state.py:1000`). The newsletter review system is entirely local: no content change, no admin footer, no new email text. Reviewer prompting is CLI-only.
- **No gate, no halt.** Gate A can halt scheduled work (`scheduled_work_allowed`, `mailer/state.py:783`). Newsletter metrics are reporting-only; a bad number produces a report, never a halt and never a suppressed send.
- **Sampling, not census.** The Watchlist adjudicates every rendered event across 9 tickers. General news filters ~215 candidates/day, so filtered-side review is a *stratified sample* with recorded strata and population weights.

---

## 3. Data model

All state lives in the existing SQLite store `data/email_state.db` (`mailer/state.py:19`), migrated to `SCHEMA_VERSION = 4`. No new `data/*.json` outputs; the existing daily JSON logs keep their current format and behaviour.

### 3.1 `newsletter_runs`

One row per completed production build.

| Field | Type | Notes |
| --- | --- | --- |
| `run_id` | TEXT PK | `uuid4().hex`, matching `mailer/service.py:98` |
| `briefing_date` | TEXT | `briefing_today().isoformat()` |
| `edition_id` | INTEGER NULL | FK `editions(id)`; NULL if no edition was produced |
| `pipeline_version` | TEXT | `calibration_version` (`models.py:124`) + quality-gate config hash + classifier/drafting model ids |
| `config_hash` | TEXT | SHA-256 over the `AgentConfig` fields that affect selection |
| `deck_target` | INTEGER | `config.importance.deck_target` |
| `candidates_total` | INTEGER | rows written for this run |
| `openai_mode` | TEXT | `full` / `classify-only` / `off` — an `off` run is not comparable |
| `created_at` | TEXT | UTC ISO-8601 |

### 3.2 `newsletter_candidates`

One row per cluster that reached classification/selection, plus one per hard-rejected article, per run.

| Field | Type | Notes |
| --- | --- | --- |
| `candidate_id` | TEXT PK | immutable run occurrence: `sha256(run_id + candidate_kind + canonical_url_or_content_hash)` |
| `story_key` | TEXT | cross-run comparison key: SHA-256 of sorted normalized source URLs; when no URL exists, SHA-256 of publisher + normalized title + published date |
| `candidate_kind` | TEXT | `cluster` or `hard_rejected_article`; prevents an article and a later cluster from sharing an identity |
| `run_id` | TEXT | FK `newsletter_runs(run_id)` |
| `briefing_date` | TEXT | indexed |
| `disposition` | TEXT | `selected` \| `filtered`; selected means chosen for an edition, not yet delivered |
| `delivery_state` | TEXT | `prepared` \| `smtp_accepted` \| `failed` \| `indeterminate`; only accepted rows count as reader-exposed sent stories |
| `filter_stage` | TEXT | `''` when sent; else `quality_gate_hard_reject`, `low_content_quality`, `evidence_gate`, `history_stale`, `source_confirmation`, `duplicate_gate_merged`, `category_full`, `below_threshold`, `unclassified` |
| `filter_reason` | TEXT | the verbatim legacy `skip_reason` / hard-reject string, preserved for traceability |
| `review_stratum` | TEXT | `sent`, `near_miss`, `mid`, `deep_filtered`, `hard_reject` (§6.2) |
| `headline` | TEXT | `cluster.title` |
| `category` | TEXT | assigned category or `uncategorized` |
| `culture_lane` | TEXT | `cluster.culture_lane` |
| `canonical_url` | TEXT | normalised `cluster.urls[0]` |
| `all_urls_json` | TEXT | JSON array, ≤5 |
| `sources_json` | TEXT | JSON array of `cluster.sources` |
| `source_count` | INTEGER | |
| `summary_excerpt` | TEXT | first 600 chars of `representative_summary` — reviewer context only |
| `delivered_paragraph` | TEXT | for `sent`: the post-compression paragraph actually emailed; `''` otherwise |
| `total_score`, `importance`, `evidence_score`, `quality_score`, `content_quality_penalty` | REAL | from `StoryCluster` (`models.py:221`) |
| `llm_importance` | INTEGER NULL | `CategoryAssignment.llm_importance` |
| `deck_rank` | INTEGER NULL | selection order for sent rows |
| `selection_phase` | TEXT | `floor` \| `remainder` \| `big_day` \| `''` (`pipeline.py:225-290`) |
| `is_update` | INTEGER | `cluster.is_update` |
| `merged_from_json` | TEXT | `cluster.merged_from` |
| `excerpt_purged_at` | TEXT NULL | set when §8.2 retention nulls the text fields |
| `created_at` | TEXT | |

Indexes: `(briefing_date, disposition)`, `(story_key)`, `(review_stratum, disposition)`, `(edition_id, delivery_state)`.

Candidate occurrences are append-only. A same-day rebuild creates a new `run_id` and new occurrence rows; labels remain attached to the exact occurrence reviewed. The report may compare rows sharing `story_key`, but never silently transfers a label to a rewritten or newly sourced story.

### 3.3 `newsletter_adjudications`

Immutable reviewer verdicts, mirroring `watchlist_adjudications` (`mailer/state.py:283`) including its `UNIQUE(subject_type, subject_id)` write-once behaviour.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | INTEGER PK | |
| `subject_type` | TEXT | `sent_story` \| `filtered_candidate` \| `manual_example` |
| `subject_id` | TEXT | `candidate_id`, or `example:<id>` |
| `verdict` | TEXT | `relevant` \| `irrelevant` \| `unclear` |
| `reason_code` | TEXT | required for `relevant` and `irrelevant`; empty only for `unclear`; selected from the fixed list in §6.3 |
| `reviewer_note` | TEXT | free text, default `''` |
| `label_schema_version` | TEXT | rubric version, e.g. `newsletter-rubric-v1` |
| `pipeline_version` | TEXT | copied from the run, so a label is attributable to the code that produced the candidate |
| `created_at` | TEXT | |

`record_newsletter_label()` validates `(subject_type, verdict)` pairs exactly as `record_adjudication` does (`mailer/state.py:1121-1129`).

### 3.4 `newsletter_manual_examples`

Independently identified stories the reviewer believes *should* have been in the briefing — the analogue of `watchlist_benchmark_events` (`mailer/state.py:269`), under the same rule from the Watchlist plan §9.4: **it may not be built solely by relabelling items NewsAgent already retrieved.**

| Field | Type | Notes |
| --- | --- | --- |
| `id` | INTEGER PK | |
| `example_date` | TEXT | the briefing date it should have appeared in |
| `headline` | TEXT | |
| `source_url` | TEXT | HTTPS required |
| `publisher` | TEXT | |
| `expected_category` | TEXT | one of the five categories, or `''` if unknown |
| `why_it_matters` | TEXT | required, non-empty |
| `provenance` | TEXT | `manual_recall`, `external_outlet`, `reader_report`, `historical_case` — never `pipeline_relabel` |
| `verdict` | TEXT | `pending` \| `relevant` \| `irrelevant` \| `unclear` |
| `matched_candidate_id` | TEXT NULL | resolved link (§6.4) |
| `match_state` | TEXT | `unmatched` \| `matched_sent` \| `matched_filtered` \| `not_retrieved` |
| `matched_by` | TEXT | `url` \| `manual` |
| `imported_at`, `reviewed_at` | TEXT | |

`UNIQUE(example_date, source_url)` — import rejects duplicates the way `import_benchmark_events` does (`mailer/state.py:931`, tested at `tests/test_watchlist_reliability.py:264`).

### 3.5 Schema version 4 migration

In `mailer/state.py::_migrate` (`:41`), add a `version == 3` branch shaped like the existing `version == 2` branch (`:88`):

1. `self._backup(connection, version=3)` — generalise `_backup_v2` (`:133`) to take a version; writes `data/email_state.db.v3-backup-<UTC stamp>`.
2. `self._create_newsletter_schema(connection)` — `CREATE TABLE IF NOT EXISTS` for all four tables, mirroring `_create_watchlist_schema` (`:149`) so repeat runs self-heal.
3. `INSERT INTO schema_migrations(version=4, applied_at, backup_path)`.
4. `PRAGMA user_version = 4`.

Keep the existing self-heal path: when `version == SCHEMA_VERSION`, also call `_create_newsletter_schema` so databases created mid-development converge (`:45-51`).

**Backfill:** the historical `data/skipped_stories_*.json`, `quality_gate_rejections_*.json`, and `category_assignments_*.json` are **not** backfilled by default — they lack decision-stage events, reliable source-URL keys, scores, deck rank, and links to what was selected. An optional script may import them as clearly non-metric review material only. Deferred to Phase N7.

---

## 4. Where candidates get persisted

Each decision must be captured at the moment it is made, not reconstructed after selection. `quality_gate.py` emits hard-rejection records; each later selection, history, evidence, duplicate, and category-cap step emits its own record with its exact stage and reason. `build_briefing_result` only combines those already-recorded decision events with post-compression paragraph text; `skipped_log.py` remains a human diagnostic and is never used as the source of truth for review metrics.

`pipeline.py` stays storage-agnostic: it builds an in-memory record set and returns it; `EmailService` decides whether to persist.

1. **New module `src/news_agent/newsletter_review.py`** (~250 lines), the general-news counterpart to `watchlist/benchmark.py` + `skipped_log.py`:
   - `@dataclass(frozen=True) CandidateRecord` — one per row in §3.2.
   - `build_candidate_records(context, selected, paragraphs, config, run_id) -> tuple[CandidateRecord, ...]` — combines captured decision events with `story_key`, `filter_stage`, `review_stratum`, `deck_rank`, and `selection_phase`.
   - `DecisionEvent` and `record_decision_event(...)` — called by the actual quality, evidence, history, duplicate, classification, and selection branches. It records the exact branch name and reason before the candidate is discarded. **The legacy `skip_reason` strings and existing JSON logs do not change.**
   - `assign_stratum(record, deck_target) -> str`.
   - `newsletter_metrics(rows) -> NewsletterMetrics` — a pure function over label rows, unit-testable without a database (same shape as `evaluate_gate`, `watchlist/gate.py:52`).
   - `format_metrics_report(metrics) -> str` — the `--newsletter-evaluation-report` body.
   - `load_manual_examples(path, categories) -> tuple[ManualExample, ...]` — import validation, mirroring `load_benchmark_candidates` (`watchlist/benchmark.py:47`).
2. **`pipeline.py`** — `BriefingBuildResult` (`:138`) gains `candidate_records: tuple[CandidateRecord, ...] = ()` and `run_id: str = ""`; `build_briefing_result` populates them. `PipelineContext.all_clusters`, `category_clusters`, `category_assignments`, and the existing `quality_gate_rejections` tuple (`:953`) supply everything needed. No new network or model calls.
3. **`mailer/service.py`** — `prepare_newsletter_edition` gains `candidate_records` and `persist_review_state: bool = not test_revision`. In one SQLite transaction it creates the production edition, writes its `selected` candidate occurrences, and links their `edition_id`. `send_edition` then records each SMTP outcome with `mark_newsletter_delivery_state(edition_id, state)`; the report counts only `smtp_accepted` rows as sent.
4. **`mailer/state.py`** — new methods: `record_newsletter_run`, `pending_newsletter_reviews(scope, limit, days)`, `record_newsletter_label`, `import_newsletter_examples`, `pending_newsletter_examples`, `review_newsletter_example`, `newsletter_review_metrics(days)`, `export_newsletter_labels(days)`, plus newsletter clauses in the retention pass called from `mailer/service.py:378`.
5. **Per-story selected identity.** `prepare_newsletter_edition` writes an addressable `(candidate_id, f"general:{category}")` row for every selected candidate. Keep existing category-message rows. The candidate record is never created by parsing formatted email text.

**Affected files, complete:**

| File | Change |
| --- | --- |
| `src/news_agent/newsletter_review.py` | new module |
| `src/news_agent/mailer/state.py` | schema v4 + migration branch, 8 new methods, retention clauses |
| `src/news_agent/pipeline.py` | build candidate records; extend `BriefingBuildResult` |
| `src/news_agent/mailer/service.py` | thread records through; `persist_review_state`; per-story `edition_stories` rows |
| `src/news_agent/cli.py` | 7 new flags + 3 review loops (§5) |
| `src/news_agent/skipped_log.py` | behaviour unchanged; `skip_reason` reused by the new stage classifier |
| `tests/test_newsletter_review.py` | new (§9) |
| `tests/test_cli.py` | new isolation cases |
| `tests/fixtures/newsletter_labels/*.json` | new locked regression fixtures |
| `scripts/backfill_newsletter_candidates.py` | optional, Phase N7 |
| `docs/decisions.md` | DEC records for §14 decisions, via the `decisiontracker` skill |
| `README.md` | review-workflow section |

---

## 5. CLI surface

All flags follow the argparse conventions at `cli.py:58-152` and the guard style at `cli.py:206-224`: review modes reject `--dry-run`, `--send`, and `--alerts`, never build the pipeline, and exit immediately.

```bash
# One story at a time. Default scope interleaves sent and filtered queues.
news-briefing --review-newsletter-evaluations
news-briefing --review-newsletter-evaluations --review-scope sent
news-briefing --review-newsletter-evaluations --review-scope filtered
news-briefing --review-newsletter-evaluations --review-limit 10 --review-days 14

# Independent "we missed this" examples — added by hand, never by relabelling.
news-briefing --newsletter-example-add
news-briefing --newsletter-example-import path/to/examples.jsonl
news-briefing --review-newsletter-examples

# Measurement and export.
news-briefing --newsletter-evaluation-report --report-days 30
news-briefing --newsletter-labels-export data/review/newsletter_labels.jsonl
```

| Flag | Type | Behaviour |
| --- | --- | --- |
| `--review-newsletter-evaluations` | store_true | Enters the interactive loop and exits |
| `--review-scope` | `all\|sent\|filtered`, default `all` | `all` alternates one sent, one filtered, so every session measures both error directions |
| `--review-limit` | int, default 20 | Max items per session; keeps a sitting to ~10–15 minutes |
| `--review-days` | int, default 30 | Pending-queue lookback |
| `--newsletter-example-add` | store_true | Interactive single-example entry, looping until `done` |
| `--newsletter-example-import` | Path | JSON or JSONL import, validated and deduplicated; prints `Imported N example(s).` |
| `--review-newsletter-examples` | store_true | Adjudicates pending examples and resolves `match_state` |
| `--newsletter-evaluation-report` | store_true | Prints §7 metrics and exits; honours the existing `--report-days` (`cli.py:83`) |
| `--newsletter-labels-export` | Path | Writes a stable-sorted JSONL label export and exits |

### 5.1 Review loop

`_review_newsletter_evaluations(store, scope, limit, days)` in `cli.py`, modelled on `_review_watchlist_evaluations` (`cli.py:652`):

```
[sent 3/20]  2026-08-04 · business · rank 4 · importance 71.2
Fed holds rates, signals one cut before year-end
Sources: Reuters, AP, Bloomberg (3)
https://example.com/...
> Delivered: <the paragraph as emailed>

Was this worth the reader's time? [relevant/irrelevant/unclear/skip/quit]:
```

```
[filtered 4/20]  2026-08-04 · uncategorized · stratum near_miss
filter: source_confirmation (no reliable source confirmation)
Regulator opens probe into ...
Sources: The Verge (1) · score 9.4 · importance 41.0
https://example.com/...
> Excerpt: <600 chars>

Should this have been in the briefing? [relevant/irrelevant/unclear/skip/quit]:
```

Rules:
- **One item at a time.** No batch mode, no bulk edit.
- `skip` leaves the item pending without writing a verdict; `quit` exits immediately without recording the current item.
- **Write-once.** A repeat verdict hits the same `UNIQUE` constraint as the Watchlist path (`mailer/state.py:1140`); the CLI prints `already reviewed` rather than surfacing a traceback.
- **Randomized review batches.** A command creates a dated batch from a frozen candidate population, a saved SHA-256 seed, and saved per-stratum population counts. It chooses uniformly at random within each stratum. A batch is shown oldest-first only *after* its randomized membership is fixed.
- **Required reason code** for every `relevant` or `irrelevant` label. `unclear` requires no reason. Invalid or empty clear-label reasons leave the item pending.
- **No network, no model calls, no email.** The review commands never construct a pipeline or reach a send path.

---

## 6. Review method

### 6.1 The two questions

Only two, and deliberately different:

- **Sent story:** *"Was this worth the reader's time in this briefing?"* — judged against `docs/Goal/newsagent-goal.md`: significance, trustworthy evidence, clarity, non-duplication. A story that is true but trivial, or a rewritten headline with no substance, is `irrelevant`.
- **Filtered candidate:** *"Should this have been in the briefing?"* — `relevant` means the reviewer would have wanted it *given the other 25 stories that day*, not merely that it is real news. This framing is load-bearing: without it nearly every filtered wire item is technically "relevant" and the false-negative rate becomes meaningless.

**Settled 2026-08-04:** A filtered candidate is relevant only if it deserved a place in that day's finished newsletter, considering the other stories selected that day. Truth alone is not enough.

`unclear` is a first-class answer, not a failure. It is excluded from both numerator and denominator and reported separately; a persistently high `unclear` share means the rubric, not the pipeline, needs work (§7.4).

### 6.2 Strata and sampling

~215 filtered candidates/day against ~25 sent makes a census impossible. `assign_stratum` buckets them:

| Stratum | Definition | Share of filtered review slots |
| --- | --- | --- |
| `near_miss` | classified, passed the evidence gate, filtered by `category_full` / `below_threshold` / `duplicate_gate_merged`, or within the top `deck_target + 15` by importance | 50% |
| `mid` | classified but failed `source_confirmation` or `history_stale` | 30% |
| `deep_filtered` | never classified (`unclassified`), or failed `evidence_gate` / `low_content_quality` | 15% |
| `hard_reject` | `quality_gate_hard_reject` (`quality_gate.py:135`) | 5% |

**Settled 2026-08-04:** Keep `deep_filtered` and `hard_reject` candidates in the initial review sample at their combined 20% share. They are low-frequency review material but are necessary to detect serious strict-filter mistakes.

`newsletter_review_batches` stores `batch_id`, `seed`, `window_start`, `window_end`, `stratum_population_json`, `stratum_sample_json`, and `created_at`. Sampling is uniform without replacement within each stratum. Population weights are therefore valid for the frozen batch frame; incomplete batches report coverage and confidence limits rather than pretending labels are a census. A disagreement-focused queue is a separate non-estimating diagnostic mode and never contributes to the population FN estimate.

### 6.3 Reason codes

Fixed and small; extensible only by decision record.

*Sent → irrelevant:* `trivial`, `duplicate_of_other_story`, `wrong_category`, `thin_evidence`, `stale`, `promotional`, `misleading_framing`, `compression_lost_meaning`.
*Filtered → relevant:* `gate_too_strict`, `evidence_underrated`, `single_source_but_credible`, `classifier_missed_category`, `wrongly_merged`, `stale_rule_too_aggressive`.

**Settled 2026-08-04:** Every `relevant` and `irrelevant` label requires one of these reason codes. `unclear` remains reason-free.

Reason-code frequency is what actually drives Phase N8 change proposals; the aggregate rate only tells you *whether* to act. `compression_lost_meaning` links this review loop to the existing compression audits (`compression_audit.py:43`) without duplicating them.

### 6.4 Manual examples and matching

`--review-newsletter-examples` resolves each example against the candidate table before asking for a verdict:

1. Normalised-URL match against `canonical_url` / `all_urls_json` → `matched_by='url'`.
2. Else show the top five same-day candidates by title similarity as suggestions only. The reviewer explicitly selects one candidate ID or answers `none` → `matched_by='manual'`.

No hash is calculated from a manual example's headline or rationale, and no fuzzy match is accepted automatically.

Resulting `match_state`:
- `matched_sent` — the system *did* send it; the example was a reviewer false alarm, which is itself useful calibration.
- `matched_filtered` — a genuine gate miss, linked to the filtered candidate.
- `not_retrieved` — never in the corpus at all. This is a **sourcing** problem (feed configuration), not a gate problem, and must not be counted against the quality gate.

That last distinction is the entire reason manual examples exist independently: a filtered-candidate label can only find misses among things that were already fetched.

---

## 7. Metrics

Computed by `newsletter_metrics()` over a `--report-days` window, keyed by `pipeline_version` so pre-change and post-change labels are never silently pooled. Every percentage prints with its counts.

### 7.1 False-positive rate (precision failure on what was sent)

```
FP_rate = irrelevant_sent / (relevant_sent + irrelevant_sent)
```
`unclear` excluded. Reported per-category as well as overall — one weak category hides easily inside a healthy aggregate.

### 7.2 False-negative rate (recall failure among what was filtered)

Per stratum:
```
FN_s = relevant_filtered_s / (relevant_filtered_s + irrelevant_filtered_s)
```
Population estimate, weighted by each stratum's share of the *filtered population* (not of the review queue):
```
FN_estimated = Σ_s ( FN_s × N_s / N_filtered_total )
```
Both the per-stratum rates and the weighted estimate print only for a completed randomized batch with saved population counts. Diagnostic queues print counts only, never a population-rate estimate.

### 7.3 Miss rate against manual examples (independent recall)

```
miss_rate = examples(relevant AND match_state != 'matched_sent') / examples(relevant)
```
Split into `matched_filtered` (gate miss — actionable here) and `not_retrieved` (sourcing miss — actionable in feed config, out of scope for the gate).

### 7.4 Health and coverage metrics

- `review_coverage` — labelled fraction of sent stories, of each filtered stratum, and of manual examples, in the window.
- `unclear_share` per subject type. **> 20% means the rubric is under-specified**; fix the rubric before reading any other number.
- `labels_total`, `labels_by_stratum`, `review_days_elapsed`, `labels_per_active_day`.
- `pipeline_version_spread` — distinct pipeline versions in the window; > 1 means the window straddles a change and the headline numbers are not pooled.
- Disagreement breakdown: FP/FN counts grouped by `filter_stage`, by primary source, and by category — the direct input to §11.2.

### 7.5 Minimum denominators before a metric is reported

Modelled on the Watchlist rule that no metric is reported before its own minimum is met (DEC-0016 / DEC-0027 / DEC-0030, `docs/plans/watchlist-retrieval-reliability.md` §9.4):

| Metric | Minimum |
| --- | --- |
| FP rate (overall) | ≥ 40 labelled sent stories |
| FP rate (per category) | ≥ 15 labelled sent stories in that category |
| FN rate (per stratum) | ≥ 25 labelled filtered candidates in that stratum |
| FN estimate (population) | every stratum with weight ≥ 15% meets its own minimum |
| Miss rate | ≥ 15 adjudicated manual examples |

Below the minimum the report prints `not yet reportable — N of M`. This is the single most important discipline here: a 3-of-4 "75% false-positive rate" would otherwise trigger a bad change.

### 7.6 Reviewer effort

At ~20 s per sent story and ~40 s per filtered candidate, the initial denominators (40 sent + 100 filtered + 15 examples) cost roughly **2.5–3.5 hours**, best spread over ~3 weeks at 10–15 min/day. Daily review matters for the same reason it did for the Watchlist: problems surface while there is still time to act.

---

## 8. Isolation, safety, and retention

### 8.1 Production-state isolation

Review state is written **only** on a real production send:

| Path | Writes review state? | Enforcement |
| --- | --- | --- |
| `--dry-run` (any target) | No | only `prepare_newsletter_edition` persists; `render_newsletter` never does |
| `--email-parity` | No | the parity path does not build the newsletter (`cli.py:94`) |
| `--email-rebuild-today` (test resend) | No | `persist_review_state = not test_revision` (`mailer/service.py:79-86`) |
| `--email-resend EDITION_ID` | No | replays a stored edition; no pipeline runs (`cli.py:231`) |
| `--restart-after-gate-failure` | No | already a no-send diagnostic dry run (`cli.py:176-185`) |
| `--activate-watchlist-gate` | No | dry-run-only by construction (`cli.py:163`) |
| `--send` with `--openai-mode off` | Yes, tagged | `newsletter_runs.openai_mode='off'`; excluded from metrics — classification and drafting were deterministic fallbacks |
| `--send --to email` / `--to both` | **Yes** | the only writing path |

**Settled 2026-08-04:** Only rows with `delivery_state='smtp_accepted'` count as sent in reader-facing quality metrics. Selected, failed, indeterminate, and unknown rows remain visible for operational diagnostics but are excluded from the sent-story denominator.

A test resend may *display* review data (its stories appear in the review queue only if a production run already recorded them) but can never create a label, mark an item reviewed, or move a metric. This mirrors the Watchlist invariants already covered by `test_test_delivery_does_not_write_watchlist_sent_history` (`tests/test_watchlist_reliability.py:537`) and `test_production_delivery_starts_watchlist_suppression` (`:548`).

Further isolation properties:
- Review commands take **no build lock** — `needs_build_lock` (`cli.py:36`) stays false for them, so reviewing never blocks the 8:15 AM scheduled send. Label writes are single-statement inserts; SQLite's own locking suffices.
- Review commands never mutate `editions`, `deliveries`, `data/story_history.json`, or any watchlist table.
- **A label is never read by the send path.** `pipeline.py` must not read `newsletter_adjudications`; asserted in test 19.

### 8.2 Retention

Extending `cleanup_watchlist_retention` (`mailer/state.py:1143`), invoked from the same post-delivery hook (`mailer/service.py:378`):

| Data | Retention | Rule |
| --- | --- | --- |
| `summary_excerpt`, `delivered_paragraph` | 30 days | Nulled for every candidate, including labelled candidates; labels retain only the stable ID, decision fields, and a bounded reason code |
| Unlabelled `newsletter_candidates` | 180 days | Deleted |
| Labelled `newsletter_candidates` | 365 days | Retain metadata only; purge rows after exporting a privacy-reviewed fixture or aggregate |
| `newsletter_adjudications` | 365 days | Retain structured verdict, reason code, schema version, and candidate ID; purge free-text reviewer notes after 30 days |
| `newsletter_manual_examples` | 365 days | Retain URL hash, category, verdict, and match state; purge headline and rationale after 30 days |
| `newsletter_runs` | 400 days | Deleted only when no candidate rows reference the run |

**Settled 2026-08-04:** Retain raw review excerpts and free-text notes for 30 days only. Retain the non-text candidate metadata and structured labels for up to one year.

There is no automatic export and no automatic Git commit. `--newsletter-labels-export` is an explicit local backup command that writes only IDs, verdicts, reason codes, dates, and aggregate-safe scores to an ignored local path. A separate human-reviewed promotion command creates a minimal, privacy-screened regression fixture; it must contain no full article text, source URL, or free-text note.

**Settled 2026-08-04:** Raw review material, source links, and reviewer notes remain local and are never automatically exported, committed, or pushed. Only a separately approved, privacy-screened fixture with no raw text, URL, or note may enter the repository.

### 8.3 Failure modes that must not break a send

The production edition and its selected candidate records are one transaction: either both exist or neither exists. A pre-delivery persistence failure aborts preparation before SMTP, because a newsletter with silently unmeasurable selected content corrupts the evaluation frame. SMTP outcomes are appended separately and retryably; an outcome-write failure creates a durable `delivery_state='unknown'` repair task and the report excludes that run until reconciliation completes.

---

## 9. Test cases

New file `tests/test_newsletter_review.py`, plus additions to `tests/test_cli.py`. All use `tmp_path` databases, as the Watchlist reliability tests do (`tests/test_watchlist_reliability.py:308`).

**Schema and migration**
1. A v3 database migrates to v4, writes a `.v3-backup-` file, and creates all four tables with the expected columns.
2. Migration is idempotent: a second `connect()` on a v4 database changes nothing and loses no rows.
3. A v2 database migrates through to v4 in one step, retaining editions/deliveries/quote rows.
4. `user_version = 5` still raises `"created by a newer NewsAgent version"`.

**Persistence and identity**
5. A production send writes exactly one candidate row per cluster; `sent` rows equal the delivered deck and `filtered` rows cover the rest.
6. URL-based `story_key` is stable across headline rewording; URL-less fallback keys are never used to auto-transfer a label.
7. A same-day production rebuild creates a distinct occurrence under a new `run_id`; no label is silently transferred between occurrences.
8. Hard-rejected articles (`quality_gate.py:135`) appear with `filter_stage='quality_gate_hard_reject'` and stratum `hard_reject`.
9. `filter_stage` splits `insufficient story context` into distinct stages while `filter_reason` still carries the legacy string verbatim, and `data/skipped_stories_<date>.json` is byte-identical to today's output for the same input.
10. Sent rows carry `deck_rank`, `selection_phase`, and the **post-compression** `delivered_paragraph` actually emailed, not the pre-compression draft.
11. Per-story `edition_stories` rows are written with the `general:` prefix and do not disturb the `watchlist:%` retention predicates.

**Isolation**
12. `--email-rebuild-today` (test resend) writes zero candidate, run, and label rows.
13. `--dry-run --to email` writes zero rows.
14. `--email-resend` writes zero rows and does not touch `newsletter_runs`.
15. `--email-parity --send` writes zero rows.
16. A production send with `--openai-mode off` writes rows tagged `openai_mode='off'`, and `newsletter_metrics` excludes them.
17. `--review-newsletter-evaluations` does not acquire the build lock: a lock held in another thread does not block it (inverse of `test_build_lock_rejects_contending_thread`, `tests/test_watchlist_reliability.py:586`).
18. A selected-candidate persistence failure rolls back the edition preparation before SMTP; a delivery-outcome persistence failure creates an `unknown` repair task and excludes the run from reports.
19. `collect_pipeline_context` and `build_briefing_result` never read `newsletter_adjudications` (store patched to raise on that table).

**Review loop**
20. An empty queue prints `No pending newsletter evaluations.` and exits 0.
21. `relevant` / `irrelevant` / `unclear` each persist with the correct `subject_type`; an invalid verdict leaves the item pending.
22. `skip` leaves the item pending; `quit` exits without recording the current item.
23. A second verdict on the same `candidate_id` is rejected write-once and reported as `already reviewed`, not raised as a traceback.
24. `--review-scope sent` presents only sent rows, `filtered` only filtered, `all` alternates.
25. `--review-limit 3` presents at most 3 items.
26. A frozen, seeded review batch samples uniformly without replacement inside every stratum, records the frame counts, and reproduces the same membership from its saved seed.
27. Review commands reject `--send`, `--dry-run`, and `--alerts` with a parser error.

**Manual examples**
28. Import rejects duplicate `(example_date, source_url)` and reports the inserted count.
29. Import rejects empty `why_it_matters`, an unknown `expected_category`, or a non-HTTPS URL.
30. Import rejects `provenance='pipeline_relabel'`.
31. Matching resolves `matched_sent` when the story was delivered, `matched_filtered` when it was a filtered candidate, `not_retrieved` when absent.
32. A `not_retrieved` example does not contribute to the gate-miss numerator.

**Metrics**
33. FP rate returns `not yet reportable` at 39 labels and a number at 40.
34. FP rate excludes `unclear` from both numerator and denominator.
35. Per-stratum FN rates and the population-weighted estimate match hand-computed expected values for a completed randomized batch; an unfinished or diagnostic batch prints no estimate.
36. A window straddling two `pipeline_version` values reports `pipeline_version_spread=2` and refuses to pool them into one headline number.
37. `unclear_share > 0.20` emits the rubric warning.
38. Per-category FP rate is suppressed below 15 labels in that category.

**Retention**
39. A 100-day-old unlabelled candidate has its excerpt nulled and `excerpt_purged_at` set.
40. A 31-day-old labelled candidate has its excerpt and free-text note purged while its structured verdict remains.
41. A 366-day-old candidate, label, and manual-example metadata is deleted after a privacy-reviewed fixture promotion check.
42. The explicit local export contains no title, URL, excerpt, delivered paragraph, or free-text note.
43. Retention is idempotent — a second pass reports zero further changes.

**Regression fixtures (Phase N8)**
44. Locked-fixture replay: for each case the current gate/stage classifier reproduces the recorded decision; a deviation fails with the case id and both decisions (same shape as `tests/test_importance_replay.py`).
45. The fixture round-trips through `--newsletter-labels-export` without reordering or field loss.

---

## 10. Implementation phases

Each phase is independently shippable and leaves the system working.

| Phase | Content | Rough size | Depends on |
| --- | --- | --- | --- |
| **N1 — Schema** | `SCHEMA_VERSION = 4`, `_create_newsletter_schema`, v3→v4 branch with backup, `schema_migrations` row. Tests 1–4. | ~150 lines + tests | — |
| **N2 — Capture** | `newsletter_review.py` record builders; `BriefingBuildResult` fields; `record_newsletter_run`; `persist_review_state` wiring; per-story `edition_stories` rows. Tests 5–19. | ~350 lines + tests | N1 |
| **N3 — Review CLI** | `--review-newsletter-evaluations`, `--review-scope`, `--review-limit`, `--review-days`; `pending_newsletter_reviews`, `record_newsletter_label`; the interactive loop. Tests 20–27. | ~250 lines + tests | N2 |
| **N4 — Manual examples** | `--newsletter-example-add`, `--newsletter-example-import`, `--review-newsletter-examples`; match resolution. Tests 28–32. | ~220 lines + tests | N3 |
| **N5 — Metrics + export** | `newsletter_metrics`, `format_metrics_report`, `--newsletter-evaluation-report`, `--newsletter-labels-export`; minimum-denominator gating. Tests 33–38, 45. | ~200 lines + tests | N3, N4 |
| **N6 — Retention** | Newsletter clauses in the retention pass, label exemptions, excerpt purge. Tests 39–43. | ~120 lines + tests | N2 |
| **N7 — Labelling window** | *No code.* 10–15 min/day until the §7.5 denominators are met (~3 weeks). Export labels after each session. Optional historical backfill script. | — | N5, N6 |
| **N8 — Controlled improvement** | Freeze a regression fixture from the labels; propose one gate change at a time; shadow-replay; measure; record a decision. Test 44. | per change | N7 |

N1–N6 total roughly 1,300 lines plus tests and change no delivery behaviour. N7 is the long pole and is reviewer time, not engineering time.

---

## 11. Using the labels (and how not to)

### 11.1 Locked regression fixtures

Once ≥ 100 labels exist, export a curated `tests/fixtures/newsletter_labels/gate_cases_v1.json`: id, headline, summary excerpt, sources, scores, recorded `filter_stage`, and the human verdict. Test 44 replays the deterministic parts of the gate against it, locking behaviour the way `importance_selection_replay.json` locks importance banding. This is the mechanism that makes a future change *safe* rather than *hopeful*.

Adversarial seeding, mirroring the Watchlist's adversarial seed set: a one-source scoop from a credible outlet; a three-source rewrite of a press release; a genuine follow-up wrongly marked stale by `apply_history` (`history.py:97`); two same-event clusters the duplicate gate should have merged; a teaser headline `TEASER_TITLE_RE` (`quality_gate.py:25`) catches correctly; and a substantive analysis piece it catches *incorrectly*.

### 11.2 Controlled quality-gate change protocol

Every change follows the same five steps, one change at a time:

1. **Diagnose from reason codes and the §7.4 disagreement breakdown**, not from the aggregate rate. `gate_too_strict` appearing 12 times on `source_confirmation` candidates is a hypothesis; a 14% FN rate is not.
2. **Propose one parameter change** — a `QualityGateConfig` threshold (`models.py:88`), a `skip_reason` boundary (`skipped_log.py:44-52`), `minimum_story_evidence_score`, or a prompt clause.
3. **Shadow-replay** against the full labelled set offline. Report the FP and FN deltas *and* how many previously-correct decisions flip. A change that fixes 6 misses while creating 9 new false positives is a regression, not an improvement.
4. **Record a decision** in `docs/decisions.md` via the `decisiontracker` skill: parameter, before/after metrics, label denominator, and the labels used.
5. **Bump `pipeline_version`** so post-change labels are not pooled with pre-change ones, then re-measure over a fresh window.

**Settled 2026-08-04:** No quality-gate change may affect daily emails until it has been shadow-tested against the reviewed corpus, its improvements and regressions have been shown to the owner, and the owner explicitly approves it.

### 11.3 Explicitly forbidden

- **No automatic threshold tuning.** No optimiser, no gradient, no nightly retrain.
- **No labels in the send path.** `pipeline.py` never reads adjudications; a label must never suppress or promote a story at build time (test 19). Otherwise the measurement instrument becomes part of what it measures and the metrics stop meaning anything.
- **No fine-tuning or few-shot injection of labelled examples into classifier/drafting prompts** without a separate explicit decision — that is a model-behaviour change with its own cost and evaluation needs, not a gate tweak.
- **No metric reported below its minimum denominator** (§7.5).
- **No pooling across `pipeline_version`.**

---

## 12. Success metrics for this plan

At the end of Phase N7:

| Criterion | Target |
| --- | --- |
| Sent stories labelled | ≥ 40, with ≥ 15 in each of ≥ 3 categories |
| Filtered candidates labelled | ≥ 100, with ≥ 25 in each of `near_miss` and `mid` |
| Manual examples adjudicated | ≥ 15 |
| `unclear_share` | ≤ 20% for both subject types |
| Reviewer burden | ≤ 15 minutes/day sustained |
| Delivery impact | Zero — no change in delivered content, send success rate, or per-run OpenAI cost attributable to this system |
| Label durability | 100% of retained structured labels recoverable from the explicit local backup after a database restore |
| Regression fixture | ≥ 1 locked fixture with ≥ 100 cases passing |

And in Phase N8: at least one gate change shipped with a measured before/after on the labelled set and a decision record — **or** a documented finding that no change is warranted, which is an equally valid outcome.

The system **fails** if labels accumulate but no metric ever crosses its minimum (reviewer burden too high — cut `--review-limit` and narrow the queue), or if `unclear_share` stays high (rubric too vague — rewrite §6.1 before collecting more labels).

---

## 13. Risks

| Risk | Severity | Mitigation |
| --- | --- | --- |
| **Reviewer fatigue** — 215 filtered candidates/day is overwhelming | High | Frozen randomized batches, `--review-limit 20`, 10–15 min/day. If it still stalls, lower batch size without changing the sampling frame. |
| **Sampling bias** — near-miss-heavy review inflates FN | High | `review_stratum` on every label; weight by population share at metric time (§7.2); print the caveat with the number. |
| **Acting on tiny denominators** | High | Hard minimums (§7.5); the report refuses to print a rate below them. |
| **Reviewer drift** — the standard shifts over months | Medium | `label_schema_version` on each label; re-review a 10-item control set at the start of each new window and compare agreement. |
| **Identity collision or drift** — headline edits, missing URLs, or merged clusters can describe the same event differently | High | Immutable run occurrences plus URL-based `story_key`; URL-less fallbacks never auto-transfer labels; hard-rejected articles use their own identity kind. |
| **Migration risk to a live database** | Medium | Automatic `.v3-backup-` before migrating, mirroring the v2 path (`mailer/state.py:133`); additive `CREATE TABLE IF NOT EXISTS` only; no existing table altered except additive `edition_stories` rows. |
| **State growth** — ~215 rows/day ≈ 78k rows/year | Low | Raw excerpts purge at 30 days and all retained metadata expires within one year; monitor the database size during the first review window. |
| **Review-state corruption** | High | Edition preparation and selected-candidate persistence are one transaction; SMTP outcome writes are retryable and unreconciled outcomes are excluded from reports. |
| **Labels leaking into the pipeline** | High if it happened | Test 19 asserts the pipeline never reads the label tables. |
| **Manual examples degenerating into relabelling** | Medium | `provenance` required and `pipeline_relabel` rejected at import (test 30); `not_retrieved` vs `matched_filtered` tracked separately so sourcing misses are not blamed on the gate. |
| **Scope creep into automatic training** | Medium | §11.3 is a hard constraint; changing it requires its own decision record. |
| **Two review systems diverging** | Low | Newsletter tables and CLI deliberately parallel the Watchlist ones; shared conventions (`_now()`, upsert style, write-once verdicts, backup-before-migrate) rather than shared code, because the metric semantics genuinely differ. |

---

## 14. Open decisions

Each should end in a `docs/decisions.md` record.

1. **Diagnostic queue ordering.** Keep population metrics limited to frozen randomized batches. Should a separate non-metric diagnostic queue be oldest-first, disagreement-focused, or a mix? *Recommendation: oldest-first first, then evaluate disagreement-focused sampling separately.*
2. **Sent-story review completeness.** Review all 25 stories on chosen days (clean per-day precision) or sample across days (broader coverage)? *Recommendation: sample across days — a single day's deck is highly correlated with that day's news.*
3. **Historical backfill.** Import old JSON logs as explicitly non-metric review-only material, or start clean? *Recommendation: start clean; old logs lack decision-stage events and reliable identities.*
4. **`unclear` on filtered candidates.** Default it to `irrelevant` for FN purposes (conservative — never claims a miss the reviewer wasn't sure about), or keep it excluded? *Recommendation: keep it excluded and report the count; folding it either way biases the headline number.*
5. **Per-category minimums.** Is 15 labels/category the right floor, or should culture carry a higher one given its separate lane-diversity selection path (`pipeline.py:228-237`)?
6. **Persist `--openai-mode off` production sends at all?** This plan persists and tags them. Skipping them is simpler but loses exactly the days when the budget was exhausted — which are the days most worth understanding.
