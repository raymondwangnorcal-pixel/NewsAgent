# Plan: Primary-Source Watchlist Coverage with Entity-Resolved Relevance

**Status:** Decision-complete; Spike 2 and implementation remain. Revised 2026-07-31 against an external review and two Watchlist Grill Me sessions to settle retrieval, relevance, evaluation, halt, recovery, notification, preflight, test-edition, concurrency, module-layout, and filing-regime behavior. The decisions are summarized in §13 and recorded durably in `docs/decisions.md`.
**Supersedes:** the Tiingo-first / EODHD-fallback design. The Yahoo-RSS design was withdrawn and is now **partially reinstated** as a clearly-subordinate tier; see §2.2 and §5.
**Decisions of record:** DEC-0001 through DEC-0048 in `docs/decisions.md`; DEC-0046 is superseded by DEC-0047.

---

## 1. Goal and priorities

A daily email that, for every watchlist ticker, either reports a material development with correct source attribution and a correct statement of how it relates to the issuer, or says plainly that there is nothing verified today.

Priorities, in order. Where they conflict, the earlier wins:

1. **Timely coverage** of material developments relevant to each ticker.
2. **Factual accuracy and clear source attribution.**
3. **No false associations** between a ticker and a merely mentioned company.
4. **A design that can scale to ~100 users** by retrieving each distinct ticker once per day and reusing the result.

Priority 1 is bounded by the daily cadence: a filing accepted at 09:15 ET reaches the reader in the next daily send, up to 24 hours later. This is a daily digest, not an alerting system.

**Priority 4 is a design constraint on V1, not a V1 deliverable.** V1 ships one shared newsletter to the existing `EMAIL_TO` recipients with the existing static nine-ticker list. No subscriber model, no per-subscriber editions, no personalization. See §8.3 and D16.

---

## 2. Approaches withdrawn

### 2.1 Paid ticker-news providers (probed 2026-07-30)

- **Tiingo News** — `HTTP 403`, `{"detail":"You do not have permission to access the News API"}`. Paid add-on not held; owner declined to purchase.
- **EODHD News, free tier** — `HTTP 200` with full article text, but `subscriptionType: "free"`, `dailyRateLimit: 20`, one news call costs 5 requests: **4 tickers/day against 9**. EODHD's pricing page lists the News API as *excluded* from Free, so the endpoint responding is an unguaranteed gap. That allowance is already consumed by the EODHD quote fallback (`src/news_agent/mailer/quotes.py:136`).
- **EODHD paid** — News only in ALL-IN-ONE: **$99.99/month, 100,000 requests/day**. Reference option for §10.

### 2.2 Yahoo RSS as sole primary source — withdrawn; retained as a subordinate tier

Withdrawn **as the primary source**: it cannot establish what a company disclosed, only what was reported about it.

Three of the four original objections are now resolved by work this plan funds anyway:

| Original objection | Status |
|---|---|
| Feeds contain off-topic items (an Nvidia article in the AAPL feed) | **Resolved** by the §6 entity classifier |
| RSS carries no publisher attribution | **Resolved** by the §5 exclusion allowlist plus post-fetch resolution |
| Extraction viability unproven (rested on one crude fetch) | **Resolved** — see §3.3 |
| Required ignoring `robots.txt` | **Not resolved. Accepted deliberately** — D19 |

It therefore returns as tier 5b: subordinate to filings, never the basis of a `DIRECT` claim, and rendered in a visibly separate block (§7).

**The standing robots-exception reminder in `docs/handoff.md` is ACTIVE again** and records the measured yield.

---

## 3. Evidence base

### 3.1 Coverage is structurally uneven

| Ticker | Issuer | Regime | Annual report | Expected coverage |
|---|---|---|---|---|
| AAPL, COST, META, NET, CURI | US domestic filers | 8-K / 10-Q / 10-K | 10-K + Exhibit 21 | Good |
| SHOP | Foreign private issuer currently using U.S. domestic forms | Observed 8-K / 10-Q / 10-K; refresh when new forms appear | Observed 10-K + Exhibit 21 | Good while domestic-form reporting continues |
| NVO | Novo Nordisk (Danish) | Foreign private issuer | 20-F | **Weak** — irregular, lagged; home-market disclosure precedes SEC |
| BN | Brookfield Corporation (Canadian) | Foreign private issuer | 40-F | **Weak** — same |
| ETHB | iShares Staked Ethereum Trust ETF | Trust / fund | Observed 8-K / 10-Q; refresh when new forms appear | Observed 10-Q; annual form must be established by Spike 2 | **Required EDGAR coverage plus bounded underlying-Ethereum events** (DEC-0023, DEC-0044) |

Official SEC evidence checked 2026-07-31 confirms ETHB CIK `0002099103` filed Form 8-K accession `0001437749-26-012415` and Form 10-Q accession `0001437749-26-015530`. Shopify's 2026 Form 8-K accession `0001594805-26-000022` says it remains a foreign private issuer but currently files periodic and current reports on U.S. domestic issuer forms. Filing processing therefore follows observed forms rather than a binary legal-regime switch (DEC-0044).

**Expected steady state.** An active US filer files roughly 10–20 8-Ks a year. Across six domestic filers that is on the order of one filing every two to three days *combined*. Most tickers, most mornings, will have nothing, and D7-R makes that visible on nearly every row.

### 3.2 The Kuwait case

The KKR release at `https://media.kkr.com/news-details?news_id=bd292000-9cc7-487b-9c6f-de43fd5a9b74` (fetched twice with different prompts, 2026-07-31) names the consortium verbatim:

> "Blackstone, Brookfield and KKR will collectively hold a 49% stake in the JV, with each investor holding an equal one-third share of that interest on equal terms."

US$16.0 billion, 2026-07-25. **No entity designation.** Both fetches also reported no quoted individuals; a claim that Bruce Flatt is quoted could not be confirmed at this URL, though both reads used the same fetch pipeline and the quote may exist in another party's release.

A summary asserting "Brookfield Asset Management is part of the consortium" is **not supported by this source**. Primary sources are routinely imprecise about which arm of a group acted; the system must say so rather than resolve it.

### 3.3 Extraction viability — tested 2026-07-31

21 articles, 3 per ticker across 7 tickers, run through the real `enrich_article()` with `article_text` policies for each observed host. Threshold `minimum_extracted_chars = 300`.

| Ticker | Usable | Longest |
|---|---|---|
| COST | 3 of 3 | 4,560 |
| CURI | 3 of 3 | 3,322 |
| NET | 3 of 3 | 4,371 |
| BN | 2 of 3 | 3,305 |
| SHOP | 2 of 3 | 2,390 |
| AAPL | 1 of 3 | 2,334 |
| META | 1 of 3 | 451 |

**7 of 7 tickers produced at least one usable article**, and this holds after removing D2-excluded publishers — the surviving successes are almost entirely `finance.yahoo.com` editorial. Per host: `finance.yahoo.com` 11 extracted / 5 `too_thin`; `fool.com` 4 of 4 (excluded by policy); `app.moby.co` `too_thin`.

Small caps outperformed. CURI, predicted to be starved, returned 2,652 / 3,322 / 2,626 characters.

**Caveat:** n=21, one day, 3 per ticker. Sufficient to prove extraction works; insufficient to predict a stable daily success rate.

### 3.4 Restricted-path yield

Measured twice, on 2026-07-30 and 2026-07-31. Of 162 candidate links across nine tickers, **12** fall under the robots-disallowed `/m/` prefix — and only for AAPL (6), NVO (3), META (3). **BN, COST, CURI, NET, SHOP and ETHB had zero.**

Of 5 restricted pages sampled in §3.3, **4 produced zero extractable text** (`extractor_returned_thin` — the page loaded; the extractor found nothing) and the fifth produced 451 characters.

Net yield of D19: roughly two to three usable articles per day, confined to the three tickers best covered by tier 5a. Recorded so the exception's cost/benefit stays visible.

---

## 4. Decision register

### 4.1 Withdrawn

| # | Decision | Reason |
|---|---|---|
| D2 | Yahoo-centred publisher allowlist with post-fetch syndication filtering | Superseded by the §5 exclusion list plus the §6 classifier |
| D3 | Do not honor `finance.yahoo.com` robots rules | Withdrawn 2026-07-31, then **reinstated the same day as D19** |

### 4.2 Carried forward

D1 (watchlist sources scoped separately from global `extraction_policies`), D4 (48-hour lookback), D9 (off-topic rejection, mechanism now §6), D10 (explicit gate before licensing).

### 4.3 Revised

**D7-R — quiet rows are explicit.** A ticker with no qualifying event renders its quote row plus the literal line `No verified news today.` Distinct from retrieval failure (§7).

**D8-R — cache retrieval separately from ticker aggregation.** Source payloads are cached by `source + discovery key + date` so one result can serve every related ticker; the per-ticker daily aggregation remains keyed by `ticker + date` (DEC-0024).

### 4.4 New

