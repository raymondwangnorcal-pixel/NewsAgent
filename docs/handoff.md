# NewsAgent Handoff

Last updated: 2026-07-31T05:20:00Z

## STANDING REMINDER — robots.txt exception is ACTIVE (opened 2026-07-30, briefly closed, reinstated 2026-07-31)

**The watchlist fetcher deliberately ignores `finance.yahoo.com/robots.txt`.** Yahoo disallows `/m/` paths for `User-agent: *`; the design fetches them anyway. Recorded as **DEC-0004** in `docs/decisions.md` and as decision D19 in `docs/plans/watchlist-retrieval-reliability.md`.

History: opened 2026-07-30 as decision D3, withdrawn 2026-07-31 when the plan moved to primary sources, then **reinstated the same day at the repository owner's explicit instruction**, reaffirmed three times including after being shown the yield measurements below.

**Measured yield, so the cost/benefit stays visible:** across two samples (2026-07-30 and 2026-07-31), 12 of 162 candidate links fall under the restricted prefix, and only for AAPL (6), NVO (3), META (3); BN, COST, CURI, NET, SHOP and ETHB had zero. Of 5 restricted pages sampled, **4 produced zero extractable text** (`extractor_returned_thin`) and the fifth produced 451 characters against a 300-character minimum. Net benefit is roughly two to three usable articles per day, confined to the three tickers the existing 18 briefing feeds already cover best.

This is a **personal-use exception**. It must be revisited before the system serves any user other than its author — the stated goal of ~100 users is precisely that trigger. Carry this section forward verbatim into every future handoff until the exception is removed. Do not quietly drop it, and do not re-argue it with the owner, who has decided.

Reconsider on: any move toward multiple users or paying customers; any sign of rate-limiting or blocking from Yahoo; or if the measured yield drops further.

## Current Goal

Deliver a daily Gmail watchlist section that, for each of nine tickers, reports a material development with correct source attribution and a correct statement of how it relates to the issuer — or says plainly that there is nothing verified. The approved design is in `docs/plans/watchlist-retrieval-reliability.md` (504 lines). **No implementation code has been written.**

## Accomplished This Session

Established by live probing that the two paid providers are unusable: Tiingo News returns `HTTP 403` `{"detail":"You do not have permission to access the News API"}`, and EODHD's free tier caps at `dailyRateLimit: 20` with news costing 5 requests per call — 4 tickers/day against 9 — while sharing that allowance with the EODHD quote fallback at `src/news_agent/mailer/quotes.py:136`. Rewrote the plan three times: from paid-provider, to Yahoo-RSS-plus-extraction, to a primary-source architecture with entity-resolved relevance. Ran an external review (`codex exec -m gpt-5.6-sol`, read-only, 137,193 tokens) that returned 10 blocking and 4 further-review findings; verified four of its claims directly and revised the plan against all 14. Proved Option B viable with a live `enrich_article()` test — 21 articles, 7 of 7 tickers yielded usable text. Created `docs/decisions.md` and recorded DEC-0001, DEC-0002 and DEC-0004.

## The A / B / C decisions and what they include

All three are **in V1** (plan decision D20). They are one pipeline with three entry points; every candidate from every tier passes the same §6 classifier and §6.5 materiality test, with no bypass for any source.

**Option A — tier 5a, cross-reference existing feeds.** The general briefing already retrieves and extracts the 18 feeds in `config/sources.toml` every run. Hand that already-materialised article set to the entity classifier before it is discarded. **Zero marginal network requests.** CNBC, MarketWatch, Axios, BBC and NPR are present as direct feeds *and* already in `allowed_domains`, so extraction works today. The five `news.google.com` feeds yield redirect URLs and remain unusable. Reuters and AP are in `allowed_domains` but have no direct feed configured. Scope guard: this reads the briefing's output and must not alter briefing selection, ordering, or Telegram behaviour. Coverage skews to large caps — these are front-page business feeds, not ticker feeds.

