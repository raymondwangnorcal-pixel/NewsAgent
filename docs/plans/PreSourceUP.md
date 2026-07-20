# Source Restructuring: Per-Category Primary/Secondary/Specialist Tiers

## Context

Today, `config/sources.toml` treats every feed the same way: a flat `reputation`/`quality_weight`, an advisory `categories` tag consulted only as a degraded fallback, and no concept of "this outlet is the authoritative primary source for category X." Corroboration ("is this story confirmed by more than one outlet?") is measured by counting distinct `article.source` strings — the feed's configured display name — with no awareness that many outlets republish the same underlying AP or Reuters wire report, which today would incorrectly count as multiple independent confirmations.

You want each of the 5 categories to have an explicit editorial hierarchy — a primary source whose coverage drives what counts as newsworthy, a secondary source that corroborates importance and accuracy, and a specialist source that adds deeper industry context to the drafted paragraph — with two hard rules: never publish a "major" story as single-sourced when independent confirmation genuinely exists, and never let syndicated copies of the same wire report count as independent confirmation.

**Two real-world constraints found during research, not assumptions:**
- **Reuters and AP have had no official public RSS feed since ~2020.** Both are only reachable via a Google-News search scoped to their domain (`site:reuters.com` / `site:apnews.com`) — the same mechanism already used for the 4 existing topic-based aggregator feeds — or via syndicated copies carried in other outlets' own feeds.
- Bloomberg, Financial Times, The New York Times, The Hollywood Reporter all have real, current feeds. Billboard almost certainly does too (same publisher family as the already-integrated Variety) but its exact URL needs a quick lookup at implementation time — not a design blocker.

## Existing mechanics this design reuses (verified directly, not assumed)

- **`fetch.py`'s `_article_source`/`_clean_title` already solve wire-source resolution for the Google-News-proxy path**: for any feed whose `name` starts with `"Google News"`, it reads the RSS `<source>` tag (Google News's own per-item attribution) and uses *that* as `article.source`, and strips the matching title suffix. So a new `"Google News Reuters"` feed (`site:reuters.com`) will already deliver `article.source == "Reuters"` cleanly — **zero new fetch-side code needed for that path.**
- **The real gap** is a *third-party outlet's own feed* carrying wire copy (e.g. a local-news feed republishing an AP story) — there, `article.source` stays the outlet's own name, and today nothing detects the underlying wire origin.
- **`pipeline.py` already has the exact convention to extend**: small `apply_*` functions that mutate cluster state in place, run in sequence inside `collect_pipeline_context()` right after category assignment (`apply_category_assignments` → `apply_content_quality_quarantine`, pipeline.py:181–232). The new tier-scoring/corroboration check is a sibling of these, not a new pipeline "stage."
- **`source_balance.py`'s `QUALITY_SOURCE_HINTS`** and **`formatting.py`'s `SOURCE_ALIASES`** are two near-duplicate source-name-canonicalization tables, both with a real latent bug: naive substring matching with no word boundaries (e.g. the key `"ap"` would match inside a hypothetical future outlet name containing "ap" as a substring). No existing feed name triggers this today, but tier-resolution raises the stakes of getting it wrong. Fix while consolidating, using the same `\b` word-boundary pattern `scoring.py`'s `_term_pattern` already establishes.
- **`cluster.py`'s `jaccard()`/`tokenize()`** are reused as a syndication-detection backstop: when an outlet doesn't credit a wire service in its title/summary at all (common, and undetectable by text-suffix matching), near-identical body text between two same-cluster articles is a stronger signal that one is a syndicated copy of the other.

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

