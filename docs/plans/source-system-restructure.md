# Source System Restructure Plan

**Status:** Proposed

## Objective

Restructure source handling around category-specific editorial roles while preserving broad discovery, low operating cost, attribution accuracy, and an audit trail for every publication decision.

The system must distinguish a hosting publisher from the organization that originated reporting. Reuters or Associated Press copies distributed through multiple publishers count as one reporting organization, not multiple confirmations.

## Current System Diagnosis

The current pipeline is deliberately lightweight:

- `config/sources.toml` describes a flat tuple of RSS feeds. `src/news_agent/config.py` maps each entry to `FeedConfig`.
- `src/news_agent/fetch.py` reads RSS/Atom items. An item's `source` label, particularly from Google News, becomes `Article.source`; that is normally the hosting publisher, not reliably the original reporter.
- `Article` in `src/news_agent/models.py` contains title, URL, publisher label, RSS summary, timestamp, reputation, and feed tags. It has no canonical URL, provenance, discovery method, body text, or source-attribution evidence.
- `src/news_agent/cluster.py` clusters from title, entity, event-term, and time similarity. It does not distinguish a duplicate article from a syndicated copy of a wire report.
- `src/news_agent/source_balance.py`, `src/news_agent/scoring.py`, and `src/news_agent/history.py` treat distinct `cluster.sources` entries as independent publishers. That can overcount Reuters/AP syndications.
- `collect_pipeline_context()` in `src/news_agent/pipeline.py` fetches, quality-filters, clusters, scores, applies history, classifies a bounded candidate pool, then selects category stories. Any targeted verification must occur before final selection and drafting, but after inexpensive preliminary ranking identifies plausible finalists.
- `src/news_agent/draft.py` receives raw article title/summary/source samples. It has no structured contract for confirmed facts, disputes, or source-role evidence.

The source-system work must preserve these existing behaviors during migration: RSS ingestion, feed-tag fallback classification, quality-gate auditing, history suppression, and deterministic drafting.

## Non-Goals

- Do not make a category hierarchy an exclusive allowlist.
- Do not scrape or persist full article bodies from a site unless the configured extraction policy permits it.
- Do not infer an original reporter from weak similarity alone.
- Do not use paid search, embeddings, or LLM analysis for every fetched article.
- Do not replace the current config or selection behavior in one rollout.

## Editorial Invariants

1. Discovery sources, hosting publishers, original reporters, and independent reporting groups are separate identities.
2. A source role is satisfied by a **confirmed reporting origin**, not merely a publisher carrying a syndicated copy. A specialist role may be satisfied by a configured specialist's own analysis or reporting; an unverified syndication does not qualify.
3. `unknown` provenance never becomes Reuters, AP, or another source by assumption.
4. A cluster's independent-reporting count is the count of distinct reporting groups, not publishers, URLs, discovery channels, or article copies.
5. Official confirmation is a separate fact axis from journalistic corroboration.
6. A conflict is a separate fact axis from corroboration. A story can be independently confirmed and still contain disputed details.
7. A single-source major claim is either explicitly attributed, held for verification, or excluded. It is never rewritten as settled fact.
8. A missing primary source may reduce role evidence, but it must not reduce factual standards or block a well-confirmed story found elsewhere.

## Identity Model

### Source identities

Each article carries the following independent fields:

| Field | Meaning | Example |
| --- | --- | --- |
| `publisher_id` | Current hosting publisher, normalized through aliases | `local_news_site` |
| `publisher_display_name` | Display name from RSS or metadata | `Example Local News` |
| `discovery_channel_id` | Mechanism that surfaced the URL | `google_news_reuters_domain` |
| `canonical_url` | Normalized destination URL, if determinable | `https://www.reuters.com/...` |
| `original_source_id` | Detected original reporter, or `unknown` | `reuters` |
| `original_source_confidence` | Confidence in the original-source assignment | `0.97` |
| `reporting_group_id` | Group counted for corroboration | `reuters`, `bloomberg`, or publisher fallback |
| `syndication_status` | `original`, `syndicated`, `unknown`, or `not_applicable` | `syndicated` |
| `syndication_group_id` | Configured wire distribution group, if confirmed | `reuters_wire` |
| `origin_instance_key` | Same-report key, never an event-cluster key | `reuters:sha256:...` |
| `content_fingerprint` | Hash of normalized permitted text fields | `sha256:...` |

