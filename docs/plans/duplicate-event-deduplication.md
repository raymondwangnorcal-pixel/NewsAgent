# Plan: Second-Pass Duplicate Gate on the Selected Deck

**Status:** Implemented and verified on 2026-07-28

## Goal

Prevent the same story from occupying more than one paragraph in a briefing.
After the deck for today's newsletter has been selected, a second pass asks an
OpenAI model to group any selected stories that describe the same underlying
event, and merges each group into one paragraph crediting every source.

## Problem being addressed

The July 27 `Test resend #4` newsletter contained no byte-for-byte duplicate
text. It contained separately drafted paragraphs about the same underlying
event: two reports on Anthropic's open-weight-model position, and two reports on
D4vd being ordered to stand trial. The duplication originated upstream of email
rendering:

- `cluster_articles()` merges an article into a cluster only at similarity
  `0.43`, then `merge_url_duplicates()` collapses shared canonical URLs.
- Two publishers can describe one event with headlines whose token overlap falls
  well under `0.43`, so both survive as separate clusters.
- Both clusters were then scored, classified, selected, and drafted
  independently. Gmail and Telegram rendered that shared briefing correctly;
  they were exposing an upstream defect.

## Where the gate runs, and why there

**The gate runs on the selected deck only** — the clusters that will actually
appear in today's newsletter, after `select_importance_deck()` has chosen them.
`deck_target` is `25` (`models.py:121`), so the universe is roughly 25 clusters
and at most 300 unordered pairs before the deterministic predicate narrows it.

This placement is precisely targeted at the stated problem. A duplicate pair
where only one member was selected is not a problem — the other is not in the
newsletter. The only duplicates that matter are the ones a reader will see, and
those are exactly the pairs the gate now examines.

Three consequences follow, and they are the point:

- **The cost question disappears.** One request per run covers every pair. There
  is no call budget to allocate, no pairs dropped by a cap, and no need to rank
  the candidate universe.
- **The deterministic predicate can be set from evidence, not from rationing.**
  Its previous job was choosing which pairs fit a scarce request budget. With no
  rationing to do, the threshold is set from the two real duplicates measured
  below.
- **No rescoring is needed.** Selection is already complete, so a merged
  cluster's `frequency_score` and `source_balance_score` no longer influence
  anything.

**The cost of this placement, stated once.** Because merging happens after
scoring and selection, a cluster's scores never reflect its true source count
while it was competing for a slot. A story confirmed by two outlets in two
separate clusters looked like two singly-sourced stories during selection, so it
may have ranked lower than it deserved — and in the worst case, neither half was
selected and the gate never sees it. This plan accepts that. Fixing it would
mean merging before scoring, which reintroduces the ~150-cluster universe, the
request budget, and the rationing that made the earlier draft complicated.

## Measured evidence: the July 27 Anthropic duplicate

Both halves of the duplicate were recovered from `data/` and run through the
real functions in `cluster.py`. Every number below is measured, not estimated.

| Cluster | Source | Canonical headline |
|---|---|---|
| A | TechCrunch | `Anthropic's Dario Amodei responds: doesn't oppose open-weight models, but fears Chinese AI` |
| B | CNBC + Axios | `Anthropic CEO Dario Amodei says AI company isn't advocating for ban of open-weight models` |

**Why the first pass missed them.** Scoring article A against cluster B with
`article_cluster_similarity()`:

```
title_jaccard  0.3529  x 0.55  = 0.1941
entity_jaccard 0.1670  x 0.20  = 0.0334
event_jaccard  0.0000  x 0.15  = 0.0000
time_proximity 1.0000  x 0.10  = 0.1000
                               = 0.3275   vs threshold 0.43
```

It was a near miss, not a wild one — short by `0.1025`. The `event_jaccard` of
zero is the single largest contributor: title A says "oppose", title B says
"ban", and only `ban` is in `EVENT_TERMS`.

**And it gets worse as coverage grows.** `cluster_tokens()` is the *union* of
every article's title tokens, so the denominator of the Jaccard grows with the
cluster. Re-running with cluster B holding both its real articles instead of one:

```
B = CNBC only     11 cluster tokens   title_j 0.3529   score 0.3275
B = CNBC + Axios  14 cluster tokens   title_j 0.3000   score 0.2983
```

A story becomes *harder* to join precisely as more outlets cover it. This is the
core argument for a second pass rather than tuning the `0.43` threshold: no
single threshold fixes a score that decays with cluster size, and lowering it far
enough to catch this pair would merge unrelated stories across the whole corpus.

**The gate catches it comfortably.** Comparing the two clusters directly, as
`duplicate_gate_candidates()` does:

| Signal | Value |
|---|---|
| Title Jaccard (cluster to cluster) | `0.3529` |
| Shared title tokens | `amodei`, `anthropic`, `dario`, `models`, `open`, `weight` |
| Shared entities | `anthropic`, plus the generic `AI` and `CEO` |
| Shared event terms | `{ban}` — both, once summaries are included |
| Hours apart | `0.0` |
| Different-development veto | not triggered (`0.3529 >= 0.32` short-circuits it) |

Eligible at every threshold tested: `0.08`, `0.12`, and `0.20`.

**The D4vd pair**, measured on its stored cluster keys, scores `0.25` title
Jaccard with empty `event_terms()` on both sides. Both known real duplicates
therefore clear `0.20` with room, which is where the threshold is now set —
`0.3529` and `0.25` against a `0.20` bar. This replaces the guessed `0.12` and
the reasoned-but-unmeasured `0.08`.

**One caveat on that second number.** The D4vd figure comes from stored
`story_key` values, which hold only the eight alphabetically-first tokens of each
title, not the full headline. Full-title Jaccard for that pair is unmeasured and
could fall either way. Step 7 resolves it against the real deck.

**A third D4vd cluster exists** — a Billboard timeline explainer, stored under
the key `case celeste charges d4vd death hernandez investigation murder`. It
covers the same subject as a retrospective rather than a report of Monday's
ruling. **All three must merge into one paragraph.** This is a decided case, not
an open one: a retrospective, timeline, or explainer about the same event is the
same story, and the Step 4 system prompt says so explicitly.

Measured at the `0.20` threshold, all three pairs are eligible, so the model sees
all three together as one candidate group:

