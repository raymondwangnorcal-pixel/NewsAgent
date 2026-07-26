# Category Fetch Reserves Update

**Status:** Implemented and live-validated  
**Date:** 2026-07-21  
**Purpose:** Context for follow-up planning

## Problem

All feeds previously competed in one recency-sorted pool capped at 240 articles. High-volume Culture feeds could occupy a disproportionate share of that pool before enrichment or classification quotas ran. Articles removed by this initial cutoff could not be recovered downstream, so slower categories such as U.S. News, Global News, and Finance could become thin even when their feeds had usable entries.

## Implemented change

The fetch stage now reserves capacity by feed-category hint before applying the global 240-article cutoff:

| Category | Internal key | Reserved articles |
| --- | --- | ---: |
| Business + Tech | `business_tech` | 40 |
| U.S. News | `domestic` | 40 |
| Global News | `global` | 40 |
| Finance | `finance` | 40 |
| Culture + Media | `culture` | 30 |

The requested reserves total 190 slots. After available reserves are satisfied, the remaining capacity is filled with the newest unselected articles across all feeds. With a 240-article global limit, this normally leaves at least 50 globally competitive slots.

The values are configured in `config/sources.toml` under `[fetch_reserves]` and loaded into `AgentConfig.category_fetch_reserves`.

## Selection behavior

`select_articles_with_category_reserves()` in `src/news_agent/fetch.py` operates on all articles inside the configured lookback window:

1. Sort all in-window articles by publication time, newest first.
2. Build one queue for each configured feed-category hint.
3. Fill deficient categories round-robin until each available reserve is satisfied or the 240-item ceiling is reached.
4. A dual-tagged article increments coverage for every applicable reserve but appears in the result only once.
5. If a category has fewer articles than its reserve, include all available articles and release the unused capacity to the global remainder.
6. Fill every remaining slot with the newest unselected articles, including general or untagged feed entries.
7. Return the final selection in descending publication-time order.

These values are minimum reservations, not category caps. A category may exceed its reserve through dual-tagged coverage or the global remainder.

Configuration loading rejects negative reserves and rejects configurations whose nominal reserves exceed `max_articles`.

## Files changed

- `config/sources.toml`: added the five requested reserve values.
- `src/news_agent/models.py`: added the default reserve mapping and `AgentConfig.category_fetch_reserves`.
- `src/news_agent/config.py`: parses and validates `[fetch_reserves]`.
- `src/news_agent/fetch.py`: added pre-cutoff reserved selection and extended `fetch_all_feeds()` to accept the mapping.
- `src/news_agent/pipeline.py`: passes configured reserves into the fetch boundary.
- `tests/test_fetch.py`: covers category starvation, dual-tag deduplication, global fill, and unused-reserve release.
- `tests/test_config.py`: verifies the production reserve mapping.

## Verification

The full test suite passed:

```text
209 passed
```

`git diff --check` also passed.

An isolated OpenAI-off live dry run fetched exactly 240 articles with this feed-hint distribution:

```text
Business + Tech: 40
U.S. News:       40
Global News:     40
Finance:         40
Culture + Media: 70
```

Culture exceeded its 30-article reserve because Culture articles remained competitive for the global remainder. The run did not send messages or modify production history.

## What this fixes

- A burst of recent Culture articles can no longer reduce another configured category below its available fetch reserve.
- Enrichment and classification quotas now receive a broader, category-balanced input set.
- The overall 240-article ceiling remains unchanged, so the change does not inherently raise the number of page extractions or OpenAI calls.

## What this does not fix

Fetch reservations guarantee feed-hinted input opportunity, not published story counts. Reserved articles can still be removed later because they:

- lack enough substantive feed or extracted-page context;
- fail the evidence threshold;
- come from aggregator-only URLs that cannot be page-enriched;
- remain unclassified in OpenAI-off mode, especially when a general feed has no category tag;
- lose final selection because of category, publisher, or Culture-lane limits.

In the live dry run, the requested fetch counts were achieved, but U.S. News, Global News, and Finance still published fewer stories because of downstream evidence and deterministic-classification constraints. A follow-up plan should distinguish fetch scarcity from post-fetch attrition rather than increasing these reserves blindly.

## Current related configuration

- `max_articles = 240`
- `lookback_hours = 30`
- `max_culture_stories = 3`
- enrichment page budget: 50
- minimum story evidence score: 1.2

The current three-story Culture publication cap is independent of the new 30-article Culture fetch reserve.

## Suggested next-plan diagnostics

For each category, compare these existing stages:

1. `fetched_articles_by_feed_hint`
2. `preliminary_clusters_by_feed_hint`
3. `enrichment_clusters_by_feed_hint`
4. `insufficient_context_by_feed_hint`
5. `classification_pool_by_feed_hint`
6. `classified_clusters_by_category`
7. `selected_stories_by_category`

The first large drop identifies whether the next change belongs in source quality, extraction policy, evidence scoring, classification, or final selection.