`reporting_group_id` is resolved as follows:

1. Use `original_source_id` only when attribution is `confirmed`.
2. Otherwise use the configured publisher reporting group, usually the publisher's own ID.
3. Mark that fallback as `provenance_unknown`; it must not satisfy Reuters/AP primary or secondary role evidence.

This intentionally favors avoiding false collapses. An uncertain local copy can be counted as its publisher for provisional diversity, but it cannot be presented as Reuters/AP confirmation or satisfy a wire role until corroborated by stronger evidence.

### No circular corroboration key

Do not store `reuters:event_cluster_123` on an article. The event cluster does not exist until clustering completes.

Use two stages instead:

1. Article-level provenance produces `origin_instance_key`, for example `reuters:<canonical-url-hash>` or `reuters:<confirmed-wire-fingerprint>`.
2. Event clustering groups articles. Within each cluster, derive `independent_source_key` as `<cluster_id>:<reporting_group_id>`.

The first key identifies copies of a report; the second counts reporting organizations inside a completed event cluster.

## Configuration Design

Keep `config/sources.toml` as the backward-compatible feed list during migration. Add `config/source_hierarchy.toml` as the source registry and role policy. This prevents the existing `load_config()` path from breaking while the richer parser is introduced.

### `config/source_hierarchy.toml`

```toml
[source_system]
mode = "shadow" # legacy | shadow | enforce
attribution_confirm_threshold = 0.90
attribution_probable_threshold = 0.70

[source_system.verification]
max_candidates_per_run = 10
max_candidates_per_category = 2
max_queries_per_candidate = 3
max_total_queries_per_run = 20
per_query_timeout_seconds = 8
run_budget_seconds = 35
allow_paid_search = false
allow_direct_extraction = false

[source_system.publication]
major_impact_threshold = 3.5
single_source_min_quality = 0.95

[[source_registry]]
id = "reuters"
display_name = "Reuters"
source_type = "wire"
reporting_group_id = "reuters"
syndication_group_id = "reuters_wire"
default_quality_weight = 0.98
aliases = ["Reuters News Service"]

[source_registry.extraction]
policy = "metadata_only" # disabled | metadata_only | permitted_text
allowed_domains = ["reuters.com"]

[[source_registry.discovery_channels]]
id = "google_news_reuters_domain"
kind = "google_news_domain"
domain = "reuters.com"
enabled = true
priority = 1

[[source_registry.discovery_channels]]
id = "reuters_syndication_detection"
kind = "syndication_detection"
enabled = true
priority = 4

[[source_registry]]
id = "bloomberg"
display_name = "Bloomberg"
source_type = "publisher"
reporting_group_id = "bloomberg"
default_quality_weight = 0.98
aliases = ["Bloomberg News"]

[category_hierarchies.business_tech]
primary = ["reuters"]
secondary = ["bloomberg"]
specialist = ["financial_times"]
fallback = ["techcrunch", "the_verge", "cnbc"]
primary_weight = 0.35
secondary_weight = 0.25
specialist_weight = 0.15

[category_hierarchies.domestic]
primary = ["associated_press"]
secondary = ["reuters"]
specialist = ["new_york_times"]
fallback = ["npr", "axios", "bbc"]
primary_weight = 0.35
secondary_weight = 0.25
specialist_weight = 0.15
```

Add equivalent entries for Global, Culture + Media, and Finance. Source IDs are stable lowercase identifiers; display names and aliases are presentation and normalization data only.

### Backward-compatible `config/sources.toml` additions

Existing `[[feeds]]` entries remain valid. New optional fields identify the collection mechanism rather than claiming the publisher or reporting origin:

```toml
[[feeds]]
id = "google_news_reuters_business"
name = "Google News Reuters Business"
url = "..."
categories = ["business_tech", "finance"]
discovery_channel_id = "google_news_reuters_domain"
source_hint_id = "reuters"
```

`source_hint_id` is not provenance. It is only an expected domain/source used to diagnose a misconfigured channel.

### Validation rules