| # | Decision |
|---|---|
| D11 | Six renderable relationship labels after D36. `FAMILY_UNRESOLVED` is the **default** when a source names a family without designating the entity |
| D12 | Relevance and materiality are separate axes; both must clear (§6.5) |
| D13 | Editorial is a **discovery signal**; the fact and link come from the primary document it points to |
| D14 | The entity map is versioned; every entry carries `source` and `as_of` |
| D15 | Regulators polled globally; counterparties discovered, never polled per-ticker |
| D16 | **V1 is one shared newsletter.** No subscriber model. Per-ticker aggregation remains keyed by `ticker + date`, while D8-R adds reusable source/discovery-key caching for later fan-out |
| D17 | **Suppression begins on successful delivery**, not on edition preparation (§8.4) |
| D18 | **Rollback requires a database restore.** Schema migration is not reversible by reverting code (§12) |
| D19 | **`finance.yahoo.com/robots.txt` is deliberately not honored** for `/m/` paths. Owner's decision, reaffirmed three times including after being shown the §3.4 yield. Personal-use exception; must be revisited before serving other users. DEC-0004; standing reminder in `docs/handoff.md` |
| D20 | **Editorial coverage enters V1 as Options A and B**: tier 5a cross-references existing briefing feeds and tier 5b retrieves the Yahoo ticker feed (§5), rendered in a separate block from disclosures (§7; DEC-0009) |
| D21 | **Option C: a ≥3% absolute daily price move flags a ticker-day**, from `quote_history`. Not a source — a flag that marks suspicious quiet rows and selects days for adjudication (§9.4; DEC-0009) |
| D22 | **The V1 domestic-issuer Form 8-K allowlist is Items 1.01–1.03, 2.01–2.06, 3.01, 4.02, 5.01–5.07, and 8.01** (DEC-0008; supersedes DEC-0005) |
| D23 | **Gate A requires at least 80% non-filing recall** against the independently labelled frame (DEC-0010) |
| D24 | **An approved editorial source may support an explicitly attributed summary in the Reported block when no primary document can be located**; it cannot establish a corporate relationship (DEC-0011) |
| D25 | **`MANAGED_CAPITAL` is included in V1** when separate relationship evidence supports the label and the email explains the economic connection without implying a direct issuer transaction (DEC-0012) |
| D26 | **Every Form 6-K is evaluated against the material-event criteria**; qualifying filings render, and headline-plus-link fallback requires official metadata that independently establishes materiality (DEC-0013) |
| D27 | **EDGAR is required for every ticker with supported SEC coverage; Options A and B are optional.** Verified stories survive an EDGAR failure but render with an official-filing retrieval warning, never a clean no-news result (DEC-0014) |
| D28 | **The watchlist keeps its guaranteed $0.25 reserve and may use unused capacity within the shared $1 run cap.** Budget exhaustion never exceeds the cap or renders unevaluated candidates as a clean quiet result (DEC-0015) |
| D29 | **The 80% non-filing recall gate requires at least 20 independently identified material events.** Forty interactive reviews remain the initial target, not a hard ceiling; extend the review count or live window until the denominator is large enough (DEC-0016; supersedes DEC-0002) |
| D30 | **Gate A triggers a targeted licensed-provider recommendation only for demonstrated contextual-coverage gaps after implementation defects are ruled out.** No provider is purchased or activated without user approval (DEC-0017) |
| D31 | **V1 merges only high-confidence duplicate events across sources.** Prefer the primary document, retain useful editorial attribution, and keep uncertain matches separate (DEC-0018) |
| D32 | **Non-self relationship evidence expires at the earlier of the next annual filing or 12 months.** Stale evidence cannot produce `AFFILIATE`, `MANAGED_CAPITAL`, or `UNDERLYING_ASSET`; direct issuer stories remain available (DEC-0019, extended by DEC-0023) |
| D33 | **Each ticker renders at most two full event stories.** Additional qualifying events appear only as brief linked mentions when important enough to retain (DEC-0020) |
| D34 | **Each ticker renders at most two additional `Also:` links** after its full stories; lower-ranked qualifying events remain diagnostic-only (DEC-0021) |
| D35 | **EDGAR identifies NewsAgent with the dedicated NewsAgent Gmail address from `SEC_CONTACT_EMAIL`.** The address is never hardcoded or emitted in ordinary diagnostics (DEC-0022) |
| D36 | **ETHB may render bounded material Ethereum events as `UNDERLYING_ASSET`.** Eligible events are protocol, staking, regulatory, security, or article-explained unusual moves, with a current prospectus citation explaining ETHB's exposure (DEC-0023) |
| D37 | **Tier 5b fetches `ETH-USD` once daily as a shared discovery key for ETHB.** Cache it independently and route its candidates through the same classifier and materiality gate (DEC-0024) |
| D38 | **Raw Watchlist responses and extracted text expire after seven days; non-body metadata expires after one year.** Active editions are protected until delivery reaches a terminal state (DEC-0025) |
| D39 | **Gate A permits at most 5% false rendered relationship claims** across `DIRECT`, `AFFILIATE`, `MANAGED_CAPITAL`, and `UNDERLYING_ASSET` (DEC-0026) |
| D40 | **The relationship-accuracy gate requires at least 20 adjudicated rendered relationship claims.** Extend the review count or live window until that denominator is reached (DEC-0027) |
| D41 | **A quiet ticker-day with an absolute move of at least 3% is a diagnostic review target, not a Gate A failure.** Only a reviewer-confirmed missed material event affects recall or supports a provider recommendation (DEC-0028) |
| D42 | **Build entity-map entries automatically from approved primary evidence and ask the user only about ambiguity.** Unresolved ambiguity fails closed and cannot produce a definitive relationship label (DEC-0029) |
| D43 | **Gate A permits at most 5% irrelevant rendered stories after at least 20 rendered stories have been adjudicated.** Extend the review count or live window until that denominator is reached (DEC-0030) |
| D44 | **No confirmed same-event duplicate may render twice in one Watchlist email.** `UNCERTAIN` pairs stay separate and count as a failure only if adjudication confirms they were the same event (DEC-0031) |
| D45 | **Required-source retrieval may fail on at most 2% of evaluated ticker-days.** Every failure remains explicit in the email and diagnostics; it never appears as a clean no-news outcome (DEC-0032) |
| D46 | **After successful EDGAR retrieval, zero eligible filings may be missed.** Outages count under D45, and the next successful run must catch up every affected eligible filing (DEC-0033) |
| D47 | **A nonempty relationship-ambiguity queue adds a compact count-only admin notice to the email.** Candidate details remain withheld and are reviewed through the local CLI (DEC-0034) |
| D48 | **Once Gate A is fully measurable, any failed threshold stops all scheduled NewsAgent email delivery.** Measurement-period delivery continues until the gate is evaluable (DEC-0035) |
| D49 | **A Gate A failure halts the entire scheduled pipeline until manual restart.** No retrieval, classification, evaluation collection, or delivery continues in the background (DEC-0036) |
| D50 | **Manual recovery runs one full no-send health check before clearing the halt.** Success starts a fresh Gate A window and resumes email on the next scheduled run; failure leaves the latch set (DEC-0037) |
| D51 | **The first fully measurable Gate A failure suppresses the newsletter and sends one final admin email containing failed metrics and the restart command.** All future delivery then halts (DEC-0038) |
| D52 | **While Gate A is measuring, one email per week shows count-only evaluation progress and remaining review minima.** No candidate details appear (DEC-0039) |
| D53 | **Gate A defaults to `DISABLED` and requires an explicit confirmed activation after the entity-map/configuration preflight, tests, and required-source dry run pass** (DEC-0040, DEC-0048) |
| D54 | **Stored resend and current-code rebuild are distinct.** `--email-resend` preserves the stored edition; `--email-rebuild-today` creates an isolated `[TEST]` edition that cannot affect production suppression, delivery history, or Gate A metrics (DEC-0041) |
| D55 | **Build the non-filing recall frame weekly and independently of NewsAgent retrieval.** Import source-backed candidates locally and count only user-confirmed material events (DEC-0042) |
| D56 | **Only one stateful email build may run at a time.** Contenders exit before side effects; transient retrieval gets at most three attempts with backoff, jitter, and `Retry-After`, and failure never becomes a successful cache entry (DEC-0043) |
| D57 | **Choose each ticker's EDGAR processing rules from observed supported forms and refresh them when new forms appear.** Legal regime remains metadata (DEC-0044) |
| D58 | **Watchlist is an internal `src/news_agent/watchlist/` package in the main checkout.** It is not a separate application or worktree (DEC-0045) |
| D59 | **Gate A `DISABLED` does not disable Watchlist delivery.** Normal runs still process Watchlist, state `Watchlist evaluation disabled.`, collect no Gate metrics, and cannot trigger gate enforcement (DEC-0047; supersedes DEC-0046) |

---

## 5. Source hierarchy

| Tier | Source | Role | In V1? |
|---|---|---|---|
| 1 | SEC EDGAR filings | Authoritative, public domain | **Yes** |
| 2 | Issuer IR newsrooms and official releases | Authoritative | **No — deferred to V1.5 by DEC-0001** |
| 3a | Regulators — SEC, FTC, DOJ, EC, FDA, FCC, CMA | Authoritative, finite, pollable globally | No — V1.5 |
| 3b | Counterparty / consortium / transaction-party releases | Authoritative, **discovered not polled** | No — V1.5 |
| 4 | Press wires with stable IDs | Authoritative | No — V1.5 |
| 5a | **Existing briefing feeds, cross-referenced** — the 18 feeds in `config/sources.toml` already fetched daily; CNBC, MarketWatch, Axios, BBC and NPR are already in `allowed_domains` | Attributed context. **Zero marginal fetches** | **Yes** |
| 5b | **Yahoo ticker/discovery-key feed** — `feeds.finance.yahoo.com/rss/2.0/headline?s=<KEY>`, currently 10 requests/day: nine watchlist symbols plus `ETH-USD` | Attributed context; the only free targeted discovery found | **Yes** |
| 6 | Licensed ticker-news provider | Deferred | See §10 |
| ✗ | Ratings mills, promotional stock-tip sites, social posts | Never | — |

Excluded permanently by allowlist, not by attempting to detect machine-generated text: `fool.com`, `247wallst.com`, `marketbeat.com`, `stocktwits.com`, `trefis.com`, and comparable outlets.

