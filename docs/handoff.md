# NewsAgent Handoff

Last updated: 2026-07-31T15:35:31-04:00

## Current Goal

Prepare and then implement `docs/plans/watchlist-retrieval-reliability.md`: a reliable Gmail Watchlist that retrieves each distinct ticker once per day, reports only verified material events with correct entity relationships and source attribution, and can later fan results out to roughly 100 users. No implementation of this plan has begun.

## Accomplished This Session

The Watchlist plan was decision-completed and committed as `f740609 docs(watchlist): finalize retrieval reliability plan`; `docs/decisions.md` now contains DEC-0001 through DEC-0039, all with `Implementation: pending`. Existing email deduplication and resend work was checkpointed as `9ab29f9 feat(email): checkpoint deduplication and resend hardening`. A linked implementation branch, `feat/watchlist-retrieval`, was created from that checkpoint; the active repository root remains on `chore/pre-watchlist-checkpoint`. The clean implementation worktree passed `370` tests. A final preimplementation review found that worktree isolation and recipient ownership are resolved, but identified the remaining prerequisites and plan corrections below.

## Outstanding Tasks

1. Correct the filing-regime assumptions in `docs/plans/watchlist-retrieval-reliability.md` before using them as implementation rules. ETHB, CIK `0002099103`, already files Forms 8-K and 10-Q and must have required EDGAR coverage. Shopify is legally a foreign private issuer but currently files on U.S. domestic forms, including Form 8-K; select processing rules from observed forms instead of a binary domestic/foreign label.
2. Run blocking Spike 2 from plan §11. Produce `config/entity_map.json`, the relationship-ambiguity queue, and per-ticker notes covering CIK, supported filing forms, annual form, subsidiary evidence, omission allowance, relationship evidence, verification date, and expiry.
3. Prepare the `feat/watchlist-retrieval` worktree runtime without copying secrets into Git. It currently lacks `.env` and `.venv`; the source environment has `EMAIL_TO` but lacks required `SEC_CONTACT_EMAIL`. Recommended setup: a dedicated virtual environment, an ignored local `.env` link or equivalent secure environment loading, and the dedicated NewsAgent address in `SEC_CONTACT_EMAIL`.
4. Add explicit test-edition behavior to the plan and implementation. Recommended contract: `--email-resend` sends the stored edition unchanged; `--email-rebuild-today` refreshes/reprocesses Watchlist sources with current code, bypasses Watchlist sent suppression, is marked test-only, and does not affect production sent history or Gate A metrics.
5. Add a preflight Gate A state. Recommended contract: start at `DISABLED`; enter `MEASURING` only through an explicit confirmed activation command after Spike 2, tests, and a successful full dry run. Existing `MEASURING`, `PASS`, and `FAIL` behavior remains unchanged afterward.
6. Specify and implement the independent non-filing benchmark workflow required by DEC-0016. The register needs at least 20 material events found independently of NewsAgent retrieval; define its approved-source acquisition cadence and import/review path so Gate A recall is measurable.
7. Specify transient retrieval and concurrency behavior: bounded retries/backoff, failed fetches not becoming permanent successful daily-cache entries, and a run lock or atomic source-key claim preventing scheduled and manual builds from processing the same cache entry concurrently.
8. Implement V1.1–V1.14, migration/rollback support, diagnostics, adjudication CLI, release gate, tests, and dry run from the plan. Update each implemented ledger entry in `docs/decisions.md` with its commit hash through the `decisiontracker` lifecycle.
9. After merging into the scheduler’s active code path, validate a no-send health check and a Gmail test edition before enabling Gate A. Do not alter Telegram behavior.

## Recommended Next Task

Amend the plan’s filing-regime table and rules for ETHB and Shopify, then execute Spike 2. The resulting verified entity map should drive EDGAR implementation rather than the plan’s static assumptions.

## Git / Remote State

Active repository root: `/Users/raymondwang/PersonalProjects/NewsAgent`.

Active branch: `chore/pre-watchlist-checkpoint` at `9ab29f973c054a25266167953651c262e1507695`. It has no configured upstream. Against the locally known `origin/main`, `git rev-list --left-right --count origin/main...HEAD` reports `0 5`; the five locally known commits are:

- `9ab29f9 feat(email): checkpoint deduplication and resend hardening`
- `f740609 docs(watchlist): finalize retrieval reliability plan`
- `a8c0d39 docs: update handoff (2026-07-31)`
- `d4237ae docs: correct handoff commit sha (2026-07-30)`
- `c8ee061 docs: update handoff (2026-07-30)`