- Every hierarchy source and fallback ID must exist in `source_registry`.
- A discovery channel ID must be unique and belong to exactly one source.
- Alias matching is case-insensitive and normalized once in the registry.
- Weight precedence is explicit: category role weight x source default quality weight; no hidden per-feed multiplier beyond the existing reputation until a later migration removes it.
- `primary`, `secondary`, and `specialist` arrays may contain multiple IDs, but a source may appear in only one role within a category unless explicitly marked dual-purpose.
- A disabled channel is reported as `disabled`, not failed.

## Acquisition and Health Boundary

Introduce a discovery boundary rather than teaching `fetch.py` every channel type:

```text
DiscoveryChannel -> FetchOutcome -> ArticleCandidate -> Attribution/Enrichment -> Article
```

`FetchOutcome` must contain `channel_id`, `status`, `attempted_at`, `duration_ms`, `article_count`, and a safe error class. Status is one of `success`, `empty`, `blocked`, `failed`, or `disabled`.

This fixes the current ambiguity in `fetch_feed()`, where a network failure and a genuinely empty feed both produce an empty list.

### Discovery order

1. Run configured RSS and Google News RSS channels for primary, secondary, and specialist source IDs.
2. Run existing safety-net feeds without assigning role credit automatically.
3. For only the bounded verification candidates, run configured domain-scoped Google News queries.
4. Run web search only when an explicit provider is configured and the per-run budget permits it.
5. Attempt direct extraction only for sources whose configuration allows it and only to collect permitted metadata/text.

Google News, a direct Reuters URL, and a syndicated Reuters copy are discovery paths or hosts; all resolve to the same reporting group only after confirmed attribution.

Persist a compact per-run source-health record. Do not store response bodies solely for health reporting. A source is considered degraded for the run only if all of its enabled channels fail or are blocked; an empty successful result is not a failure.

## Attribution and Syndication

### Evidence collection

Attribution runs in cheap-to-expensive order:

1. Canonical URL domain and aggregator-provided publisher label.
2. Explicit source/byline and copyright text available in RSS or permitted metadata.
3. Structured metadata and JSON-LD from permitted enrichment.
4. Deterministic normalized title/summary fingerprint comparison against known wire candidates in the same time window.
5. Quotation/dateline overlap only when permitted text exists.
6. Optional embedding or LLM review only for a bounded major-story ambiguity; it is disabled by default.

The stage returns evidence codes, not an opaque explanation. Example: `explicit_reuters_byline`, `canonical_reuters_domain`, `matching_wire_fingerprint`.

### Confidence policy

| Attribution result | Confidence | Corroboration behavior |
| --- | ---: | --- |
| `confirmed` | >= configured confirm threshold | Use original reporting group; collapse confirmed syndications |
| `probable` | >= probable threshold and < confirm threshold | Log as probable; do not satisfy a named role or collapse source counts |
| `unknown` | below probable threshold or no evidence | Preserve publisher reporting group and do not invent origin |

Do not use headline similarity by itself to mark a wire origin. A false positive can erase independent confirmation; it is more harmful than leaving an ambiguous copy uncollapsed.

### Duplicate versus same reporting origin

- **Duplicate article:** same normalized canonical URL, or repeated fetch of the same host article. Deduplicate before clustering.
- **Syndicated copy:** a distinct publisher URL with confirmed shared original reporter and matching origin-instance fingerprint. Keep it in the event cluster for publisher audit, but do not add corroboration credit.
- **Independent coverage:** a different reporting group covering the same event. Add corroboration credit only after event clustering.

Use deterministic URL, title, summary, and metadata fingerprints first. Keep full-text hashes only when extraction policy permits the underlying content. Store hashes and evidence codes rather than full third-party text in long-lived audit records.

## Clustering and Corroboration

Retain `cluster_articles()` as the initial event-clustering mechanism, then enrich clusters with provenance. Do not try to use `independent_source_key` before the cluster exists.

Add these derived fields to `StoryCluster` or a nested `StoryEvidence` value:

```python
independent_reporting_groups: tuple[str, ...]
syndicated_publishers: tuple[str, ...]
official_evidence: tuple[OfficialEvidence, ...]
role_presence: SourceRolePresence
journalistic_status: str
conflict_status: str
verification: VerificationOutcome
```