| Pair | Title Jaccard | Specific shared entities | Eligible |
|---|---|---|---|
| BBC × Deadline | `0.3333` | `d4vd`, `david anthony burke` | yes |
| Deadline × Billboard | `0.3571` | `d4vd` | yes |
| BBC × Billboard | `0.2143` | `d4vd` | yes |

**Note the margin on that last row.** `0.2143` clears `0.20` by only `0.0143`. If
Step 7 raises the threshold past `0.215`, that link disappears. The timeline
would still reach the model through Deadline — one eligible pair is enough to put
all three in the same group — but the trio would then hang on a single link.
Weigh this when choosing the final threshold.

## Scope

In scope: two or more clusters *in today's selected deck* that describe one
event.

Out of scope: the same story recurring across editions on consecutive days.
`apply_history()` (`history.py:97`) already handles that via keyword overlap. If
cross-edition repetition is also a problem, it is a change to history's
thresholds, not to this gate.

## Model

`gpt-5.6-terra`, the model every other OpenAI stage in this repo already uses,
at `medium` reasoning effort.

The earlier draft specified `gpt-5-nano` purely as a cost optimization. Two
things retired that choice: accuracy is the stated priority, and `gpt-5-nano`
could not be confirmed to exist — it appears nowhere in the local Codex model
roster (`~/.codex/models_cache.json`, fetched 2026-07-29), which lists
`gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna`, `gpt-5.5`, `gpt-5.4`, and
`gpt-5.4-mini`. Terra is confirmed available, already in production here, and
already covered by the dated price verification in `[openai_costs]`.

Using the same model everywhere also means the gate needs no pricing table of
its own. It records against the existing `OpenAICostConfig` rates, and Step 1
below is correspondingly small.

**Cost.** Only clusters inside a multi-member component are sent, which on a
typical day is a handful rather than the whole deck. The budget estimator charges
one token per byte plus 512 overhead (`openai_budget.py:22-37`); a six-cluster
payload pre-books roughly `$0.008` of input and `$0.030` of output — near
`$0.038` per run against a `$1.00` cap. The 40-cluster worst case allowed by
`max_clusters_per_request` pre-books about `$0.077`. Real cost will be lower,
since true token counts run well under byte counts. For scale, your recent
compression audits cost `$0.0235`–`$0.0246` per run on the same model.

## Revision history

Kept so the reasoning behind each choice is not relitigated.

1. **Position moved twice.** The first draft ran the gate immediately after
   `cluster_articles(survivors)`, where `total_score` is still `0.0` for every
   cluster and the universe is ~150 clusters (~11,000 pairs) against a 12-call
   cap. The second draft moved it after scoring. This draft moves it after
   selection, which removes the budget problem entirely.
2. **Output token ceiling raised from 32 to 2000.** Reasoning tokens are billed
   against `max_output_tokens`, and exhausting it returns `status="incomplete"`
   with an empty `output_text`. A 32-token ceiling would very likely have
   produced empty responses on every call — a silently inert feature that still
   costs money. Repo precedent: the good/junk verdict over 40 articles allocates
   2000 (`quality_gate.py:218`); watchlist uses 700; classification uses 6000.
3. **One batched request instead of one per pair**, matching `quality_gate.py`.
4. **A disjoint-event veto reuses `different_development()`.** The first draft's
   predicate routed around a guard this repo already has.
5. **The Tesla non-merge test was wrong as written.** With the literal fixture
   titles `"Tesla earnings beat"` / `"Tesla safety recall"`, measured Jaccard is
   `0.2` and shared entities is `{tesla}`, so the pair qualified — contradicting
   the assertion. It is excluded by the veto, not the threshold, and the test
   below now says so.
6. **Pairwise booleans replaced with set partitioning.** Two earlier drafts asked
   one true/false question per pair — first merging by connected component, which
   chains unrelated stories, then by clique, which cannot deliver a three-way
   merge unless every internal pair is separately affirmed. Neither could
   guarantee the required D4vd outcome. The model now partitions each
   deterministic component directly. See Step 4.
7. **A dead requirement removed.** "Exclude pairs whose canonical URLs overlap"
   is unreachable: `cluster_articles()` returns `merge_url_duplicates(clusters)`
   (`cluster.py:225`).
8. **`reasoning_effort` validation corrected.** The earlier draft accepted
   `minimal`, which no model in the local roster supports. Valid efforts are
   `low`, `medium`, `high`, and `xhigh`.

## Decisions locked by this plan

- The gate runs inside `collect_pipeline_context()`, immediately after
  `category_clusters = selection_result.category_clusters` and before
  `selected_counts` is computed. Drafting, compression, presentation ordering,
  and `save_story_history()` all consume the merged deck with no changes.
- **A merged deck is a smaller deck, and that is accepted.** Set partitioning
  means a group of three collapses to one, so a day like July 27 loses three
  slots — one to the Anthropic pair, two to the D4vd trio — and a `deck_target`
  of 25 delivers 22. The gate does not backfill. A duplicate means there were
  genuinely fewer distinct stories than the deck targeted, and reporting 22 real
  stories is the honest outcome. Backfilling would require re-running selection
  with merged clusters in place and re-deriving category floors; it can follow
  later if the shrinkage proves material. `deck_selected` reports the post-merge
  count.

  **A consequence to watch, not to fix here:** selection satisfies the
  `[selection_limits]` floors, and the gate can then remove members, so a
  category can end up under its floor after the fact.
  `underfilled_reasons_by_category` is computed after the merge and will report
  it; nothing acts on it. Confirm in Step 7 whether any category actually
  breaches its floor on a real run.
- **Cross-category merges are allowed.** Two selected clusters may sit in
  different categories and still be the same event; a reader seeing it twice in
  one email does not care that the sections differ. The destination keeps its
  own category and the other member is removed from its category list, which
  means one category's count drops. This is counted separately in diagnostics
  because it can push a category under its floor.
- The destination cluster's `importance` becomes the maximum of the merged
  members', so presentation ordering reflects the strongest of the two.
- The destination keeps the stronger story's existing `key`. Absorbed keys are
  recorded in `merged_from`; the merge must not recompute the destination key.
  This preserves the category assignment and outlier filtering already stored
  under that identity.
- **A merged cluster does not use the normal headline rule.**
  `choose_canonical_headline()` breaks reputation ties by preferring the *longer*
  title, which hands the headline to explainers and timelines — the longest
  headlines in any group. Now that retrospectives merge, merged clusters get
  their own chooser. See Step 4.
