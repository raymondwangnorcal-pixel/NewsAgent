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
| `watchlist_events` (what rendered) | `mailer/state.py:203` | `newsletter_candidates` rows with `disposition='selected'`, counted as sent only when the linked production edition reached SMTP acceptance |
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
| `briefing_date` | TEXT | invocation date captured once from `briefing_now()` immediately after acquiring the build lock; never recomputed mid-run |
| `edition_id` | INTEGER NULL | FK `editions(id)`; NULL if no edition was produced |
| `pipeline_version` | TEXT | `calibration_version` (`models.py:124`) + quality-gate config hash + classifier/drafting model ids |
| `config_hash` | TEXT | SHA-256 over the `AgentConfig` fields that affect selection |
| `deck_target` | INTEGER | `config.importance.deck_target` |
| `candidates_total` | INTEGER | rows written for this run |
| `openai_mode` | TEXT | `full` / `classify-only` / `off` — an `off` run is not comparable |
| `history_update_json` | TEXT NULL | transient story-history outbox: canonical target JSON plus input/target SHA-256 hashes; cleared after successful application or same-day delivery expiry |
| `history_applied_at` | TEXT NULL | set only after the update is atomically installed in `data/story_history.json`; SMTP is forbidden while NULL |
| `history_abandoned_at` | TEXT NULL | set at briefing-date rollover when a pending update expires; both automatic send and `--email-resend` must refuse the linked edition |
| `created_at` | TEXT | UTC ISO-8601 |

### 3.2 `newsletter_candidates`

One row per post-quality-gate cluster considered by history, evidence, classification, or selection, plus one per hard-rejected article, per run.

| Field | Type | Notes |
| --- | --- | --- |
| `candidate_id` | TEXT PK | immutable run occurrence: SHA-256 over canonical compact JSON `[run_id, candidate_kind, story_key]`; never concatenate variable-length components directly |
| `story_key` | TEXT | cross-run comparison key: SHA-256 of sorted normalized source URLs; when no URL exists, SHA-256 of publisher + normalized title + published date |
| `candidate_kind` | TEXT | `cluster` or `hard_rejected_article`; prevents an article and a later cluster from sharing an identity |
| `run_id` | TEXT | FK `newsletter_runs(run_id)` |
| `briefing_date` | TEXT | indexed |
| `disposition` | TEXT | `selected` \| `filtered`; selected means chosen for an edition, not yet delivered |
| `filter_stage` | TEXT | `''` when selected; otherwise the terminal stage: `quality_gate`, `history`, `evidence`, `classification`, `duplicate`, or `selection` |
| `filter_reason_code` | TEXT | exact branch-owned terminal code, such as `quality_gate_hard_reject`, `history_stale`, `evidence_gate`, `classification_pool_excluded`, `duplicate_gate_merged`, `selection_category_ceiling`, `selection_source_cap`, `selection_culture_lane_cap`, `selection_deck_capacity`, or `selection_below_threshold` |
| `legacy_skip_reason` | TEXT | the verbatim legacy `skip_reason` / hard-reject string, preserved for traceability but never treated as the causal source of truth |
| `review_stratum` | TEXT | `selected`, `near_miss`, `mid`, `deep_filtered`, `hard_reject` (§6.2); the CLI calls accepted selected rows “sent” |
| `headline` | TEXT NULL | `cluster.title`; nulled after 30 days |
| `category` | TEXT | assigned category or `uncategorized` |
| `culture_lane` | TEXT | `cluster.culture_lane` |
| `canonical_url` | TEXT NULL | normalised `cluster.urls[0]`; nulled after 30 days |
| `all_urls_json` | TEXT NULL | JSON array, ≤5; nulled after 30 days |
| `url_hashes_json` | TEXT | stable-sorted SHA-256 hashes of the normalized URLs, ≤5; retained after raw URLs are purged |
| `sources_json` | TEXT | JSON array of `cluster.sources` |
| `source_count` | INTEGER | |
| `summary_excerpt` | TEXT NULL | first 600 chars of `representative_summary` — reviewer context only; nulled after 30 days |
| `delivered_paragraph` | TEXT NULL | for selected: the post-compression paragraph actually emailed; `''` otherwise; nulled after 30 days |
| `total_score`, `importance`, `evidence_score`, `quality_score`, `content_quality_penalty` | REAL | from `StoryCluster` (`models.py:221`) |
| `llm_importance` | INTEGER NULL | `CategoryAssignment.llm_importance` |
| `deck_rank` | INTEGER NULL | selection order for selected rows |
| `selection_phase` | TEXT | `floor` \| `remainder` \| `big_day` \| `''` (`pipeline.py:225-290`) |
| `is_update` | INTEGER | `cluster.is_update` |
| `merged_from_json` | TEXT | `cluster.merged_from` |
| `excerpt_purged_at` | TEXT NULL | set when §8.2 retention nulls the text fields |
| `created_at` | TEXT | |

Constraints: `UNIQUE(run_id, candidate_kind, story_key)` prevents duplicate occurrences for the same logical candidate inside one run. Indexes: `(briefing_date, disposition)`, `(run_id, disposition)`, `(story_key)`, `(review_stratum, disposition)`. Delivery is not duplicated on candidate rows: reports join `newsletter_candidates.run_id → newsletter_runs.edition_id → editions.state`.

Candidate occurrences are append-only. A same-day rebuild creates a new `run_id` and new occurrence rows; labels remain attached to the exact occurrence reviewed. The report may compare rows sharing `story_key`, but never silently transfers a label or stratum to a rewritten or newly sourced story. `candidate_id` is deterministic only within its run tuple; the fixed-length `run_id` and constrained `candidate_kind` already make the original collision example inapplicable, while canonical tuple encoding makes the identity contract robust to future schema changes.

### 3.3 `newsletter_adjudications`

Immutable reviewer verdicts, mirroring `watchlist_adjudications` (`mailer/state.py:283`) including its `UNIQUE(subject_type, subject_id)` write-once behaviour.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | INTEGER PK | |
| `subject_type` | TEXT | `sent_story` \| `filtered_candidate` |
| `subject_id` | TEXT | `candidate_id` |
| `verdict` | TEXT | `relevant` \| `irrelevant` \| `unclear` |
| `reason_code` | TEXT | required for `relevant` and `irrelevant`; empty only for `unclear`; selected from the fixed list in §6.3 |
| `reviewer_note` | TEXT NULL | free text, default `''`; nulled after 30 days |
| `label_schema_version` | TEXT | rubric version, e.g. `newsletter-rubric-v1` |
| `pipeline_version` | TEXT | copied from the run, so a label is attributable to the code that produced the candidate |
| `created_at` | TEXT | |

`record_newsletter_label()` validates `(subject_type, verdict)` pairs exactly as `record_adjudication` does (`mailer/state.py:1121-1129`). Manual-example verdicts remain on `newsletter_manual_examples`, matching the existing `watchlist_benchmark_events` pattern; they are not duplicated in this table.

### 3.4 `newsletter_manual_examples`

Independently identified stories the reviewer believes *should* have been in the briefing — the analogue of `watchlist_benchmark_events` (`mailer/state.py:269`), under the same rule from the Watchlist plan §9.4: **it may not be built solely by relabelling items NewsAgent already retrieved.**

| Field | Type | Notes |
| --- | --- | --- |
| `id` | INTEGER PK | |
| `example_date` | TEXT | the briefing date it should have appeared in |
| `headline` | TEXT NULL | required at import; nulled after 30 days |
| `source_url` | TEXT NULL | HTTPS required at import; nulled after 30 days |
| `source_url_hash` | TEXT | SHA-256 of the normalized URL; retained for matching and deduplication |
| `publisher` | TEXT | |
| `expected_category` | TEXT | one of the five categories, or `''` if unknown |
| `why_it_matters` | TEXT NULL | required and non-empty at import; nulled after 30 days |
| `provenance` | TEXT | `manual_recall`, `external_outlet`, `reader_report`, `historical_case` — never `pipeline_relabel` |
| `verdict` | TEXT | `pending` \| `relevant` \| `irrelevant` \| `unclear` |
| `matched_candidate_id` | TEXT NULL | resolved link (§6.4) |
| `match_state` | TEXT | `unmatched` \| `matched_sent` \| `matched_filtered` \| `not_retrieved` |
| `matched_by` | TEXT | `url` \| `manual` |
| `pipeline_version` | TEXT NULL | set at review resolution from the chosen report-eligible accepted production run for `example_date`; pending examples may be NULL |
| `label_schema_version` | TEXT NULL | set when the verdict is recorded; pending examples may be NULL |
| `imported_at`, `reviewed_at` | TEXT | |