Use independent dimensions instead of one mutually exclusive status:

- `journalistic_status`: `unconfirmed`, `single_original_source`, or `independently_confirmed`
- `official_confirmation`: boolean plus evidence list
- `conflict_status`: `none`, `non_material`, or `material`

An official filing can confirm a core event while reporting remains single-source. Two outlets can independently confirm an event but disagree on a material figure.

## Major-Story Publication Gate

### Candidate definition

A story is a verification candidate when it is in the preliminary final-candidate set and either:

- `impact_score >= major_impact_threshold`,
- it is associated with a configured market mover or official emergency signal, or
- it would otherwise be one of the top two selected stories in its category.

This definition is configurable and initially runs in shadow mode. The system may publish fewer stories in a category rather than backfilling with weakly supported material.

### Decision table

| Conditions | Outcome | Drafting rule |
| --- | --- | --- |
| Two or more independent reporting groups, no material conflict | `publish` | State confirmed core facts normally |
| Credible official evidence confirms core event, no material conflict | `publish` | Cite/describe the official action when useful |
| One high-quality group; verification budget used; no confirmation found; reporting is clearly exclusive | `publish_attributed` | Open with `Reuters reports`, `Bloomberg reports`, or equivalent |
| One high-quality group; no official evidence; not a clearly exclusive report | `hold_for_verification` | Exclude from this run and log reason |
| Material conflict | `publish_attributed` only for undisputed facts, otherwise `hold_for_verification` | Hedge disputed facts or omit them |
| Low-confidence provenance or low-quality single source | `exclude` | Do not draft |

“No confirmation found” means the configured budget completed without an independent result. It never means the broader internet was exhaustively searched.

## Role-Aware Ranking

Preserve inspectable score components in `src/news_agent/scoring.py` rather than immediately hiding behavior in one aggregate score:

```text
newsworthiness_score
corroboration_score
source_quality_score
context_depth_score
role_evidence_score
recency_score
impact_score
content_quality_penalty
```

Rules:

- `corroboration_score` derives from `independent_reporting_groups`, never publisher count.
- `context_depth_score` rewards a configured specialist source only when its own reporting/analysis is actually present; it does not increase confirmation count.
- `role_evidence_score` rewards confirmed primary/secondary origin presence, but has a capped effect and cannot turn a single-source claim into independently confirmed reporting.
- `source_balance_score` changes from `len(cluster.sources)` to independent-group diversity while retaining publisher distribution for output diagnostics.
- History must store both publisher and independent-group snapshots. Otherwise a newly discovered syndicated copy would be mistaken for a meaningful source update.

The global candidate pool in `pipeline.py` remains bounded, but the revised selector must reserve enough preliminary candidates per category to avoid a globally dominant topic suppressing verification of a legitimate lower-scoring category story.

## Drafting Contract

Replace raw-article-only drafting input with a structured `StoryPackage` built after corroboration and the publication gate:

```json
{
  "story_id": "...",
  "category": "finance",
  "confirmed_core_facts": ["..."],
  "attributed_claims": [{"claim": "...", "source_id": "bloomberg"}],
  "independent_reporting_groups": ["bloomberg", "reuters"],
  "official_evidence": [],
  "context_sources": ["financial_times"],
  "material_conflicts": [],
  "source_roles": {"primary": ["bloomberg"], "secondary": ["reuters"], "specialist": ["financial_times"]},
  "publication_outcome": "publish"
}
```

Update `DraftCandidate`, `_candidate_payload()`, the OpenAI prompt, and the deterministic fallback in `src/news_agent/draft.py` so that:

1. The opening uses confirmed core facts only.
2. Single-source or disputed claims are attributed in both LLM and fallback drafts.
3. Specialist material is context, not corroboration.
4. Syndicated publisher counts are never exposed as independent evidence.
5. The fallback keeps the same attribution requirement as the LLM path.

## Logging and Explainability

Add a versioned `data/source_system_audit_YYYY-MM-DD.json` journal. It complements, rather than replaces, the existing quality-gate audit.

For each run, record:

```yaml
schema_version:
mode: legacy | shadow | enforce
channel_health:
source_availability:
verification_budget:
stories:
  - cluster_id:
    category:
    preliminary_rank:
    role_presence:
    independent_reporting_groups:
    syndicated_publishers:
    official_evidence_codes:
    journalistic_status:
    conflict_status:
    verification_queries:
    verification_outcome:
    publication_outcome:
    why_selected:
```

Store URLs, normalized IDs, hashes, counts, evidence codes, and status decisions. Do not retain full third-party article text unless a source policy permits it. Include a schema version so historical records remain interpretable as attribution logic evolves.

## File-by-File Implementation Plan

1. Add `config/source_hierarchy.toml` with source registry, aliases, channel definitions, category roles, budgets, and publication policy. Keep all current feeds in `config/sources.toml`.
2. Extend `src/news_agent/models.py` with source-system config values, `ArticleProvenance`, `SourceEvidence`, `OfficialEvidence`, verification outcome, and cluster evidence fields. Keep `Article.source` as a compatibility display field during migration.
3. Extend `src/news_agent/config.py` to load and validate the new configuration. Preserve the current `AgentConfig(feeds=...)` constructor contract with defaults.
4. Refactor `src/news_agent/fetch.py` so RSS discovery returns `FetchOutcome` records plus candidates. Keep `fetch_all_feeds()` as a compatibility wrapper until callers migrate.
5. Add `src/news_agent/source_discovery.py` for channel dispatch and fallback order. Initial support is RSS and Google News RSS only; web search and direct extraction require explicitly configured providers/policies.
6. Add `src/news_agent/attribution.py` for alias normalization, canonicalization, deterministic evidence extraction, confidence calculation, and provenance assignment.
7. Add `src/news_agent/syndication.py` for duplicate/syndication fingerprints and grouping. It must operate before scoring but after provenance normalization.
8. Update `src/news_agent/cluster.py` to retain current cheap event clustering, then attach event-level independent groups after cluster construction. Add regression protection for broad-topic false merges.
9. Update `src/news_agent/source_balance.py` and `src/news_agent/scoring.py` to compute diversity and corroboration from independent reporting groups, retaining publisher distribution separately.
10. Update `src/news_agent/history.py` to version records and differentiate a new independent group from a new syndicated publisher.
11. Update `src/news_agent/pipeline.py` to run preliminary scoring/category assignment, bounded verification, rescore affected clusters, apply the publication gate, and write the source-system audit before final category selection.
12. Update `src/news_agent/draft.py` to consume `StoryPackage`; update `src/news_agent/classify.py` only where source-role context improves category assignment without allowing role evidence to override article content.
13. Update `src/news_agent/alerts.py` so breaking-news alerts use the same corroboration/publication rules or explicitly opt into a separately documented, more conservative alert policy.
14. Add `src/news_agent/source_system_report.py` or extend `quality_report.py` with source health, attribution-confidence, syndication-collapse, verification-cost, and gate-outcome reports.
15. Add `docs/adr/ADR-0004-source-provenance-and-corroboration.md` describing the identity model, confidence policy, and choice to favor false negatives over false positive wire attribution.

## Migration and Rollout

### Phase 0: Decisions and fixtures

- Approve roles, extraction policies, search provider availability, and budget defaults.
- Create static RSS/metadata fixtures for Reuters/AP originals, syndications, independent coverage, official documents, and conflicts.
- Add `BRIEFING_SOURCE_SYSTEM_MODE=legacy|shadow|enforce`, defaulting to `legacy`.

### Phase 1: Registry and compatibility

- Add source IDs, aliases, hierarchy config, and validation.
- Add feed/channel IDs without changing fetch, ranking, or selection.
- Exit criterion: all current feeds parse unchanged and every configured role resolves to a registry source.

### Phase 2: Provenance shadow mode

- Produce attribution evidence and source health logs without changing scores.
- Exit criteria: no missing source identifiers; manually review a representative sample of confirmed/probable Reuters/AP assignments; record unknown-attribution rate.

### Phase 3: Syndication and corroboration shadow mode

- Derive independent reporting groups and calculate alternate score components without changing final selection.
- Exit criteria: fixture precision is 100% for confirmed syndications; sampled real runs show no unacceptable false collapses; track changes in source diversity and history updates.