**Why 3b is discovered, not polled.** You cannot know a counterparty before announcement; at ~300 distinct tickers the set is unbounded. Tier 5 establishes *that* an event occurred and names the parties; the system then attempts to fetch the named party's own release and cites that when available. The Kuwait deal is only reachable this way. If no primary document can be located, D24 permits a clearly attributed editorial summary instead.

**Tier 5 fallback rule.** Tier 5 never overrides a primary document. Where one exists, the rendered factual claim and event link use it even when tier 5 surfaced the event. Where no primary document can be located, an approved tier-5 source may support a concise summary in the **Reported** block only when every claim is clearly attributed in prose—for example, `Reuters reports ...`—and the story links that editorial article (DEC-0011). Tier 5 may establish that an exact issuer is the reported party or subject, but it may not establish a static corporate relationship such as controlled affiliate, managed capital, or family membership; that evidence remains separate under §6 and DEC-0006.

**One pipeline, three entry points.** Tiers 1, 5a and 5b are not separate systems. Every candidate from every tier passes through the same §6 classifier and the same §6.5 materiality test — there is no bypass for any source. That single choke point is where the false-association protection lives. Tier 5b currently costs ten requests per day—nine watchlist symbols plus `ETH-USD`—and reuses everything downstream; the expensive component is the classifier, and it is built once.

**Ordering within an event.** Tier 1 outranks tier 5. Tier 5a outranks 5b when both carry the same story, because 5a's publishers are already vetted for the general briefing.

---

## 6. Entity resolution

### 6.1 Two judgments, not one

The label is **derived**, never asserted by the map alone. A map entry establishes only a static relationship; an exact legal-name match can still be `MENTION_ONLY`.

**`entity_relationship`** — static, from `config/entity_map.json`:
`self` | `controlled_affiliate` | `managed_capital` | `underlying_asset` | `family_ambiguous` | `unrelated`

**`event_role`** — per item, from the document:
`party` (transaction participant) | `subject` (the item is about them) | `quoted_speaker` | `mentioned` | `absent`

### 6.2 Decision table

| `entity_relationship` | `event_role` | Label |
|---|---|---|
| `self` | `party` or `subject` | `DIRECT` |
| `controlled_affiliate` | `party` or `subject` | `AFFILIATE` |
| `managed_capital` | `party` or `subject` | `MANAGED_CAPITAL` |
| `underlying_asset` | `subject` | `UNDERLYING_ASSET` |
| `family_ambiguous` | `party` or `subject` | `FAMILY_UNRESOLVED` |
| any | `quoted_speaker` only | `MENTION_ONLY` |
| any | `mentioned` or `absent` | `MENTION_ONLY` |
| `unrelated` | any | `MENTION_ONLY` |

`MENTION_ONLY` never renders. `FAMILY_UNRESOLVED` renders with mandatory hedged wording.

**Officer-quote rule.** A quote from an officer of entity X is not evidence X is the transaction party; a group officer speaks for the family. `quoted_speaker` alone can never produce a renderable label.

**Resolution rule.** `FAMILY_UNRESOLVED` is resolved only by a *separate official source* from the issuer or named affiliate — never from editorial, never by inference.

### 6.3 Entity map schema

`config/entity_map.json`, versioned, validated by a JSON Schema with fixtures. `legal_regime` is descriptive metadata; `observed_forms` selects processing behavior and is refreshed when a new form appears (DEC-0044). A valid CIK with supported observed forms sets `required_edgar: true`, including ETHB and Shopify.

```json
{
  "schema_version": 1,
  "tickers": {
    "BN": {
      "legal_issuer": "Brookfield Corporation",
      "legal_regime": "foreign_private_issuer",
      "cik": "0001001085",
      "observed_forms": ["6-K", "40-F"],
      "required_edgar": true,
      "annual_form": "40-F",
      "names": [
        {
          "name": "Brookfield Corporation",
          "relationship": "self",
          "match": {"word_boundary": true, "min_tokens": 2},
          "source": "SEC company_tickers.json",
          "verified_at": "2026-07-31",
          "expires_at": null
        },
        {
          "name": "Brookfield",
          "relationship": "family_ambiguous",
          "match": {"word_boundary": true, "min_tokens": 1},
          "source": "manual: family name shared across separately listed issuers",
          "verified_at": "2026-07-31",
          "expires_at": "2027-07-31",
          "verified_against_annual_accession": "current annual filing accession"
        }
      ],
      "negative_names": ["Brookfield Properties Retail", "Brookfield, Wisconsin"]
    }
  }
}
```

**Alias collision policy.** Word-boundary matching only. Aliases shorter than four characters, or equal to the ticker symbol, require a `requires_context` term present in the same document — `NET` requires "Cloudflare", `Meta` requires a disambiguating term. `negative_names` are checked first and reject the match outright. Every alias-derived match records the matched span.

**Discovery keys are not aliases.** A ticker entry may declare source-specific discovery keys that widen candidate retrieval without asserting relevance. ETHB declares Yahoo key `ETH-USD` under D37. Items from that feed still need an Ethereum entity match, the `UNDERLYING_ASSET` relationship, and the D36 materiality test; membership in the feed alone proves nothing.

**Relationship freshness.** Every non-`self` relationship entry carries `source`, `verified_at`, `expires_at`, and the annual-filing or prospectus accession used during verification when applicable (DEC-0019). It becomes stale at the earlier of `expires_at` or observation of a newer governing filing. A stale entry cannot yield `AFFILIATE`, `MANAGED_CAPITAL`, or `UNDERLYING_ASSET`. It may yield `FAMILY_UNRESOLVED` only when current event or issuer evidence still establishes the shared-family relevance; otherwise the association becomes `MENTION_ONLY` and does not render. A stale underlying-asset entry is suppressed until reverified; it never downgrades to a family label. Staleness never blocks a separately established `DIRECT` story. `self` identity does not use the 12-month expiry, but the CIK/legal-name mapping is refreshed during bootstrap and when SEC mapping changes are detected.

**Classifier output must carry evidence.** Each classification returns the label, the matched name, the character span, the document ID, relationship evidence ID and freshness state, `entity_map.schema_version`, and the classifier version. Ambiguous outcomes return `FAMILY_UNRESOLVED`, never a guess.

**Cost control.** Deterministic alias matching runs first and rejects most candidates. A model call resolves `event_role` only for surviving items, cached by `(document_id, ticker, classifier_version)`. The watchlist has a guaranteed $0.25 reserve and may then consume any capacity the general briefing leaves unused inside the shared $1 per-run cap (DEC-0015). Deterministic filing decisions and valid cached classifications continue without new model spend. Once the total cap is reached, no further model call is made; each unevaluated candidate is persisted with `budget_exhausted`, and only its ticker becomes classification-incomplete.

### 6.4 Bootstrapping the map

Exhibit 21 to the 10-K is **positive evidence only. Absence is never evidence that an entity is unrelated.** Item 601 permits omitting subsidiaries not significant in aggregate; formats vary; some issuers report none.

It also does not cover the watchlist uniformly: BN files 40-F, NVO files 20-F, ETHB is a trust. §11 requires a per-ticker bootstrap spike recording the applicable annual form, the exhibit's presence and format, and the automated evidence result.

**Automatic bootstrap with exception review.** The bootstrap derives entries only from the approved primary evidence in §5 and stores the exact evidence reference. A well-supported entry needs no user action. A conflicting, incomplete, or family-level relationship enters an ambiguity queue for one-at-a-time user review (DEC-0029). Until reviewed, it cannot yield `DIRECT`, `AFFILIATE`, `MANAGED_CAPITAL`, or `UNDERLYING_ASSET`; it may yield `FAMILY_UNRESOLVED` only when current evidence independently establishes that family-level connection. Otherwise it becomes `MENTION_ONLY` and does not render.

When the queue is nonempty, the email adds only `Watchlist review needed: N relationship(s).` as an administrative footer (DEC-0034). It does not identify the ticker, candidate, or proposed relationship. The normal newsletter continues, and the withheld candidate is available only in the authenticated local CLI review flow.

### 6.5 Materiality — V1 policy

Relevance answers *is this connected?* Materiality answers *does this plausibly matter to a holder?* Both must clear.

**Tier 1 — deterministic form/item allowlist.** 8-K item numbers are themselves a materiality taxonomy. A configured allowlist of form types and item numbers decides materiality with no model call and no fabrication risk. **For domestic issuers, the V1 Form 8-K allowlist is Items 1.01–1.03, 2.01–2.06, 3.01, 4.02, 5.01–5.07, and 8.01** (DEC-0008). This includes the explicitly requested Items 5.04–5.06 as well as Item 8.01; diagnostics must measure how much routine filing volume they add. Items outside the allowlist are retrieved, logged, and not rendered. The allowlist lives in config and is version-stamped into diagnostics.

**Foreign issuers — Form 6-K content judgment.** A 6-K has no standardized item-number taxonomy. Each 6-K is therefore extracted and evaluated against the same configured material-event categories (DEC-0013). A qualifying filing renders. If extraction or summary generation fails, headline-plus-link fallback is allowed only when official EDGAR metadata or the filing title independently identifies a qualifying event; the email explicitly says the summary could not be generated. When neither the content nor official metadata establishes materiality, the filing is excluded and its reason is persisted in diagnostics.

**Non-filing items — the existing model judgment.** `summarize_watchlist()` already returns a `material` boolean (`src/news_agent/mailer/watchlist_news.py:23-40`, `162-171`). It is retained for non-filing items. It is not applied to itemized 8-Ks, which use the deterministic allowlist above.

**ETHB underlying-asset boundary.** An Ethereum item may clear materiality for ETHB only when it concerns a major protocol change, staking economics or access, material regulation, a significant security incident, or an unusual ETH market move that the linked article credibly explains (DEC-0023). General cryptocurrency commentary, forecasts, promotional content, routine price recaps, and events about unrelated tokens do not qualify. The event source establishes what happened; a current ETHB prospectus separately establishes the underlying-asset relationship.