`UNIQUE(example_date, source_url_hash)` — import rejects normalized-URL duplicates even after the raw URL is purged, the way `import_benchmark_events` rejects duplicate benchmark identities (`mailer/state.py:931`, tested at `tests/test_watchlist_reliability.py:264`).

### 3.5 `newsletter_review_batches`

One immutable sampling frame per randomized filtered-candidate review batch.

| Field | Type | Notes |
| --- | --- | --- |
| `batch_id` | TEXT PK | `uuid4().hex` |
| `pipeline_version` | TEXT | one version only; a batch never spans versions |
| `label_schema_version` | TEXT | one rubric version only; a batch never spans rubric changes |
| `seed` | TEXT | saved SHA-256 seed |
| `window_start`, `window_end` | TEXT | inclusive briefing-date frame |
| `stratum_population_json` | TEXT | exact eligible population count per stratum at freeze time |
| `stratum_target_json` | TEXT | review target per stratum after applying §6.2 |
| `candidate_ids_json` | TEXT | stable-sorted sampled candidate IDs; immutable after creation |
| `created_at` | TEXT | UTC ISO-8601 |

Completion is derived: every sampled ID must have an adjudication (`relevant`, `irrelevant`, or `unclear`); `skip` and `quit` leave the batch incomplete. Each stratum samples `ceil(conclusive_target / 0.80)` candidates, capped by the frozen population, so the batch can tolerate up to the allowed 20% `unclear` share without changing membership. Rates exclude `unclear`, report its share, and require the conclusive-label minimums in §7.5. If a completed batch still misses a conclusive target, the rubric must be corrected, `label_schema_version` bumped, and a new batch collected; membership, population counts, and targets never change after creation.

### 3.6 Schema version 4 migration

In `mailer/state.py::_migrate` (`:41`), add a `version == 3` branch shaped like the existing `version == 2` branch (`:88`):

1. `self._backup(connection, version=3)` — generalise `_backup_v2` (`:133`) to take a version; writes `data/email_state.db.v3-backup-<UTC stamp>`.
2. `self._create_newsletter_schema(connection)` — `CREATE TABLE IF NOT EXISTS` for all five tables, mirroring `_create_watchlist_schema` (`:149`) so repeat runs self-heal.
3. `INSERT INTO schema_migrations(version=4, applied_at, backup_path)`.
4. `PRAGMA user_version = 4`.

Keep the existing self-heal path: when `version == SCHEMA_VERSION`, also call `_create_newsletter_schema` so databases created mid-development converge (`:45-51`).

**Backfill:** the historical `data/skipped_stories_*.json`, `quality_gate_rejections_*.json`, and `category_assignments_*.json` are **not** backfilled by default — they lack decision-stage events, reliable source-URL keys, scores, deck rank, and links to what was selected. An optional script may import them as clearly non-metric review material only. Deferred to Phase N7.

---

## 4. Where candidates get persisted

Each terminal decision must be owned by the function that makes it, not reconstructed from `skipped_log.py` after selection. Irreversible filters emit a `DecisionEvent` when the candidate leaves the pipeline. Selection is different: an individual `continue` can be temporary because a candidate may be admitted in a later floor, remainder, relaxation, or big-day pass. Therefore `select_importance_deck` returns one final `SelectionOutcome` per surviving cluster after all passes complete. `build_briefing_result` combines those events and outcomes with post-compression paragraph text; `skipped_log.py` remains a human diagnostic and is never the causal source of truth for review metrics.

Every evaluated occurrence has exactly one terminal outcome: selected, or one filtered `filter_stage` + `filter_reason_code`. The capture invariant is:

```text
hard-rejected articles + post-clustering candidates
    == selected candidate records + filtered candidate records
```

The implementation mapping is part of the contract:

| Terminal outcome | Owner | Emission point |
| --- | --- | --- |
| `quality_gate / quality_gate_hard_reject` | `quality_gate.py::apply_quality_gate` | Immediately before the article is omitted from survivors |
| `history / history_stale` | `history.py::apply_history` | When `skip_reason` becomes `stale/repeated from yesterday` |
| `evidence / evidence_gate` | `pipeline.py::apply_evidence_gate` | When the evidence threshold makes the cluster ineligible |
| `classification / classification_pool_excluded` | `pipeline.py::finalize_classification_outcomes` | Called exactly once after `assignments.update(backfill_assignments)` and before the final `select_importance_deck`; emits once for every cluster with no prior terminal event, no `skip_reason`, `evidence_score >= minimum_evidence`, and `cluster.key not in assignments` |
| `duplicate / duplicate_gate_merged` | `duplicate_gate.py::apply_duplicate_gate` | Before the removed cluster is dropped from `all_clusters` |
| `selection / selection_*` | `pipeline.py::select_importance_deck` | Once, after all selection passes, using deterministic final-reason precedence documented beside the selector |

`finalize_classification_outcomes` is the only classification-exclusion emission point. Initial-pool misses remain provisional through backfill; a degraded fallback assignment counts as assigned, and no eligible cluster may reach final selection with both no assignment and no terminal event.

The selection precedence is mutually exclusive and tested: category ceiling, then binding culture-lane or source cap, then deck capacity, then big-day importance threshold, then `selection_below_threshold`. When both Culture constraints bind, `selection_source_cap` wins because the selector checks source capacity first; `selection_culture_lane_cap` is emitted only when source capacity remains. `low content quality` and `no reliable source confirmation` remain verbatim legacy diagnostic strings; because neither is currently a standalone pipeline gate, neither may be recorded as a causal `filter_stage` unless implementation first introduces an explicit gate through a separate decision.

`pipeline.py` stays storage-agnostic: it builds an in-memory record set and returns it; `EmailService` decides whether to persist.

1. **New module `src/news_agent/newsletter_review.py`** (~250 lines), the general-news counterpart to `watchlist/benchmark.py` + `skipped_log.py`:
   - `@dataclass(frozen=True) CandidateRecord` — one per row in §3.2.
   - `build_candidate_records(context, selected, paragraphs, config, run_id) -> tuple[CandidateRecord, ...]` — combines captured terminal decisions with `story_key`, `filter_stage`, `filter_reason_code`, `review_stratum`, `deck_rank`, and `selection_phase`, then enforces the capture invariant.
   - `DecisionEvent`, `SelectionOutcome`, and their pure builders — called or returned by the owners in the mapping above. They record the exact branch name and reason before a candidate becomes unreachable. **The legacy `skip_reason` strings and existing JSON logs do not change.**
   - `assign_stratum(record, deck_target) -> str`.
   - `newsletter_metrics(rows) -> NewsletterMetrics` — a pure function over label rows, unit-testable without a database (same shape as `evaluate_gate`, `watchlist/gate.py:52`).
   - `format_metrics_report(metrics) -> str` — the `--newsletter-evaluation-report` body.
   - `load_manual_examples(path, categories) -> tuple[ManualExample, ...]` — import validation, mirroring `load_benchmark_candidates` (`watchlist/benchmark.py:47`).