New `SourceTierConfig(primary: str, secondary: str, specialist: str)` in `models.py`; `AgentConfig.source_tiers: dict[CategoryName, SourceTierConfig] = field(default_factory=dict)` — defaulted, so this is resolved **after** classification assigns `cluster.category` (Reuters is primary for 2 categories and secondary for 2 others — a per-feed static role can't express that; only a per-(category, tier) lookup can). `config.py` parses it following the existing `settings`-block flat-table pattern.

### 2. New feeds in `[[feeds]]`

- `"Google News Reuters"` (`site:reuters.com`, broad — not scoped to one category's keywords, since Reuters serves 4 of 5 categories across tiers) and `"Google News AP"` (`site:apnews.com`), following the exact existing aggregator-feed shape.
- Direct feeds for Bloomberg (`feeds.bloomberg.com/markets/news.rss`), Financial Times, The New York Times, The Hollywood Reporter, Billboard (URL TBD).
- **BBC**: reuse the existing "BBC World" feed rather than adding a redundant one — after the word-boundary fix, "BBC World" still canonicalizes to "BBC" correctly for tier matching.
- Variety already exists.

### 3. Source canonicalization + syndication detection (`source_balance.py`)

Consolidate `QUALITY_SOURCE_HINTS` + `SOURCE_ALIASES` into one shared, word-boundary-safe table (fixes the substring bug as a side effect). Add `canonical_source_identity(article: Article, cluster_articles: list[Article]) -> str`, layering three signals in order:
1. `article.source` matched against the canonical table (covers the Google-News-proxy path, already clean per `fetch.py`).
2. A wire-service name in the article's title-tail (extends the existing `strip_source_names`/`SOURCE_SPLIT_RE` building block with a small static wire-agency name list) — covers outlets that credit the wire in-title.
3. **Backstop**: near-identical summary text (via `jaccard(tokenize(...))`, reused from `cluster.py`) against another article in the same cluster that's already wire-identified — covers outlets that don't credit the wire at all.

**Explicitly not 100% reliable** — flagged as a known limitation, not silently assumed solved: an outlet that neither credits the wire nor closely mirrors its wording is undetectable from `Article`'s current fields (no byline field exists). Worth stating in the ADR.

**Critical guardrail** (the one real risk the validation pass surfaced): canonical identity is used **only** for tier-resolution and corroboration counting. It must never overwrite `article.source`, `StoryCluster.sources`, or feed the "(via X, Y)" display line or `top_for_category`'s per-source diversity cap (scoring.py:109) — those must keep showing/keying on the real, distinct outlet names. Canonicalization and display identity stay separate derived values.

### 4. New post-classification function in `pipeline.py`: `apply_source_tier_scoring`

Runs right after `apply_category_assignments`, before `apply_content_quality_quarantine` (matching the existing convention). For each classified, not-yet-skipped cluster:
- Resolve canonical source identities for all its articles (dedup).
- Look up `source_tiers[cluster.category]`; skip entirely if that category has no tier config (no-op, not an error).
- **Prioritization** (soft signal, not a hard gate — matches the existing scoring philosophy of weighted signals rather than pass/fail rules, and "prioritize stories from the primary source" reads as a ranking preference, not "reject stories the primary source didn't cover"): add a `total_score` bonus when the primary-tier source is present, a smaller bonus for secondary, none for specialist-only. This re-ranks *before* `top_for_category`'s per-category cap runs, so tier-matching stories are more likely to make the final cut.
- **Corroboration integrity**: compute independent canonical-source count. If raw `cluster.source_count >= 2` (looks corroborated) but the canonical count is only 1 (it's the same wire report counted twice), set a new, more specific skip reason — `"single wire-syndicated source only"` — distinct from the existing `"no reliable source confirmation"` reason (which already correctly handles the case of a *genuine* single-source story with no syndication trickery involved; that path is untouched).

### 5. Specialist-source flagging for drafting (`pipeline.py` + `draft.py`)

Extend `DraftCandidate` with `specialist_article_urls: tuple[str, ...] = ()` (additive, defaulted). `build_draft_candidates` populates it when a cluster contains an article whose canonical source matches that category's specialist tier. `draft.py`'s payload/prompt gets a small addition: articles flagged as specialist-sourced are marked in the JSON payload, with an instruction to draw on them specifically for deeper explanatory/industry detail — not to change the one-paragraph structure, just to weight which source informs the "why it matters" framing.

## Open decision needing your confirmation

**Is "prioritize primary source" a soft ranking boost (my recommendation above) or a hard requirement** — i.e., should a story with only secondary+specialist coverage (no primary-tier source at all) be blocked from that category entirely? I've designed it as soft, since a hard requirement would mean, for example, a major U.S. News story that AP simply didn't cover (but Reuters and NYT did) gets dropped outright — which seems like the wrong tradeoff for a personal briefing tool. Flag if you want the harder rule instead.

## File changes

| File | Change |
|---|---|
| `config/sources.toml` | New `[source_tiers.*]` blocks; new Reuters/AP proxy feeds + Bloomberg/FT/NYT/Hollywood Reporter/Billboard direct feeds |
| `src/news_agent/models.py` | New `SourceTierConfig`; `AgentConfig.source_tiers` (defaulted); `DraftCandidate.specialist_article_urls` (defaulted) |
| `src/news_agent/config.py` | Parse `[source_tiers.*]`, flat-table pattern, optional/backward-compatible |
| `src/news_agent/source_balance.py` | Consolidate + word-boundary-fix the two alias tables; new `canonical_source_identity()` (title-suffix + body-similarity signals) |
| `src/news_agent/pipeline.py` | New `apply_source_tier_scoring()`, called in `collect_pipeline_context`; `build_draft_candidates` populates specialist flags |
| `src/news_agent/draft.py` | Payload/prompt addition for specialist-flagged articles |
| `src/news_agent/skipped_log.py` | New `"single wire-syndicated source only"` skip reason |
| `tests/test_source_balance.py` | **New file** — `source_balance.py` currently has zero dedicated tests. Word-boundary regression test, title-suffix detection, body-similarity backstop, canonicalization ≠ display identity |
| `tests/test_pipeline.py` | Tier-boost ordering, corroboration skip-reason (syndicated-looks-corroborated case vs. genuine single-source case unchanged), specialist-flag propagation into `DraftCandidate` |
| `tests/test_config.py` | `[source_tiers.*]` parsing, absent-section default |
| `tests/test_draft.py` | Specialist-flagged article influences payload without changing the one-paragraph output contract |

## Verification

1. `python3 -m pytest -q` — full suite green.
2. Live `--dry-run --show-skipped` smoke test: confirm the new feeds actually fetch (Reuters/AP proxy feeds return real articles resolving to clean `article.source`), confirm at least one story per category shows tier-boosted ranking, confirm the skipped-stories table shows the new `"single wire-syndicated source only"` reason distinctly from `"no reliable source confirmation"` when applicable.
3. Spot-check `docs/category-guidelines.md` alignment isn't affected — this change only touches source acquisition/corroboration/prioritization, not category *placement* rules, which stay entirely with `classify.py`.

## Status

**Not approved for implementation.** Saved for reference per your request. Open decision above still needs your answer before this moves forward.