Without this section V1 would either render every relevant item, contradicting D12, or retain an unspecified implicit classifier.

---

## 7. Outcome model

Per source, one state: `OK` | `NOT_MODIFIED` | `UNSUPPORTED` | `FAILED`.

**V1 requirement policy.** EDGAR is required for every ticker that Spike 2 identifies as having supported SEC filing coverage. Tier 5a existing-feed cross-referencing and tier 5b Yahoo ticker retrieval are optional (DEC-0014). A genuinely unsupported filing source is recorded as `UNSUPPORTED` and treated as absent, not failed. The V1.5 source registry extends this policy to issuer-IR sources.

Ticker output has two independent axes so usable stories are not discarded merely because another source failed:

| Axis | Values | Rule |
|---|---|---|
| Content | `MATERIAL` or `QUIET` | `MATERIAL` when at least one qualifying item exists; otherwise `QUIET` among successfully processed inputs |
| Retrieval | `COMPLETE`, `PARTIAL`, or `FAILED` | `COMPLETE` when required sources succeed, no optional source fails, and all candidates are evaluated; `PARTIAL` when required sources succeed but an optional source fails or a candidate remains unevaluated because the run budget was reached; `FAILED` when any required source fails |

`NOT_MODIFIED` counts as successful only with a usable cached body inside the configured staleness limit; otherwise it counts as `FAILED` for that source.

Rendering:

| Content + retrieval | Render |
|---|---|
| `MATERIAL + COMPLETE` | Attributed stories, required relationship sentences, and best available links under §5 |
| `MATERIAL + PARTIAL` | The same stories plus reason-specific warning text: `Some optional news sources failed.` and/or `Additional stories could not be evaluated because the $1 run budget was reached.` |
| `MATERIAL + FAILED` | The same stories plus `Official filing retrieval failed.` |
| Relevant item, no summary | Headline, an explicit note that the summary could not be generated, and the best available source link; no generated prose |
| `QUIET + COMPLETE` | Quote row plus the literal `No verified news today.` |
| `QUIET + PARTIAL` | Source failure only: `No verified news today (partial sources).` Budget exhaustion: `No verified news today (classification incomplete: budget limit).` If both apply, include both parenthetical reasons |
| `QUIET + FAILED` | Quote row plus `Official filing retrieval failed; no complete news determination was possible.` Never render "no news" |

**Two blocks per ticker, always labelled.** Disclosures and coverage are never interleaved, so the reader always knows which they are reading:

```
NVO   $xx.xx   -3.4%

  Disclosed
    6-K filed 08:12 ET — interim results
    <primary link>

  Reported
    "Novo cuts outlook as obesity competition bites" — Reuters, 09:41 ET
    <publisher link>
```

A ticker may have one block, both, or neither. `No verified news today.` renders when **both** are empty. If disclosures are empty but coverage is present, the row is not quiet — it shows coverage only.

**Per-ticker volume.** After event-level merging, rank qualifying events by investor impact; use source authority and recency as deterministic tie-breakers. Render only the top one or two as full stories (DEC-0020). At most the next two sufficiently important qualifying events appear as concise `Also:` headline-and-link lines without a second full summary (DEC-0021). Lower-ranked events do not render. Diagnostics retain every qualifying event, its rank, and its disposition (`full_story`, `also_mention`, or `not_rendered`) so brevity cannot be mistaken for a retrieval miss.

**Filings need not be summarized.** Rendering form type, item number, acceptance time, and document link — "8-K accepted 09:15 ET — Item 2.02, Results of Operations" — is information-dense, free, and carries zero fabrication risk.

**Relationship provenance.** Event evidence and relationship evidence are stored as separate structured fields. The event link cites the event document only. The relationship sentence carries its own short citation — "affiliate per BN 40-F Ex-21, 2025" — and never implies the event document proves corporate structure. *(Required by DEC-0006; see §13.)*

**Managed-capital wording.** `MANAGED_CAPITAL` is a V1 renderable label (DEC-0012). It uses explicit language such as `Relevance: Brookfield's asset-management platform` plus the separate relationship citation. It never says or implies that BN itself entered the transaction unless the event evidence independently establishes that fact.

**Underlying-asset wording.** `UNDERLYING_ASSET` is a V1 renderable label for ETHB (DEC-0023). It states the connection directly—for example, `Relevance: ETHB holds ether, so this affects the fund's underlying asset`—and cites the current trust prospectus separately from the event source. It never implies that the trust sponsored, controlled, or participated in the Ethereum event.

**Administrative footer.** A nonempty relationship-ambiguity queue adds the compact count-only notice from DEC-0034 after the Watchlist section. While Gate A is `DISABLED`, every normal edition says `Watchlist evaluation disabled.` even though Watchlist retrieval and rendering continue (DEC-0047). While Gate A is `MEASURING`, every seventh completed evaluation day also adds a compact line with elapsed days and the counts remaining to reach the three §9.4 review minima (DEC-0039). These notices are not stories, do not consume a ticker's story or `Also:` limit, and expose no unverified candidate detail.

---

## 8. State

### 8.1 Extend the existing database

`data/email_state.db` already exists at `SCHEMA_VERSION = 2` with migrations, editions, deliveries, and `quote_cache` (`src/news_agent/mailer/state.py:14-16`, `34-99`). V1 **extends** it to version 3. It does not introduce a second store.

### 8.2 New tables

- `watchlist_source_cache` — key `(source_id, discovery_key, briefing_date)`; retrieved payload, validators, source state, and fetch timestamps. Each distinct key is fetched at most once per day and can serve multiple tickers.
- `watchlist_daily_cache` — key `(ticker, briefing_date)`; per-ticker aggregation referencing the relevant source-cache rows, plus content and retrieval states.
- `watchlist_documents` — `document_id` (accession number, feed GUID, or canonical URL in that precedence), issuer, form/type, acceptance time, first-observed time, URL, content hash of *extracted* content.
- `watchlist_events` — `event_id`, member `document_id`s, linkage basis.
- `watchlist_sent_history` — `(event_id, ticker)`, first-delivered timestamp.
- `watchlist_diagnostics` — per ticker-run, the fields in §9.2.
- `watchlist_gate_windows` — versioned Gate A state, window timestamps, activation/preflight evidence, metric numerators and denominators, failure reasons, halt state, and recovery audit events.
- `watchlist_adjudications` — immutable reviewer verdicts for rendered claims, story relevance, rejected items, large-move reviews, and benchmark events.
- `watchlist_benchmark_events` — independently researched non-filing candidates with source URL, ticker, event date, materiality rationale, import provenance, and adjudication status.
- `quote_history` — `(ticker, trading_date)` close and previous close. **Required**: existing `quote_cache` is keyed on ticker alone and holds only the latest row (`state.py:91-94`), so the large-move review diagnostic cannot be computed from it.

### 8.3 Scope boundary

No `subscribers` table in V1 (D16). Per-ticker aggregation keyed by `ticker + date` is fan-out-correct, while source retrieval keyed by `source + discovery key + date` prevents duplicate fetches across tickers or future subscribers (D8-R). Adding subscribers later is additive and does not invalidate stored rows.

### 8.4 Identity and suppression

Three distinct identifiers:

- `candidate_id` — one retrieved item from one source, before dedup.
- `document_id` — one canonical document. Precedence: accession number, then feed GUID, then canonical URL. Content hash is a fallback only, computed over **extracted content, never the page**, since nav and ad churn produce false positives on every fetch.
- `event_id` — one real-world event, possibly spanning several documents.

**Suppression is keyed on `event_id` and begins on successful delivery** (D17). Edition membership is recorded separately from delivery. Current code records only the ticker as the watchlist story ID, at edition preparation, before SMTP (`src/news_agent/mailer/service.py:47-53`); recording suppression there would hide an event permanently after a failed send.

**Test editions never enter production suppression or evaluation state** (DEC-0041). `--email-resend` sends the stored edition bytes unchanged. `--email-rebuild-today` rebuilds with current code and sources, prefixes the subject with `[TEST]`, bypasses Watchlist sent suppression, and writes only test-scoped edition and delivery records; it never mutates `watchlist_sent_history` or Gate A metrics.

### 8.5 Build serialization and source-cache integrity

A process-level lock covers every stateful email build, scheduled or manual (DEC-0043). A contending process exits before network access, model calls, state mutation, or delivery with the explicit result `another build is already running`. The lock is released automatically on process exit; no stale timestamp lease is required.

Transient HTTP failures receive at most three total attempts with exponential backoff, jitter, and `Retry-After` support. A failed attempt is persisted for diagnostics but never marks `(source_id, discovery_key, briefing_date)` successful. Only a validated response or a `NOT_MODIFIED` response backed by a usable unexpired body can populate a successful daily-cache entry.

### 8.6 Deduplication in V1

V1 links documents at the event level in two stages (DEC-0018):

1. **Deterministic linkage:** identical `document_id`; an 8-K and its own `EX-99` exhibit linked by accession; identical canonical URLs or content hashes. Same issuer, date, and form type alone are **not** enough—an issuer may disclose several distinct events on one day.
2. **High-confidence event linkage:** compare only documents attached to the same ticker inside the lookback window. Merge only when the event type, occurrence date, named parties or subject, and material facts are compatible and the linkage decision is `SAME_EVENT`. Record the matching evidence and classifier/version. `DIFFERENT_EVENT` and `UNCERTAIN` remain separate.

`apply_duplicate_gate()` is *not* reusable directly: it requires `AgentConfig`, category assignments, and a budget (`src/news_agent/duplicate_gate.py:73-119`). Implement a watchlist-specific bounded linkage step with structured `SAME_EVENT | DIFFERENT_EVENT | UNCERTAIN` output. Any model-assisted comparison uses the D28 budget; budget exhaustion leaves the pair `UNCERTAIN` and separate rather than forcing a merge.