- Any failure — model unavailable, OpenAI disabled, budget exhausted, malformed
  JSON, `status="incomplete"`, a rejected set, an omitted cluster — leaves those
  clusters separate. The gate can only ever remove duplication. It can never add a
  story, drop a distinct one, or change any text.

## Non-goals

- Do not lower the `cluster_articles()` threshold of `0.43`.
- Do not apply the gate to `mailer/watchlist_news.py`. Watchlist runs an
  independent Google-News-per-ticker pipeline (`watchlist_news.py:69`), so one
  event can in principle appear in both the general briefing and the watchlist
  section of the same email. Deferred by decision, not overlooked; revisit only
  if it is observed happening.
- Do not change the `$1.00` run budget or draw on the watchlist reserve.
- Do not backfill the deck after a merge. See above.
- Do not adjust `has_meaningful_update()` (`history.py:87-94`). A merged record
  saves more sources, so tomorrow's follow-up needs a genuinely new outlet — or
  four new keywords, or a changed summary — to count as an update rather than a
  repeat. That is a real behavior change, reviewed and accepted as consistent
  with wanting fewer repeats.
- Do not send or resend an email while implementing or testing this work.
- Do not extend `EVENT_TERMS`. See "Known limitation".

## Known limitation

`EVENT_TERMS` (`cluster.py:21-51`) holds 29 terms weighted toward business and
politics. It contains `lawsuit`, `charges`, and `probe`, but not `trial`,
`verdict`, `ruling`, or `indictment`.

Measured against the real July 27 run: both D4vd cluster keys produce an empty
`event_terms()` set, and their title Jaccard is `0.25`. So that pair qualifies on
title overlap alone, above the `0.20` threshold below, and it is
never wrongly vetoed — `different_development()` only vetoes when *both* sides
carry event terms. The gap costs nothing here. Widening the vocabulary would
change first-pass clustering for every story in the corpus and belongs in its own
change with its own regression run.

## Preconditions

- [ ] `PYTHONPATH=src python3 -m pytest` exits `0` before the first change.
- [ ] The local `.env` contains `OPENAI_API_KEY` for the Step 7 smoke test. Unit
  tests must mock the structured response and must never require it.

## Steps

### 1. Configure the gate

**Files:** `models.py`, `config.py`, `config/sources.toml`, `tests/test_config.py`

```python
@dataclass(frozen=True)
class DuplicateGateConfig:
    enabled: bool = True
    candidate_window_hours: float = 24.0
    candidate_title_jaccard_threshold: float = 0.20
    max_clusters_per_request: int = 40
    max_output_tokens_per_request: int = 2000
    reasoning_effort: str = "medium"
    max_component_size: int = 4
    summary_truncate_chars: int = 250
```

No model or pricing fields. The gate uses `config.openai_costs.model` and the
existing `[openai_costs]` rates, exactly like every other stage.

Add it as a field on `AgentConfig` with this as the default factory, parse the
`[duplicate_gate]` table in `load_config()`, and validate in
`validate_duplicate_gate_config()` following the house pattern at
`config.py:59-88`. Reject: a nonpositive window; a threshold outside
`[0.0, 1.0]`; nonpositive `max_clusters_per_request`,
`max_output_tokens_per_request`, or `summary_truncate_chars`; a
`max_component_size` below 2; a `reasoning_effort` outside
`{"low", "medium", "high", "xhigh"}`. Every message carries the
`duplicate_gate.<key>` prefix.

Add to `config/sources.toml` after `[openai_costs]`:

```toml
[duplicate_gate]
enabled = true
candidate_window_hours = 24.0
# Set from measurement, not guesswork: the July 27 Anthropic duplicate scores
# 0.3529 and the D4vd pair 0.25. Both clear this with room. Re-check in Step 7.
candidate_title_jaccard_threshold = 0.20
max_clusters_per_request = 40
# Reasoning tokens count against this ceiling. Do not lower without re-reading
# item 2 of the revision history.
max_output_tokens_per_request = 2000
reasoning_effort = "medium"
max_component_size = 4
summary_truncate_chars = 250
```

Test that defaults load as written, and that a bad threshold, a bad reasoning
effort, and a `max_component_size` of 1 each raise `ValueError` naming the key.

**Verify:** `PYTHONPATH=src python3 -m pytest tests/test_config.py -q`

**Commit:** `feat(config): add duplicate gate configuration`

### 2. Support reasoning effort and guard incomplete responses

**Files:** `openai_client.py`, `tests/test_openai_client.py`

Extend `request_structured_response()` with one keyword-only argument
defaulting to current behavior:

```python
reasoning_effort: str = "",
```

Pass `reasoning={"effort": reasoning_effort}` only when it is non-empty, so the
five existing call sites are byte-for-byte unchanged.

Add an incomplete-response guard, which the codebase currently lacks. When
`getattr(response, "status", "") == "incomplete"`, record usage — the tokens
were spent — then `record_failure(budget_stage, f"{stage}_incomplete_response")`
and return a `StructuredResponseOutcome` with no response. Today an incomplete
response reaches the caller and fails later as a JSON parse error, which reports
the wrong cause. This benefits every stage, not only the gate.

No `pricing` or `allow_env_model_override` argument is needed. The gate uses the
same model and rates as everything else, so `OPENAI_MODEL` should apply to it
exactly as it applies to classification and drafting.

Tests:

- `reasoning` is absent from the request when `reasoning_effort` is `""`, and
  present as `{"effort": "medium"}` when set.
- A mocked `status="incomplete"` response yields `response is None`, an
  `_incomplete_response` failure reason, and recorded token cost.

**Verify:** `PYTHONPATH=src python3 -m pytest tests/test_openai_client.py tests/test_openai_budget.py -q`

**Commit:** `feat(openai): reasoning effort and incomplete-response guard`

### 3. Generate eligible pairs from the deck

**Files:** `cluster.py`, `tests/test_cluster.py`

Add two functions to `cluster.py`.

First, lift the existing article-vs-cluster guard to cluster-vs-cluster, reusing
its exact semantics (`cluster.py:164-172`) — it vetoes only when both sides have
event terms and those sets are disjoint:

```python
def clusters_are_different_developments(
    left: StoryCluster,
    right: StoryCluster,
    title_score: float,
) -> bool:
    if not (cluster_entities(left) & cluster_entities(right)) or title_score >= 0.32:
        return False
    left_events = cluster_event_terms(left)
    right_events = cluster_event_terms(right)
    return bool(left_events and right_events and left_events.isdisjoint(right_events))
```

Second, a generic-entity filter. Running the real Anthropic pair showed
`extract_entities()` returns `AI` and `CEO` as entities for both clusters. Two
unrelated tech stories with a chief executive in the headline therefore "share an
entity", which makes `bool(shared_entities)` nearly vacuous across the
`business_tech` section and leaves the predicate doing no work.

The cause is in `extract_entities()` (`cluster.py:103-112`): the `CAPITAL_PHRASE_RE`
branch filters against `ENTITY_STOPWORDS`, but the `TICKER_RE` branch does not,
so any 2–6 character uppercase token is admitted unfiltered. `CEO` is already in
`ENTITY_STOPWORDS` and still gets through by the other path.

**Do not fix `extract_entities()` in this plan.** It feeds `entity_score` in
first-pass clustering, and changing it re-clusters the whole corpus. Filter
locally in the gate instead:

```python
GENERIC_GATE_ENTITIES = frozenset({"AI", "CEO", "CFO", "COO", "CTO", "US", "USA", "UK", "EU", "GDP", "IPO"})


def specific_shared_entities(left: StoryCluster, right: StoryCluster) -> set[str]:
    shared = cluster_entities(left) & cluster_entities(right)
    return {entity for entity in shared if entity.upper() not in GENERIC_GATE_ENTITIES}
```

On the real Anthropic pair this leaves `{anthropic}`, so the pair stays eligible.

Third, `duplicate_gate_candidates(clusters, config)`, taking the flattened
selected deck. For each unordered pair, a pair is eligible when all four hold:

```python
hours_apart(left.latest_published_at, right.latest_published_at) <= config.candidate_window_hours
and bool(specific_shared_entities(left, right))
and (title_jaccard >= config.candidate_title_jaccard_threshold or bool(shared_event_terms))
and not clusters_are_different_developments(left, right, title_jaccard)
```

Note that `clusters_are_different_developments()` keeps using the unfiltered
`cluster_entities()` intersection, so it matches the existing
`different_development()` semantics exactly. Only eligibility uses the filtered
set.

Return pairs ordered by descending `(len(shared_entities), title_jaccard)`, tie-
broken on the pair's two `story_key` values so ordering is stable across runs.
Ordering exists only for deterministic payloads and readable diagnostics; with a
25-cluster deck every eligible pair fits in one request.

Do not filter on overlapping canonical URLs — `cluster_articles()` already ends
in `merge_url_duplicates()`, so the condition is unreachable.

Tests. **Use the real July 27 headlines as the primary fixture**, not invented
ones, and assert the measured numbers so a regression in `tokenize()`,
`extract_entities()`, or the predicate is caught immediately:

- The two real Anthropic clusters — TechCrunch's `Anthropic's Dario Amodei
  responds: doesn't oppose open-weight models, but fears Chinese AI` and CNBC's
  `Anthropic CEO Dario Amodei says AI company isn't advocating for ban of
  open-weight models` — become one eligible pair. Assert title Jaccard `0.3529`
  to four places, shared specific entities `{anthropic}`, and that the veto does
  not fire.
- A companion test asserts `article_cluster_similarity()` still scores that pair
  at `0.3275`, below the `0.43` first-pass threshold. If someone later retunes
  clustering so the first pass catches it, this test fails and tells them the
  gate's primary fixture is now dead rather than leaving it silently passing.
- The `AI`/`CEO` regression: two unrelated `business_tech` clusters that share
  only those two entities are **not** eligible. This is what the generic filter
  exists for, and without the test it can be deleted as apparently redundant.
- `"Tesla tops Q2 earnings estimates as margins recover"` and
  `"Tesla recalls 375,000 vehicles over steering fault"` are excluded. Assert
  both reasons independently: measured Jaccard `0.0833` is below the `0.20`
  threshold, *and* the pair is vetoed on `{earnings}` versus `{recall}`.
- The short forms `"Tesla earnings beat"` / `"Tesla safety recall"` (Jaccard
  `0.2`) sit exactly at the threshold and are excluded by the veto alone. This is
  the regression test for the veto, and it is why the veto is not optional.
- Two D4vd trial fixtures become an eligible pair with no shared event terms.
- A pair 30 hours apart is excluded on the window.
- A 25-cluster deck with no related stories produces zero eligible pairs, and
  the caller makes no request.

**Verify:** `PYTHONPATH=src python3 -m pytest tests/test_cluster.py -q`

**Commit:** `feat(cluster): identify eligible duplicate-gate pairs`

### 4. Partition candidate groups in one request and merge each set

**Files:** `duplicate_gate.py`, `cluster.py`, `models.py`,
`tests/test_duplicate_gate.py`, `tests/test_cluster.py`

Create `duplicate_gate.py` with the contract values:

```python
DUPLICATE_GATE_SYSTEM_PROMPT = (
    "You are given groups of candidate news stories. Within each group, decide "
    "which stories describe the same underlying event and should become one "
    "combined paragraph for the reader.\n\n"
    "Put stories together when they report the same event from different "
    "outlets, even when their angles, headlines, or emphasis differ. Put them "
    "together when one is a retrospective, timeline, explainer, or background "
    "piece about the same event another reports — differing depth or format is "
    "not a different event.\n\n"
    "Keep stories apart when they are separate developments involving the same "
    "company, person, or topic, including a follow-up that adds a materially new "
    "event, and including a market move reported alongside the news that caused "
    "it.\n\n"
    "Return one entry per set of stories that belong together, listing every "
    "cluster_id in that set. A set must contain at least two cluster_ids. Omit "
    "any story that belongs with nothing else. Never place one cluster_id in two "
    "sets, and never list a cluster_id that was not in the same candidate group."
)

DUPLICATE_GATE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "same_event_sets": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "cluster_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["cluster_ids"],
            },
        }
    },
    "required": ["same_event_sets"],
}
```