### Phase 4: Bounded verification

- Enable domain-scoped Google News verification for capped candidates only. Keep web search/direct extraction disabled unless explicitly configured.
- Exit criteria: query counts, elapsed time, and failure rates remain under configured budgets; verification outcomes appear in every applicable audit record.

### Phase 5: Enforce publication gate

- Enable the gate behind `enforce`, initially in dry-run comparison mode and then for briefings.
- Exit criteria: audit shows every major single-source story has an attributed, held, or excluded outcome; category fill rate remains acceptable without weakening policy.

### Phase 6: Cleanup

- Remove legacy-only source-count scoring after stable comparison results.
- Keep compatibility readers for historical audit and history records until their retention window expires.

## Test Plan

### Unit fixtures

1. Reuters original plus two confirmed Reuters syndications yields one independent reporting group.
2. AP original plus local AP republication yields one independent reporting group.
3. Reuters plus Bloomberg on the same event yields two groups.
4. Reuters plus AP plus Reuters syndication yields two groups.
5. A Bloomberg exclusive with no independent confirmation produces `publish_attributed` only after the verification budget completes.
6. One outlet plus an SEC filing is officially confirmed but remains separately marked as single-source journalism.
7. Variety and The Hollywood Reporter independently covering an event yield two groups.
8. A specialist analysis source raises context depth but not corroboration.
9. Unknown attribution does not satisfy Reuters/AP role evidence or collapse publisher diversity.
10. Similar headlines for different developments do not merge.
11. A material conflicting number produces a conflict status and causes the number to be hedged or omitted.
12. A later, materially updated wire report is not deduplicated as a stale copy merely because the origin matches.

### Integration tests

1. A failed primary channel and successful fallback channels produce a degraded health state, not a fake empty success.
2. Google News discovery of a Reuters destination and a confirmed syndication resolve to one reporting group.
3. Candidate selection respects run, per-category, and per-story verification budgets.
4. No confirmation result is logged as `not_found_within_budget`, never as proof no confirmation exists.
5. The publication gate excludes, holds, or attributes each major single-source fixture as specified.
6. Alerts follow the same source-system gate.
7. The legacy mode produces the current result shape; shadow mode produces equivalent briefings plus audit data.

### Evaluation checks

Compare legacy and shadow runs over saved fixtures and representative daily snapshots:

- successful-feed rate and channel failure classification;
- attribution confidence distribution and manual precision sample;
- false syndication-collapse rate;
- false event-merge rate;
- independent-source diversity before/after;
- selection overlap and category fill rate;
- number and cost of verification queries;
- rate of single-source stories published with required attribution.

## Risks and Unresolved Decisions

| Decision or risk | Required resolution |
| --- | --- |
| Web-search provider | Choose a provider, cost ceiling, and credential model before enabling `web_search`. Otherwise keep it disabled. |
| Extraction permissions | Define domain-specific policies and retention rules before requesting article bodies or metadata beyond RSS. |
| Role interpretation | Confirm that primary/secondary are confirmed-original-reporting roles, while specialist can be source analysis/context. |
| Confidence calibration | Set thresholds from fixtures and manual review; do not hard-code them from intuition alone. |
| Major threshold | Calibrate `major_impact_threshold` against shadow-run selection data before enforcement. |
| Sparse categories | Accept fewer stories rather than weakening corroboration; document whether any category has an exception. |
| Breaking alerts | Decide whether alerts share the exact gate or require a stricter dedicated policy. |
| Historical data | Version history/audit readers so existing `story_history.json` and quality logs remain readable. |

## Recommended Implementation Order

1. Approve the unresolved decisions and write ADR-0004.
2. Add registry/config validation and static fixtures.
3. Add channel outcomes and provenance data structures with no behavioral changes.
4. Implement deterministic attribution and syndication grouping in shadow mode.
5. Add alternate corroboration/role score components and history compatibility.
6. Add source-system audit reports and comparison tooling.
7. Add bounded Google News verification.
8. Introduce `StoryPackage` and attribution-safe drafting.
9. Enforce the publication gate only after shadow metrics and manual review meet the exit criteria.