2. **`pipeline.py` / `history.py`** — `BriefingBuildResult` (`:138`) gains `briefing_date`, `candidate_records: tuple[CandidateRecord, ...] = ()`, `run_id: str = ""`, and a deterministic `history_update`. The CLI passes the invocation-captured date through the pipeline, dated diagnostic-log helpers, email header, and persistence; none calls `briefing_today()` independently on this path. Production email builds call history with `persist_history=False`; no production history file is changed during the build. `PipelineContext` retains terminal events even when a duplicate-gate branch removes a cluster from `all_clusters`. `history.py` gains pure update construction plus same-directory atomic installation: the payload records canonical target JSON and the input/target hashes, recognizes an already-installed target as success, and refuses to overwrite an unexpected current hash. No new network or model calls.
3. **`mailer/service.py` / `cli.py`** — `prepare_newsletter_edition` gains `candidate_records`, `history_update`, and `persist_review_state: bool = not test_revision`. In one SQLite transaction it creates the production edition, writes the `newsletter_run` and transient history outbox, persists all selected and filtered candidate occurrences, and adds per-story `edition_stories` rows. After commit, it atomically applies the saved history update and marks it applied before SMTP. At the beginning of each production-send invocation, the CLI resumes any complete pre-SMTP edition for the current briefing date—applying a pending history payload if necessary and sending the already-rendered edition—before considering a new pipeline build. §8.3 defines bounded SQLite retries, exact exit behaviour, and date-rollover abandonment.
4. **`mailer/state.py`** — new methods: `record_newsletter_run`, `pending_production_newsletter`, `mark_newsletter_history_applied`, `abandon_stale_newsletter`, `create_newsletter_review_batch`, `pending_newsletter_reviews(scope, limit, days)`, `record_newsletter_label`, `import_newsletter_examples`, `pending_newsletter_examples`, `review_newsletter_example`, `newsletter_review_metrics(days)`, `export_newsletter_labels(days)`, plus newsletter clauses in the retention pass called from `mailer/service.py:378`.
5. **Per-story selected identity.** `prepare_newsletter_edition` writes an addressable `(candidate_id, f"general:{category}")` row for every selected candidate. Keep existing category-message rows. The candidate record is never created by parsing formatted email text. A selected story enters reader-facing metrics only when its linked production `editions.state` is `smtp_accepted`, which follows the existing any-recipient-accepted watermark.

**Affected files, complete:**

| File | Change |
| --- | --- |
| `src/news_agent/newsletter_review.py` | new module |
| `src/news_agent/mailer/state.py` | schema v4 + migration branch, 12 new methods, retention clauses |
| `src/news_agent/pipeline.py` | build candidate records and deferred history update; extend `BriefingBuildResult` |
| `src/news_agent/history.py` | pure history-update construction and atomic idempotent application |
| `src/news_agent/time.py` | existing briefing timezone remains authoritative; `briefing_now()` supplies the captured invocation clock |
| `src/news_agent/mailer/service.py` | transactional review persistence, history application, prepared-edition resume, per-story identities |
| `src/news_agent/cli.py` | 10 new flags + 3 review modes; pilot guard, scheduled prepared-edition resume, and explicit persistence-failure exit |
| `src/news_agent/skipped_log.py` | behaviour unchanged; `skip_reason` copied only into `legacy_skip_reason` |
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
# One story at a time. After a batch exists, default scope interleaves sent and filtered queues.
news-briefing --review-newsletter-evaluations
news-briefing --review-newsletter-evaluations --review-scope sent
news-briefing --review-newsletter-evaluations --review-scope filtered
news-briefing --review-newsletter-evaluations --review-limit 10 --review-days 14
news-briefing --newsletter-review-batch-create

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
| `--review-scope` | `all\|sent\|filtered`, default `all` | After a batch exists, `all` alternates one sent and one filtered item; during the pilot it reviews sent items only and prints pilot progress |
| `--review-limit` | int, default 20 | Max items per session; keeps a sitting to ~10–15 minutes |
| `--review-days` | int, default 30 | Pending-queue lookback |
| `--newsletter-review-batch-create` | store_true | After an eligible seven-day pilot, explicitly freezes one version-scoped filtered-candidate batch; otherwise prints progress and exits 0 without writing |
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
Deck context: 25 accepted stories · type `deck` to view

Was this worth the reader's time? [relevant/irrelevant/unclear/deck/skip/quit]:
```

```
[filtered 4/20]  2026-08-04 · uncategorized · stratum near_miss
Regulator opens probe into ...
Sources: The Verge (1) · score 9.4 · importance 41.0
https://example.com/...
> Excerpt: <600 chars>
Deck context: 25 accepted stories · type `deck` to view
Filter diagnostics hidden · type `details` to view