**Option B — tier 5b, Yahoo ticker feed.** One request per ticker per day to `feeds.finance.yahoo.com/rss/2.0/headline?s=<TICKER>&region=US&lang=en-US`. Nine requests total. Apply the 48-hour age filter, drop D2-excluded hosts before fetching, treat `/video/` as headline-only, and per D19 **do not skip `/m/` paths**. Expect `too_thin` on most `/m/` pages; that is an ordinary non-material outcome, not a failure. This is the only free per-ticker discovery found, and it carries the small-cap coverage.

**Option C — D21, price-move flag.** Absolute daily move computed from the new `quote_history` table. **Not a source and not a retrieval trigger in V1.** It stamps the ticker-day diagnostic, marks suspicious quiet rows, and selects days for Mode 2 adjudication.

Rendering: two labelled blocks per ticker, *Disclosed* and *Reported*, never interleaved. `No verified news today.` renders only when both are empty.

## Outstanding Tasks

1. **Answer Q4 — which 8-K item numbers count as material.** Item 8.01 "Other Events" is the live question: including it floods the email with routine filings; excluding it misses real news companies file there. Plan default is exclude initially, log what it would have surfaced, review after two weeks. This decides email volume and blocks §6.5.
2. **Answer Q3** (relationship citation in the email or only in diagnostics; default: in the email as a short parenthetical) and **Q5** (`PARTIAL` wording; default: `No verified news today (partial sources).`). Both low-stakes.
3. **Run Spike 2** — per-ticker entity-map bootstrap: applicable annual form (10-K / 20-F / 40-F / none), whether a subsidiary exhibit exists, its format, whether the omission allowance was exercised. Blocks V1.
4. **Implement V1** per plan §9.1, steps V1.1–V1.11.
5. **Build the adjudication tool** (plan §9.4) before the 30-day window starts, or Gate A cannot be computed retrospectively.
6. **Decide whether to commit the pre-existing uncommitted work** — 26 modified tracked files plus untracked `src/news_agent/duplicate_gate.py`, `tests/test_duplicate_gate.py`, `tests/test_openai_client.py`. Unrelated to this plan and untouched this session.

## Recommended Next Task

Answer Q4, then run Spike 2. Q4 blocks the materiality allowlist and Spike 2 blocks the entity map; both precede any code in V1.1.

## Git / Remote State

Branch `main`, tracking `origin/main`. Remote freshness verified — `git fetch --quiet origin` succeeded 2026-07-31. `git rev-list --left-right --count origin/main...HEAD` reports `0 2`: **2 unpushed local commits**, `c8ee061 docs: update handoff (2026-07-30)` and `d4237ae docs: correct handoff commit sha (2026-07-30)`. Nothing to pull. No rebase, merge, or cherry-pick in progress; HEAD is attached.

Working tree dirty and intentionally so: 26 modified tracked files and 20 untracked paths, including generated `data/` output. Preserve all of it.

**Concurrency warning.** Another agent session is writing to this repository. `DEC-0003 — Every email story has a short bold headline` appeared in `docs/decisions.md` between two of this session's writes, attributed to a Codex task on 2026-07-31. This session's record collided on that ID and was renumbered to DEC-0004; the other record was left untouched. Re-read `docs/decisions.md` before appending, and expect concurrent edits under `docs/`.

Handoff commit: made (this file only). See `git log -- docs/handoff.md`.

## Validation

