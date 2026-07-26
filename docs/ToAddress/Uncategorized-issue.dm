# The "Uncategorized" Skip Bucket

**Status:** Diagnosed, not yet fixed.
**Date:** 2026-07-25

## Summary

Across a sample of skipped-story logs (`data/skipped_stories_2026-07-1[7-9]*.json`,
`data/skipped_stories_2026-07-2*.json`, ~7 days), the `uncategorized` bucket accounts
for 561 skipped clusters — far more than any real category:

| Category | Skipped |
| --- | ---: |
| uncategorized | 561 |
| culture | 35 |
| finance | 27 |
| global | 15 |
| domestic | 15 |
| business_tech | 9 |

Within the 561 `uncategorized` rows:

| Reason | Count |
| --- | ---: |
| insufficient story context | 379 |
| no reliable source confirmation | 172 |
| category already full | 6 |
| low content quality | 4 |

**109 of the 561 (≈19%) carry a nonempty `watchlist_match`** — i.e. they're about
topics (`AI`, `NVDA`, `AAPL`, `IPOs`, `inflation`, ...) the user explicitly
configured the agent to track. All 109 show `importance: 0`, meaning they were
never scored by the importance system at all.

## Mechanical root cause

`cluster.category` starts as `""` and is only ever set by `apply_category_assignments()`,
which only runs on clusters that made it into the classification pool. "Uncategorized"
is not a category the pipeline assigns — it is the absence of any classification
attempt. Two independent, earlier pipeline steps can produce that absence:

### Gap A — the evidence gate runs before classification, on the full cluster list (379 rows)

```python
apply_evidence_gate(clusters, minimum_evidence)               # pipeline.py:621 — runs on ALL clusters
candidates = select_classification_candidates(clusters, ...)  # pipeline.py:622 — pool built only after
```

`apply_evidence_gate()` sets `skip_reason = "insufficient story context"` on any
cluster with `evidence_score < minimum_story_evidence_score` (currently `1.2`,
`config/sources.toml`), before that cluster is ever considered for classification.
A story can fail purely on thinness of feed/page content and never get a chance
to be judged on newsworthiness at all.

### Gap B — the classification pool itself is capped, independent of quality (up to 172 rows)

```python
GLOBAL_CLASSIFICATION_POOL_SIZE = 30
CATEGORY_CLASSIFICATION_RESERVE = 10
MAX_CLASSIFICATION_POOL_SIZE = 80
```
(`pipeline.py:77-79`)

Only 80 clusters per run ever reach `classify_clusters()`. Everything else — even
a cluster that cleared the evidence gate cleanly — never gets a category, never
gets an importance score (importance is computed only after classification, which
is why every uncategorized row shows `importance: 0`), and falls through to
`skip_reason()`'s generic checks in `skipped_log.py`:

```python
if cluster.source_count <= 1 and cluster.impact_score < 3.0:
    return "no reliable source confirmation"
```

This branch fires on these clusters regardless of pool status — so a meaningful
chunk of the 172 "no reliable source confirmation" rows are single-source stories
that were never actually evaluated for corroboration; they simply didn't make the
top-80 cut.

## Why this matters more than it looks

- The 561-row bucket dwarfs every real category's skip count combined.
- ~1 in 5 of those rows are about explicitly watchlisted topics, dropped before
  the classification/importance system ever sees them — not after an editorial
  judgment rejected them.
- Any downstream fix that operates after classification (e.g. source-tier scoring,
  corroboration weighting, presentation ordering) has **zero effect** on this
  bucket, because these clusters never reach that stage of the pipeline at all.

## What's needed before fixing it

The pipeline already emits diagnostics that can separate Gap A from Gap B precisely:

- `fetched_articles_by_feed_hint`
- `classification_pool_by_feed_hint`
- `insufficient_context_by_feed_hint`

(all added by the `culture-briefing-consistency` work, see
`docs/plans/completed/culture-briefing-consistency.md`)

Running `--show-diagnostics` on a live dry run would show the exact pre-pool
cluster count vs. the 80-slot classification cap vs. the evidence-gate reject
count. That would tell us whether the right fix is:

1. raise `GLOBAL_CLASSIFICATION_POOL_SIZE` / `MAX_CLASSIFICATION_POOL_SIZE`,
2. lower `minimum_story_evidence_score` slightly, or
3. neither — i.e. the pool cap is fine and most of this is genuinely thin content,

rather than guessing from skip-log aggregates alone, which is as far as this
diagnosis goes today.

## Non-goals / explicitly not the fix

- This is not solved by `docs/plans/source-restructure-synthesized.md` (or its
  sibling drafts) — that work all operates downstream of
  `apply_category_assignments()`, which none of these 561 clusters ever reach.
- Not proposing a specific threshold change yet — no diagnostics run has been
  done to confirm which of Gap A / Gap B dominates in practice; this doc is the
  diagnosis, not the implementation plan.
