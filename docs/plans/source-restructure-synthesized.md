# Source Restructuring: Synthesized Plan

Synthesizes `docs/plans/PreSourceUP.md` (scoped to this codebase's size) and
`docs/plans/source-system-restructure.md` (rigorous provenance model, built for a
much larger system) into one implementable plan, optimized for **factual accuracy**
and a **$5–10/month** OpenAI cost band.

## Context

You asked for a per-category primary/secondary/specialist source hierarchy with two
hard rules: never publish a major story as single-sourced when independent
confirmation exists, and never count syndicated copies of the same AP/Reuters
report as multiple confirmations. Two plans were drafted independently.
`PreSourceUP.md` is right-sized for this ~4,300-line personal tool but under-guards
against false-positive wire attribution. `source-system-restructure.md` gets the
accuracy principles right but proposes machinery (a full identity model, active
verification search, a phased legacy/shadow/enforce rollout, 15 touched files) that
adds cost and complexity this project doesn't need. This plan takes the accuracy
principles from the second and the implementation scope of the first.

## What's adopted from each, and why

**From `PreSourceUP.md` (kept as-is):**
- `[source_tiers.<category>]` config shape, one source per role — the richer
  multi-source-per-role arrays in `source-system-restructure.md` aren't needed
  since you specified exactly one outlet per role.
- Reuters/AP acquired via Google-News domain-scoped proxy feeds
  (`site:reuters.com` / `site:apnews.com`) — both have had no public RSS feed
  since ~2020, this is the only real acquisition path.
- **`fetch.py` needs zero changes** — `_article_source`/`_clean_title` already
  resolve the real outlet name for any `"Google News *"` feed via the RSS
  `<source>` tag. `source-system-restructure.md`'s `DiscoveryChannel`/
  `FetchOutcome` abstraction would rebuild something that already works.
- Extending existing files (`source_balance.py`, `pipeline.py`, `draft.py`) rather
  than the second plan's 6 new modules
  (`source_discovery.py`/`attribution.py`/`syndication.py`/`source_system_report.py`).
- Soft ranking boost for primary-source presence, not a hard gate — both plans'
  own stated invariants agree a missing primary source shouldn't block a
  well-confirmed story found elsewhere.

**From `source-system-restructure.md` (adopted, scoped down):**
- **"Favor false negatives over false positives" for wire attribution** — this is
  the one real gap in `PreSourceUP.md`'s original design. A body-text-similarity
  match alone must NOT collapse two articles into one counted source; it's only
  strong enough to be logged as "possibly the same wire report," never strong
  enough to reduce a story's corroboration count. An unconfirmed duplicate left
  uncollapsed is a much smaller accuracy risk than a false merge that erases real
  independent confirmation.
- **Publish / attribute / hold framing for single-source major stories** — adopted,
  but resolved through the *existing* `draft.py` LLM call (already asks for
  hedged language) instead of a new verification-search pipeline stage. No new
  API calls, no added cost.
- **Specialist context is explicitly not corroboration credit** — stated as its
  own rule so it doesn't get conflated with the corroboration count during
  implementation.
- **Explainable audit logging** — adopted as a lightweight extension of the
  existing `write_category_assignments`-style log, not a new versioned schema
  system.

**Explicitly cut** (real cost/complexity with no payoff at this scale):
- The full identity model (`publisher_id`, `discovery_channel_id`,
  `canonical_url`, `origin_instance_key`, `content_fingerprint`, etc.) — replaced
  by one canonicalization function.
- **Active bounded verification search** (`source-system-restructure.md` Phase 4)
  — new runtime Google queries to hunt for corroboration on ambiguous stories.
  This is the single biggest cost/complexity driver in that plan and isn't
  necessary: the existing fetch pool (13+ feeds, up to 240 articles/day) is
  already a large corroboration surface.
- Material-conflict detection (`conflict_status`, disputed-figure hedging) — needs
  new claim-level extraction/comparison across articles; not requested in the
  original ask and not achievable without a new LLM analysis pass.
- The full `StoryPackage` structured-extraction contract — replaced by adding a
  few fields (`corroboration_status`, `source_roles`) to the existing
  `DraftCandidate`, consumed by the *existing* draft prompt.
- `config/source_hierarchy.toml` as a parallel config system — folded into
  `sources.toml`.
- Legacy/shadow/enforce runtime mode-switching + 6-phase rollout — replaced by
  this project's established pattern: implement on a branch, checkpoint commits,
  test, live smoke-test, merge (used successfully for both the quality-gate and
  briefing-redesign work already in this repo's history).
- Extending `alerts.py` to share the gate — genuinely out of scope; flagged as a
  natural follow-up, not part of this plan.

## Design

### 1. Config: `[source_tiers.<category>]` in `sources.toml`

```toml
[source_tiers.business_tech]
primary = "Reuters"
secondary = "Bloomberg"
specialist = "Financial Times"

[source_tiers.domestic]
primary = "Associated Press"
secondary = "Reuters"
specialist = "The New York Times"

[source_tiers.global]
primary = "Reuters"
secondary = "Associated Press"
specialist = "BBC News"

[source_tiers.culture]
primary = "Variety"
secondary = "The Hollywood Reporter"
specialist = "Billboard"

[source_tiers.finance]
primary = "Bloomberg"
secondary = "Reuters"
specialist = "Financial Times"
```

`SourceTierConfig(primary, secondary, specialist)` in `models.py`;
`AgentConfig.source_tiers: dict[CategoryName, SourceTierConfig]` (defaulted,
optional — absent section is a no-op, not an error).

### 2. New feeds

- `"Google News Reuters"` (`site:reuters.com`) and `"Google News AP"`
  (`site:apnews.com`) — broad, not topic-scoped, since these two outlets serve
  4 of 5 categories across different tiers.
- Direct feeds: Bloomberg, Financial Times, The New York Times, The Hollywood
  Reporter, Billboard (exact URL to confirm at implementation time).
- BBC: reuse the existing "BBC World" feed for the "BBC News" specialist slot,
  no redundant new feed.

### 3. Two-tier source attribution (`source_balance.py`)

Consolidate `QUALITY_SOURCE_HINTS` + `SOURCE_ALIASES` into one word-boundary-safe
table (fixes a real latent substring-match bug in both — e.g. `"ap"` could
currently match inside a hypothetical future outlet name as a bare substring).

Add `resolve_source_attribution(article, cluster_articles) -> SourceAttribution`
with two confidence levels, not a continuous score (simpler, and matches how
confidently these signals can actually be computed from RSS-only data):

- **`confirmed`**: `article.source` already resolves to a known wire/major outlet
  (covers the Google-News-proxy path — already clean per `fetch.py`), OR the
  title carries an explicit wire-service credit suffix.
- **`uncertain`**: only signal is near-identical body text (via `jaccard()`,
  reused from `cluster.py`) to an already-`confirmed` wire article in the same
  cluster. **Logged, but never collapses the corroboration count** — this is the
  "favor false negatives" rule from `source-system-restructure.md`, and the
  single most important accuracy fix over the original `PreSourceUP.md` draft.
- No signal at all → the article's own display name stands as its own
  independent source, unchanged.

**Guardrail, unchanged from `PreSourceUP.md`**: this resolved identity is used
*only* for corroboration counting. It never overwrites `article.source`,
`StoryCluster.sources`, the "(via X, Y)" display line, or `top_for_category`'s
per-source diversity cap — those stay keyed on real, distinct outlet names.

### 4. Post-classification scoring + corroboration (`pipeline.py`)

New `apply_source_tier_scoring()`, called right after `apply_category_assignments`
and before `apply_content_quality_quarantine` — same convention as the existing
`apply_*` functions in this file.

- **Soft primary/secondary boost** to `total_score` when a `confirmed`-tier
  source is present for the cluster's category. No boost for specialist-only
  presence (specialist is context, not corroboration — stated explicitly per the
  adopted invariant).
- **Corroboration integrity check**: if raw `cluster.source_count >= 2` but the
  `confirmed`-attribution count is only 1 (same wire report counted twice), set
  skip reason `"single wire-syndicated source only"` — distinct from the
  existing `"no reliable source confirmation"` (which already correctly handles
  a *genuine* single-source story; untouched).
- **`uncertain`-only duplicates are never used to reduce this count** — per the
  false-negative-favoring rule, an ambiguous body-similarity match doesn't
  demote a story that otherwise looks corroborated.

### 5. Drafting: attribution-aware, not a new pipeline stage (`draft.py`)

Extend `DraftCandidate` with two additive fields:
- `corroboration_status: str` — `"confirmed"` (2+ independent confirmed
  sources) or `"single_source"`.
- `specialist_article_urls: tuple[str, ...]` — populated when a cluster contains
  an article whose resolved source matches that category's specialist tier.

The *existing* draft prompt gets two small additions (no new API call, no new
pipeline stage):
1. When `corroboration_status == "single_source"`, instruct the model to write
   the story with clear attribution ("Reuters reports...") rather than stating
   it as flatly confirmed fact — directly implements the publish/attribute
   distinction from `source-system-restructure.md` at zero added cost, since
   `draft.py` already asks for hedged language on unconfirmed claims.
2. Specialist-flagged articles are marked in the payload with an instruction to
   draw on them for deeper industry/explanatory detail specifically.

### 6. Lightweight audit log

Extend the existing `category_assignments_*.json` log (or add one small sibling
file, matching that exact write pattern) with per-cluster
`corroboration_status`, resolved tier membership, and the attribution confidence
level used. No new schema-versioning system — this repo's git history plus this
plan document is the versioning record, consistent with how every other log in
this codebase already works.

## Cost

Current production baseline (real run, not estimated): ~39,000 input / ~4,900
output tokens/day across classify + draft + quality-gate judge.

| Model | Baseline/month | + this feature's ~15% prompt overhead |
|---|---|---|
| `gpt-5.6-terra` (currently configured) | $3.76 | **$4.33** — under your $5 floor |
| `gpt-5.6-sol` (flagship) | $7.52 | **$8.65** — inside your $5–10 band |

**Recommendation: switch `OPENAI_MODEL` from `gpt-5.6-terra` to `gpt-5.6-sol`.**
You specified a floor as well as a ceiling, which reads as "spend for quality
within this band" rather than "minimize cost" — and accuracy is this plan's
explicit top priority. Sol is the more capable model per the current OpenAI
lineup, fits the band with room to spare, and the active-verification-search
machinery that would have pushed real cost risk into `source-system-restructure.md`'s
version of this plan has been cut entirely.

No new call types are added anywhere in this design — the cost increase is
purely proportional to slightly larger payloads on the two calls that already
exist.

## File changes

| File | Change |
|---|---|
| `config/sources.toml` | `[source_tiers.*]` blocks; new Reuters/AP proxy feeds + Bloomberg/FT/NYT/Hollywood Reporter/Billboard feeds |
| `src/news_agent/models.py` | `SourceTierConfig`; `AgentConfig.source_tiers` (defaulted); `DraftCandidate.corroboration_status` + `.specialist_article_urls` (defaulted) |
| `src/news_agent/config.py` | Parse `[source_tiers.*]`, optional/backward-compatible |
| `src/news_agent/source_balance.py` | Consolidated + word-boundary-fixed alias table; `resolve_source_attribution()` (confirmed/uncertain, two-tier) |
| `src/news_agent/pipeline.py` | `apply_source_tier_scoring()`; `build_draft_candidates` populates corroboration status + specialist flags |
| `src/news_agent/draft.py` | Prompt additions for attribution-required and specialist-flagged articles |
| `src/news_agent/skipped_log.py` | New `"single wire-syndicated source only"` skip reason |
| `tests/test_source_balance.py` | **New file** (zero existing coverage today) — word-boundary regression, confirmed vs. uncertain attribution, uncertain-never-collapses-count, canonical identity ≠ display identity |
| `tests/test_pipeline.py` | Tier-boost ordering, corroboration skip reason (syndicated-looks-corroborated vs. genuine single-source, unchanged), specialist-flag propagation |
| `tests/test_config.py` | `[source_tiers.*]` parsing, absent-section default |
| `tests/test_draft.py` | Attribution-required and specialist-flagged prompt behavior, one-paragraph contract unchanged |
| `.env`, GitHub Actions secret | `OPENAI_MODEL` → `gpt-5.6-sol` |

## Open decision carried over from `PreSourceUP.md`

Already resolved by this synthesis: primary-source presence is a soft ranking
boost, not a hard requirement to publish. Both source plans' own stated
invariants agreed on this, so it's no longer open.

## Verification

1. `python3 -m pytest -q` — full suite green, including new `test_source_balance.py`.
2. Live `--dry-run --show-skipped` smoke test: confirm the new Reuters/AP proxy
   feeds resolve to clean `article.source` values, confirm at least one
   `"single wire-syndicated source only"` case appears distinctly from
   `"no reliable source confirmation"` when applicable, confirm attributed
   ("X reports...") phrasing appears for genuine single-source major stories.
3. Recompute actual daily token usage post-implementation against this plan's
   cost table to confirm the real overhead lands where estimated, not just the
   projection.

## Status

**Not approved for implementation.** Awaiting your go-ahead.