One merged event renders as one story. Its primary document supplies the canonical link and factual basis when available; the render may retain one useful secondary attribution such as `Also reported by Reuters`. Without a primary document, source precedence in §5 chooses the canonical editorial source. `event_id` and sent-history suppression apply to the merged event, not its member documents.

### 8.7 Retention

Retention applies only to Watchlist state introduced by this plan (DEC-0025):

- **Seven days:** raw HTTP response bodies, feed payload bodies, and extracted article or filing text. At expiry, null or delete the body fields and record `payload_purged_at`; preserve no duplicate body elsewhere in Watchlist state.
- **One year:** canonical URLs, source IDs and discovery keys, feed GUIDs and SEC accessions, request validators, timestamps, content hashes, classifications and evidence spans, relationship-freshness results, event membership, diagnostics, adjudications, quote history, and sent-delivery history.
- **Operational configuration:** the current entity map and its current relationship citations remain versioned configuration governed by D32, not disposable cache payloads. Historical superseded map metadata follows the one-year rule.

Cleanup runs idempotently after a daily run reaches a terminal delivery state. It never purges data referenced by an edition whose delivery or retry is still pending, even if a clock anomaly crosses a retention boundary. It logs only row counts and retention class—never deleted text. The 48-hour suppression check uses retained metadata, so raw-body deletion cannot cause a resend.

---

## 9. Implementation

### 9.1 V1 steps

**V1.0 — Package boundary.** Implement the feature as `src/news_agent/watchlist/` with explicit domain models and injected retrieval/state collaborators (DEC-0045). `src/news_agent/mailer/` remains responsible for edition and SMTP orchestration; compatibility imports may temporarily preserve existing call sites, but new Watchlist domain logic does not accumulate in `mailer/watchlist_news.py`.

**V1.1 — EDGAR client.** A dedicated client, not the generic `FeedConfig` path. SEC's supported company-history interface is the JSON Submissions API, requiring CIKs zero-padded to ten digits, and returns compact columnar filing data the RSS path would discard. Implement: accession URL construction, acceptance-time capture, amendment and observed-form filtering, a per-CIK successful-retrieval watermark, catch-up enumeration from the last successful watermark after an outage, a conservative global rate limiter below SEC's published ceiling, conditional requests with validator storage, and fixtures for each observed filer class (10-K/8-K, 20-F/6-K, 40-F/6-K, trust 10-Q/8-K). Apply the DEC-0043 three-attempt transient retry policy. Legal regime remains metadata; `observed_forms` from the entity map selects processing and refreshes when a new form appears (DEC-0044). An eligible filing is a configured Watchlist filing form accepted after the prior successful watermark and before the current edition cutoff; processing includes an explicit materiality/render or exclusion disposition, not necessarily a rendered story (DEC-0033).

**V1.2 — User-Agent.** SEC asks for an identifying organization and contact address. The current shared agent is the placeholder `morning-news-agent/0.1 (+https://example.local)` (`src/news_agent/fetch.py:19`). EDGAR uses an application-specific `User-Agent` containing the dedicated NewsAgent Gmail address loaded from the separate required environment variable `SEC_CONTACT_EMAIL` (DEC-0022). Do not derive it implicitly from `EMAIL_FROM`, hardcode it, persist it in diagnostics, or print the constructed header. Startup and dry-run configuration validation report only whether the setting is present and syntactically valid; an EDGAR-enabled run fails clearly before retrieval when it is missing or invalid.

**V1.3 — Tier 2 is NOT in V1** (DEC-0001). Issuer IR retrieval and Spike 1 both move to V1.5. Recorded here so the omission is deliberate rather than forgotten: `fetch_feed_with_status()` parses only RSS/Atom and sends no conditional headers (`src/news_agent/fetch.py:136-197`), so conditional-request support is new work whenever tier 2 lands.

**V1.3a — Tier 5a, cross-reference.** The general briefing already retrieves and extracts its 18 feeds each run. Hand that already-materialised article set to the §6 classifier against the entity map before it is discarded. **No new network requests.** Scope guard: this reads the briefing's output; it must not alter briefing selection, ordering, or Telegram behaviour.

**V1.3b — Tier 5b, ticker/discovery-key feed.** Build the distinct set of configured Yahoo discovery keys, fetch each once per day, and cache by `(source_id, discovery_key, briefing_date)`. The current set is the nine watchlist symbols plus `ETH-USD` for ETHB (DEC-0024), for ten requests. Use `feeds.finance.yahoo.com/rss/2.0/headline?s=<KEY>&region=US&lang=en-US`. Apply the D4 age filter, drop D2-excluded hosts before fetching, and per D19 **do not skip `/m/` paths**. `/video/` remains headline-only on text-quality grounds. Per-domain rate limiting applies. Expect `too_thin` on most `/m/` pages (§3.4) — that is an ordinary non-material outcome, not a failure.

**V1.3c — Price-move flag (D21).** Compute the absolute daily move from `quote_history` and stamp it on the ticker-day diagnostic. Not a source and not a retrieval trigger in V1; it marks suspicious quiet rows and selects days for §9.4 adjudication.

**V1.4 — Entity map** per §6.3, with schema validation and fixtures, automatically bootstrapped per §6.4 and §11. Persist an ambiguity queue and review status; no unreviewed ambiguous entry may emit a definitive relationship label (DEC-0029). Add `--review-watchlist-relationships`, which presents pending entries one at a time with the candidate, proposed relationship, and evidence; the email exposes only the pending count (DEC-0034).

**V1.5 — Classification** per §6.1–6.2, with evidence spans and versioned output.

**V1.6 — Materiality** per §6.5.

**V1.7 — State, serialization, and retention** per §8, migrating `email_state.db` v2 → v3, with an automatic pre-migration backup and a migration test from a v2 fixture. Add the process-level build lock, Gate A state, benchmark/adjudication state, test-edition isolation, source-cache success rules, and idempotent seven-day body and one-year metadata cleanup protected by active-edition references.

**V1.8 — Rendering** per §7. Delete `GOOGLE_NEWS_BASE` and `google_news_feed()` from `src/news_agent/mailer/watchlist_news.py`. **Scope note: this removes Google RSS from *watchlist retrieval only*.** The five Google News feeds in `config/sources.toml` serving the general briefing are **out of scope and must not be touched** — removing them would change Telegram and general-news coverage, contradicting §14.

**V1.9 — Diagnostics** per §9.2. Must exist from the first run or the gate cannot be computed retrospectively.

**V1.10 — Tests.** `MENTION_ONLY` never renders; a family-named item classifies `FAMILY_UNRESOLVED` and renders hedged wording; current `MANAGED_CAPITAL` evidence renders the economic relationship and separate citation without implying a direct issuer transaction; current `UNDERLYING_ASSET` evidence renders an eligible Ethereum event for ETHB with a separate prospectus citation and no claim of trust participation; general crypto commentary, unrelated-token events, unexplained price recaps, and stale prospectus relationships do not render for ETHB; `ETH-USD` is fetched once per day even when multiple tickers map to it, its cache is distinct from ETHB's ticker-feed cache, and its candidates prove no relevance until the shared classifier and materiality gate pass; automatic entity bootstrap accepts well-supported primary evidence without user action, queues conflicting or incomplete evidence for review, and prevents unreviewed ambiguity from emitting a definitive label; the 12-month boundary and observation of a newer governing filing each make non-self evidence stale; stale evidence cannot yield `AFFILIATE`, `MANAGED_CAPITAL`, or `UNDERLYING_ASSET`, downgrades to `FAMILY_UNRESOLVED` only with current family evidence, otherwise suppresses the association, and never blocks a separate `DIRECT` story; an officer quote does not upgrade a label; an alias collision (`NET`, `Meta`) is rejected without its context term; a negative name is rejected; a tier-5-only event renders in Reported with explicit publisher attribution and cannot establish a corporate relationship; a material 6-K renders after content evaluation; an unextractable 6-K uses headline fallback only when official metadata establishes materiality; an indeterminate 6-K is excluded with a diagnostic reason; each content/retrieval combination in §7 renders exactly as specified; verified editorial stories survive an EDGAR failure with the required warning; no failed required source can produce a no-news message; the watchlist may consume unused shared budget after its reserve but total estimated spend never exceeds $1; budget exhaustion records every unevaluated candidate, retains completed stories, and produces the classification-incomplete warning; exact documents and an 8-K/`EX-99` pair merge; high-confidence filing/editorial and editorial/editorial pairs merge into one story with primary-source precedence; same-day distinct events and `UNCERTAIN` pairs remain separate; a confirmed same-event pair rendered twice in one edition fails Gate A; a budget-exhausted linkage comparison remains separate; merged-event suppression begins only after successful delivery; a ticker with five or more qualifying events renders at most two full stories and two linked `Also:` mentions while recording every event's rank and disposition; `SEC_CONTACT_EMAIL` is required and validated for EDGAR, the dedicated header is sent, and neither the address nor constructed header appears in diagnostics or logs; retention keeps bodies through the seven-day boundary and purges them immediately after it, keeps metadata through one year and purges it after, protects active editions, preserves suppression after body deletion, and is idempotent; the relationship-accuracy metric includes `DIRECT`, `AFFILIATE`, `MANAGED_CAPITAL`, and `UNDERLYING_ASSET`, remains unreported below 20 adjudicated claims, passes at exactly 5%, and fails above 5%; the irrelevant-story metric remains unreported below 20 adjudicated rendered stories, passes at exactly 5%, and fails above 5%; a quiet ≥3% move enters the review queue but does not fail Gate A unless adjudication confirms a missed material event, which then enters the recall denominator; migration from a v2 database fixture succeeds; ETF and foreign-issuer tickers behave against thin feeds.

