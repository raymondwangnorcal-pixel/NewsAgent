# ADR-0003: Batched LLM fallback for ambiguous content-quality verdicts

## Status

Accepted

## Context

Regex heuristics have a blind spot: sarcasm, unusual phrasing, or a single weak signal (one
teaser-pattern match, one borderline-length summary) that regex alone can't confidently classify
as junk or legitimate. The codebase already calls OpenAI synchronously and blocking from async
code (`summarize.py`'s `_generate_structured_briefings`, called from `build_briefing_result`
without `await`), using `client.responses.create(...)` with a strict `json_schema` response
format. No existing code in this repo mocks the OpenAI client in tests; `generate_briefings_with_openai`
and its wrappers are exercised in tests only by monkeypatching the wrapper function itself, never
the underlying API call.

## Decision

- Articles get bucketed by regex signal count: 0 triggered heuristics = `clear_good` (penalty 0),
  1 triggered heuristic = `ambiguous`, 2+ = `clear_bad` (penalty applied directly, no LLM call —
  confident enough without one).
- For the `ambiguous` bucket, when `openai_mode != "off"`, make **batched** structured-output
  OpenAI calls (not one call per article), chunked at 40 articles per call with per-article
  title+summary truncated to a fixed length before prompt construction — this bounds latency and
  cost per call regardless of bucket size, and follows the existing summarize.py call pattern
  (`client.responses.create` with `json_schema`, strict mode) rather than inventing a new call
  shape. A single unbounded call was considered and rejected: production rejection logs
  (`data/quality_gate_rejections_2026-07-12.json`, `-14.json`) show ~150-165 articles/day
  currently hard-rejected under the *old*, wider hard-reject rule — under the narrowed rule most
  of those move into the ambiguous bucket instead, so an uncapped call is a realistic, not
  hypothetical, risk on exactly the days the feature matters most.
- The call is wrapped in an explicit `try/except` at the call site (no existing precedent for this
  in `summarize.py` — that function has no error handling on the API call itself, so this is new).
  On any exception (network, API, malformed response), degrade to the regex-only ambiguous-tier
  penalty and continue — never raise out of the quality gate.
- Treat the LLM's article-text input as untrusted data: request a constrained enum verdict
  (`"good"` / `"junk"` per article via the JSON schema), never free text, and never feed the
  response back into another prompt. This is the one new trust boundary in this feature (RSS
  content flowing into an LLM call for the first time in this codebase's filtering path).
- Runs when `openai_mode` is `"full"` or `"polish"` (matching the existing flag), skipped when
  `"off"` — no new independent flag, per locked direction.

## Consequences

- First OpenAI-client-mocking test in this repo: needs a `FakeOpenAI`-class-swap fixture
  (monkeypatch `news_agent.quality_gate.OpenAI`), following the class-swap pattern already used
  for `FakeTelegramSender` in `tests/test_notifications.py`.
- Cost/latency is bounded to one call per run regardless of ambiguous-article volume.
- A production LLM outage degrades gracefully to the regex verdict rather than breaking the
  pipeline.
