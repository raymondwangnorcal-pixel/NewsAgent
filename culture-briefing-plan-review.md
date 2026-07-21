# Culture Briefing Plan — Review

Review of the 9-point plan against the current implementation (`pipeline.py`, `classify.py`, `enrichment.py`, `scoring.py`, `cli.py`, `config.py`, `models.py`, `evidence.py`, `config/sources.toml`).

The diagnosis is right: Culture feeds (ESPN 0.7, Variety 0.75, Google News Culture 0.68) plus a market/social-only `impact_score` keyword set mean Culture loses the global-score race at every stage. Several items below have gaps against the actual code that should be resolved before implementation.

## 1. Reserve Culture candidates before the global top-50 cutoff

- Feed tags live on `Article.feed_categories`, not on `StoryCluster`. A cluster-level tag rule is needed (any-article match vs. majority vote). The Verge's dual tag (`business_tech`, `culture`) means its clusters would reserve slots in *two* categories — decide if that's intended.
- The 75 cap trims a possible 80-story union, but trim order is undefined. Specify it (global first, then reserves round-robin by rank), or drop the cap since dedup usually lands under 75 anyway.
- Bigger issue: pre-filter the pool by `evidence_score >= 1.2`. `apply_evidence_gate` currently runs *after* classification, so reserved Culture slots can be spent on stories that are guaranteed to be gated out later.
- The acceptance test should pin down the tag-derivation rule, or an NPR-sourced culture story (empty `feed_categories`) will silently fail it.

## 2. Reserve enrichment requests by category

- "6 attempts" ≈ 3 clusters at `max_articles_per_cluster = 2`. That's thin for a 6-story target once gate failures are factored in. Reserve *clusters*, not raw page attempts.
- Reserved clusters may sit beyond `max_clusters_per_run = 40`; the selection loop in `enrich_clusters` needs restructuring, not just a budget-math change.
- Google News Culture redirects mostly die at `redirect_not_permitted` — only `espn.com` and `variety.com` currently have `article_text` policies. A failed attempt is not "unused" budget; reserved attempts will burn on aggregator dead-ends. Prefer policy-covered URLs when spending reserved budget.

## 3. Separate "no drafting" from "no classification"

- `draft.py` is missing from the plan's file list, but `draft_paragraphs` checks `openai_mode != "off"` — passing `"classify-only"` through unchanged would *enable* LLM drafting rather than disable it. Cleanest fix: resolve the mode into two booleans (`classify_llm`, `draft_llm`) at the pipeline boundary so downstream modules stay binary.
- There's a third OpenAI capability the two-way split ignores: `judge_ambiguous_articles` in the quality gate, gated on `resolved_mode != "off"` inside `collect_pipeline_context`. Decide explicitly which of the three modes should run it.

## 4. Make the deterministic fallback category-balanced

- Using "fewer assigned candidates" as the *first* tie-break criterion makes the fallback order-dependent — output shifts with the ranking order between runs — and lets balance override signal. Swap criteria 1 and 2: `feed_source_type` first, balance only on true ties.
- `source_type` values ("business", "tech", etc.) don't map 1:1 to category names — an explicit mapping table is needed.

## 5. Score cultural importance explicitly

- This *is* a second keyword taxonomy, in tension with the plan's own prohibition in this same section. Acceptable to live in `scoring.py`, but: terms like "championship" or "record revenue" will fire on sports previews and finance stories too — gate the bonus on a culture feed-tag hint.
- Specify the weight inside `total_score`: `impact_score` is currently multiplied by ×2.4, so an unweighted flat +2.0 addition is a large, uncalibrated distortion.
- Once items 1 and 2 land, this may turn out to be unnecessary — make it conditional on what the new diagnostics (item 9) actually show.

## 6. Add direct Culture discovery channels

- The checklist is good but understates one requirement: every new domain needs an `[[extraction_policies]]` `article_text` entry, or its stories hit `domain_not_permitted` and likely fail the evidence gate.
- Prefer *overlapping* outlets (THR + Deadline + Variety) over one-off additions. Single-source clusters below `impact_score` 3.0 eat a −1.5 penalty in `score_clusters`, so coverage overlap that yields 2-source Culture clusters is itself a scoring fix, not just a coverage one.

## 7. Introduce Culture lanes

- "Maximum 2 sports stories" is redundant with "maximum 2 per lane," since sports is itself a lane — drop one of the two rules.
- `CLASSIFY_SCHEMA` uses `strict: True` JSON schema mode, so `culture_lane` must be a required property on every assignment (empty/null for non-Culture categories).
- The culture selector must *replace*, not layer on top of, `top_for_category`'s existing `max_per_source = limit // 2` logic, or the two constraints will fight each other.

## 8. Add a minimum section target with controlled backfill

- This is a larger refactor than described. Enrichment currently runs once, early, against *preliminary* clusters — before the quality gate and before final reclustering. A mid-pipeline backfill pass means calling `enrich_clusters` again on final cluster objects, then re-running `apply_cluster_evidence_scores` and the evidence gate for just the affected clusters. Budget this as structural work, not a simple loop addition.

## 9. Add category-health diagnostics

- `cluster.category` is empty until classification runs, so `enrichment_attempts_by_category` and `insufficient_context_by_category` (for candidates that never made the pool) can only ever be feed-hint-based, not true-category-based. Name the fields accordingly, or add an explicit `"unclassified"` bucket, or the numbers will mislead readers of `--show-diagnostics`.

## Recommended ordering — one change

Move diagnostics (item 9) to step 1 instead of step 8. It's cheap to build, it establishes a measurement baseline before any tuning happens, and it's the evidence needed to decide whether item 5 (culture-impact scoring) is even necessary. Otherwise, the plan's stated order is correct, and the claim that items 1–3 solve most of the day-to-day inconsistency holds up against the code.