**Gate-metric boundary tests.** The required-source failure ratio passes at exactly 2% and fails above it; every failed ticker-day keeps a visible failure outcome. Unsupported sources are excluded from both numerator and denominator. A successful EDGAR response that omits or leaves any eligible accession unprocessed fails Gate A, while an outage records a retrieval failure and the next successful run processes every accession since the previous successful watermark exactly once. A nonempty ambiguity queue renders only the correct count in the admin footer, withholds every candidate detail, and remains reviewable one item at a time through the local CLI; an empty queue renders no notice.

**Delivery-guard tests.** `DISABLED` permits normal Watchlist and general-briefing delivery, renders `Watchlist evaluation disabled.`, records no Gate A metrics, and cannot enforce a gate-triggered shutdown (DEC-0047). `MEASURING` and `PASS` permit scheduled pipeline execution and SMTP delivery. A metric without its required evidence denominator keeps the gate `MEASURING` rather than manufacturing a pass or fail. The first fully evaluated `FAIL` suppresses the regular newsletter and permits only the DEC-0038 administrative alert workflow; after its terminal delivery outcome, every scheduled invocation exits before network access, model calls, candidate processing, evaluation collection, or SMTP, suppressing both the general briefing and Watchlist (DEC-0035–DEC-0036). The halt latch survives process and machine restarts and cannot clear automatically. Only the manually confirmed recovery command may bypass it for one no-send health check; failed checks leave all state halted, while success records an audit event, resets metrics into a new versioned `MEASURING` window, and permits only the next scheduled run to resume delivery (DEC-0037). Contending builds exit without side effects, all transient retrieval attempts are bounded at three, and a failed fetch never becomes a successful cache hit (DEC-0043).

**V1.11 — Dry run and activation.** `PYTHONPATH=src .venv/bin/python -m news_agent.cli --dry-run --to email --show-diagnostics` exits `0`, sends nothing, every required EDGAR source succeeds, every Disclosed link is a primary-source URL, every Reported link is either the event's primary document or an approved editorial source with explicit attribution, every non-self relationship has separate evidence, and no credential appears. Gate A defaults to `DISABLED`. Add an explicit confirmed activation command that starts a fresh `MEASURING` window only when the entity map and `SEC_CONTACT_EMAIL` validate, all required tests are recorded as passing, the latest full no-send dry run has no required EDGAR, migration, or processing failure, and the implementation version matches (DEC-0040, DEC-0048). Optional-source failures and fail-closed unresolved relationship ambiguities do not block activation.

**V1.11a — Test editions.** Preserve `--email-resend` as an unchanged stored-edition resend. `--email-rebuild-today --send --to email --confirm` builds an isolated `[TEST]` edition with current code and sources, bypasses Watchlist sent suppression, and cannot modify production delivery history, sent-event history, or Gate A metrics (DEC-0041).

**V1.12 — Halt recovery.** Add `--restart-after-gate-failure --confirm`. It is rejected when the gate is not halted or confirmation is absent. When halted, it runs the V1.11 pipeline without SMTP while preserving the old failed window and latch. Only after a fully successful health check does one transaction record the recovery event, clear the latch, and create a fresh versioned `MEASURING` window; the command itself never sends an email (DEC-0037).

**V1.13 — Final failure alert.** On the first transition of a fully measurable gate to `FAIL`, do not send the prepared newsletter. Persist one administrative alert keyed uniquely by the Gate A window, containing only the failing metric names, measured values and thresholds, plus `--restart-after-gate-failure --confirm`; do not include briefing stories. Use a stable Message-ID. Apply the existing bounded email retry policy only to definite pre-accept failures; an ambiguous SMTP outcome is recorded and not retried, preventing a possible duplicate. Once the alert is accepted or reaches a terminal failure outcome, scheduled work is fully halted (DEC-0038).

**V1.14 — Weekly measurement reminder.** On completed evaluation days 7, 14, 21, and every seventh day thereafter while the gate remains `MEASURING`, append a count-only footer with days completed and `max(0, minimum - adjudicated)` for the non-filing-event, definitive-relationship-claim, and rendered-story denominators. Reset the cadence with a fresh Gate A window. Do not include candidate names, headlines, tickers, or links (DEC-0039). Tests cover no reminder on day 6, correct counts on days 7 and 14, no reminder after `PASS` or `FAIL`, and cadence reset after validated recovery.

**Baseline:** `PYTHONPATH=src pytest -q` reports `370 passed`. `.venv/bin/python` has no `pytest`; use the interpreter on `PATH`.

### 9.2 Diagnostics — metric to field

Every §10 metric maps to persisted fields. Anything not listed here cannot be measured.

| Metric | Required fields |
|---|---|
| Retrieval reliability | source ID, discovery key, per-source state, required/optional designation, ticker content state, ticker retrieval state, run ID |
| Filing processing | independently enumerated eligible accession denominator, successful-retrieval watermark, acceptance time, first-observed time, edition cutoff, materiality/render disposition |
| Outage catch-up | failed run ID, last successful watermark, recovery run ID, accessions expected and processed, duplicate-processing count |
| Relationship accuracy | predicted label, relationship evidence, `document_id`, map version, classifier version, adjudicated label, false-claim numerator and total-claim denominator |
| Non-filing recall | independently collected sampling frame — labelling only retrieved items cannot reveal misses |
| Confirmed same-email duplicates | edition ID, ticker, repeated `event_id` or adjudicated same-event pair, member `document_id`s, linkage basis |
| Large-move review diagnostic | `quote_history` joined on trading date to ticker outcome date, review status, and any confirmed missed-event ID |
| Classification completeness | watchlist reserve used, total run budget used, budget-exhausted flag, unevaluated candidate IDs and tickers |
| Relationship freshness | relationship evidence ID, verified-at and expires-at timestamps, annual accession used, observed current accession, freshness state, downgrade or suppression reason |
| Per-ticker selection | event importance rank, source-authority and recency tie-break values, render disposition, omission reason |
| Retention | payload-created and purged timestamps, metadata-created timestamp, active-edition protection flag, cleanup counts by retention class |

### 9.3 V1.5

Tiers 3a, 3b and 4, plus future improvements for resolving currently `UNCERTAIN` event pairs. Tier 5 (D20), `MANAGED_CAPITAL` (D25), `UNDERLYING_ASSET` for ETHB (D36), and high-confidence cross-tier event merging (D31) have moved into V1.

### 9.4 Adjudication tool (DEC-0016; supersedes DEC-0002)

A CLI mode presents items one at a time and records the reviewer's judgment into `watchlist_adjudications`. **Forty adjudications are the initial target, not a hard ceiling.** Review roughly two per day rather than in one sitting so problems surface while there is still time to act. Gate A does not apply the 80% recall threshold until the independent denominator contains at least 20 known material non-filing events (DEC-0016), the 5% relationship-error threshold until at least 20 rendered relationship claims have been adjudicated (DEC-0027), or the 5% irrelevant-story threshold until at least 20 rendered stories have been adjudicated (DEC-0030); extend the review count or live window until every applicable denominator is met.

The weekly email reminder reports only those three remaining counts plus elapsed evaluation days (DEC-0039). It does not replace the interactive CLI or count an item as reviewed.

**Mode 1 — verify what rendered.** Shows ticker, date, absolute price move, headline, source, timestamp, the predicted label, **the matched name and its character span**, and the materiality basis. Asks: is this genuinely about this company; would it matter to a holder; was the relationship correct. ~30–60 seconds each.

**Mode 2 — detect misses.** Shows ticker-days where the move was ≥3% (D21) and the outcome was `QUIET`, and asks whether there was news the reviewer would have wanted, with an optional link. ~3–5 minutes each, because it requires looking up what actually happened. It also presents events from an independently maintained benchmark register. Each benchmark row stores ticker, date, direct source, materiality verdict, and whether NewsAgent found it. The register may incorporate confirmed events found during Mode 2, scheduled ticker review, and documented historical cases, but it may not be built solely by relabelling items NewsAgent already retrieved.

**Independent benchmark workflow.** At least weekly, an agent researches the configured tickers without reading or seeding queries from NewsAgent candidate, document, event, or diagnostic tables (DEC-0042). Candidate sources are issuer releases, regulator releases, and approved editorial reporting. Import through a local CLI using a JSON or JSONL record containing ticker, event date, source URL, headline or stable source identifier, and a concise materiality rationale. The importer rejects duplicates and unsupported tickers, records import provenance, and queues each candidate for one-at-a-time user adjudication. Only `material` verdicts count toward the minimum-20 non-filing denominator; `not_material` and `unclear` remain auditable but do not enter it.

**Stratified sampling, not random.** The initial 40 must include items the system *rejected*, or over-rejection is unmeasurable — and over-rejection is the likelier failure mode with primary sources. Strata may overlap; if the minimum denominator or target coverage is not met, continue sampling.

| Stratum | Minimum or initial target |
|---|---|
| Independently identified material non-filing events used for recall | **Minimum 20** |
| Rendered `DIRECT`, `AFFILIATE`, `MANAGED_CAPITAL`, or `UNDERLYING_ASSET` relationship claims used for accuracy | **Minimum 20** |
| Rendered stories used for irrelevant-story rate | **Minimum 20** |
| Items classified `MENTION_ONLY` and discarded | Target 10 |
| `QUIET` days with a ≥3% move | Target 10 |
| Deliberately hard cases — family ambiguity, fund-versus-parent, name collision, supplier mention | Target 5 |

