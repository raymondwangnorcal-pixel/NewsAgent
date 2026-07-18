# ADR-0001: Soft content-quality scoring instead of hard rejection

## Status

Accepted

## Context

The uncommitted `src/news_agent/quality_gate.py` hard-rejects articles pre-clustering using regex
heuristics: summary too short (<80 chars), summary near-duplicates the title (Jaccard > 0.85),
teaser-style titles without a concrete fact, and catalyst-less stock-tip headlines. A hard reject
is unrecoverable — if a heuristic misfires on a real story, that story is silently gone with no
way to observe or correct it later. `ceo-review` (SCOPE EXPANSION) flagged this as the core risk
of the current design: a regex list can only grow, and nothing measures whether it's helping.

`StoryCluster` already carries a `quality_score` field (`source_balance.cluster_quality_score`) —
a **source-reputation** signal (AP/Reuters/Bloomberg score higher), unrelated to article content.
That name is taken; the new signal needs a distinct name to avoid confusion.

## Decision

- Reserve hard rejection for near-certain junk only: empty/whitespace-only summary, and
  summary-duplicates-title (Jaccard > 0.85 or exact match). These stay in
  `data/quality_gate_rejections_{date}.json` exactly as today (filename/format unchanged for
  continuity with data already being logged).
- Everything else (thin-but-nonempty summary, teaser title, catalyst-less stock tip) becomes a
  **soft penalty**: a new frozen-dataclass field `Article.content_quality_penalty: float = 0.0`,
  set via `dataclasses.replace()` after regex scoring, before `cluster_articles()` runs (articles
  are frozen; clustering must consume already-scored articles).
- `StoryCluster` gets an analogous `content_quality_penalty: float = 0.0` field, computed inside
  the existing `score_clusters()` loop (`scoring.py`, alongside `cluster.quality_score =
  cluster_quality_score(cluster)`) as the **mean** of `content_quality_penalty` across the
  cluster's articles (equivalently: the penalty scales with the fraction of the cluster's
  coverage that's junky). A single teaser-headline article alongside one clean report only
  moderately dilutes the penalty rather than zeroing it, and a cluster that's mostly junk with one
  weak clean corroborator stays meaningfully penalized rather than being fully absolved.

  MIN-aggregation was the original design (rationale: "one clean source vouches for the whole
  cluster") but was rejected during `validate` after independent review showed it combines badly
  with `scoring.py`'s pre-existing source-count-rewards-credibility mechanics
  (`frequency_score`, `source_balance_score`, the multi-source bonus): under MIN, a cluster with 5
  junk articles and 1 clean one would fully zero its content penalty *and* outrank a solitary
  clean single-source report, because more sources earn more credit independent of content
  quality. Mean-aggregation directly ties the penalty to how junk-heavy the cluster's coverage
  actually is, closing that gap.
- `content_quality_penalty` subtracts from `total_score` in `scoring.py`, as a new term separate
  from `quality_score` (source reputation, unchanged) and `impact_score`.
- After category assignment, clusters whose mean `content_quality_penalty` meets the configured
  `low_content_quality_skip_threshold` receive the existing `"low content quality"` skip reason.
  They remain in the ranked cluster list and skipped-story audit, but cannot enter a published
  briefing section or breaking-news alert.
- The penalty is capped (max 2.5) to bound compounding with the existing single-source-cluster
  penalty (`scoring.py` `total_score -= 1.5` when `source_count == 1 and impact_score < 3.0`) and
  the existing `quality_score < 0.55` skip check in `skipped_log.py` — three independent
  mechanisms can now all fire on the same single-source, low-signal cluster. This is not double
  counting the same signal, but it is compounding across signals, so it needs an explicit test
  (single-source cluster + max content penalty) rather than being assumed safe.
- `skipped_log.py` gets a new skip-reason branch (`"low content quality"`) distinct from the
  existing `"low source quality"` branch, so the CLI debug output distinguishes *why* a story was
  skipped — this is what closes ceo-review's "feedback loop" requirement, reusing the existing
  skipped-stories reporting mechanism instead of building a parallel one.

## Consequences

- A terse-but-real story stays available for clustering and audit; clusters that remain below the
  configured quality bar are explicitly quarantined before publication and recorded as such.
- `quality_gate.py` keeps its name and its rejection-log filename (only the narrow hard-reject set
  changes); no unrelated file renames.
- Requires a dedicated test proving the single-source compounding-penalty scenario doesn't over-
  suppress legitimate single-source breaking news.