**Why sets and not pairwise booleans.** An earlier draft asked one true/false
question per pair and rebuilt groups from the answers. That cannot reliably
produce a three-way merge: the two D4vd trial reports plus the Billboard timeline
require three separate affirmations, and any one "false" leaves a partial merge.
Reconstructing groups from pair votes also forces a choice between transitive
chaining (merge `A`, `B`, `C` when `A~C` was never affirmed) and refusing
three-way merges altogether. Asking the model to partition a group directly
removes the arithmetic: it sees all candidates at once and makes one holistic
judgment, which is strictly better informed than three independent binary ones.

**The deterministic graph still bounds the model.** Build connected components
over the eligible pairs from Step 3. The model is shown one candidate group per
component and may only split a component, never join across two. A set naming
cluster ids from different components is rejected wholesale and counted. This is
what keeps a looser threshold from letting the model assemble arbitrary merges.

**Payload.** Emit each cluster once inside its component:

```json
{
  "groups": [
    {"group_id": "g0", "clusters": [
      {"id": "c0", "title": "...", "published_at": "...",
       "source": "...", "summary": "..."}
    ]}
  ]
}
```

One article per cluster — the highest-evidence one — with its summary truncated
to `config.summary_truncate_chars`. Omit URLs; the model does not need them and
they are pure token cost.

**Request.** Call `request_structured_response()` with `stage` and
`budget_stage` both `"duplicate_gate"`,
`default_model=config.openai_costs.model`,
`reasoning_effort=config.duplicate_gate.reasoning_effort`, and
`max_output_tokens=config.duplicate_gate.max_output_tokens_per_request`. Make no
request at all when there are zero eligible pairs, and none when every component
is a single cluster.

**Truncate by whole component, never by pair.** Include components in descending
order of size, then of best internal pair score, until adding the next would push
the payload past `config.max_clusters_per_request`. Cutting mid-component would
hand the model a fragment of a group and make a three-way merge impossible while
appearing to work. Count the components dropped this way. With a 25-story deck
this should never trigger, which is exactly why it must be correct when it does.

Parse defensively, in the style of `quality_gate.py:315-327`: catch any exception
around `json.loads(outcome.response.output_text)`, record
`duplicate_gate_malformed_response`, and treat the whole request as
unadjudicated.

**Validate every returned set before acting on it.** Reject and count a set that:
contains fewer than two ids; names an unknown id; names ids from more than one
component; exceeds `config.max_component_size`; or names an id already claimed by
an accepted set. Rejection is per-set — one bad set does not discard the others.

**Add one field to `StoryCluster` first**, in `models.py`:

```python
merged_from: tuple[str, ...] = ()
"""Keys of the clusters absorbed by the duplicate gate. Empty for every cluster
that was never merged. `bool(merged_from)` is the is-merged test used by
drafting, formatting, and diagnostics."""
```

One field does three jobs: it flags merged stories to the drafting prompt
(Step 6), it keeps the absorbed keys reportable in diagnostics, and it makes
"was this cluster merged" answerable anywhere downstream without a side table.

**A merged cluster needs its own headline rule.** Add to `cluster.py`, beside
`choose_canonical_headline()`:

```python
RETROSPECTIVE_TITLE_MARKERS = (
    "timeline",
    "explainer",
    "explained",
    "everything we know",
    "what to know",
    "what we know",
    "a look at",
    "recap",
)


def is_retrospective_title(title: str) -> bool:
    lowered = title.lower()
    return any(marker in lowered for marker in RETROSPECTIVE_TITLE_MARKERS)


def choose_merged_headline(articles: list[Article]) -> str:
    if not articles:
        return ""
    ranked = sorted(
        articles,
        key=lambda item: (
            not is_retrospective_title(item.title),
            item.reputation,
            item.published_at,
        ),
        reverse=True,
    )
    return ranked[0].title
```

`choose_canonical_headline()` (`cluster.py:188-197`) breaks reputation ties by
preferring the *longer* title. Explainers and timelines carry the longest
headlines in any group, so once retrospectives merge, that rule actively selects
the wrong one. Measured on the real trio: BBC World is `0.95`, Deadline and
Billboard are both `0.8`, and Billboard's `D4vd Murder Case: A Timeline of the
Investigation & Charges…` is the longest title present. BBC's reputation saves
this particular case — remove BBC and the timeline headline wins outright.

That headline is not cosmetic: it becomes `DraftCandidate.title` and enters the
drafting payload, steering the model toward writing a retrospective instead of
leading with the ruling.

**Do not change `choose_canonical_headline()` itself.** It runs on every cluster
in first-pass clustering, and altering it re-titles the whole corpus.

**Merge.** For each accepted set, choose the member with the highest
`(importance, total_score, latest_published_at, key)` as the destination, extend
its `articles` with every other member's, set its `importance` to the set
maximum, set `merged_from` to the other members' keys captured *before*
`refresh_cluster()` runs, call `refresh_cluster(destination)`, then override the
title:

```python
destination.title = choose_merged_headline(destination.articles) or destination.title
```

Do not call `refresh_cluster()` for the merged destination because it would
replace the key used by `category_assignments`. Preserve the chosen
destination's key, set `merged_from` from the absorbed members' existing keys,
and choose only the merged title:

```python
destination.title = choose_merged_headline(destination.articles) or destination.title
```

Return three things: the surviving clusters, the **list of removed cluster
objects**, and a `DuplicateGateStats` record carrying deck size, eligible pairs,
candidate components, clusters offered, components dropped by the payload cap,
sets returned, sets rejected, sets merged, clusters removed, cross-category
merges, and whether a request was made.

The removed-object list is not optional bookkeeping. Two consumers outside the
newsletter read the *unmerged* cluster list and would otherwise still see both
halves of every duplicate — see Step 5.

Tests, with `request_structured_response()` mocked throughout:

- Two Anthropic clusters returned as one set: one cluster survives carrying both
  source articles, and its `sources` lists both outlets.
- **The three real D4vd clusters** — BBC's `US singer D4vd to go on trial for
  murder in death of 14-year-old`, Deadline's trial report, and Billboard's
  `D4vd Murder Case: A Timeline of the Investigation & Charges…` — form one
  component and merge into a single cluster carrying all three sources when the
  model returns them as one set. This is the fixture pinning both the
  retrospective rule in the prompt and the three-way merge; it is the case the
  pairwise design could not guarantee.
- Zero eligible pairs, and separately every component of size one: no request is
  made at all.
- Malformed JSON, an `error_code` outcome, and a `None` response each leave every
  cluster intact.
- A set naming an unknown cluster id is rejected and counted; other sets in the
  same response still merge.
- A set spanning two components is rejected wholesale.
- A set of one id is rejected.
- A cluster id appearing in two sets is honoured once; the second set is
  rejected.
- A six-member set with `max_component_size=4` is rejected and merges nothing.
- The model splitting a three-cluster component into one pair plus one omitted
  cluster merges exactly the pair.
- The destination's `importance` equals the set maximum, not its own.
- **The merged D4vd trio is titled `US singer D4vd to go on trial for murder in
  death of 14-year-old`**, not Billboard's timeline headline.
- The same trio with the BBC article removed is titled from Deadline, not
  Billboard — Deadline and Billboard tie at `0.8` reputation, so this asserts the
  retrospective marker is doing the work rather than reputation.
- `is_retrospective_title` is `False` for an ordinary news headline containing
  none of the markers, so unmerged behavior is untouched.

**Verify:** `PYTHONPATH=src python3 -m pytest tests/test_duplicate_gate.py -q`

**Commit:** `feat(dedup): adjudicate and merge duplicates in the selected deck`

### 5. Wire the gate into selection and surface the decisions

**Files:** `pipeline.py`, `models.py`, `cli.py`, `tests/test_pipeline.py`,
`tests/test_cli.py`

In `collect_pipeline_context()`, insert immediately after
`category_clusters = selection_result.category_clusters` (`pipeline.py:648`) and
before `selected_counts` is computed (`:651`):

```python
duplicate_gate_stats = DuplicateGateStats()
if config.duplicate_gate.enabled and capabilities.classify and capabilities.draft:
    category_clusters, removed_clusters, duplicate_gate_stats = apply_duplicate_gate(
        category_clusters,
        config,
        assignments=assignments,
        budget=budget,
    )
    if removed_clusters:
        removed_ids = {id(cluster) for cluster in removed_clusters}
        clusters = [cluster for cluster in clusters if id(cluster) not in removed_ids]