**`unclear` is a permitted verdict** and is recorded as such. Forcing a binary on a genuinely ambiguous item manufactures precision that does not exist; the rate of `unclear` is itself a finding.

Estimated reviewer effort for the initial 40: **1.5–2.5 hours**, most of it Mode 2. More time is required if any 20-item minimum has not yet been reached.

---

## 10. Release gate

**Metrics are versioned by implementation.** Each measurement records the release it evaluates. **No V1.5 deployment occurs inside a measurement window**; mixing releases makes the aggregate uninterpretable.

**Two gates, not one.**

**The gate collapsed from two windows to one.** The earlier split existed because V1 had no editorial tier, so the contextual metrics would have measured absent features and the licensing decision sat ~60 days out. With D20 putting tiers 5a and 5b into V1, **non-filing recall, false-positive rate, and reviewer-confirmed misses from large-move diagnostics become measurable in the first window** — so Gate A now decides licensing, and Gate B narrows to validating counterparty discovery after V1.5.

**Gate A — V1, at least 30 days live.** Measures what V1 actually builds. The window extends beyond 30 days when necessary to reach the D29 recall denominator, D40 relationship-claim denominator, or D43 rendered-story denominator:

| Metric | Threshold |
|---|---|
| Required-source retrieval reliability (`FAILURE` ticker-days / evaluated ticker-days) | **≤2%** (DEC-0032) |
| Eligible filing processing after successful EDGAR retrieval | **100%; zero system-caused misses** (DEC-0033) |
| Post-outage filing catch-up on the next successful run | **100%**, with each accession processed exactly once (DEC-0033) |
| False-relationship rate across rendered claims labelled `DIRECT`, `AFFILIATE`, `MANAGED_CAPITAL`, or `UNDERLYING_ASSET` | **≤5%** (DEC-0026) |
| Confirmed same-event duplicates within one Watchlist email | **Zero** (DEC-0031); `UNCERTAIN` pairs count only after same-event adjudication |
| Irrelevant-story rate (rendered stories the reviewer judges immaterial) | **≤5%, after at least 20 reviews** (DEC-0030) |
| Non-filing recall against the §9.4 labelled frame | **≥80%** (DEC-0010) |

**Gate A decides whether to recommend a licensed provider, not whether to activate one** (DEC-0017). A retrieval-reliability, filing-freshness, filing-recall, relationship-accuracy, or duplicate failure is an implementation/source-quality problem to fix directly; it does not by itself justify a news subscription. If non-filing recall is below 80%, first rule out such implementation defects, then produce a gap report grouped by ticker, event type, and missing source class. Quiet ≥3% move days are review targets only; they support the gap report only when the reviewer confirms a missed material event (DEC-0028). The report must compare the cheapest provider that covers those specific gaps, its expected recurring cost, and where it would enter the source hierarchy. Purchase and activation require separate user approval.

**Gate B — after V1.5 is frozen, 30 days live.** Narrowed to what V1.5 adds: whether counterparty discovery (tier 3b) catches events like the §3.2 Kuwait consortium that reach the world through a third party, and whether its new source tiers preserve the V1 event-level duplicate rate.

**Replay is a supplement, not a substitute.** EDGAR has a complete archive, so the filings path replays exactly. Issuer newsrooms, wires, and editorial have no historical feed and paywalled archives close the rest. Replay validates tier 1 and **cannot** measure the contextual gap — the question the licensing decision turns on.

**Ground truth requires labour.** Relationship accuracy, story relevance, and non-filing recall are not computable without hand-labelled examples. Start with 40 interactive adjudications, seeded adversarially with the Brookfield family ambiguity, a fund-versus-parent case, a same-name collision, and a supplier mention. Continue until the independent recall frame contains at least 20 confirmed material non-filing events, at least 20 rendered definitive relationship claims have been adjudicated, and at least 20 rendered stories have been reviewed for investor relevance; no metric is reported before its own minimum is met.

**Decision rule.** At Gate A, recommend a tier-6 provider when non-filing recall falls below 80%, but only after the root-cause check above confirms a contextual-coverage gap. A quiet ≥3% move never independently triggers a recommendation; a reviewer-confirmed missed event enters the non-filing recall frame. Gate B evaluates the later V1.5 additions and does not postpone or automatically reopen the Gate A licensing decision.

**Where it enters if later approved.** As tier 6, an additional discovery and context layer, never replacing tiers 1–4. Its items pass the same entity classification with no exemption; where an event has any primary document the render links the primary document; it never outranks a primary source in merging. Provider-only claims follow the same explicit-attribution and separate-relationship-evidence rules as D24.

Quiet days are expected. The system is judged on whether it **misses known material events or invents relevance**, not on whether every ticker has an item daily.

**Delivery enforcement.** Persist Gate A state as `DISABLED`, `MEASURING`, `PASS`, or `FAIL` with the evaluated implementation version, window, metric numerators and denominators, preflight evidence, and reasons. `DISABLED` is the default: normal runs still retrieve and render Watchlist, add `Watchlist evaluation disabled.`, collect no Gate A metrics, and apply no gate-triggered halt (DEC-0040, DEC-0047). Only the confirmed activation command in V1.11 may open a `MEASURING` window after the DEC-0048 preflight succeeds. Scheduled email delivery continues while the gate is `MEASURING` so the evidence can be collected. Once the gate is fully evaluable, any failed threshold suppresses the regular newsletter, sets `FAIL`, and runs exactly the bounded final administrative-alert workflow in V1.13 (DEC-0038). After that alert reaches a terminal outcome, a durable halt latch makes the scheduled entrypoint exit before retrieval, classification, evaluation collection, or SMTP—including the general briefing—and never clears itself (DEC-0035–DEC-0036). Recovery requires `--restart-after-gate-failure --confirm`: a full no-send health check runs while the latch remains set, failure changes nothing, and success atomically records the recovery, opens a fresh `MEASURING` window, and enables the next scheduled run (DEC-0037).

---

## 11. Pre-implementation spikes

Neither writes production code. **Spike 2 blocks V1. Spike 1 moved to V1.5 with tier 2 (DEC-0001).**

**Spike 1 — tier-2 source registry (V1.5, not blocking V1).** For each of the nine tickers, probe and record the actual official endpoint: URL, format (RSS/Atom/JSON/HTML), stable identifier available, update cadence, `robots.txt` result, `ETag`/`Last-Modified` support, and whether a machine-readable feed exists at all. Official sources are heterogeneous — Apple exposes a newsroom page, Costco an investor-news application page; there is no demonstrated common contract. Output: `config/source_registry.json` with an adapter type per endpoint and a required/optional flag. **Issuers with no usable endpoint are recorded `UNSUPPORTED` and tier 2 is skipped for them** — not treated as failure.

**Spike 2 — entity-map bootstrap.** Per ticker, automatically record legal regime as metadata, whether SEC filing coverage is supported and therefore required under D27, the set of observed supported forms, the applicable annual form (10-K, 20-F, 40-F, trust-specific form, or unknown), whether a subsidiary exhibit exists, its format, whether the omission allowance was exercised, and the evidence outcome (DEC-0044). The observed-form set, not legal regime, selects processing behavior and is refreshed when a new form appears. For every non-self relationship, record the evidence source, verification date, expiry date, and annual accession or prospectus used under D32. Well-supported entries are accepted automatically; conflicting, incomplete, or family-level relationships enter the user ambiguity queue and fail closed until reviewed (DEC-0029). Output: the first `config/entity_map.json`, the ambiguity queue, and a note per ticker on what could not be established.

---

## 12. Rollback

**Reverting the commit is not sufficient.** `_migrate()` raises `RuntimeError` when the database version exceeds `SCHEMA_VERSION` (`src/news_agent/mailer/state.py:35-37`), so a v3 database against reverted v2 code makes the mailer refuse to start.

Rollback procedure: stop the scheduled job; restore the pre-migration backup of `data/email_state.db`; revert the commit; verify startup. **The migration must take that backup automatically and record its path**, and a test must cover the restore path. Quotes, budgets, Gmail delivery, and Telegram are otherwise untouched. No provider credentials are added or removed.

---

## 13. Settled decisions

**Settled 2026-07-31:** Q1 — tier 2 deferred, V1 primary source is EDGAR only (DEC-0001), with Options A, B, and C included in V1 (DEC-0009; D20–D21). Q2 — interactive adjudication starts with a 40-item target and extends until at least 20 independently identified material non-filing events support the recall denominator (DEC-0016, superseding DEC-0002; §9.4), with an 80% minimum non-filing recall threshold (DEC-0010; D23). Q6 — Gate A triggers a targeted provider recommendation for confirmed contextual-coverage gaps, never an automatic purchase or activation (DEC-0017, D30; §10). Robots exception kept (DEC-0004, D19).