- `PYTHONPATH=src pytest -q` → `370 passed in 0.41s` (2026-07-30). **Note: `.venv/bin/python` has no `pytest`; use the interpreter on `PATH`.** Not re-run this session — no source code was modified.
- Provider probes (2026-07-30): Tiingo `HTTP 403`; EODHD `HTTP 200`, 20 records with full text, `subscriptionType: "free"`, `dailyRateLimit: 20`; EODHD ALL-IN-ONE is $99.99/month at 100,000 requests/day, and the $19.99 and $29.99 tiers exclude news.
- Extraction viability (2026-07-31), real `enrich_article()`, 21 articles, `minimum_extracted_chars = 300`: **7 of 7 tickers produced at least one usable article.** COST 3/3 (max 4,560 chars), CURI 3/3 (3,322), NET 3/3 (4,371), BN 2/3, SHOP 2/3, AAPL 1/3, META 1/3. Per host: `finance.yahoo.com` 11 extracted / 5 `too_thin`; `fool.com` 4/4 (excluded by policy); `app.moby.co` `too_thin`. **Caveat: n=21, single day, 3 per ticker — proves extraction works, does not predict a stable daily rate.**
- Kuwait entity case (`https://media.kkr.com/news-details?news_id=bd292000-9cc7-487b-9c6f-de43fd5a9b74`, fetched twice with different prompts): names the consortium verbatim as "Blackstone, Brookfield and KKR", 49% collective, one-third each, US$16.0B, 2026-07-25. **No entity designation.** Both fetches reported no quoted individuals; a claim that Bruce Flatt is quoted could not be confirmed at this URL, though both reads used the same fetch pipeline and the quote may exist in another party's release.
- External review verified against code: `state.py:35-37` refuses a database newer than `SCHEMA_VERSION = 2`; 5 `news.google.com` feeds exist in `config/sources.toml`; `apply_duplicate_gate()` requires `AgentConfig` plus assignments plus budget (`duplicate_gate.py:73-119`) and is **not** a reusable deterministic primitive; `service.py:47-53` records the watchlist story ID as the ticker alone, before SMTP.
- Not run: any dry run of the mailer, and any implementation test — no code exists yet.

## Risks / Decisions

- **Settled, in `docs/decisions.md`:** DEC-0001 V1 is filings-only, issuer IR deferred; DEC-0002 evaluation uses 40 interactively adjudicated items; DEC-0004 the robots exception. DEC-0003 belongs to another session.
- **Plan/ledger consistency was repaired this session.** The plan had listed tier 2 in V1 against DEC-0001; §5, §9.1 V1.3 and §11 now mark tier 2 and Spike 1 as V1.5. Spike 2 alone blocks V1.
- **Coverage will be sparse and that is designed.** Six domestic filers produce on the order of one 8-K every two to three days combined. `No verified news today.` will appear on most rows most mornings.
- **ETHB and CURI have effectively no coverage** — ETHB returned 2 items, newest 2026-03-16; CURI's newest was 2026-07-15. Both render as quiet quote rows by design.
- **BN and NVO are foreign private issuers** filing 40-F/20-F irregularly. BN motivated the entity-map design but is poorly covered in V1, since counterparty discovery (tier 3b) is V1.5. Read Gate A accordingly.
- **`FAMILY_UNRESOLVED` is the default label** wherever a source names a corporate family without designating the entity. It is resolved only by a separate official source, never from editorial and never by inference. A quote from a group officer cannot upgrade a label.
- **Rollback requires a database restore** (D18). Reverting the commit alone leaves a v3 database against v2 code and the mailer refuses to start.
- **Gate A now decides the licensed-provider question at 30 days**, not 60, because editorial entered V1. Adopt tier 6 if the unexplained-move rate exceeds 20% or non-filing recall falls below 70%. **The 70% figure was invented by the agent and has not been endorsed by the owner.**
- Ground truth requires human labour: 40 items, ~1.5–2.5 hours across the window, stratified to include rejected items or over-rejection is unmeasurable.
- Credentials for Tiingo and EODHD are in `.env`. Never print, log, or document their values.

## Archive Decision

Safe to archive: No

Reason: no implementation exists, three plan decisions remain open (Q3, Q4, Q5), Spike 2 has not run, and the repository holds 26 modified and 20 untracked paths that are unrelated to this plan and uncommitted.

Next action: answer Q4, then run Spike 2.