Remote freshness: not verified because the active branch has no upstream and no network fetch was performed during this handoff audit. Do not infer that the five commits are pushed.

Working tree before this handoff: five modified tracked `data/` files and fourteen untracked generated `data/` paths; none were staged. They are runtime artifacts and were deliberately left uncommitted. No merge, rebase, cherry-pick, or detached-HEAD state was detected. Branch `feat/watchlist-retrieval` also points to checkpoint `9ab29f9`; a separate linked worktree was created for implementation earlier in this session.

Handoff commit: made for `docs/handoff.md` only; see `git log -- docs/handoff.md`. No push was performed.

## Validation

- Clean implementation worktree: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src pytest -q -p no:cacheprovider` completed with `370 passed in 0.47s`; test-generated `data/email_state.lock` was restored afterward and the worktree was clean.
- Active worktree audit: `git status --short --branch` found five modified tracked files and fourteen untracked paths, all under `data/`; the index was empty.
- Environment presence check, without printing values: the implementation worktree lacks `.env` and `.venv`; the source `.env` contains `EMAIL_TO` and lacks `SEC_CONTACT_EMAIL`; `.env` is ignored by Git.
- Official SEC evidence checked 2026-07-31: ETHB has a Form 8-K at `https://www.sec.gov/Archives/edgar/data/2099103/000143774926012415/0001437749-26-012415-index.htm` and a Form 10-Q at `https://www.sec.gov/Archives/edgar/data/2099103/000143774926015530/0001437749-26-015530-index.html`. Shopify’s 2026 Form 8-K at `https://www.sec.gov/Archives/edgar/data/1594805/000159480526000022/shop-20260508.htm` states that it is a foreign private issuer currently filing periodic and current reports on U.S. domestic issuer forms.
- Current SEC guidance still states a maximum of 10 requests per second; implementation should nevertheless use a lower conservative limiter and an identifying `User-Agent` sourced from `SEC_CONTACT_EMAIL`.
- No Watchlist implementation tests or live mailer dry run were run because implementation has not started and the required environment/Spike 2 outputs are absent.

## Risks / Decisions

- DEC-0001 through DEC-0039 are settled but unimplemented. The old handoff’s open Q3–Q5 and 70% recall figure are stale. Current Gate A non-filing recall is at least 80% with a minimum of 20 independently identified material non-filing events.
- The existing plan says it is ready for implementation, but its static filing-regime evidence is incomplete. In particular, describing ETHB as having thin trust-only coverage omits current 8-K/10-Q filings. Spike 2 must be authoritative.
- The rebuild/resend and Gate A activation contracts above are recommended but not yet recorded as durable decisions. They should be settled before state and delivery-guard code is written.
- Ground truth remains partly manual: Gate A also requires at least 20 adjudicated definitive relationship claims and 20 reviewed rendered stories, and the live window extends beyond 30 days until all denominators exist.
- A fully measurable Gate A failure is intentionally configured to suppress the newsletter, send one final administrative failure email, and halt all scheduled NewsAgent work until a confirmed no-send recovery succeeds. Both current recipient inboxes are controlled by the repository owner; do not record their addresses.
- Standing Yahoo exception: DEC-0004 deliberately permits fetching Yahoo `/m/` paths despite `finance.yahoo.com/robots.txt`. Prior measured yield was 12 restricted links among 162 candidates; four of five sampled restricted pages yielded no extractable text and one yielded 451 characters. This is personal-V1-only and must be guarded by explicit configuration and removed or reconsidered before any second user, paid use, rate-limit/block signal, or broader deployment.
- Preserve the shared `$1.00` OpenAI cap, the guaranteed `$0.25` Watchlist reserve, the ten-ticker limit, Gmail-only temporary delivery mode, quote-only quiet rows, explicit retrieval-failure wording, no false ticker associations, and unchanged Telegram behavior.
- `data/email_state.db` is ignored and absent from the clean implementation worktree. Develop migrations against fixtures and rehearse rollout against a disposable copy of the live v2 database; rollback requires restoring the automatic pre-migration backup, not merely reverting code.
- Never print or document API keys, Gmail credentials, recipient addresses, or `SEC_CONTACT_EMAIL`.

## Archive Decision

Safe to archive: No

Reason: Watchlist implementation has not begun; Spike 2, plan corrections, environment setup, remaining state/testing contracts, implementation, and required validation are outstanding. The active working tree also contains uncommitted generated data.

Next action: correct the plan’s SEC regime assumptions and run Spike 2 from `docs/plans/watchlist-retrieval-reliability.md` §11.
