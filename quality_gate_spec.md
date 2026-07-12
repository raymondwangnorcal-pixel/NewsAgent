# Task: add an information-quality gate to the news pipeline

Repo: `morning-news-agent` (package `news_agent` under `src/news_agent`).

## Goal

Add a filter that runs **after articles are fetched and parsed, before clustering and scoring**. It rejects legitimate-looking but editorially empty teaser headlines (e.g. "Market Ready To Go?", "What To Know", "Buy Points", "How To", "Watch", "Top Picks") whose RSS description carries no actual reportable event. This is a distinct concern from spam/junk filtering — these are real articles from real feeds, they just don't say anything.

Do not try to rewrite a vague title into news. If the RSS feed doesn't supply an actual fact, drop the article and let a stronger source (if any exists in the same fetch batch) carry the story instead. OpenAI polish mode may improve wording later in the pipeline, but it must never invent the missing event — this constraint should already hold given the existing `polish_system_prompt()` instruction not to add new facts, but call it out explicitly in review.

## Where it plugs in

New module: `src/news_agent/quality_gate.py`.

Wire it into `src/news_agent/pipeline.py`, inside `collect_pipeline_context()`, immediately after fetching and before clustering:

```python
articles = await fetch_all_feeds(config.feeds, config.lookback_hours, config.max_articles)
articles, gate_rejections = filter_low_quality_articles(articles)
clusters = score_clusters(cluster_articles(articles), config, watchlist_entries=watchlist_entries)
```

Filtering at the **article** level (not cluster level) matters: if one source runs a teaser ("Buy Points: 3 Stocks Ready To Go") but another source covers the same underlying event with real facts, dropping only the teaser article still lets the cluster form correctly from the good article. Filtering after clustering would risk losing the whole story if the teaser article happened to seed the cluster.

## Data available to the gate

Each `Article` (see `src/news_agent/models.py`) has `title`, `summary` (the cleaned RSS description — HTML already stripped by `fetch.py`'s `_clean_text`), `source`, `url`, `published_at`. The gate should operate on `title` and `summary` only.

## Algorithm

Implement `quality_gate_reason(article: Article) -> str | None` — returns `None` if the article passes, or a short machine-readable rejection reason string if it should be dropped (for logging). Then `filter_low_quality_articles(articles: list[Article]) -> tuple[list[Article], list[tuple[Article, str]]]` — returns the surviving articles and a list of `(article, reason)` for everything rejected.

Checks, in order:

1. **Minimum non-duplicative summary length.**
   - Normalize `summary` (collapse whitespace).
   - Reject (`"summary_too_short"`) if the cleaned summary is under 80 characters.
   - Reject (`"summary_duplicates_title"`) if the summary is just the title repeated/echoed (e.g. exact match, or token-set Jaccard overlap with the title above ~0.85 using the existing `tokenize()` helper in `cluster.py`). A description that just restates the headline isn't new information.

2. **Teaser/question phrasing penalty on the title.**
   - Build a case-insensitive pattern/keyword list for teaser constructions: titles ending in `?` combined with vagueness, and phrases like "what to know", "how to", "top picks", "buy points", "stocks to watch", "things to know", "need to know", "here's what", "everything you need to know", "ready to go", "explained", "by the numbers", "watchlist". Keep this list in a `TEASER_PATTERNS` tuple of compiled regexes at module level so it's easy to extend.
   - This alone doesn't reject the article — it's a signal combined with check 3.

3. **Require at least one concrete fact in `title + " " + summary`.**
   - A concrete fact is any of: a percentage (`r"\d+(\.\d+)?\s?%"`), a dollar amount (`r"\$\s?\d"`), a reported-action verb in past tense (build a `REPORTED_ACTION_VERBS` set: announced, launched, reported, posted, filed, agreed, signed, approved, rejected, sentenced, raised, cut, fired, resigned, appointed, acquired, sued, fined, banned, recalled, evacuated, closed, opened, won, lost, died, killed, resigns, steps down, and similar), or a named-event noun (reuse `EVENT_TERMS` from `src/news_agent/cluster.py` — acquisition, earnings, merger, ipo, lawsuit, tariff, sanctions, layoffs, recall, etc.).
   - If none of these appear anywhere in title+summary, reject (`"no_concrete_event"`).

4. **Stock-picking / listicle commentary needs an explicit catalyst.**
   - If the title matches generic stock-tip phrasing (top picks, buy points, stocks to watch, "N stocks to..., best stocks") reject (`"stock_tip_no_catalyst"`) *unless* check 3 already found a concrete fact tied to a market catalyst (percentage/price plus an earnings/guidance/deal/rate-type event term in the same text). Good example that should pass: "Nvidia rose 4% after earnings guidance." Bad example that should be rejected: "3 Stocks Ready To Go — Buy Points To Watch" with a summary that just restates the same list framing with no numbers or named event.

5. If none of the above trip, the article passes (`None`).

Keep the gate conservative: when genuinely unsure, prefer to keep the article rather than drop real news. The goal is killing obvious teaser/listicle chaff, not tightening the whole pipeline's recall.

## Logging / auditability

Follow the existing pattern in `src/news_agent/skipped_log.py` (which already writes a silent daily audit file for skipped stories). Add a similar silent write for quality-gate rejections to `data/quality_gate_rejections_YYYY-MM-DD.json`, one entry per rejected article: `title`, `source`, `url`, `reason`. Extend the CLI's existing `--show-skipped` output (in `src/news_agent/cli.py`) to also print a short "Quality gate rejected N articles" line with a few examples, so this is visible in `--dry-run --show-skipped` without adding a new flag.

## Config

No new required config. Optionally add an escape hatch consistent with the existing env-var pattern in `src/news_agent/config.py` (e.g. `BRIEFING_DISABLE_QUALITY_GATE=true` to no-op the filter for debugging), but this is a nice-to-have, not a requirement.

## Tests

Add `tests/test_quality_gate.py` covering at minimum:

- A teaser/question title ("Market Ready To Go?") with a short or title-duplicating summary is rejected with reason `no_concrete_event` or `summary_too_short`.
- "Nvidia rose 4% after earnings guidance" (title or summary containing both a percentage and an event term) passes.
- A generic "Top Stocks To Watch This Week" with a listicle summary and no numbers/event terms is rejected with reason `stock_tip_no_catalyst` or `no_concrete_event`.
- A real policy/legal story with a named concrete development but no percentages or dollar signs still passes (concrete-fact check isn't finance-only).
- `filter_low_quality_articles` preserves article order and only removes the flagged ones.

## Acceptance criteria

- `filter_low_quality_articles` is called from `collect_pipeline_context` before `cluster_articles`.
- Existing test suite (`pytest -q` from repo root, needs Python 3.11+ for `tomllib`) still passes.
- New `tests/test_quality_gate.py` passes.
- `news-briefing --dry-run --no-openai --show-skipped` shows a quality-gate rejection count alongside the existing skipped-story table.