Should this have been in the briefing? [relevant/irrelevant/unclear/deck/details/skip/quit]:
```

Rules:
- **One item at a time.** No batch mode, no bulk edit.
- `deck` prints the same briefing date's SMTP-accepted selected headlines, grouped by category and ordered by `deck_rank`, then re-prompts without writing. This supplies the comparison set required by §6.1; it never substitutes a rebuilt or non-accepted deck.
- `details` reveals the current filtered item's `filter_stage`, `filter_reason_code`, a short code explanation, and legacy diagnostic, then re-prompts without writing. Those fields are hidden initially to reduce anchoring on the pipeline's reason; the verdict remains whether the story deserved a slot, not whether the named rule behaved as designed.
- `skip` leaves the item pending without writing a verdict; `quit` exits immediately without recording the current item.
- **Write-once.** A repeat verdict hits the same `UNIQUE` constraint as the Watchlist path (`mailer/state.py:1140`); the CLI prints `already reviewed` rather than surfacing a traceback.
- **Randomized review batches.** A command creates a dated batch from a single-version frozen candidate population, saved per-stratum counts and targets, and a saved SHA-256 seed. It chooses uniformly at random within each stratum. A batch is shown oldest-first only *after* its randomized membership is fixed.
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

~215 filtered candidates/day against ~25 selected makes a census impractical. `assign_stratum` applies this ordered, mutually exclusive rule to the occurrence's terminal decision:

| Order | Stratum | Definition |
| --- | --- | --- |
| 1 | `hard_reject` | `filter_reason_code='quality_gate_hard_reject'` |
| 2 | `deep_filtered` | terminal stage `evidence` or `classification`; also explicit future strict content gates, but never a legacy diagnostic string alone |
| 3 | `mid` | `filter_reason_code='history_stale'`, or a selection-stage occurrence whose legacy diagnostic is `no reliable source confirmation` |
| 4 | `near_miss` | duplicate- or selection-stage occurrence not assigned above, including candidates within the top `deck_target + 15` eligible ranks |

Stratum is a property of one run occurrence, not of `story_key`. The same story may legitimately appear in different strata on different runs as sources, history, classification, configuration, or deck competition change. Metrics use the labelled occurrence's recorded stratum and never transfer or retroactively recompute it.

The original `50/30/15/5` slot split is withdrawn. Available logs for the four measured days are too coarse to reproduce the new terminal stages, but their legacy reasons already show that a fixed 5% hard-reject allocation can badly under-sample a materially large population. After capture ships, the system observes at least seven production days, saves each day's exact stratum counts, and derives the first metric batch targets from that pilot:

1. At least 25 conclusive labels in `near_miss` and `mid` because they are the most actionable strata.
2. At least 25 conclusive labels in every other stratum whose frozen population weight is ≥15%.
3. At least 10 conclusive labels in every nonempty lower-weight stratum as a safety probe.
4. Sample `ceil(target / 0.80)` items per stratum to allow for the maximum acceptable `unclear` share without mutating a frozen batch.
5. If the frozen frame cannot supply that sample, extend the window; never replace missing items from a different pipeline version.

Batch construction allocates sampled items to the remaining per-stratum targets, rather than applying permanent percentages. Sampling is uniform without replacement inside each stratum and saves the seed, population counts, targets, and candidate IDs in `newsletter_review_batches`. Population weights are therefore valid for the frozen batch frame. A completed batch reports weighted rates and confidence intervals; an incomplete batch reports only coverage and provisional counts. A disagreement-focused queue is a separate non-estimating diagnostic mode and never contributes to the population FN estimate.

**Pilot availability contract.** An eligible pilot consists of seven distinct, report-eligible production briefing dates with complete candidate capture, an SMTP-accepted linked edition, the same `pipeline_version`, the same `label_schema_version`, and comparable OpenAI mode. Until all seven exist:

- sent-story and manual-example review remain available;
- `--review-scope filtered` prints `Filtered review unavailable — pilot N of 7 eligible days.` and exits 0 without writing;
- the default `all` scope prints the same progress, then reviews sent stories only;
- `--newsletter-review-batch-create` prints progress and exits 0 without creating a batch; and
- no filtered-candidate adjudication—metric or diagnostic—is accepted, so early labels cannot deplete or bias the later randomized frame.

Once the pilot is complete, batch creation is explicit and freezes the first filtered frame. A `pipeline_version` or `label_schema_version` change starts a new seven-day pilot for that version pair; days from older or mixed versions are never backfilled into it. Filtered review consumes the oldest incomplete frozen batch and never silently falls back to an ad-hoc queue.

N7 begins only after N1–N6 are complete and the intended selection configuration, model identifiers, and rubric are frozen for the measurement window. If a version still changes, previously collected sent-story labels remain valid historical labels for their recorded version but do not count toward the new version's 40-label FP denominator; completed manual-example verdicts are treated the same way through their resolved version fields. The report shows each old version's counts, including `not yet reportable`, rather than calling the labels orphaned or pooling different selectors. Restarting the pilot and denominators is an accepted schedule cost of changing the system under evaluation.

**Revised 2026-08-05:** Deep-filtered and hard-rejected candidates remain mandatory review material, but their allocation is pilot-calibrated with explicit per-stratum floors rather than fixed at a combined 20% share.

### 6.3 Reason codes

Fixed and small; extensible only by decision record.

*Sent → irrelevant:* `trivial`, `duplicate_of_other_story`, `wrong_category`, `thin_evidence`, `stale`, `promotional`, `misleading_framing`, `compression_lost_meaning`.
*Filtered → relevant:* `gate_too_strict`, `evidence_underrated`, `single_source_but_credible`, `classifier_missed_category`, `wrongly_merged`, `stale_rule_too_aggressive`.

**Settled 2026-08-04:** Every `relevant` and `irrelevant` label requires one of these reason codes. `unclear` remains reason-free.

Reason-code frequency is what actually drives Phase N8 change proposals; the aggregate rate only tells you *whether* to act. `compression_lost_meaning` links this review loop to the existing compression audits (`compression_audit.py:43`) without duplicating them.

### 6.4 Manual examples and matching

`--review-newsletter-examples` resolves each example against the candidate table before asking for a verdict:

1. Resolve the report-eligible SMTP-accepted production run for `example_date`. If none exists, leave the example pending. If more than one exists, show run ID, header, creation time, and `pipeline_version` and require the reviewer to choose one or quit; never guess which run the example evaluates.
2. Normalised-URL-hash match against that run's `url_hashes_json` → `matched_by='url'`; raw URLs are displayed only while still retained.
3. Else show the top five candidates from that run by title similarity as suggestions only. The reviewer explicitly selects one candidate ID or answers `none` → `matched_by='manual'`.

No hash is calculated from a manual example's headline or rationale, and no fuzzy match is accepted automatically.

When the verdict is recorded, copy the resolved run's `pipeline_version` and the current `label_schema_version` onto the manual example. This gives `not_retrieved` examples a real pipeline attribution even though no candidate matched, and makes the §7 no-pooling rule enforceable.

Resulting `match_state`:
- `matched_sent` — the system *did* send it; the example was a reviewer false alarm, which is itself useful calibration.
- `matched_filtered` — a genuine gate miss, linked to the filtered candidate.
- `not_retrieved` — never in the corpus at all. This is a **sourcing** problem (feed configuration), not a gate problem, and must not be counted against the quality gate.

That last distinction is the entire reason manual examples exist independently: a filtered-candidate label can only find misses among things that were already fetched.

---

## 7. Metrics

Computed by `newsletter_metrics()` over a `--report-days` window, keyed by both `pipeline_version` and `label_schema_version` so neither pipeline changes nor rubric changes are silently pooled. This applies to sent labels, filtered batches, and resolved manual examples. Every percentage prints with its counts.

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
Both the per-stratum rates and the weighted estimate print only for a completed randomized batch with saved population counts and satisfied conclusive-label floors. Print a 95% Wilson interval for each stratum and a stratified 95% confidence interval for the weighted estimate; do not describe a wide interval as a precise rate. Diagnostic queues print counts only, never a population-rate estimate.

### 7.3 Miss rate against manual examples (independent recall)

```
miss_rate = examples(relevant AND match_state != 'matched_sent') / examples(relevant)
```
Split into `matched_filtered` (gate miss — actionable here) and `not_retrieved` (sourcing miss — actionable in feed config, out of scope for the gate).

### 7.4 Health and coverage metrics

- `review_coverage` — labelled fraction of sent stories, of each filtered stratum, and of manual examples, in the window.
- `unclear_share` per subject type. **> 20% means the rubric is under-specified**; fix the rubric before reading any other number.
- `labels_total`, `labels_by_stratum`, `review_days_elapsed`, `labels_per_active_day`.
- `pipeline_version_spread` and `label_schema_version_spread` — distinct pipeline and rubric versions in the window; > 1 means the window straddles a change and headline numbers are not pooled. The report prints separate per-version counts and `not yet reportable` status so pre-change work remains visible without contaminating the current estimate.
- Disagreement breakdown: FP/FN counts grouped by `filter_stage`, by primary source, and by category — the direct input to §11.2.

### 7.5 Minimum denominators before a metric is reported

Modelled on the Watchlist rule that no metric is reported before its own minimum is met (DEC-0016 / DEC-0027 / DEC-0030, `docs/plans/watchlist-retrieval-reliability.md` §9.4):

| Metric | Minimum |
| --- | --- |
| FP rate (overall) | ≥ 40 labelled sent stories |
| FP rate (per category) | ≥ 15 labelled sent stories in that category |
| FN rate (`near_miss`, `mid`) | ≥ 25 conclusive labelled candidates in that stratum |
| FN rate (other strata) | ≥ 25 conclusive labels when population weight is ≥ 15%; otherwise ≥ 10 for the safety-probe rate |
| FN estimate (population) | completed frozen batch; every nonempty stratum meets its applicable target; `unclear` reported separately |
| Miss rate | ≥ 15 adjudicated manual examples |

Below the minimum the report prints `not yet reportable — N of M`. This is the single most important discipline here: a 3-of-4 "75% false-positive rate" would otherwise trigger a bad change.

### 7.6 Reviewer effort

At ~20 s per sent story and ~40 s per filtered candidate, 40 sent + 100 filtered + 15 examples is only a planning floor. The pilot may require a larger filtered batch to satisfy every stratum target, so Phase N7 budgets **3–5 hours initially** and extends beyond three weeks when the frozen population or `unclear` labels leave a denominator short. Daily review matters for the same reason it did for the Watchlist: problems surface while there is still time to act.

---

## 8. Isolation, safety, and retention

### 8.1 Production-state isolation

Pipeline-generated run and candidate state is written **only** while preparing a real production send; human labels and manual examples are written only by their explicit review commands:

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

**Settled 2026-08-04; storage clarified 2026-08-05:** A selected candidate counts as sent only when its linked production edition has `editions.state='smtp_accepted'`. The state is derived through `newsletter_runs.edition_id`; it is not copied onto candidate rows. Existing edition aggregation applies: acceptance by any configured recipient is sufficient to establish reader exposure. `prepared`, `sending`, `failed`, and `indeterminate` editions remain visible for operational diagnostics and are excluded from sent-story metrics.

A test resend may *display* review data (its stories appear in the review queue only if a production run already recorded them) but can never create a label, mark an item reviewed, or move a metric. This mirrors the Watchlist invariants already covered by `test_test_delivery_does_not_write_watchlist_sent_history` (`tests/test_watchlist_reliability.py:537`) and `test_production_delivery_starts_watchlist_suppression` (`:548`).

Further isolation properties:
- Review commands take **no build lock** — `needs_build_lock` (`cli.py:36`) stays false for them, so reviewing never blocks the scheduled production attempts beginning at 08:20. Label writes are single-statement inserts; SQLite's own locking suffices.
- Review commands never mutate `editions`, `deliveries`, `data/story_history.json`, or any watchlist table.
- **A label is never read by the send path.** `pipeline.py` has no persistence dependency, and production SQL must never read `newsletter_adjudications`; asserted by complementary architecture and runtime tests in §9.

### 8.2 Retention

Extending `cleanup_watchlist_retention` (`mailer/state.py:1143`), invoked from the same post-delivery hook (`mailer/service.py:378`):

| Data | Retention | Rule |
| --- | --- | --- |
| `headline`, `summary_excerpt`, `delivered_paragraph`, `canonical_url`, `all_urls_json` | 30 days | Nulled for every candidate, including labelled candidates; `story_key` retains only a one-way URL/content hash for matching |
| Unlabelled `newsletter_candidates` | 180 days | Deleted |
| Labelled `newsletter_candidates` | 365 days | Retain structured occurrence metadata only; delete at expiry whether or not an optional local export or privacy-screened fixture was created |
| `newsletter_adjudications` | 365 days | Retain structured verdict, reason code, schema version, and candidate ID; purge free-text reviewer notes after 30 days |
| `newsletter_manual_examples` | 365 days | Retain URL hash, category, verdict, match state, pipeline version, and rubric version; purge source URL, headline, and rationale after 30 days |
| `newsletter_review_batches` | 400 days | Retain immutable population counts, targets, seed, and candidate-ID membership until referenced candidate/label retention completes |
| `newsletter_runs.history_update_json` | Current briefing date only | Clear immediately after application; at briefing-date rollover, set `history_abandoned_at`, mark any still-pre-SMTP edition failed, clear the payload, and permanently forbid that stale edition from sending |
| Other `newsletter_runs` fields | 400 days | Deleted only when no candidate rows reference the run |

**Settled 2026-08-04; clarified 2026-08-05:** Retain source-derived titles, excerpts, delivered text, direct source URLs, manual-example rationale, and reviewer notes for 30 days only. Retain one-way URL/content hashes, non-text candidate metadata, and structured labels for up to one year.

The retention asymmetry is intentional. Metrics join an adjudication only to its exact labelled occurrence; they never require an unlabelled occurrence with the same `story_key`. Manual-example URL matching compares normalized URL hashes and therefore does not require a retained sibling or raw URL after day 30. Title-similarity suggestions are available only while both titles remain inside the 30-day raw-material window. Cleanup deletes dependents in referentially safe order, and tests prove no metric changes when an unrelated unlabelled sibling expires.

There is no automatic export and no automatic Git commit. `--newsletter-labels-export` is an explicit local backup command that writes only IDs, verdicts, reason codes, dates, and aggregate-safe scores to an ignored local path. The transient history outbox is operational state, may contain source-derived text already present in `story_history.json`, and is never included in review exports. It is cleared on successful application or at briefing-date rollover, whichever comes first. A separate human-reviewed promotion command creates a minimal, privacy-screened regression fixture; it must contain no full article text, source URL, or free-text note.

**Settled 2026-08-04:** Raw review material, source links, and reviewer notes remain local and are never automatically exported, committed, or pushed. Only a separately approved, privacy-screened fixture with no raw text, URL, or note may enter the repository.

### 8.3 Pre-SMTP failure and retry contract

The production edition, newsletter run, transient history outbox, all candidate records, and per-story `edition_stories` rows are one transaction: either all exist or none exists. SMTP is forbidden until that transaction commits and `history_applied_at` is durable.

The production CLI acquires the existing build lock, then captures `invocation_started_at = briefing_now()` exactly once and derives `invocation_briefing_date = invocation_started_at.date()`. That frozen date supplies every date-dependent identity and output on the run: log paths, `BriefingBuildResult.briefing_date`, email header, `newsletter_runs.briefing_date`, resume lookup, and candidate rows. Mid-run calls to `briefing_today()` are forbidden on this path. The wall clock is checked once more immediately before history installation; durable history acknowledgement is the delivery-lease commit point. The exact workflow is:

1. Using the frozen invocation date, first abandon any pre-SMTP edition whose `briefing_date` is earlier: set `history_abandoned_at`, set its edition state to `failed`, clear its history payload, and make it permanently ineligible for automatic send or `--email-resend`. Then query for a complete pre-SMTP production edition whose date equals the frozen date.
2. If a matching prepared edition exists, resume that exact stored edition before running the pipeline. Never rebuild, redraft, relabel, or replace its captured date.
3. Otherwise build once with `persist_history=False` and the frozen date. The result contains that date plus a canonical history target and the input and target SHA-256 hashes; the build itself has no history-file or SMTP side effect.
4. Persist the edition, run, history outbox, candidates, and per-story identities in one SQLite transaction. Retry the whole transaction only for `SQLITE_BUSY` or `SQLITE_LOCKED`, at most three total attempts with bounded 100 ms and 250 ms backoffs. Constraint, schema, serialization, disk, and other errors are not retried.
5. If persistence still fails, roll back, raise `NewsletterReviewPersistenceError`, print a specific CLI error, and exit nonzero. No edition fragment, history mutation, or SMTP attempt remains. A later scheduler invocation may perform a fresh build because nothing was durably prepared.
6. If `history_applied_at` is already set on a resumed same-date edition, skip directly to step 7. Otherwise, immediately before history installation, compare `briefing_now().date()` with the frozen date. If it has advanced, abandon the prepared edition as in step 1, raise `NewsletterBriefingDateExpired`, and exit nonzero without changing history or calling SMTP. If the date still matches, atomically install the history target in the same directory as `story_history.json`. The applier succeeds if the current hash matches the recorded input, or treats an already-matching target hash as idempotent success; any third hash is a conflict and is never overwritten. Mark `history_applied_at` and clear the payload in a short SQLite transaction.
7. Durable `history_applied_at` commits a delivery lease for the current invocation. Do not re-evaluate `briefing_today()` between acknowledgement and SMTP: if midnight falls in that narrow interval, send the exact leased edition rather than create an unsent history mutation. If history installation or acknowledgement fails, exit nonzero with the prepared edition and payload intact and do not call SMTP. A later invocation with the same frozen date resumes it; a later-date invocation abandons it under step 1. If installation succeeded but acknowledgement failed, target-hash recognition makes the retry idempotent.

The launchd schedule supplies independent attempts at 08:20, 08:25, 08:30, and 08:35 (`scripts/com.newsagent.briefing.plist`); it does not inspect the prior exit code, use `KeepAlive`, or retry within one long-running process. Therefore nonzero is observable failure, while later fixed-time invocations provide the same-morning retry opportunity. If the 08:35 attempt fails, the prepared state remains for diagnosis but there is no further automatic same-day attempt; operator action is required. Review code itself never initiates a resend.

SMTP outcomes continue through the existing `deliveries` and `editions.state` transaction. Once SMTP has begun, existing failed/indeterminate and explicit resend semantics remain unchanged. Reports continue to exclude every non-accepted edition.

---

## 9. Test cases

New file `tests/test_newsletter_review.py`, plus additions to `tests/test_cli.py`. All use `tmp_path` databases, as the Watchlist reliability tests do (`tests/test_watchlist_reliability.py:308`).

**Schema and migration**
1. A v3 database migrates to v4, writes a `.v3-backup-` file, and creates all five tables with the expected columns, constraints, and indexes.
2. Migration is idempotent: a second `connect()` on a v4 database changes nothing and loses no rows.
3. A v2 database migrates through to v4 in one step, retaining editions/deliveries/quote rows.
4. `user_version = 5` still raises `"created by a newer NewsAgent version"`.

**Persistence and identity**
5. Production preparation writes exactly one occurrence per logical cluster or hard-rejected article; canonical tuple hashing is deterministic, the per-run uniqueness constraint rejects duplicates, and the capture cardinality invariant holds.
6. URL-based `story_key` is stable across headline rewording; URL-less fallback keys are never used to auto-transfer a label.
7. A same-day production rebuild creates a distinct occurrence under a new `run_id`; no label is silently transferred between occurrences.
8. Hard-rejected articles (`quality_gate.py:135`) appear with `filter_stage='quality_gate'`, `filter_reason_code='quality_gate_hard_reject'`, and stratum `hard_reject`.
9. Every filtered occurrence has exactly one branch-owned `filter_stage` and `filter_reason_code`; `legacy_skip_reason` still carries today's diagnostic string verbatim, and `data/skipped_stories_<date>.json` is byte-identical to today's output for the same input. An initial-pool omission later assigned by backfill gets no classification-exclusion event, while every eligible key still absent from final `assignments` gets exactly one `classification_pool_excluded` event before final selection.
10. Selected rows carry `deck_rank`, `selection_phase`, and the **post-compression** `delivered_paragraph` actually emailed, not the pre-compression draft.
11. Per-story `edition_stories` rows are written with the `general:` prefix and do not disturb the `watchlist:%` retention predicates.

**Isolation**
12. `--email-rebuild-today` (test resend) writes zero candidate, run, and label rows.
13. `--dry-run --to email` writes zero rows.
14. `--email-resend` writes zero rows and does not touch `newsletter_runs`.
15. `--email-parity --send` writes zero rows.
16. A production send with `--openai-mode off` writes rows tagged `openai_mode='off'`, and `newsletter_metrics` excludes them.
17. `--review-newsletter-evaluations` does not acquire the build lock: a lock held in another thread does not block it (inverse of `test_build_lock_rejects_contending_thread`, `tests/test_watchlist_reliability.py:586`).
18. An injected `SQLITE_BUSY` or `SQLITE_LOCKED` retries the whole preparation transaction at most three total attempts with the specified backoffs; success creates exactly one edition/run/candidate set, and no history or SMTP side effect occurs before commit.
19. Exhausted lock retries and non-retriable constraint/schema failures roll back the edition, run, history outbox, every candidate, and every per-story identity, raise `NewsletterReviewPersistenceError`, print a specific error, and exit nonzero without changing history or calling SMTP.
20. A history-install or acknowledgement failure after database commit leaves one complete prepared edition and blocks SMTP. A later same-date invocation resumes the byte-identical stored edition without pipeline/model calls; reapplying an already-installed target is idempotent. A fake clock that crosses midnight before history installation abandons the edition without history or SMTP, while a crossing after durable history acknowledgement completes SMTP under the committed lease. Every dated path, header, and row uses the one invocation-captured date; a later-date invocation fails and purges an unleased stale edition, sets `history_abandoned_at`, and makes automatic send plus `--email-resend` refuse it.
21. A missing terminal SMTP write leaves the edition non-accepted and excludes the run from reports; accepted, failed, prepared, sending, and indeterminate multi-recipient cases follow the existing edition aggregation contract.
22. Two independent guards prohibit label leakage: an architecture test prevents `pipeline.py` from importing persistence/review-query modules, and a production-path integration test uses SQLite authorization or tracing to fail any read from `newsletter_adjudications`. A grep for the literal table name alone is insufficient.

**Review loop**
23. An empty eligible queue prints `No pending newsletter evaluations.` and exits 0.
24. `relevant` / `irrelevant` / `unclear` each persist with the correct `subject_type`; an invalid verdict leaves the item pending.
25. `skip` leaves the item pending; `quit` exits without recording the current item.
26. `deck` shows exactly the linked SMTP-accepted day's selected headlines in category/rank order, and `details` reveals the terminal and legacy diagnostics only on request; both re-prompt without writing.
27. A second verdict on the same `candidate_id` is rejected write-once and reported as `already reviewed`, not raised as a traceback.
28. With an active frozen batch, `--review-scope sent` presents only SMTP-accepted selected rows, `filtered` only sampled filtered rows, and `all` alternates; filtered review never falls back to unsampled candidates.
29. At six eligible pilot days, filtered scope and batch creation print `pilot 6 of 7` and write nothing, while `all` reviews sent rows only. A seventh same-version day enables batch creation; a pipeline- or rubric-version change resets progress and every denominator for the new pair, no early filtered adjudication is accepted, and older sent/manual labels remain visible only under their recorded historical version.
30. `--review-limit 3` presents at most 3 items.
31. After an eligible seven-day pilot, explicit batch creation derives per-stratum targets under §6.2, samples uniformly without replacement, saves frame counts/targets/membership, and reproduces the same membership from its seed; no batch spans `pipeline_version` or `label_schema_version`.
32. Review and batch commands reject `--send`, `--dry-run`, and `--alerts` with a parser error.

**Manual examples**
33. Import rejects duplicate normalized `(example_date, source_url_hash)` identities and reports the inserted count.
34. Import rejects empty `why_it_matters`, an unknown `expected_category`, or a non-HTTPS URL.
35. Import rejects `provenance='pipeline_relabel'`.
36. Matching resolves `matched_sent` when the story was delivered, `matched_filtered` when it was a filtered candidate, and `not_retrieved` when absent; all three copy the chosen accepted run's `pipeline_version` and the current `label_schema_version`. Zero eligible runs leave the example pending, while multiple runs require an explicit run-ID choice and `quit` writes nothing.
37. A `not_retrieved` example does not contribute to the gate-miss numerator.

**Metrics**
38. FP rate returns `not yet reportable` at 39 labels and a number at 40.
39. FP rate excludes `unclear` from both numerator and denominator.
40. Per-stratum FN rates, Wilson intervals, and the population-weighted estimate and confidence interval match hand-computed expected values for a completed randomized batch; an unfinished, under-target, or diagnostic batch prints no estimate.
41. A window straddling two `pipeline_version` or `label_schema_version` values reports separate sent, filtered, and manual-example counts for each pair, refuses to pool them into one headline number, and prints `not yet reportable` independently for underpowered historical versions.
42. `unclear_share > 0.20` emits the rubric warning.
43. Per-category FP rate is suppressed below 15 labels in that category.

**Retention**
44. A 31-day-old candidate has its headline, raw URLs, excerpt, and delivered paragraph nulled while `story_key`, `url_hashes_json`, and `excerpt_purged_at` remain.
45. A 31-day-old labelled candidate and manual example lose free text and raw URLs while structured verdicts and normalized URL hashes remain matchable.
46. Deleting a 181-day-old unlabelled occurrence does not change metrics for a labelled occurrence sharing its `story_key`; a 366-day-old candidate, label, and manual-example metadata is deleted in referentially safe order.
47. The explicit local export contains no title, URL, excerpt, delivered paragraph, free-text note, or history-outbox payload.
48. History payloads clear immediately after acknowledgement and at date-rollover abandonment; cleanup never deletes or redacts an active same-date prepared payload before resume.
49. Retention is idempotent — a second pass reports zero further changes, and referenced batch/run rows survive until their dependents expire.

**Regression fixtures (Phase N8)**
50. Locked-fixture replay: for each case the current gate/stage classifier reproduces the recorded decision; a deviation fails with the case id and both decisions (same shape as `tests/test_importance_replay.py`).
51. The structured local export is stable-sorted and round-trips without field loss; every repository-fixture case declares `text_origin='synthetic'`, and a privacy assertion forbids URLs, raw source text, delivered paragraphs, and reviewer notes.

---

## 10. Implementation phases

Each phase is independently reviewable and leaves the system working. Phase boundaries follow production risk rather than line-count estimates.

| Phase | Content | Exit criterion | Depends on |
| --- | --- | --- | --- |
| **N1 — Schema** | `SCHEMA_VERSION = 4`, five newsletter tables, history-outbox lifecycle fields, v3→v4 backup, constraints, indexes, migration row. | Tests 1–4; migration and rollback verified against copied state | — |
| **N2a — Pure capture** | `DecisionEvent`, `SelectionOutcome`, canonical identities, strata, record builders, deterministic history target/hash builder, and `BriefingBuildResult` fields; no database or mailer changes. | Tests 5–10 plus pure history hash/idempotency cases; capture cardinality invariant holds on representative pipelines | N1 |
| **N2b — Transactional preparation** | `record_newsletter_run`; `persist_review_state`; atomic edition/run/history-outbox/candidate/per-story writes; bounded SQLite retry; atomic history apply; same-date resume and rollover abandonment. | Tests 11–20; injected failures prove rollback or resumable prepared state before SMTP | N2a |
| **N2c — Delivery attribution and isolation** | Join candidates through runs to existing edition state; production-query guard; SMTP/resend guards for unapplied or abandoned history; no candidate-level delivery state. | Tests 21–22 plus accepted/failed/indeterminate multi-recipient integration cases | N2b |
| **N3 — Review CLI + batches** | Review flags and loop; `deck`/`details`; pending queries; write-once labels; pilot guard; explicit pilot-derived frozen batch construction. | Tests 23–32 | N2c |
| **N4 — Manual examples** | Add/import/review commands; hash-based matching and match resolution. | Tests 33–37 | N3 |
| **N5 — Metrics + export** | Metrics, intervals, report, local export, and denominator gating. | Tests 38–43, 51 | N3, N4 |
| **N6 — Retention** | Newsletter cleanup clauses, transient history-payload lifecycle, raw-field nulling, dependency-safe deletion. | Tests 44–49 | N2b |
| **N7 — Pilot + labelling window** | *No code.* Freeze selection configuration, model IDs, and rubric; observe at least seven same-version production days, freeze calibrated targets, then review 10–15 min/day until every §7.5 denominator is met. Export structured labels after each session. | All success criteria in §12; duration extends or restarts when a stratum is short or the version changes | N5, N6 |
| **N8 — Controlled improvement** | Build a privacy-screened regression fixture; propose one gate change at a time; shadow-replay locally; measure; record a decision. | Test 50 and owner approval | N7 |

N2 is intentionally split because capture correctness, pre-SMTP transactional preparation, and delivery attribution have different failure modes. Normal successful sends keep identical content, model usage, and SMTP behavior. N2b deliberately adds one failure-policy change: review-state persistence or history acknowledgement failure aborts before SMTP instead of sending an unmeasurable edition, while the fixed launchd attempts resume complete prepared state within the same briefing date.

The history outbox remains inside N2b rather than becoming N0: its storage is created by N1, and activating it separately from the edition/run/candidate transaction would temporarily permit either pre-transaction history mutation or an unmeasurable send. N2b therefore has three mandatory internal rollout gates: (a) pure target/hash/atomic-install and frozen-clock fault tests, (b) transaction/resume/abandonment integration tests with scheduled production activation disabled, and (c) one end-to-end activation only after both suites pass. No intermediate release may enable only half of the contract.

---

## 11. Using the labels (and how not to)

### 11.1 Locked regression fixtures

Once the §7.5 targets are met, use the local labelled corpus to author a curated `tests/fixtures/newsletter_labels/gate_cases_v1.json`. Repository fixtures contain structured scores and decisions plus human-authored synthetic text needed to exercise deterministic gates; every case declares `text_origin='synthetic'`, and no raw source title, excerpt, URL, delivered paragraph, or reviewer note is copied from the export. Test 50 replays the deterministic parts of the gate against this fixture, locking behaviour the way `importance_selection_replay.json` locks importance banding. Shadow evaluation still runs locally against the full retained labelled corpus; the repository fixture is a safe regression subset, not a raw export.

Adversarial seeding, mirroring the Watchlist's adversarial seed set: a one-source scoop from a credible outlet; a three-source rewrite of a press release; a genuine follow-up wrongly marked stale by `apply_history` (`history.py:97`); two same-event clusters the duplicate gate should have merged; a teaser headline `TEASER_TITLE_RE` (`quality_gate.py:25`) catches correctly; and a substantive analysis piece it catches *incorrectly*.

### 11.2 Controlled quality-gate change protocol

Every change follows the same five steps, one change at a time:

1. **Diagnose from reason codes and the §7.4 disagreement breakdown**, not from the aggregate rate. `gate_too_strict` appearing 12 times on selection-stage candidates whose legacy diagnostic is `no reliable source confirmation` is a hypothesis; a 14% FN rate is not.
2. **Propose one causal parameter change** — a `QualityGateConfig` threshold (`models.py:88`), `minimum_story_evidence_score`, a duplicate/selection rule, or a prompt clause. Changing only `skipped_log.py` labels cannot improve selection because those strings are diagnostic rather than causal.
3. **Shadow-replay** against the full labelled set offline. Report the FP and FN deltas *and* how many previously-correct decisions flip. A change that fixes 6 misses while creating 9 new false positives is a regression, not an improvement.
4. **Record a decision** in `docs/decisions.md` via the `decisiontracker` skill: parameter, before/after metrics, label denominator, and the labels used.
5. **Bump `pipeline_version`** so post-change labels are not pooled with pre-change ones, then re-measure over a fresh window.

**Settled 2026-08-04:** No quality-gate change may affect daily emails until it has been shadow-tested against the reviewed corpus, its improvements and regressions have been shown to the owner, and the owner explicitly approves it.

### 11.3 Explicitly forbidden

- **No automatic threshold tuning.** No optimiser, no gradient, no nightly retrain.
- **No labels in the send path.** `pipeline.py` never reads adjudications; a label must never suppress or promote a story at build time (test 22). Otherwise the measurement instrument becomes part of what it measures and the metrics stop meaning anything.
- **No fine-tuning or few-shot injection of labelled examples into classifier/drafting prompts** without a separate explicit decision — that is a model-behaviour change with its own cost and evaluation needs, not a gate tweak.
- **No metric reported below its minimum denominator** (§7.5).
- **No pooling across `pipeline_version` or `label_schema_version`.**

---

## 12. Success metrics for this plan

At the end of Phase N7:

| Criterion | Target |
| --- | --- |
| Sent stories labelled | ≥ 40, with ≥ 15 in each of ≥ 3 categories |
| Filtered candidates labelled | Every frozen-batch target in §6.2 met; expected floor ≥ 100, extended as needed |
| Manual examples adjudicated | ≥ 15 |
| `unclear_share` | ≤ 20% for both subject types |
| Reviewer burden | ≤ 15 minutes/day sustained |
| Delivery impact | Normal path: zero content, SMTP, or per-run OpenAI-cost change. Fault path: bounded lock retries, nonzero failure, same-date prepared-edition resume, and date-rollover abandonment follow §8.3; SMTP never receives an unmeasurable or stale prepared edition. |
| Label durability | 100% of retained structured labels recoverable from the explicit local backup after a database restore |
| Regression-fixture source set | Enough reviewed cases to author a ≥100-case privacy-screened fixture in Phase N8 |

And in Phase N8: at least one ≥100-case privacy-screened fixture passes, plus one gate change ships with a measured before/after on the labelled set and a decision record — **or** a documented finding that no change is warranted, which is an equally valid outcome.

The system **fails** if labels accumulate but no metric ever crosses its minimum (reviewer burden too high — cut `--review-limit` and narrow the queue), or if `unclear_share` stays high (rubric too vague — rewrite §6.1 before collecting more labels).

---

## 13. Risks

| Risk | Severity | Mitigation |
| --- | --- | --- |
| **Reviewer fatigue** — 215 filtered candidates/day is overwhelming | High | Frozen randomized batches, `--review-limit 20`, 10–15 min/day. If it still stalls, lower batch size without changing the sampling frame. |
| **Sampling bias or underpowered strata** | High | Seven-day pilot, frozen per-stratum targets, uniform within-stratum sampling, population weighting, and confidence intervals (§§6.2, 7.2). |
| **Acting on tiny denominators** | High | Hard minimums (§7.5); the report refuses to print a rate below them. |
| **Reviewer drift** — the standard shifts over months | Medium | `label_schema_version` on each label; re-review a 10-item control set at the start of each new window and compare agreement. |
| **Identity collision or drift** — headline edits, missing URLs, or merged clusters can describe the same event differently | High | Immutable run occurrences plus URL-based `story_key`; URL-less fallbacks never auto-transfer labels; hard-rejected articles use their own identity kind. |
| **Migration risk to a live database** | Medium | Automatic `.v3-backup-` before migrating, mirroring the v2 path (`mailer/state.py:133`); additive `CREATE TABLE IF NOT EXISTS` only; no existing table altered except additive `edition_stories` rows. |
| **State growth** — ~215 rows/day ≈ 78k rows/year | Low | Raw fields purge at 30 days, candidate/label metadata expires within one year, and batch/run frames expire by 400 days; monitor database size during the first review window. |
| **Review-state corruption or split-brain history** | High | Edition, run, history outbox, all candidates, and per-story identities are one pre-SMTP transaction; hash-checked atomic history application is idempotent; SMTP requires durable acknowledgement. |
| **Transient persistence failure delays delivery** | High | Retry only SQLite busy/locked errors with bounded backoff; fixed 08:20/25/30/35 launchd attempts resume a complete same-date edition; nonzero exits remain observable, and stale prepared editions are abandoned at date rollover. |
| **A run crosses briefing-date rollover** | Medium | Capture one invocation date under the build lock, pass it to every dated output, recheck before history installation, and treat durable history acknowledgement as the delivery-lease commit point (§8.3). |
| **Filter diagnostics anchor the reviewer** | Medium | Hide terminal and legacy diagnostics until `details`; provide the accepted daily deck through `deck` so the reviewer can apply the comparative rubric without first seeing the pipeline's rationale. |
| **Pilot labels bias the first filtered frame** | Medium | Permit sent/manual review during the seven-day pilot but reject every filtered adjudication until an explicit version-scoped batch is frozen. |
| **Pipeline churn repeatedly resets denominators** | Medium | Start N7 only after N1–N6 and a version freeze; retain pre-change labels as visible historical evidence but never pool them into the new selector's FP, FN, or miss rate. Timeline extension is explicit. |
| **Labels leaking into the pipeline** | High if it happened | Test 22 asserts the pipeline never reads the label tables. |
| **Manual examples degenerating into relabelling** | Medium | `provenance` required and `pipeline_relabel` rejected at import (test 35); `not_retrieved` vs `matched_filtered` tracked separately so sourcing misses are not blamed on the gate. |
| **Scope creep into automatic training** | Medium | §11.3 is a hard constraint; changing it requires its own decision record. |
| **Two review systems diverging** | Low | Newsletter tables and CLI deliberately parallel the Watchlist ones; shared conventions (`_now()`, upsert style, write-once verdicts, backup-before-migrate) rather than shared code, because the metric semantics genuinely differ. |

---

## 14. Open decisions

Each should end in a `docs/decisions.md` record.

1. **Diagnostic queue ordering.** **Settled 2026-08-06:** Keep population metrics limited to frozen randomized batches and order the separate non-metric diagnostic queue oldest-first. Revisit disagreement-focused sampling only as a distinct later policy decision.
2. **Sent-story review completeness.** **Settled 2026-08-06:** Sample SMTP-accepted sent stories across briefing days. A single daily deck is highly correlated with that day's news, so broader multi-day coverage is preferred over complete chosen-day decks.
3. **Historical backfill.** **Settled 2026-08-06:** Start clean. Do not import old JSON logs, including as non-metric review material, because they lack reliable decision-stage events and occurrence identities.
4. **Per-category minimums.** **Settled 2026-08-06:** Keep the initial floor at 15 sent-story labels per category, including Culture. Reassess a Culture-specific increase only after a version-scoped pilot provides evidence.
5. **Persist `--openai-mode off` production sends at all?** **Settled 2026-08-06:** Persist and tag them. Skipping is simpler but loses exactly the budget-constrained days that are most useful to understand; version-scoped metrics prevent incompatible pooling.

---

## 15. Review resolution

The 2026-08-05 design review is incorporated into the normative sections above:

1. Candidate IDs use canonical tuple encoding, while recognizing that the originally cited collision cannot occur under the current fixed `run_id` and constrained-kind contract (§3.2).
2. Candidate-level delivery state and the unsupported `unknown` repair state are removed; sent exposure derives from the existing edition/delivery state machine (§§3.2, 8.1, 8.3).
3. Strata are occurrence-scoped, ordered, and mutually exclusive; cross-run `story_key` matches never transfer strata or labels (§6.2).
4. Fixed sampling percentages are replaced by a seven-day pilot, frozen population counts, explicit per-stratum targets, and confidence intervals (§§6.2, 7.2, 7.5).
5. Retention asymmetry, hash-only matching after raw-field purge, and sibling-independence are explicit (§8.2).
6. Label isolation uses both an architecture boundary and a runtime database-read guard, not literal-string grep alone (§9 test 22).
7. Terminal decision ownership and call sites are mapped, with one-outcome and cardinality invariants (§4).
8. Phase N2 is split by risk boundary instead of justified by an unsupported line-count estimate (§10).
9. Additional review corrections add the missing review-batch schema, reconcile `selected` versus `sent`, remove the nonexistent candidate `edition_id` index, and separate privacy-screened fixtures from raw local exports (§§2, 3.2–3.5, 8.1–8.2, 9, and 11.1).

## 16. Post-revision review resolution

The Claude post-revision note dated 2026-08-05 is resolved as follows:

1. **Selection diagnostics and reviewer context:** The suggested emphasis on selection sub-reasons would anchor the verdict on the pipeline's own explanation. The corrected prompt instead hides terminal and legacy diagnostics by default, exposes them through `details`, and adds `deck` so the reviewer can inspect the exact accepted comparison set required by the rubric (§§5.1, 6.1; tests 26 and 28).
2. **Retriability and scheduler behaviour:** The plan now specifies a history outbox, bounded retries for SQLite busy/locked only, a dedicated nonzero persistence error, idempotent hash-checked history application, same-date prepared-edition resume, date-rollover abandonment, and the actual four fixed launchd attempts (§§3.1, 8.2, 8.3; tests 18–21).
3. **Pilot review availability:** Sent-story and manual-example review remain available during the pilot; filtered labels are deliberately refused until seven eligible same-version days exist and an explicit immutable batch is frozen. This avoids consuming or biasing the first randomized frame (§6.2; tests 29 and 31).
4. **Stale `unclear` observation:** No revision is needed. §14 item 4 is already the per-category denominator question; the earlier `unclear` open decision had been removed, and §§3.5, 6.1, and 7.2 remain the settled contract.

---

## 17. Third-pass review resolution

The Claude third-pass note dated 2026-08-05 is resolved as follows:

1. **History-outbox phase boundary:** The risk is valid, but N0 is not: the outbox depends on the N1 schema and is the atomic bridge between review persistence and history. §10 keeps it in N2b and adds three mandatory internal rollout gates so installer/clock faults and transaction/resume faults pass independently before production activation.
2. **Rollover clock:** Accepted. The production path now captures one `briefing_now()` date under the build lock, passes it to every dated artifact, rechecks immediately before history installation, and treats durable history acknowledgement as the delivery-lease commit point (§§3.1, 4, 8.3; test 20).
3. **Classification finalization:** Accepted. `finalize_classification_outcomes` now has an exact call site after backfill assignments are merged and an exact eligibility predicate; initial-pool omissions are not terminal (§4; test 9).
4. **Cross-references:** Audited. §15 item 9 now points to the specific subsections that implement its four listed corrections; §§15–17 use current test numbers and existing headings.
5. **Labels across version changes:** The timeline concern is valid, but cross-version pooling is rejected because FP, FN, and miss rates measure the selector that produced the candidates. Old sent and manual labels remain visible historical evidence under their version and may be underpowered; N7 starts after a version freeze, and any later version change explicitly restarts the pilot and denominators. Manual examples now record both version dimensions so `not_retrieved` cases are attributable (§§3.4, 6.2, 6.4, 7, 10; tests 29, 36, and 41).