**Settled in the Watchlist Grill Me session:** Q4 — use the complete domestic-issuer Form 8-K allowlist in D22, including Items 5.04–5.06 and 8.01, and measure the routine-filing volume those broader items add (DEC-0008, superseding DEC-0005). Q3 — show the short relationship explanation and its citation in the email (DEC-0006). Q5 — render `No verified news today (partial sources).` for a quiet ticker whose optional sources partially failed (DEC-0007). Editorial fallback — when no primary document can be located, allow an approved editorial source to support a clearly attributed summary in the Reported block without using it as corporate-relationship evidence (DEC-0011, D24). Managed capital — include `MANAGED_CAPITAL` in V1 with explicit relationship wording and separate evidence (DEC-0012, D25). Foreign issuers — evaluate each 6-K for materiality and use headline fallback only when official metadata establishes a qualifying event (DEC-0013, D26). Source requirements — require supported EDGAR coverage, treat Options A and B as optional, and preserve verified stories alongside an explicit EDGAR-failure warning (DEC-0014, D27). Budget — guarantee the $0.25 watchlist reserve, allow unused capacity inside the $1 run cap, and label budget-exhausted tickers classification-incomplete (DEC-0015, D28). Deduplication — merge high-confidence duplicate events in V1 with primary-source precedence and retain uncertain pairs separately (DEC-0018, D31); no confirmed same event may render twice in one email, while uncertain pairs fail only if adjudication confirms duplication (DEC-0031, D44). Relationship freshness — expire non-self evidence after 12 months or a newer governing filing and prevent stale evidence from producing `AFFILIATE`, `MANAGED_CAPITAL`, or `UNDERLYING_ASSET` (DEC-0019, D32). Per-ticker brevity — render at most two full stories and no more than two additional linked mentions (DEC-0020–DEC-0021, D33–D34). SEC contact — identify EDGAR requests with the dedicated NewsAgent Gmail supplied through `SEC_CONTACT_EMAIL` without logging it (DEC-0022, D35). ETHB — include bounded material Ethereum events as `UNDERLYING_ASSET` with a current prospectus citation and fetch `ETH-USD` once daily as its shared discovery key (DEC-0023–DEC-0024, D36–D37). Retention — purge raw Watchlist payloads and extracted text after seven days and non-body metadata after one year, protecting active editions (DEC-0025, D38). Relationship accuracy — Gate A allows at most 5% false rendered relationship claims across the four definitive labels and requires at least 20 adjudicated claims before enforcing the threshold (DEC-0026–DEC-0027, D39–D40). Large moves — quiet ≥3% move days are diagnostic review targets only, and affect recall or provider recommendations only after a reviewer confirms a missed material event (DEC-0028, D41). Entity-map bootstrap — accept well-supported official evidence automatically, send only ambiguity to the user, and fail closed until ambiguous entries are reviewed (DEC-0029, D42). Story relevance — Gate A permits at most 5% irrelevant rendered stories after at least 20 rendered stories have been reviewed (DEC-0030, D43). Retrieval reliability — required sources may fail on at most 2% of evaluated ticker-days, and every failure remains explicit (DEC-0032, D45). SEC filings — after a successful EDGAR retrieval, zero eligible filings may be missed; outages use the retrieval allowance and must be caught up completely on the next successful run (DEC-0033, D46). Ambiguity notice — a count-only admin footer signals pending relationship reviews while details remain withheld for local CLI review (DEC-0034, D47). Gate failure — once all minimum evidence exists, any failed Gate A threshold suppresses the regular newsletter, sends one final metrics-and-restart admin email, then halts every scheduled pipeline task until a confirmed no-send health check succeeds and opens a fresh evaluation window (DEC-0035–DEC-0038, D48–D51). Measurement reminders — every seventh completed evaluation day shows count-only progress and remaining review minima (DEC-0039, D52).

**Settled in the second Watchlist Grill Me session:** Gate A defaults to `DISABLED` and requires confirmed activation after configuration, tests, and a successful required-source dry run (DEC-0040, DEC-0048; D53). Stored resends remain unchanged while rebuilt `[TEST]` editions are isolated from production delivery, suppression, and evaluation state (DEC-0041; D54). The non-filing recall frame is researched independently at least weekly, imported locally, and counted only after user adjudication (DEC-0042; D55). Stateful builds are serialized and transient retrieval receives at most three bounded attempts without failure poisoning the success cache (DEC-0043; D56). EDGAR processing follows observed per-ticker forms rather than legal regime, correcting ETHB and Shopify coverage (DEC-0044; D57). Watchlist is implemented as `src/news_agent/watchlist/` in the main checkout, not a separate application or worktree (DEC-0045; D58). The initial decision to skip Watchlist while Gate A was disabled (DEC-0046) was superseded: normal runs still deliver Watchlist, state `Watchlist evaluation disabled.`, collect no Gate metrics, and cannot trigger gate enforcement until activation (DEC-0047; D59).

---

## 14. Acceptance criteria

- Every rendered claim is supported by the document it links to; relationship claims cite their own source separately from event claims.
- Every rendered relationship label carries cited evidence; Gate A permits at most 5% of adjudicated `DIRECT`, `AFFILIATE`, `MANAGED_CAPITAL`, and `UNDERLYING_ASSET` claims to be judged false. Family-level ambiguity renders as `FAMILY_UNRESOLVED` with hedged wording.
- The Gate A relationship-error result remains unreported until at least 20 rendered definitive relationship claims have been adjudicated; the evaluation window extends until that minimum is met.
- A quiet ticker-day with an absolute move of at least 3% enters the adjudication queue but cannot fail Gate A or trigger a provider recommendation unless review confirms a missed material event.
- Entity-map entries with clear approved primary evidence require no user action; ambiguous entries enter a review queue and cannot emit a definitive relationship label until resolved.
- The Gate A irrelevant-story rate is at most 5% and remains unreported until at least 20 rendered stories have been adjudicated; the evaluation window extends until that minimum is met.
- No stale non-self evidence produces `AFFILIATE`, `MANAGED_CAPITAL`, or `UNDERLYING_ASSET`; stale evidence does not block separately supported direct issuer stories.
- ETHB underlying-asset stories are limited to the D36 event types, cite a current prospectus for the relationship, and never imply the trust participated in the Ethereum event.
- `ETH-USD` is fetched at most once per day regardless of how many tickers or future subscribers use it; its candidates receive no relevance shortcut.
- Raw Watchlist responses and extracted text are absent after seven days; non-body metadata is absent after one year; active editions are exempt until terminal delivery state, and cleanup never weakens suppression.
- No `MENTION_ONLY` item reaches a reader.
- No source outside §5 is used. Rate limits and site terms are respected everywhere; **the single robots exception is D19 (`finance.yahoo.com/m/`) and no other**. SEC requests carry the dedicated contact from `SEC_CONTACT_EMAIL`, while logs and diagnostics expose only validation status.
- Every candidate from every tier passes the §6 classifier and §6.5 materiality test; no source bypasses either.
- Disclosures and coverage render in separate labelled blocks and are never interleaved.
- The §7 content/retrieval combinations are exhaustive; verified stories survive source failures with the specified warning, `No verified news today.` is never shown for a required-source failure, and `UNSUPPORTED` is never counted as failure.
- Suppression prevents redelivery of an `event_id` only after successful delivery.
- Every high-confidence same-event set renders once with primary-source precedence. Distinct and `UNCERTAIN` events remain separate; no low-confidence merge is permitted.
- No confirmed same event renders twice in one Watchlist email. An `UNCERTAIN` pair is permitted to remain separate and becomes a Gate A failure only if adjudication confirms duplication.
- Required-source retrieval failures occupy at most 2% of evaluated ticker-days and always render as an explicit failure or partial-source warning, never as a clean no-news result.
- After a successful EDGAR retrieval, every eligible filing before the edition cutoff receives a persisted disposition. After an outage, the next successful run processes every eligible accession since the last successful watermark exactly once.
- A nonempty relationship-ambiguity queue adds only a count-only admin footer; unverified candidate details remain out of the email and are available through the local review CLI.
- While Gate A is `MEASURING`, every seventh completed evaluation day adds a count-only footer showing elapsed days and remaining review minima, with no candidate details.
- Scheduled email delivery continues while Gate A is `MEASURING`; after a fully measurable `FAIL`, only the one final DEC-0038 administrative notice may be attempted, and no regular NewsAgent email reaches any recipient.
- A durable Gate A failure latch makes later scheduled invocations exit before retrieval, classification, evaluation collection, or SMTP and remains set across restarts until explicit manual recovery.
- Manual recovery requires the confirmed restart command and a successful full no-send health check; failure preserves the halt, while success starts a fresh versioned Gate A window and resumes email only on the next scheduled run.
- The first fully measurable Gate A failure suppresses the newsletter and emits one admin-only failure notice with failed metrics and the restart command; after a terminal alert outcome, no further email or pipeline work occurs.
- Each ticker renders at most two full event stories and two concise linked `Also:` mentions; diagnostics preserve every qualifying event's disposition.
- **The five Google News feeds in `config/sources.toml` are unchanged**; Telegram and general-briefing coverage are unaffected.
- The 10-ticker cap, shared deadline, and Gmail-only delivery are unchanged. The watchlist receives at least its $0.25 reserve and may use otherwise-unused capacity, but total OpenAI spend never exceeds the $1 per-run cap; budget exhaustion is visible per D28.
- No licensed provider is purchased or activated automatically. Any Gate A recommendation identifies measured coverage gaps, the cheapest targeted option, expected recurring cost, and exact insertion point before requesting user approval.
- Gate A begins `DISABLED`; normal editions still contain Watchlist and the exact evaluation-disabled notice, but no Gate A numerator or denominator changes and no gate-triggered halt is possible.
- Gate A activation fails closed unless the entity map and SEC contact validate, required tests pass, a version-matched no-send dry run completes with every required EDGAR source successful, and no migration or processing error occurs. Optional-source failures and safely withheld ambiguity do not block activation.
- `--email-resend` sends stored content unchanged. `--email-rebuild-today` produces an isolated `[TEST]` edition and cannot mutate production edition delivery, event suppression, or Gate A state.
- A contending stateful build exits before any network, model, state, or delivery side effect. Transient fetches receive no more than three attempts, and a failed response cannot satisfy a daily success-cache lookup.
- Shopify and ETHB use the observed filing forms recorded in `config/entity_map.json`; a legal-regime label alone never selects or excludes processing.
- Weekly benchmark imports are independent of NewsAgent discovery and require user-confirmed materiality before entering the non-filing recall denominator.
- New Watchlist domain logic lives under `src/news_agent/watchlist/`; no separate Watchlist checkout, application, virtual environment, or credential file is required.
- Generated `data/` and credentials are untouched by the commit.