```

`apply_duplicate_gate` takes and returns the category-keyed dict. Internally it
flattens with the existing `selected_clusters()` helper (`pipeline.py:754`),
runs the gate, and rebuilds the dict with removed members dropped from their
category lists.

**The `clusters` filter is the important line.** Two consumers outside the
newsletter read `all_clusters`, which is this same list (`pipeline.py:663`), and
both would otherwise still see both halves of every duplicate:

- `generate_alerts(context.all_clusters, ...)` at `pipeline.py:1031`. The alert
  path calls `collect_pipeline_context()`, so today it would *pay for the gate
  request and then discard the result*. Worse, `cluster_alerts()` trigger terms
  include `"ai"` (`alerts.py:107`), which both July 27 Anthropic clusters match —
  so the exact duplicate this plan exists to kill would still fire twice through
  `--alerts`.
- `build_skipped_stories(context.all_clusters, selected, ...)` at
  `pipeline.py:893`. It computes `selected_ids = {id(cluster) for cluster in
  selected_clusters}` (`skipped_log.py:60`) — object identity. An absorbed
  cluster is not in `selected`, so without the filter it is written to the
  skipped-stories log with a rejection reason despite having been published as
  part of a merged story. That log is the diagnostic used to tune everything
  else; it must not lie.

`capabilities.draft` is required as well as `capabilities.classify`. In
`classify-only` mode drafting falls back to the extractive path, which takes text
from a single article — so a merged cluster would render one outlet's words under
a credit line naming three. Better not to merge at all in that mode.

Two existing lines must change, because both are now stale after a merge:

- `selected_counts` at `:651` is already derived from `category_clusters`, so it
  is correct with no edit. Confirm this in a test rather than assuming it.
- `deck_selected=selection_result.selected_count` at `:694` **is** stale. Change
  it to count the merged deck. `floor_selected_by_category`,
  `remainder_selected_by_category`, and `big_day_selected_by_category` describe
  what selection did and should stay as they are — add a comment saying so, so a
  later reader does not "fix" them.

Add defaulted `PipelineDiagnostics` fields and print them all in
`print_diagnostics()`:

```python
duplicate_gate_deck_size: int = 0
duplicate_gate_eligible_pairs: int = 0
duplicate_gate_candidate_components: int = 0
duplicate_gate_clusters_offered: int = 0
duplicate_gate_components_dropped: int = 0
duplicate_gate_sets_returned: int = 0
duplicate_gate_sets_rejected: int = 0
duplicate_gate_sets_merged: int = 0
duplicate_gate_clusters_removed: int = 0
duplicate_gate_cross_category_merges: int = 0
```

The generic `openai_cost_by_stage` and `openai_stage_outcomes` output already
carries the `duplicate_gate` cost, request count, and fallback reasons with no
new code.

`sets_returned` against `sets_rejected` is the health signal: a persistent gap
means the model is proposing sets the deterministic components do not support,
and the threshold or the prompt needs attention.

Tests:

- Two Anthropic clusters in the deck returned as one set yield one downstream
  draft candidate whose `sources` contains both outlets.
- `deck_selected` reports the post-merge count, and `selected_stories_by_category`
  agrees with it.
- A cross-category merge removes the member from its own category list and
  increments `duplicate_gate_cross_category_merges`.
- `save_story_history()` writes one record for a merged story, not two.
- **An absorbed cluster does not appear in the skipped-stories log.** Build a
  deck with a merged pair, run `build_skipped_stories()` on the post-gate
  `all_clusters`, and assert the absorbed cluster is absent. Without the
  `clusters` filter this test fails.
- **`generate_alerts()` produces one alert for a merged story, not two.** Use the
  two real Anthropic clusters, which both match the `"ai"` trigger term.
- `openai_mode="off"`, `openai_mode="classify-only"`, and
  `duplicate_gate.enabled = false` each leave the deck untouched with no request
  made.
- An exhausted budget yields the original deck and a recorded fallback.
- A CLI test with mocked diagnostics asserts `--dry-run --show-diagnostics`
  prints all ten fields.
- An email-rendering regression passes one merged `BriefingParagraph` carrying
  two sources and asserts the paragraph appears exactly once in the parity plain
  text and once in the native email HTML, with both source links present.

**Verify:**

```bash
PYTHONPATH=src python3 -m pytest tests/test_pipeline.py tests/test_cli.py tests/test_mailer.py tests/test_formatting.py -q
```

**Commit:** `feat(pipeline): run the duplicate gate on the selected deck`

### 6. Make a merged story draft as one combined summary

**Files:** `pipeline.py`, `draft.py`, `formatting.py`, `config/sources.toml`,
`models.py`, `tests/test_draft.py`, `tests/test_pipeline.py`,
`tests/test_formatting.py`

Merging clusters is necessary but not sufficient. The target output for the
merged D4vd story is a compact summary drawing on both reports, with both
outlets credited:

> A Los Angeles judge ordered musician D4vd, 21, whose legal name is David
> Anthony Burke, to stand trial on charges including murder, child sexual abuse,
> and mutilating human remains in the death of 14-year-old Celeste Rivas
> Hernandez. Following a five-day preliminary hearing with testimony from a dozen
> witnesses, prosecutors alleged Burke stabbed and dismembered Hernandez after
> years of abuse. He pleaded not guilty and remains held without bail;
> arraignment is set for Aug. 31. Her remains were found last September in a
> Tesla registered to his address. *(via Deadline, BBC World)*

One part of that already works: `format_paragraph_item()` emits
`f"(via {sources})"` at `formatting.py:200`, and `StoryCluster.sources` orders
outlets by reputation. No formatting change is needed for the credit line.

Three things do need changing.

**a. The drafting sample can miss one side of the merge.** `_candidate_payload()`
sends `candidate.articles[:ARTICLES_PER_STORY_SAMPLE]` with the constant set to
`3` (`draft.py:16`), and `build_draft_candidates()` re-ranks purely by evidence
score (`pipeline.py:801`). A merged cluster whose stronger half contributes the
top three articles would be drafted entirely from that half — the paragraph would
read as one report, and the second outlet would appear in the credit line without
having contributed a fact.

Fix it by selecting for source diversity rather than raw evidence rank. In
`build_draft_candidates()`, choose the sample by taking the highest-evidence
article from each distinct source first, then filling any remaining slots by
evidence:

```python
def _diverse_article_sample(articles: tuple[Article, ...], limit: int) -> tuple[Article, ...]:
    ranked = rank_articles_by_evidence(list(articles))
    chosen: list[Article] = []
    seen_sources: set[str] = set()
    for article in ranked:
        if article.source not in seen_sources:
            chosen.append(article)
            seen_sources.add(article.source)
        if len(chosen) == limit:
            return tuple(chosen)
    for article in ranked:
        if article not in chosen:
            chosen.append(article)
        if len(chosen) == limit:
            break
    return tuple(chosen)
