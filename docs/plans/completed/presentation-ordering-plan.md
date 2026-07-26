# Presentation Ordering — Implementation Plan (Item 5)

**Status:** Implemented
**Date:** 2026-07-21
**Scope:** Within-category display order by importance, on all channels (SMS, Telegram, console).
**Depends on:** The importance-scoring work (`importance-scoring-plan.md`), which is already implemented in the tree — `cluster.importance` and `_selection_key` exist.

## Goal

Render each category's stories with the highest-importance story first, so the reader leads with what matters most and, on SMS, the story dropped for length is always the least important one. This is presentation only: it changes *order*, never *which* stories are selected.

## Implemented design

The selection stage keeps its prior per-category `total_score` order through drafting:

```python
drafting_order = {
    category: sorted(items, key=lambda cluster: cluster.total_score, reverse=True)
    for category, items in selected.items()
}
return SelectionResult(drafting_order, ...)
```

That order becomes `context.category_clusters` and flows through `build_draft_candidates()` → `draft_paragraphs()`, preserving the pre-change OpenAI prompt sequence. After drafting, `order_paragraphs_for_presentation()` maps each completed paragraph back to its selected cluster and applies the canonical importance key. `build_briefing_sections()` and every renderer preserve that final sequence. SMS char-fitting drops whole stories **from the end** (`format_category_message`).

This keeps the feature presentation-only even for a sequence-sensitive language model: drafting inputs do not move, while all channels receive importance-ranked paragraphs and SMS drops the least-important tail first.

## The change

The codebase already defines the canonical deterministic importance key, used to rank the selection pool:

```python
def _selection_key(cluster: StoryCluster) -> tuple[float, float, float, str]:
    return (
        -cluster.importance,
        -cluster.total_score,
        -cluster.latest_published_at.timestamp(),
        story_identity(cluster),
    )
```

Item 5 is implemented after drafting:

```python
presentation_paragraphs = order_paragraphs_for_presentation(
    drafted_paragraphs,
    context.category_clusters,
)
```

The helper builds per-category paragraph ranks from selected clusters sorted by `_selection_key`. Unknown paragraphs retain their original relative order as a defensive fallback.

## Why this is enough

- **All channels, for free.** The sort happens once, upstream of drafting and rendering; SMS, Telegram, and console all render the same ordered `section.paragraphs`. No per-channel code.
- **SMS truncation benefit.** Because rendering drops from the end, importance-descending order means the least important story is the first casualty of the character budget — the intended behavior.
- **More deterministic, not less.** The current `total_score` sort leaves ties to Python's stable-sort incidental order. `_selection_key` breaks ties explicitly by `total_score`, then recency, then `story_identity`, so order is fully reproducible.
- **Clean back-compat / disable path.** When importance is disabled, `cluster.importance` is `0.0` for every cluster, so `_selection_key` reduces to `total_score` descending — identical to today's output. No separate flag is required, and none is added.

## Non-effects (verify, don't change)

- **Selection is unchanged.** Only the ordering of already-selected clusters changes; deck size, floors, ceilings, big-day slots, and source/lane caps are untouched.
- **Diagnostics are unchanged.** `floor/remainder/big_day/source_cap` counts are order-independent.
- **Drafting input is unchanged.** Stories reach the OpenAI batch in the prior `total_score` order; only completed paragraph sequence changes.
- **History and dedup are unchanged.** `selected_clusters()` flattens by identity; order does not affect what is persisted.
- **Culture and finance are consistent.** Culture stories reorder within the Culture section by the same key; finance `lead_lines` (the market ticker) render above paragraphs as today and are not part of this ordering.

## File-by-file

- `src/news_agent/pipeline.py`: retain `total_score` order through selection/drafting; add `order_paragraphs_for_presentation()` and call it after `draft_paragraphs()` but before `build_briefing_sections()`.
- `tests/test_pipeline.py`: add ordering tests (below).
- `tests/test_formatting.py`: add the SMS drop-order test (below).
- No changes to `models.py`, `config.py`, `config/sources.toml`, or `formatting.py`.

## Implementation steps

### Step 1 — add the post-draft presentation boundary

Keep the drafting order intact, then rank completed paragraphs using their selected clusters. Run:

```bash
python3 -m pytest tests/test_pipeline.py -q
```

**Tests:**

- `test_presentation_order_ranks_by_importance_not_total_score` — a story with lower `total_score` but higher `importance` leads its category section.
- `test_presentation_order_ties_break_by_total_score_then_recency_then_identity` — equal importance falls back through the key deterministically.
- `test_presentation_order_matches_total_score_when_importance_disabled` — with `importance.enabled = false` (all importance `0.0`), order equals the previous `total_score` ordering (back-compat).
- `test_build_result_reorders_only_after_drafting` — captures that drafting still receives total-score order while sections receive importance order.

### Step 2 — confirm all-channel and SMS-truncation behavior

**Tests (`tests/test_formatting.py`):**

- `test_sms_truncation_drops_lowest_importance_story_first` — build a section whose paragraphs are in importance order, force the SMS char budget, and assert the omitted story is the lowest-importance one and the retained ones stay in order.
- `test_all_channels_share_presentation_order` — the same section renders paragraphs in identical order for `sms`, `telegram`, and `console`.

### Step 3 — full regression

```bash
python3 -m pytest -q
git diff --check
```

Expect the pre-change suite plus the new tests to pass; no other test should need updating (no existing test pins `presentation_order` to `total_score` — verified by grep).

## Live validation

In an isolated dry run (`--openai-mode full --ignore-history --show-diagnostics`, no `--send`), confirm each category section leads with its highest-importance story and that any "+ N more stories omitted for length" on SMS corresponds to the lowest-importance tail.

## Rollback

Revert the implementation commit. Alternatively, because the disabled-importance path reduces the presentation key to `total_score`, setting `importance.enabled = false` restores the previous display order without a code change.

## Non-goals

The other two presentation levers from item 5 are explicitly **out of scope** here (not selected):

- No reordering of the five category **sections**; they keep the fixed Business → U.S. → Global → Culture → Finance order.
- No cross-category **"top story of the day" highlight block**.

Both remain available as later follow-ups; this plan covers within-category ordering only.

## Cross-reference

This supersedes the "within-category display order" portion of the *Presentation and ordering (scope)* note in `importance-scoring-plan.md`. That doc's Non-goals bullet should be narrowed to cover only section reordering and the top-story highlight once this lands.