```

Note this changes drafting for *unmerged* multi-source clusters too. Today a
cluster with two Reuters articles and one AP article may send all three Reuters-
heavy; afterwards it sends one per outlet first. That is a defensible improvement
on its own, but it is a behavior change outside the duplicate gate and must be
called out in the commit message rather than slipped in.

**b. The source cap will start binding.** `max_sources_per_story = 3`
(`config/sources.toml:6`). Two merged two-source clusters produce four outlets
and one gets dropped, which contradicts "list the sources of both". Raise it to
`5`. This affects unmerged stories with many outlets as well — a deliberate
choice, since a well-corroborated story crediting five outlets is desirable.

**b2. Telegram discards the credit line before it discards stories.**
`format_category_message()` (`formatting.py:141-157`) reduces an over-long
section by first setting `include_sources = False` for the *entire section*, and
only then dropping stories:

```python
if include_sources:
    include_sources = False
    continue
visible_count -= 1
```

Telegram's `max_chars` is `3600`; email's is `None`, so email is unaffected. Both
changes above push toward that ceiling — merged paragraphs are longer, and the
cap just went from 3 to 5 — so the `(via …)` requirement can vanish in Telegram
and take every other story's attribution with it.

Fix it narrowly rather than reordering the fallback, which exists deliberately so
a section loses attribution before it loses a story. Add `is_merged: bool = False`
to `BriefingParagraph`, populate it from `bool(cluster.merged_from)`, and in
`build_section_text()` emit the `(via …)` line for a merged paragraph even when
`include_sources` is `False`. A merged story's attribution is load-bearing —
it is the reader's only signal that two reports were combined.

**b3. An emptied category renders as a bare heading.** `build_briefing_sections()`
creates a section for every entry in `CATEGORY_NAMES` unconditionally
(`pipeline.py:813-829`). Rendering an empty one produces exactly this and nothing
more:

```
🎭 CULTURE + MEDIA · July 28
```

Cross-category merges are allowed, so a single-story category whose story is
absorbed elsewhere hits this. **This is pre-existing** — any category that
selects nothing today already renders a bare header — so treat the fix as an
independent improvement: skip sections with no paragraphs in
`build_briefing_sections()`, and add a formatting test for it. If you would
rather not change existing behavior in this plan, the alternative is forbidding a
merge that would empty a category, but that trades a cosmetic defect for a real
duplicate surviving into the newsletter. Skipping the empty section is the better
trade.

**c. Preserve the destination key and union outlier decisions.**
`build_draft_candidates()` does `category_assignments.get(cluster.key)`
(`pipeline.py:796`). Keep the destination's existing key so that lookup remains
valid. Pass `assignments` into `apply_duplicate_gate()` and replace the
destination's assignment with a `CategoryAssignment` whose `outlier_urls` is the
union of every merged member's; leave absorbed members' old entries in place for
auditability.

**d. Sentence-count guidance.** `DRAFT_SYSTEM_PROMPT` asks for "normally 55-90
words and 2-3 sentences" (`draft.py:78`). The target above is roughly 88 words in
four sentences: within the word range, past the sentence guidance. Add
`is_merged: bool = False` to `DraftCandidate`, set it from
`bool(cluster.merged_from)` in `build_draft_candidates()`, include it in the
payload, and add one line to the prompt:

> When a story is marked `is_merged`, it combines reporting from separate
> outlets on one event. Draw the strongest specific facts from each, and use up
> to 4 sentences within the same word range rather than 2-3.

**Compression interacts, and that is fine.** `min_words_to_compress = 40`, so an
88-word merged paragraph will go through compression. `COMPRESS_SYSTEM_PROMPT`
preserves every named entity, figure, date, and attribution and removes only
redundancy — which is exactly right for a paragraph assembled from two reports
that repeat each other. Verify rather than assume: the smoke test in Step 7
checks a merged paragraph after compression, and `guard_deltas` in the
compression audit already records entity loss.

Tests:

- `_diverse_article_sample` on a merged cluster with three high-evidence articles
  from outlet A and one from outlet B returns at least one B article.
- A merged D4vd draft candidate carries articles from both original clusters, and
  its `sources` lists both outlets in reputation order.
- Rendering that merged paragraph produces exactly one `(via …)` line naming both
  outlets, in both parity plain text and native email HTML.
- A four-source merged story renders all four, proving the cap was raised.
- A merged cluster whose members had different `outlier_urls` yields a draft
  candidate excluding the union of both sets.
- `is_merged` is `False` for every unmerged candidate, and `True` exactly when
  `cluster.merged_from` is non-empty. The payload omits no existing field.
- A merged paragraph keeps its `(via …)` line in telegram mode when the section
  is over `max_chars` and unmerged paragraphs have lost theirs.
- A category emptied by a cross-category merge produces no section at all, rather
  than a bare heading.

**Verify:**

```bash
PYTHONPATH=src python3 -m pytest tests/test_draft.py tests/test_pipeline.py tests/test_formatting.py tests/test_mailer.py -q
```

**Commit:** `feat(draft): compose merged stories from every contributing source`

### 7. Calibrate the threshold, then smoke-test with no send

**Files changed:** `config/sources.toml` only, and only if calibration says so.

The `0.20` threshold is anchored on two measured duplicates (`0.3529` and
`0.25`). Two data points set a floor, not a ceiling — they show the threshold is
low enough to catch known duplicates, but say nothing about how many *unrelated*
pairs it also admits. That is what this step measures, using no API calls.

Write a throwaway script at
`/private/tmp/claude-501/-Users-raymondwang-PersonalProjects-NewsAgent/8221310d-20d4-4f1c-bca5-820bd764b355/scratchpad/calibrate_gate.py`
— outside `src/` and outside the repo. It should load the stored July 26 and
July 27 run artifacts under `data/`, reconstruct the selected deck, and report,
for thresholds `0.10` through `0.36` in steps of `0.02`: the eligible pair count,
how many pairs the generic-entity filter removed, how many the veto removed, and
every eligible pair with both titles. The range brackets both measured
duplicates, so the report shows exactly where each one drops out.

Capture the model's response once and replay it from a fixture for the rest of
the step. The gate is a model call, so re-calling makes the calibration a moving
target.

**A human reads that list and picks the threshold.** Do not automate this
decision. The deck is small enough that every eligible pair can be read; the
question is whether the pairs at a given setting look like genuine same-event
candidates or like noise. Append the chosen value and the pair count it produced
to the existing `candidate_title_jaccard_threshold` comment in the TOML.

Then run one no-send preview:

```bash
PYTHONPATH=src .venv/bin/python -m news_agent.cli --dry-run --to email --show-diagnostics
```

Expected:

- no SMTP delivery occurs;
- all seven `duplicate_gate_*` fields print;
- `duplicate_gate` appears in stage outcomes only when eligible pairs existed;
- at most one `duplicate_gate` request was made;
- any repeated event appears as one paragraph carrying multiple source links;
- `deck_selected` equals `deck_target` minus `duplicate_gate_clusters_removed`;
- total reported OpenAI cost remains at or below `$1.00`;
- no category falls below its `[selection_limits]` floor after merging — check
  `underfilled_reason_by_category` against `selected_stories_by_category`. If one
  does, record it; the plan does not backfill, and this is the run that shows
  whether that matters.

If no eligible pairs occur naturally, the fixture tests from Steps 3–6 stand as
the evidence. Do not force a live email or hand-edit history to manufacture a
duplicate.

Record the affirmation rate — affirmed over adjudicated — in the commit message
as the tuning baseline. Near 100% means the predicate is only forwarding obvious
pairs and the model is adding little. Near 0% means it is forwarding noise.

**Commit:** `test(dedup): calibrate duplicate-gate threshold and verify no-send run`

## Implementation record

- The gate uses `gpt-5.6-terra` at medium reasoning and preserves every story on
  API, budget, incomplete-response, malformed-output, or validation failure.
- The selected destination keeps its stable key. Cross-category merges remain
  in the stronger story's category, can reduce the deck or a category below its
  configured floor, and do not trigger backfill.
- Per the implementation grilling, source-diverse evidence ordering and the
  five-source credit allowance apply only to merged stories. Unmerged stories
  retain their existing evidence order and three-source cap. In native email,
  every credited source in a merged story receives its own hyperlink.
- Empty non-Finance sections are omitted. Finance remains present when it has
  quote lead lines even if it has no qualifying story paragraph.
- Historical calibration exposed generic sentence starters being treated as
  entities. The gate-local filter was tightened without changing first-pass
  clustering. The threshold remains `0.20`.
- The 2026-07-28 no-send run offered 3 eligible pairs in 3 components, returned
  and merged 1 set, rejected 0 sets, removed 1 cluster, and made exactly 1 gate
  request. The observed affirmation rate was 33.3%.
- The same run selected 21 stories before the gate and 20 after it. No category
  fell below its configured floor. Total OpenAI cost was `$0.220062`, below the
  `$1.00` cap, and no SMTP delivery occurred.
- Email dry-run reporting was corrected so `--show-diagnostics` is honored
  instead of returning immediately after the newsletter preview.
- Verification at implementation completion: `370 passed`; `git diff --check`
  also passed.

## Rollback

- To disable without a code rollback, set `enabled = false` under
  `[duplicate_gate]` in `config/sources.toml`. `--no-openai` disables it too,
  via `capabilities.classify`, and `--classify-only` skips it via
  `capabilities.draft`.
- To revert the implementation, revert commits in reverse order: calibration,
  merged drafting, pipeline wiring, gate module, candidate generation, OpenAI client,
  configuration. The `openai_client.py` change in Step 2 is the one place where
  reverting touches other stages — the incomplete-response guard is shared. Run
  the full suite after that revert specifically, not just the dedup tests.
- No database schema, delivery state, email edition, or story-history migration
  is part of this plan.
- If the gate merges two genuinely distinct events, set `enabled = false`, keep
  the offending pair as a `tests/test_cluster.py` fixture asserting it is not
  even eligible, and tighten the deterministic predicate before re-enabling. Do
  not respond by editing the system prompt alone; the predicate is the testable
  part.
