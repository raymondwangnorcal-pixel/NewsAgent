# Project Decisions

## DEC-0001 — V1 watchlist retrieval is SEC filings only

- Date: 2026-07-31
- Owner: user
- Status at record: active
- Decision: The first version of watchlist retrieval uses SEC EDGAR filings as its only primary source; issuer investor-relations pages are deferred to a later version.
- Rationale: Issuer IR endpoints are heterogeneous across the nine tickers and require a per-issuer probe plus conditional-request support the codebase lacks, which would delay the start of the 30-day evaluation window.
- Scope: `src/news_agent/mailer/watchlist_news.py` retrieval tiers; the V1 section of the watchlist plan; the Gate A evaluation.
- Implementation: pending
- Recorded against HEAD: `d4237aef7a8aab2ff0088952598df7c4e78b7c58`
- Supersedes: none
- Evidence: `docs/plans/watchlist-retrieval-reliability.md` §5 and §13 Q1.

## DEC-0002 — Evaluation ground truth is 40 interactively adjudicated items

- Date: 2026-07-31
- Owner: user
- Status at record: active
- Decision: The evaluation will use 40 hand-labelled news items, presented one at a time by an agent that asks the reviewer whether the item genuinely correlates with the ticker, rather than expecting offline labelling.
- Rationale: Classification precision and non-filing recall cannot be computed without human ground truth, and an interactive prompt is the only form the reviewer committed to completing.
- Scope: Evaluation tooling, the diagnostics schema that stores adjudications, and the Gate B thresholds that depend on labelled data.
- Implementation: pending
- Recorded against HEAD: `d4237aef7a8aab2ff0088952598df7c4e78b7c58`
- Supersedes: none
- Evidence: `docs/plans/watchlist-retrieval-reliability.md` §10 and §13 Q2.

## DEC-0003 — Every email story has a short bold headline

- Date: 2026-07-31
- Owner: user
- Status at record: active
- Decision: Every story in the mobile-oriented email briefing will display a short bold headline above its summary.
- Rationale: Distinct headlines make dense briefing content easier to scan and help readers decide which summaries to read on a phone.
- Scope: Draft output, briefing story data, email formatting, HTML newsletter rendering, and related tests.
- Implementation: pending
- Recorded against HEAD: `d4237aef7a8aab2ff0088952598df7c4e78b7c58`
- Supersedes: none
- Evidence: User-approved feature request in the Codex task on 2026-07-31.

## DEC-0004 — Yahoo robots.txt disallowance is deliberately not honored

- Date: 2026-07-31
- Owner: user
- Status at record: active
- Decision: The watchlist retrieval path fetches Yahoo Finance pages under paths that Yahoo's robots.txt disallows for all automated visitors, rather than skipping them.
- Rationale: The repository owner reaffirmed this choice three times, most recently after being shown measurements indicating the restricted pages are a small and low-yield share of available candidates.
- Scope: Watchlist article retrieval only; applies to one publisher's restricted path prefix. Does not change retrieval policy for any other source, and does not affect the general briefing pipeline.
- Implementation: pending
- Recorded against HEAD: `d4237aef7a8aab2ff0088952598df7c4e78b7c58`
- Supersedes: none
- Evidence: `docs/plans/watchlist-retrieval-reliability.md` decision D19 and the measurements recorded there; a standing reminder is maintained in `docs/handoff.md`. Measured yield across two samples: 12 of 162 candidate links fall under the restricted prefix, and 4 of 5 sampled restricted pages produced no extractable text.

## DEC-0005 — Include 8-K Item 8.01 in V1 materiality

- Date: 2026-07-31
- Owner: user
- Status at record: active
- Decision: Include SEC Form 8-K Item 8.01, Other Events, in the V1 materiality allowlist.
- Rationale: The user prefers catching material developments that issuers place in Item 8.01, accepting the risk of added routine filings.
- Scope: The filing materiality configuration, Watchlist rendering volume, diagnostics, and the two-week review of filing quality.
- Implementation: pending
- Recorded against HEAD: `a8c0d39fc05a28b16fc71ef9b20367bfde07c2db`
- Supersedes: none
- Evidence: User decision during the Watchlist Grill Me session; `docs/plans/watchlist-retrieval-reliability.md` §13 Q4.

## DEC-0006 — Show relationship evidence in the email

- Date: 2026-07-31
- Owner: user
- Status at record: active
- Decision: Include a short relationship explanation and citation in the email whenever a Watchlist event relates through an affiliate, managed-capital platform, or unresolved corporate family.
- Rationale: The reader must be able to see why an event relates to the watched issuer, including BN, without inferring direct ownership or participation.
- Scope: Watchlist relationship prose, HTML/plain-text rendering, and relationship-evidence diagnostics.
- Implementation: pending
- Recorded against HEAD: `a8c0d39fc05a28b16fc71ef9b20367bfde07c2db`
- Supersedes: none
- Evidence: User decision during the Watchlist Grill Me session; `docs/plans/watchlist-retrieval-reliability.md` §13 Q3.

## DEC-0007 — Label partial-source quiet rows explicitly

- Date: 2026-07-31
- Owner: user
- Status at record: active
- Decision: Render `No verified news today (partial sources).` when required sources succeed, no qualifying item exists, and one or more optional sources fail.
- Rationale: Follow the plan's recommended wording so a quiet row is not mistaken for full retrieval coverage.
- Scope: Watchlist outcome aggregation, plain-text and HTML rendering, and related tests.
- Implementation: pending
- Recorded against HEAD: `a8c0d39fc05a28b16fc71ef9b20367bfde07c2db`
- Supersedes: none
- Evidence: User decision during the Watchlist Grill Me session; `docs/plans/watchlist-retrieval-reliability.md` §13 Q5.

## DEC-0008 — Define the complete V1 8-K materiality allowlist

- Date: 2026-07-31
- Owner: user
- Status at record: active
- Decision: The V1 Form 8-K materiality allowlist is Items 1.01–1.03, 2.01–2.06, 3.01, 4.02, 5.01–5.07, and 8.01.
- Rationale: This list covers the recommended investor-relevant agreement, financial, listing, accounting, control, governance, employee-plan, ethics, shell-status, voting, and other-event disclosures, including the user's explicit choice to retain Items 5.04–5.06.
- Scope: SEC filing materiality configuration for domestic issuers, Watchlist rendering, diagnostics, fixtures, and tests.
- Implementation: pending
- Recorded against HEAD: `a8c0d39fc05a28b16fc71ef9b20367bfde07c2db`
- Supersedes: DEC-0005
- Evidence: User decision during the Watchlist Grill Me session; `docs/plans/watchlist-retrieval-reliability.md` §6.5 and §13.

## Update — 2026-07-31 — DEC-0005

- Type: supersession
- Implementation commit: not applicable
- Superseded by: DEC-0008
- Note: The complete V1 allowlist incorporates the earlier decision to include Item 8.01.

## DEC-0009 — Include discovery Options A, B, and C in V1

- Date: 2026-07-31
- Owner: user
- Status at record: active
- Decision: V1 includes Option A, cross-referencing the existing briefing feeds; Option B, per-ticker Yahoo RSS discovery; and Option C, a daily absolute price-move diagnostic flag at 3% or greater.
- Rationale: Together these options add broad and ticker-targeted discovery at minimal incremental cost while using price movement to identify suspicious coverage gaps without treating the move itself as news.
- Scope: V1 Watchlist discovery inputs, the shared relevance and materiality pipeline, quote-history diagnostics, and evaluation sampling.
- Implementation: pending
- Recorded against HEAD: `a8c0d39fc05a28b16fc71ef9b20367bfde07c2db`
- Supersedes: none
- Evidence: User-confirmed A/B/C decision; `docs/plans/watchlist-retrieval-reliability.md` D20–D21, §5, and §9.4.

## DEC-0010 — Require 80% non-filing recall at Gate A

- Date: 2026-07-31
- Owner: user
- Status at record: active
- Decision: V1 must achieve at least 80% recall for known relevant non-filing events in the Gate A evaluation.
- Rationale: Missing more than two of every ten known relevant non-filing events would not meet the desired watchlist reliability, while 80% remains realistic for the lowest-cost source architecture.
- Scope: Gate A acceptance criteria, evaluation reporting, and the trigger for investigating source gaps or a licensed provider.
- Implementation: pending
- Recorded against HEAD: `a8c0d39fc05a28b16fc71ef9b20367bfde07c2db`
- Supersedes: none
- Evidence: User decision during the Watchlist Grill Me session; `docs/plans/watchlist-retrieval-reliability.md` §10.

## DEC-0011 — Permit explicitly attributed editorial summaries

- Date: 2026-07-31
- Owner: user
- Status at record: active
- Decision: When no primary document can be located, an approved editorial source may support a concise summary in the Reported block if the prose explicitly attributes the report and links the editorial article.
- Rationale: This preserves timely coverage of material developments while making clear that the claim comes from editorial reporting rather than an issuer or regulator disclosure.
- Scope: Tier 5 source policy, Watchlist drafting, source links, relationship safeguards, diagnostics, and rendering tests.
- Implementation: pending
- Recorded against HEAD: `a8c0d39fc05a28b16fc71ef9b20367bfde07c2db`
- Supersedes: none
- Evidence: User selected Option A during the Watchlist Grill Me session; `docs/plans/watchlist-retrieval-reliability.md` §5 and §7.

## DEC-0012 — Include managed-capital relationships in V1

- Date: 2026-07-31
- Owner: user
- Status at record: active
- Decision: V1 may render a material event classified `MANAGED_CAPITAL` when the relationship is supported by separate evidence and explained explicitly in the email.
- Rationale: This captures economically relevant developments such as Brookfield Asset Management activity for BN without falsely describing them as direct transactions by the watched issuer.
- Scope: Entity-map relationships, V1 classification, relationship provenance, Watchlist rendering, diagnostics, and tests.
- Implementation: pending
- Recorded against HEAD: `a8c0d39fc05a28b16fc71ef9b20367bfde07c2db`
- Supersedes: none
- Evidence: User selected Option A during the Watchlist Grill Me session; `docs/plans/watchlist-retrieval-reliability.md` §6, §9.3, and §13.

## DEC-0013 — Evaluate foreign-issuer 6-K filings for materiality

- Date: 2026-07-31
- Owner: user
- Status at record: active
- Decision: V1 evaluates each Form 6-K against the configured material-event criteria; it renders a qualifying filing, uses headline-plus-link fallback only when official metadata establishes materiality but a summary cannot be generated, and otherwise excludes the filing with a diagnostic record.
- Rationale: Forms 6-K have no item-number taxonomy, so content-based evaluation provides useful BN and NVO coverage without rendering every routine foreign-issuer filing.
- Scope: EDGAR ingestion, foreign-issuer materiality classification, summary fallback, diagnostics, Watchlist rendering, and tests.
- Implementation: pending
- Recorded against HEAD: `a8c0d39fc05a28b16fc71ef9b20367bfde07c2db`
- Supersedes: none
- Evidence: User selected Option A during the Watchlist Grill Me session; `docs/plans/watchlist-retrieval-reliability.md` §6.5 and §7.

## DEC-0014 — Require EDGAR and treat editorial discovery as optional

- Date: 2026-07-31
- Owner: user
- Status at record: active
- Decision: For every ticker with supported SEC filing coverage, EDGAR is required while the existing briefing feeds and Yahoo ticker feed are optional; if EDGAR fails, verified editorial stories still render with an explicit official-filing retrieval warning and the ticker never renders a clean no-news result.
- Rationale: This preserves useful reporting while making an incomplete authoritative-source check visible instead of misrepresenting it as a verified quiet day.
- Scope: V1 source requirements, ticker outcome aggregation, failure rendering, diagnostics, entity-map bootstrap, and tests.
- Implementation: pending
- Recorded against HEAD: `a8c0d39fc05a28b16fc71ef9b20367bfde07c2db`
- Supersedes: none
- Evidence: User selected Option A during the Watchlist Grill Me session; `docs/plans/watchlist-retrieval-reliability.md` §7 and §11.

## DEC-0015 — Let the watchlist use unused run budget without exceeding $1

- Date: 2026-07-31
- Owner: user
- Status at record: active
- Decision: The watchlist retains its guaranteed $0.25 OpenAI reserve and may consume unused capacity within the shared $1 per-run cap; once the total cap is reached, completed results render, unevaluated candidates are recorded, and the ticker is labelled classification-incomplete rather than cleanly quiet.
- Rationale: This improves coverage when general briefing costs are low while preserving the absolute cost cap and preventing budget exhaustion from masquerading as no news.
- Scope: OpenAI budget allocation, Watchlist classification, ticker retrieval state, diagnostics, rendering, and tests.
- Implementation: pending
- Recorded against HEAD: `a8c0d39fc05a28b16fc71ef9b20367bfde07c2db`
- Supersedes: none
- Evidence: User selected Option A during the Watchlist Grill Me session; `docs/plans/watchlist-retrieval-reliability.md` §6 and §7.

## DEC-0016 — Require at least 20 known non-filing events for recall

- Date: 2026-07-31
- Owner: user
- Status at record: active
- Decision: Gate A keeps 40 interactive adjudications as its initial target but cannot apply the 80% non-filing recall threshold until its denominator contains at least 20 independently identified material non-filing events; the review count or live window extends as needed.
- Rationale: A minimum 20-event denominator limits each event to five percentage points and prevents a small number of cases from making the recall gate misleadingly volatile.
- Scope: Gate A duration, adjudication sampling, recall calculation, evaluation reporting, and reviewer workload.
- Implementation: pending
- Recorded against HEAD: `a8c0d39fc05a28b16fc71ef9b20367bfde07c2db`
- Supersedes: DEC-0002
- Evidence: User selected Option A during the Watchlist Grill Me session; `docs/plans/watchlist-retrieval-reliability.md` §9.4 and §10.

## Update — 2026-07-31 — DEC-0002

- Type: supersession
- Implementation commit: not applicable
- Superseded by: DEC-0016
- Note: Interactive adjudication remains, but 40 is now an initial target rather than a hard ceiling because recall requires at least 20 known events.

## DEC-0017 — Gate A triggers a targeted provider recommendation

- Date: 2026-07-31
- Owner: user
- Status at record: active
- Decision: If Gate A misses the 80% non-filing recall threshold or exceeds the 20% unexplained-move threshold after implementation defects are ruled out, NewsAgent produces a targeted licensed-provider recommendation with measured gaps and expected cost but does not purchase or activate a provider without user approval.
- Rationale: Licensing should address demonstrated contextual-coverage gaps without creating an automatic or unnecessarily broad recurring expense.
- Scope: Gate A reporting, root-cause analysis, provider evaluation, cost estimates, and authorization boundaries.
- Implementation: pending
- Recorded against HEAD: `a8c0d39fc05a28b16fc71ef9b20367bfde07c2db`
- Supersedes: none
- Evidence: User selected Option A during the Watchlist Grill Me session; `docs/plans/watchlist-retrieval-reliability.md` §10.

## DEC-0018 — Merge only high-confidence duplicate events in V1

- Date: 2026-07-31
- Owner: user
- Status at record: active
- Decision: V1 merges documents into one Watchlist story when they clearly describe the same real-world event, prefers the primary document while retaining useful editorial attribution, and keeps uncertain matches as separate stories.
- Rationale: This prevents obvious repetition without risking the loss of distinct developments through aggressive merging.
- Scope: Event identity, cross-source deduplication, source precedence, Watchlist rendering, sent-history suppression, diagnostics, and tests.
- Implementation: pending
- Recorded against HEAD: `a8c0d39fc05a28b16fc71ef9b20367bfde07c2db`
- Supersedes: none
- Evidence: User selected Option A during the Watchlist Grill Me session; `docs/plans/watchlist-retrieval-reliability.md` §8.5 and §10.

## DEC-0019 — Expire and reverify non-self relationship evidence

- Date: 2026-07-31
- Owner: user
- Status at record: active
- Decision: V1 reverifies non-self entity relationships at the earlier of a new annual filing or 12 months after verification; stale evidence cannot produce `AFFILIATE` or `MANAGED_CAPITAL`, may downgrade to `FAMILY_UNRESOLVED` only when current evidence still supports family relevance, and otherwise suppresses the association without blocking direct issuer stories.
- Rationale: Corporate ownership and economic relationships change, so stale evidence must not create false associations while unrelated direct coverage remains useful.
- Scope: Entity-map schema and maintenance, classification, relationship rendering, diagnostics, and tests.
- Implementation: pending
- Recorded against HEAD: `a8c0d39fc05a28b16fc71ef9b20367bfde07c2db`
- Supersedes: none
- Evidence: User selected Option A during the Watchlist Grill Me session; `docs/plans/watchlist-retrieval-reliability.md` §6.3–6.4.

## DEC-0020 — Limit each ticker to one or two full stories

- Date: 2026-07-31
- Owner: user
- Status at record: active
- Decision: Each Watchlist ticker renders only its one or two most important material events as full stories, while additional qualifying events appear only as brief linked mentions when important enough to retain.
- Rationale: The email should remain concise and scannable without silently discarding other genuinely important developments.
- Scope: Per-ticker event ranking, email rendering, diagnostics for non-featured material events, and tests.
- Implementation: pending
- Recorded against HEAD: `a8c0d39fc05a28b16fc71ef9b20367bfde07c2db`
- Supersedes: none
- Evidence: Existing approved requirement in `docs/plans/email-restructuring.md` and `src/news_agent/mailer/watchlist_news.py`, reconciled into the active Watchlist plan.

## DEC-0021 — Cap overflow events at two links per ticker

- Date: 2026-07-31
- Owner: user
- Status at record: active
- Decision: After a ticker's one or two full Watchlist stories, the email may render no more than two additional qualifying events as concise linked `Also:` mentions.
- Rationale: A two-link overflow preserves noteworthy secondary developments without allowing busy tickers to dominate the concise mobile email.
- Scope: Per-ticker event selection, email rendering, diagnostics for omitted material events, and tests.
- Implementation: pending
- Recorded against HEAD: `a8c0d39fc05a28b16fc71ef9b20367bfde07c2db`
- Supersedes: none
- Evidence: User selected Option A during the Watchlist Grill Me session; `docs/plans/watchlist-retrieval-reliability.md` §7.

## DEC-0022 — Use the dedicated NewsAgent Gmail as the SEC contact

- Date: 2026-07-31
- Owner: user
- Status at record: active
- Decision: EDGAR requests use the dedicated NewsAgent Gmail address as their contact identity, loaded from a separate `SEC_CONTACT_EMAIL` environment variable and never hardcoded or emitted in ordinary diagnostics.
- Rationale: This satisfies the SEC contact requirement while avoiding disclosure of the user's everyday personal address and keeping configuration auditable.
- Scope: EDGAR client configuration, startup validation, request headers, secret redaction, diagnostics, and tests.
- Implementation: pending
- Recorded against HEAD: `a8c0d39fc05a28b16fc71ef9b20367bfde07c2db`
- Supersedes: none
- Evidence: User selected Option A during the Watchlist Grill Me session; `docs/plans/watchlist-retrieval-reliability.md` V1.2.

## DEC-0023 — Include material Ethereum events for ETHB

- Date: 2026-07-31
- Owner: user
- Status at record: active
- Decision: V1 adds an `UNDERLYING_ASSET` relationship for ETHB and may render material Ethereum protocol, staking, regulatory, security, or credibly explained unusual-market-move events when a current trust prospectus supports the relationship and the email explains it explicitly.
- Rationale: ETHB's investor relevance depends heavily on its underlying ether exposure, but limiting eligible event types prevents the Watchlist from becoming a general cryptocurrency-news feed.
- Scope: Entity-map relationships, ETHB classification and materiality, relationship provenance, Watchlist rendering, diagnostics, and tests.
- Implementation: pending
- Recorded against HEAD: `a8c0d39fc05a28b16fc71ef9b20367bfde07c2db`
- Supersedes: none
- Evidence: User selected Option A during the Watchlist Grill Me session; `docs/plans/watchlist-retrieval-reliability.md` §3 and §6.

## DEC-0024 — Retrieve ETH-USD once daily for ETHB discovery

- Date: 2026-07-31
- Owner: user
- Status at record: active
- Decision: V1 retrieves the Yahoo `ETH-USD` ticker feed once per day as a separate discovery key, caches it independently, and routes only qualifying Ethereum events to ETHB through the shared relevance and materiality pipeline.
- Rationale: A targeted underlying-asset feed makes ETHB coverage functional with one additional free request while preserving retrieve-once reuse for future funds sharing the same underlying asset.
- Scope: Tier 5b discovery configuration, cache keys, ETHB candidate routing, request counts, diagnostics, and tests.
- Implementation: pending
- Recorded against HEAD: `a8c0d39fc05a28b16fc71ef9b20367bfde07c2db`
- Supersedes: none
- Evidence: User selected Option A during the Watchlist Grill Me session; `docs/plans/watchlist-retrieval-reliability.md` §5, §8, and §9.1.

## DEC-0025 — Retain raw Watchlist text for seven days and metadata for one year

- Date: 2026-07-31
- Owner: user
- Status at record: active
- Decision: V1 deletes Watchlist raw responses and extracted article or filing text after seven days while retaining non-body metadata, hashes, classifications, event identities, diagnostics, and delivery history for one year.
- Rationale: Seven days covers the 48-hour lookback, retries, and short-term debugging while limiting storage of publisher content and preserving sufficient evidence for evaluation and reliability analysis.
- Scope: Watchlist database retention, cleanup scheduling, cache payloads, extracted text, metadata, diagnostics, sent history, and tests.
- Implementation: pending
- Recorded against HEAD: `a8c0d39fc05a28b16fc71ef9b20367bfde07c2db`
- Supersedes: none
- Evidence: User selected Option A during the Watchlist Grill Me session; `docs/plans/watchlist-retrieval-reliability.md` §8.

## DEC-0026 — Allow up to 5% false rendered relationship claims

- Date: 2026-07-31
- Owner: user
- Status at record: active
- Decision: Gate A permits a false-relationship rate of at most 5% across rendered `DIRECT`, `AFFILIATE`, `MANAGED_CAPITAL`, and `UNDERLYING_ASSET` claims.
- Rationale: The user chose a measurable low error tolerance instead of making one relationship error automatically fail the release gate.
- Scope: Gate A relationship-accuracy metric, adjudication reporting, release criteria, diagnostics, and tests.
- Implementation: pending
- Recorded against HEAD: `a8c0d39fc05a28b16fc71ef9b20367bfde07c2db`
- Supersedes: none
- Evidence: User selected Option B during the Watchlist Grill Me session; `docs/plans/watchlist-retrieval-reliability.md` §10.

## DEC-0027 — Require 20 reviewed relationship claims for Gate A

- Date: 2026-07-31
- Owner: user
- Status at record: active
- Decision: Gate A does not report or enforce the 5% false-relationship threshold until at least 20 rendered relationship claims have been adjudicated; the review count or live window extends until that minimum is reached.
- Rationale: With fewer than 20 claims, one error would exceed 5%, making the chosen tolerance behave like zero tolerance and producing an unstable release result.
- Scope: Gate A sample-size rules, interactive adjudication, evaluation duration, diagnostics, release reporting, and tests.
- Implementation: pending
- Recorded against HEAD: `a8c0d39fc05a28b16fc71ef9b20367bfde07c2db`
- Supersedes: none
- Evidence: User selected Option A during the Watchlist Grill Me session; `docs/plans/watchlist-retrieval-reliability.md` §9.4 and §10.

## DEC-0028 — Treat unexplained large moves as diagnostics only

- Date: 2026-07-31
- Owner: user
- Status at record: active
- Decision: Ticker-days with an absolute price move of at least 3% and no verified story are review targets, not a numeric Gate A pass-or-fail metric; only a reviewer-confirmed missed material event affects recall or supports a licensed-provider recommendation.
- Rationale: A stock can move sharply without a specific qualifying event, so failing the release on unexplained moves would encourage irrelevant or invented story associations.
- Scope: Gate A metrics, large-move diagnostics, interactive adjudication, contextual-gap reporting, provider recommendations, and tests.
- Implementation: pending
- Recorded against HEAD: `a8c0d39fc05a28b16fc71ef9b20367bfde07c2db`
- Supersedes: none
- Evidence: User selected Option A during the Watchlist Grill Me session; `docs/plans/watchlist-retrieval-reliability.md` §9.4 and §10.

## DEC-0029 — Auto-build the entity map and review only ambiguity

- Date: 2026-07-31
- Owner: user
- Status at record: active
- Decision: V1 automatically builds entity-map entries from official filings and other approved primary evidence; the user reviews only ambiguous relationships, and unresolved ambiguous entries fail closed rather than producing a definitive relationship label.
- Rationale: This preserves unattended operation for well-supported relationships while keeping human control over the cases most likely to create false company associations.
- Scope: Entity-map bootstrap, relationship evidence, ambiguity queue, configuration generation, classifier behavior, diagnostics, and tests.
- Implementation: pending
- Recorded against HEAD: `a8c0d39fc05a28b16fc71ef9b20367bfde07c2db`
- Supersedes: none
- Evidence: User selected Option A during the Watchlist Grill Me session; `docs/plans/watchlist-retrieval-reliability.md` §6 and §11.

## DEC-0030 — Cap irrelevant rendered stories at 5% after 20 reviews

- Date: 2026-07-31
- Owner: user
- Status at record: active
- Decision: Gate A permits at most 5% of reviewed rendered Watchlist stories to be judged irrelevant to an investor, and the metric is not reported or enforced until at least 20 rendered stories have been adjudicated.
- Rationale: A low tolerance limits newsletter noise while the minimum denominator prevents one judgment in a very small sample from producing an unstable release result.
- Scope: Gate A false-positive metric, interactive adjudication, evaluation duration, materiality diagnostics, release reporting, and tests.
- Implementation: pending
- Recorded against HEAD: `a8c0d39fc05a28b16fc71ef9b20367bfde07c2db`
- Supersedes: none
- Evidence: User selected Option A during the Watchlist Grill Me session; `docs/plans/watchlist-retrieval-reliability.md` §9.4 and §10.

## DEC-0031 — Allow zero confirmed same-event duplicates per email

- Date: 2026-07-31
- Owner: user
- Status at record: active
- Decision: Gate A allows no confirmed instance of the same event rendering more than once in a single Watchlist email; uncertain event pairs remain separate unless adjudication confirms they describe the same event.
- Rationale: A duplicated event directly degrades the newsletter, while preserving uncertain pairs avoids incorrectly merging distinct developments.
- Scope: Event-level deduplication, Gate A duplicate metric, adjudication, email rendering, diagnostics, and tests.
- Implementation: pending
- Recorded against HEAD: `a8c0d39fc05a28b16fc71ef9b20367bfde07c2db`
- Supersedes: none
- Evidence: User selected Option A during the Watchlist Grill Me session; `docs/plans/watchlist-retrieval-reliability.md` §8.4 and §10.

## DEC-0032 — Cap required-retrieval failures at 2% of ticker-days

- Date: 2026-07-31
- Owner: user
- Status at record: active
- Decision: Gate A permits required-source retrieval failures on at most 2% of evaluated Watchlist ticker-days, and every such failure must remain explicit in the email outcome and diagnostics rather than appearing as a clean no-news day.
- Rationale: The threshold tolerates a small number of transient upstream or network outages while still requiring reliable daily operation and transparent failures.
- Scope: Gate A retrieval-reliability metric, ticker outcome rendering, source diagnostics, evaluation reporting, and tests.
- Implementation: pending
- Recorded against HEAD: `a8c0d39fc05a28b16fc71ef9b20367bfde07c2db`
- Supersedes: none
- Evidence: User selected Option A during the Watchlist Grill Me session; `docs/plans/watchlist-retrieval-reliability.md` §7 and §10.

## DEC-0033 — Allow zero system-caused SEC filing misses

- Date: 2026-07-31
- Owner: user
- Status at record: active
- Decision: After a successful EDGAR retrieval, NewsAgent must discover and process every eligible filing accepted before the edition cutoff for the next email; an upstream SEC or network outage is counted under the 2% retrieval-failure allowance and every affected eligible filing must be caught up on the next successful run.
- Rationale: This sets zero tolerance for misses the system controls while reconciling filing coverage with the separately approved allowance for brief upstream outages.
- Scope: EDGAR discovery, filing processing, edition cutoff, outage recovery, Gate A filing metrics, diagnostics, and tests.
- Implementation: pending
- Recorded against HEAD: `a8c0d39fc05a28b16fc71ef9b20367bfde07c2db`
- Supersedes: none
- Evidence: User selected Option A during the Watchlist Grill Me session; `docs/plans/watchlist-retrieval-reliability.md` §9 and §10.

## DEC-0034 — Show a compact pending-relationship-review notice

- Date: 2026-07-31
- Owner: user
- Status at record: active
- Decision: When the entity-map ambiguity queue is nonempty, the email includes a brief administrative notice stating how many Watchlist relationships need review; the unverified candidate stories remain withheld, and V1 review occurs through the local CLI.
- Rationale: The notice prevents ambiguous relationships from remaining unnoticed without exposing potentially false company associations in the newsletter.
- Scope: Email rendering, ambiguity queue, local review CLI, withheld-candidate state, diagnostics, and tests.
- Implementation: pending
- Recorded against HEAD: `a8c0d39fc05a28b16fc71ef9b20367bfde07c2db`
- Supersedes: none
- Evidence: User selected Option A during the Watchlist Grill Me session; `docs/plans/watchlist-retrieval-reliability.md` §6, §7, and §9.

## DEC-0035 — Stop all scheduled email delivery after a measurable Gate A failure

- Date: 2026-07-31
- Owner: user
- Status at record: active
- Decision: Once every Gate A metric has its required minimum evidence and Gate A is evaluated, any failing threshold stops all scheduled NewsAgent email delivery, including the general briefing and Watchlist; delivery continues during the initial measurement window while the gate is not yet evaluable.
- Rationale: The user chose to prevent any scheduled newsletter delivery after the Watchlist fails its approved release-quality standards.
- Scope: Scheduler delivery guard, Gate A state, general briefing email, Watchlist email, SMTP suppression, diagnostics, recovery workflow, and tests.
- Implementation: pending
- Recorded against HEAD: `a8c0d39fc05a28b16fc71ef9b20367bfde07c2db`
- Supersedes: none
- Evidence: User selected Option B and explicitly requested that it be recorded during the Watchlist Grill Me session; `docs/plans/watchlist-retrieval-reliability.md` §10.

## DEC-0036 — Halt all scheduled pipeline work after Gate A failure

- Date: 2026-07-31
- Owner: user
- Status at record: active
- Decision: After a fully measurable Gate A failure, scheduled NewsAgent runs perform no retrieval, classification, diagnostics collection, or email delivery until the user manually restarts the system.
- Rationale: The user chose a complete operational halt rather than continuing an unsent evaluation pipeline or allowing automatic recovery.
- Scope: Scheduler gate latch, startup guard, network and model calls, diagnostics, email delivery, manual restart workflow, and tests.
- Implementation: pending
- Recorded against HEAD: `a8c0d39fc05a28b16fc71ef9b20367bfde07c2db`
- Supersedes: none
- Evidence: User selected Option B during the Watchlist Grill Me session; `docs/plans/watchlist-retrieval-reliability.md` §10.

## DEC-0037 — Restart a halted pipeline only after a successful no-send health check

- Date: 2026-07-31
- Owner: user
- Status at record: active
- Decision: A dedicated manually confirmed restart command bypasses the halt latch only to run one full no-send health check; it clears the latch after success, starts a fresh Gate A measurement window, and allows email to resume on the next scheduled run, while any failed health check leaves the system halted.
- Rationale: This provides a practical recovery path without resuming delivery before the updated system proves it can complete a full pipeline run safely.
- Scope: Recovery CLI, halt-latch bypass, dry-run validation, Gate A window reset, audit state, scheduler resumption, and tests.
- Implementation: pending
- Recorded against HEAD: `a8c0d39fc05a28b16fc71ef9b20367bfde07c2db`
- Supersedes: none
- Evidence: User selected Option A during the Watchlist Grill Me session; `docs/plans/watchlist-retrieval-reliability.md` §9 and §10.

## DEC-0038 — Send one final administrative Gate A failure email

- Date: 2026-07-31
- Owner: user
- Status at record: active
- Decision: When a fully measurable Gate A first fails, NewsAgent suppresses the regular newsletter and sends one final administrative email containing only the failed metrics and the confirmed manual restart command, then halts all scheduled work and future email.
- Rationale: The user needs a clear notice that the system stopped and an actionable recovery path without receiving newsletter content that failed the approved gate.
- Scope: Gate transition, administrative email, regular-edition suppression, idempotent delivery, retry handling, halt latch, and tests.
- Implementation: pending
- Recorded against HEAD: `a8c0d39fc05a28b16fc71ef9b20367bfde07c2db`
- Supersedes: none
- Evidence: User selected Option A during the Watchlist Grill Me session; `docs/plans/watchlist-retrieval-reliability.md` §10.

## DEC-0039 — Show weekly Gate A review-progress reminders

- Date: 2026-07-31
- Owner: user
- Status at record: active
- Decision: While Gate A is in `MEASURING`, one email per week includes a compact count-only footer showing elapsed evaluation days and the remaining independently identified events, relationship claims, and rendered-story reviews needed before the gate is evaluable.
- Rationale: A weekly reminder prevents the measurement phase from continuing indefinitely because required human reviews were overlooked, without adding daily newsletter clutter.
- Scope: Gate A progress calculation, email footer scheduling, adjudication counts, rendering, diagnostics, and tests.
- Implementation: pending
- Recorded against HEAD: `a8c0d39fc05a28b16fc71ef9b20367bfde07c2db`
- Supersedes: none
- Evidence: User selected Option A during the Watchlist Grill Me session; `docs/plans/watchlist-retrieval-reliability.md` §7, §9.4, and §10.

## DEC-0040 — Gate A starts disabled and requires confirmed activation

- Date: 2026-07-31
- Owner: user
- Status at record: active
- Decision: Gate A defaults to `DISABLED` and enters `MEASURING` only through an explicit confirmed activation command after Spike 2, all required tests, and a successful full no-send dry run.
- Rationale: Evaluation data and delivery enforcement must not activate before the entity map, implementation, and operational validation are ready.
- Scope: Gate A state model, activation CLI, scheduler delivery guard, evaluation-window initialization, diagnostics, and tests.
- Implementation: pending
- Recorded against HEAD: `c42db98b6023c75919e73e9862a733c664518b25`
- Supersedes: none
- Evidence: User decision during the Watchlist Grill Me session on 2026-07-31; `docs/plans/watchlist-retrieval-reliability.md` §10.

## DEC-0041 — Rebuilt test editions are isolated from production state

- Date: 2026-07-31
- Owner: user
- Status at record: active
- Decision: `--email-resend` sends a stored edition unchanged, while `--email-rebuild-today` fetches and rebuilds the current edition with a `[TEST]` subject, bypasses Watchlist sent suppression, and keeps its delivery records, sent history, and Gate A metrics separate from production.
- Rationale: Testing current retrieval and rendering must not suppress production stories, alter an archived edition, or contaminate release-gate evidence.
- Scope: Email CLI, edition creation, delivery records, Watchlist suppression, subject rendering, Gate A metrics, diagnostics, and tests.
- Implementation: pending
- Recorded against HEAD: `c42db98b6023c75919e73e9862a733c664518b25`
- Supersedes: none
- Evidence: User decision during the Watchlist Grill Me session on 2026-07-31; `docs/plans/watchlist-retrieval-reliability.md` delivery validation requirements.

## DEC-0042 — Build non-filing recall ground truth independently each week

- Date: 2026-07-31
- Owner: user
- Status at record: active
- Decision: Gate A uses weekly agent-assisted research independent of NewsAgent retrieval, drawing candidates from issuer releases, regulator releases, and approved editorial reporting, importing ticker, event date, source URL, and materiality rationale through a local CLI, and counting only user-confirmed material events toward the minimum-20 non-filing recall denominator.
- Rationale: Recall cannot be measured against candidates discovered by the system being evaluated, while independent research and human confirmation provide usable ground truth without purchasing a data feed.
- Scope: Gate A benchmark acquisition cadence, benchmark schema, import CLI, interactive adjudication, recall calculation, diagnostics, and tests.
- Implementation: pending
- Recorded against HEAD: `c42db98b6023c75919e73e9862a733c664518b25`
- Supersedes: none
- Evidence: User decision during the Watchlist Grill Me session on 2026-07-31; `docs/plans/watchlist-retrieval-reliability.md` §9.4 and §10.

## DEC-0043 — Serialize stateful builds and bound transient retrieval retries

- Date: 2026-07-31
- Owner: user
- Status at record: active
- Decision: NewsAgent permits only one stateful email build at a time through a process-level lock; a contending run exits before retrieval, model calls, state mutation, or delivery with an explicit already-running result, while transient fetches receive at most three attempts with exponential backoff, jitter, and `Retry-After` support and failed fetches never become successful daily-cache entries.
- Rationale: Global serialization avoids conflicting edition, suppression, budget, and source-cache mutations, while bounded retry behavior recovers brief upstream failures without hiding persistent failures or duplicating work.
- Scope: Scheduled and manual email builds, run locking, HTTP retrieval, source caching, diagnostics, CLI exit behavior, and tests.
- Implementation: pending
- Recorded against HEAD: `c42db98b6023c75919e73e9862a733c664518b25`
- Supersedes: none
- Evidence: User decision during the Watchlist Grill Me session on 2026-07-31; `docs/plans/watchlist-retrieval-reliability.md` §8 and §14.

## DEC-0044 — Select EDGAR processing rules from observed forms

- Date: 2026-07-31
- Owner: user
- Status at record: active
- Decision: EDGAR coverage and processing rules are configured per ticker from observed supported filing forms and refreshed when new forms appear, while legal issuer regime remains metadata and does not exclusively select forms; ETHB therefore requires EDGAR coverage for its observed trust filings and Shopify currently uses its observed domestic forms.
- Rationale: Legal classification does not reliably predict operational filing behavior, and static regime routing would omit current ETHB filings and misroute Shopify.
- Scope: Spike 2, entity-map filing metadata, EDGAR form filtering, filing fixtures, diagnostics, and tests.
- Implementation: pending
- Recorded against HEAD: `c42db98b6023c75919e73e9862a733c664518b25`
- Supersedes: none
- Evidence: User decision during the Watchlist Grill Me session on 2026-07-31; `docs/plans/watchlist-retrieval-reliability.md` §3.1, §6.4, §11, and V1.1.

## DEC-0045 — Keep Watchlist implementation inside the main application tree

- Date: 2026-07-31
- Owner: user
- Status at record: active
- Decision: Implement Watchlist as a dedicated `src/news_agent/watchlist/` package inside the existing NewsAgent checkout, with no separate Watchlist worktree or application and with the repository's existing local environment.
- Rationale: Watchlist is a component of NewsAgent and should have an explicit internal module boundary without duplicating the repository or maintaining a separate development environment.
- Scope: Source-package layout, imports from mailer and CLI code, test layout, local development workflow, and removal of the obsolete linked Watchlist worktree.
- Implementation: pending
- Recorded against HEAD: `c42db98b6023c75919e73e9862a733c664518b25`
- Supersedes: none
- Evidence: User decision during the Watchlist Grill Me session on 2026-07-31.

## DEC-0046 — Disabled Gate A skips Watchlist work and says so

- Date: 2026-07-31
- Owner: user
- Status at record: active
- Decision: While Gate A is `DISABLED`, normal NewsAgent runs continue the existing general briefing, perform no Watchlist retrieval, classification, rendering, or evaluation collection, and include the explicit notice `Watchlist disabled.`; confirmed activation enables Watchlist processing and opens a fresh `MEASURING` window together.
- Rationale: Unvalidated Watchlist behavior must not run or reach readers, but disabling it must neither hide its status nor interrupt the established general briefing.
- Scope: Scheduler entrypoint, Watchlist orchestration, email rendering, Gate A state transitions, diagnostics, and tests.
- Implementation: pending
- Recorded against HEAD: `c42db98b6023c75919e73e9862a733c664518b25`
- Supersedes: none
- Evidence: User decision during the Watchlist Grill Me session on 2026-07-31; `docs/plans/watchlist-retrieval-reliability.md` §7 and §10.

## DEC-0047 — Disabled Gate A does not disable Watchlist delivery

- Date: 2026-07-31
- Owner: user
- Status at record: active
- Decision: While Gate A is `DISABLED`, normal NewsAgent runs still retrieve, classify, and render Watchlist content, the email states `Watchlist evaluation disabled.`, no Gate A metrics accumulate, and no gate-triggered delivery or pipeline shutdown can occur.
- Rationale: The user wants Watchlist to operate during the pre-measurement period while keeping release-gate evidence and enforcement explicitly inactive until confirmed activation.
- Scope: Watchlist orchestration, email rendering, Gate A metrics and state transitions, scheduler enforcement, diagnostics, and tests.
- Implementation: pending
- Recorded against HEAD: `c42db98b6023c75919e73e9862a733c664518b25`
- Supersedes: DEC-0046
- Evidence: User clarification during the Watchlist Grill Me session on 2026-07-31; `docs/plans/watchlist-retrieval-reliability.md` §7 and §10.

## Update — 2026-07-31 — DEC-0046

- Type: supersession
- Implementation commit: not applicable
- Superseded by: DEC-0047
- Note: Gate A being disabled no longer skips Watchlist processing or delivery; it disables only evaluation and enforcement.

## DEC-0048 — Gate A activation requires a successful required-source preflight

- Date: 2026-07-31
- Owner: user
- Status at record: active
- Decision: Confirmed Gate A activation requires valid entity-map and SEC contact configuration, all required tests passing, a full no-send dry run in which every required EDGAR source succeeds, and no migration or processing error; optional-source failures and unresolved relationships already withheld by the ambiguity policy do not block activation.
- Rationale: Activation must prove the authoritative retrieval and state paths work without letting transient optional outages or safely unresolved relationships prevent measurement indefinitely.
- Scope: Gate A activation CLI, preflight evidence, configuration validation, EDGAR diagnostics, migration health, ambiguity handling, and tests.
- Implementation: pending
- Recorded against HEAD: `c42db98b6023c75919e73e9862a733c664518b25`
- Supersedes: none
- Evidence: User decision during the Watchlist Grill Me session on 2026-07-31; `docs/plans/watchlist-retrieval-reliability.md` §10 and V1.11.

## Update — 2026-07-31 — DEC-0001

- Type: implementation
- Implementation commit: `ba59a3f7453ebe947be1f21cb1b26906026d4ddd` — feat(watchlist): implement reliable retrieval and gate
- Superseded by: none
- Note: V1 uses EDGAR as its official filing source and keeps issuer-IR retrieval deferred.

## Update — 2026-07-31 — DEC-0004

- Type: implementation
- Implementation commit: `ba59a3f7453ebe947be1f21cb1b26906026d4ddd` — feat(watchlist): implement reliable retrieval and gate
- Superseded by: none
- Note: Yahoo discovery retains the approved personal-use path policy while excluding unapproved publishers.

## Update — 2026-07-31 — DEC-0006

- Type: implementation
- Implementation commit: `ba59a3f7453ebe947be1f21cb1b26906026d4ddd` — feat(watchlist): implement reliable retrieval and gate
- Superseded by: none
- Note: Rendered relationship explanations include a separate evidence link.

## Update — 2026-07-31 — DEC-0007

- Type: implementation
- Implementation commit: `ba59a3f7453ebe947be1f21cb1b26906026d4ddd` — feat(watchlist): implement reliable retrieval and gate
- Superseded by: none
- Note: Quiet rows distinguish optional-source partial coverage from verified quiet and required-source failure.

## Update — 2026-07-31 — DEC-0008

- Type: implementation
- Implementation commit: `ba59a3f7453ebe947be1f21cb1b26906026d4ddd` — feat(watchlist): implement reliable retrieval and gate
- Superseded by: none
- Note: The deterministic Form 8-K item allowlist matches the settled V1 policy.

## Update — 2026-07-31 — DEC-0009

- Type: implementation
- Implementation commit: `ba59a3f7453ebe947be1f21cb1b26906026d4ddd` — feat(watchlist): implement reliable retrieval and gate
- Superseded by: none
- Note: V1 reuses general-feed articles, retrieves distinct Yahoo keys, and records large-move review targets.

## Update — 2026-07-31 — DEC-0010

- Type: implementation
- Implementation commit: `ba59a3f7453ebe947be1f21cb1b26906026d4ddd` — feat(watchlist): implement reliable retrieval and gate
- Superseded by: none
- Note: Gate A enforces the settled 80 percent non-filing recall boundary.

## Update — 2026-07-31 — DEC-0011

- Type: implementation
- Implementation commit: `ba59a3f7453ebe947be1f21cb1b26906026d4ddd` — feat(watchlist): implement reliable retrieval and gate
- Superseded by: none
- Note: Approved editorial evidence renders in a separately attributed Reported block.

## Update — 2026-07-31 — DEC-0012

- Type: implementation
- Implementation commit: `ba59a3f7453ebe947be1f21cb1b26906026d4ddd` — feat(watchlist): implement reliable retrieval and gate
- Superseded by: none
- Note: Managed-capital stories use explicit non-issuer wording and separate relationship evidence.

## Update — 2026-07-31 — DEC-0013

- Type: implementation
- Implementation commit: `ba59a3f7453ebe947be1f21cb1b26906026d4ddd` — feat(watchlist): implement reliable retrieval and gate
- Superseded by: none
- Note: Form 6-K documents receive content evaluation with fail-closed official-metadata fallback.

## Update — 2026-07-31 — DEC-0014

- Type: implementation
- Implementation commit: `ba59a3f7453ebe947be1f21cb1b26906026d4ddd` — feat(watchlist): implement reliable retrieval and gate
- Superseded by: none
- Note: Supported EDGAR coverage is required while editorial discovery remains optional.

## Update — 2026-07-31 — DEC-0015

- Type: implementation
- Implementation commit: `ba59a3f7453ebe947be1f21cb1b26906026d4ddd` — feat(watchlist): implement reliable retrieval and gate
- Superseded by: none
- Note: Watchlist model work uses the shared capped budget and its reserved capacity.

## Update — 2026-07-31 — DEC-0016

- Type: implementation
- Implementation commit: `ba59a3f7453ebe947be1f21cb1b26906026d4ddd` — feat(watchlist): implement reliable retrieval and gate
- Superseded by: none
- Note: Benchmark import and one-at-a-time review enforce the minimum independent material-event denominator.

## Update — 2026-07-31 — DEC-0019

- Type: implementation
- Implementation commit: `ba59a3f7453ebe947be1f21cb1b26906026d4ddd` — feat(watchlist): implement reliable retrieval and gate
- Superseded by: none
- Note: Non-self evidence expires by date and when a newer governing annual accession is observed.

## Update — 2026-07-31 — DEC-0020

- Type: implementation
- Implementation commit: `ba59a3f7453ebe947be1f21cb1b26906026d4ddd` — feat(watchlist): implement reliable retrieval and gate
- Superseded by: none
- Note: Rendering allocates at most two full event slots per ticker.

## Update — 2026-07-31 — DEC-0021

- Type: implementation
- Implementation commit: `ba59a3f7453ebe947be1f21cb1b26906026d4ddd` — feat(watchlist): implement reliable retrieval and gate
- Superseded by: none
- Note: Overflow disclosure links are capped at two per ticker.

## Update — 2026-07-31 — DEC-0022

- Type: implementation
- Implementation commit: `ba59a3f7453ebe947be1f21cb1b26906026d4ddd` — feat(watchlist): implement reliable retrieval and gate
- Superseded by: none
- Note: EDGAR requires a separately configured contact and does not emit it in diagnostics.

## Update — 2026-07-31 — DEC-0023

- Type: implementation
- Implementation commit: `ba59a3f7453ebe947be1f21cb1b26906026d4ddd` — feat(watchlist): implement reliable retrieval and gate
- Superseded by: none
- Note: ETHB can use bounded material Ethereum events with explicit underlying-asset wording and evidence.

## Update — 2026-07-31 — DEC-0024

- Type: implementation
- Implementation commit: `ba59a3f7453ebe947be1f21cb1b26906026d4ddd` — feat(watchlist): implement reliable retrieval and gate
- Superseded by: none
- Note: Distinct discovery-key caching fetches ETH-USD at most once per day and routes it only to ETHB.

## Update — 2026-07-31 — DEC-0025

- Type: implementation
- Implementation commit: `ba59a3f7453ebe947be1f21cb1b26906026d4ddd` — feat(watchlist): implement reliable retrieval and gate
- Superseded by: none
- Note: Idempotent retention purges bodies after seven days and metadata after one year while protecting active editions.

## Update — 2026-07-31 — DEC-0026

- Type: implementation
- Implementation commit: `ba59a3f7453ebe947be1f21cb1b26906026d4ddd` — feat(watchlist): implement reliable retrieval and gate
- Superseded by: none
- Note: Gate A enforces the five percent false-relationship threshold.

## Update — 2026-07-31 — DEC-0027

- Type: implementation
- Implementation commit: `ba59a3f7453ebe947be1f21cb1b26906026d4ddd` — feat(watchlist): implement reliable retrieval and gate
- Superseded by: none
- Note: Relationship accuracy remains unevaluable below twenty definitive adjudications.

## Update — 2026-07-31 — DEC-0028

- Type: implementation
- Implementation commit: `ba59a3f7453ebe947be1f21cb1b26906026d4ddd` — feat(watchlist): implement reliable retrieval and gate
- Superseded by: none
- Note: Quiet moves of at least three percent enter local review without directly failing Gate A.

## Update — 2026-07-31 — DEC-0030

- Type: implementation
- Implementation commit: `ba59a3f7453ebe947be1f21cb1b26906026d4ddd` — feat(watchlist): implement reliable retrieval and gate
- Superseded by: none
- Note: Gate A applies the five percent irrelevant-story threshold only after twenty conclusive reviews.

## Update — 2026-07-31 — DEC-0032

- Type: implementation
- Implementation commit: `ba59a3f7453ebe947be1f21cb1b26906026d4ddd` — feat(watchlist): implement reliable retrieval and gate
- Superseded by: none
- Note: Required retrieval failures are persisted and evaluated against the two percent boundary.

## Update — 2026-07-31 — DEC-0033

- Type: implementation
- Implementation commit: `ba59a3f7453ebe947be1f21cb1b26906026d4ddd` — feat(watchlist): implement reliable retrieval and gate
- Superseded by: none
- Note: EDGAR watermarks, catch-up enumeration, and filing dispositions support zero system-caused filing misses.

## Update — 2026-07-31 — DEC-0034

- Type: implementation
- Implementation commit: `ba59a3f7453ebe947be1f21cb1b26906026d4ddd` — feat(watchlist): implement reliable retrieval and gate
- Superseded by: none
- Note: Editions show only the pending relationship-review count and keep details in the local CLI.

## Update — 2026-07-31 — DEC-0035

- Type: implementation
- Implementation commit: `ba59a3f7453ebe947be1f21cb1b26906026d4ddd` — feat(watchlist): implement reliable retrieval and gate
- Superseded by: none
- Note: A measurable failed gate suppresses the regular scheduled newsletter.

## Update — 2026-07-31 — DEC-0036

- Type: implementation
- Implementation commit: `ba59a3f7453ebe947be1f21cb1b26906026d4ddd` — feat(watchlist): implement reliable retrieval and gate
- Superseded by: none
- Note: A durable latch makes later scheduled invocations exit before pipeline work.

## Update — 2026-07-31 — DEC-0037

- Type: implementation
- Implementation commit: `ba59a3f7453ebe947be1f21cb1b26906026d4ddd` — feat(watchlist): implement reliable retrieval and gate
- Superseded by: none
- Note: Confirmed recovery performs a no-send health check and opens a fresh measuring window only on success.

## Update — 2026-07-31 — DEC-0038

- Type: implementation
- Implementation commit: `ba59a3f7453ebe947be1f21cb1b26906026d4ddd` — feat(watchlist): implement reliable retrieval and gate
- Superseded by: none
- Note: The failed window produces one stable administrative alert before the halt becomes terminal.

## Update — 2026-07-31 — DEC-0039

- Type: implementation
- Implementation commit: `ba59a3f7453ebe947be1f21cb1b26906026d4ddd` — feat(watchlist): implement reliable retrieval and gate
- Superseded by: none
- Note: Measuring editions show count-only progress on each seventh evaluation day.

## Update — 2026-07-31 — DEC-0040

- Type: implementation
- Implementation commit: `ba59a3f7453ebe947be1f21cb1b26906026d4ddd` — feat(watchlist): implement reliable retrieval and gate
- Superseded by: none
- Note: Schema migration creates Gate A disabled and activation requires explicit confirmation.

## Update — 2026-07-31 — DEC-0041

- Type: implementation
- Implementation commit: `ba59a3f7453ebe947be1f21cb1b26906026d4ddd` — feat(watchlist): implement reliable retrieval and gate
- Superseded by: none
- Note: Rebuilt test editions refresh sources without writing production suppression, delivery, cache, watermark, quote, or Gate state.

## Update — 2026-07-31 — DEC-0043

- Type: implementation
- Implementation commit: `ba59a3f7453ebe947be1f21cb1b26906026d4ddd` — feat(watchlist): implement reliable retrieval and gate
- Superseded by: none
- Note: Stateful email builds use a global lock and transient HTTP work is bounded to three attempts without cache poisoning.

## Update — 2026-07-31 — DEC-0045

- Type: implementation
- Implementation commit: `ba59a3f7453ebe947be1f21cb1b26906026d4ddd` — feat(watchlist): implement reliable retrieval and gate
- Superseded by: none
- Note: Watchlist domain code now lives inside the main news_agent package.

## Update — 2026-07-31 — DEC-0047

- Type: implementation
- Implementation commit: `ba59a3f7453ebe947be1f21cb1b26906026d4ddd` — feat(watchlist): implement reliable retrieval and gate
- Superseded by: none
- Note: Disabled evaluation permits normal Watchlist retrieval and renders the exact disabled notice without collecting Gate metrics.

## Update — 2026-07-31 — DEC-0048

- Type: implementation
- Implementation commit: `ba59a3f7453ebe947be1f21cb1b26906026d4ddd` — feat(watchlist): implement reliable retrieval and gate
- Superseded by: none
- Note: Activation checks entity configuration, SEC contact, tests, implementation version, and required-source dry-run outcomes.

## DEC-0049 — Reject non-feed source responses before caching

- Date: 2026-07-31
- Owner: shared
- Status at record: active
- Decision: Treat a response whose XML root is not RSS, Atom, or RDF as an invalid source feed, and do not cache it as a successful empty result.
- Rationale: Provider error pages can return HTTP success while containing valid HTML; failing closed preserves same-day retryability and prevents false quiet Watchlist rows.
- Scope: Shared RSS and Atom retrieval, source-cache state, and Watchlist editorial discovery.
- Implementation: pending
- Recorded against HEAD: `b4c96449575ccde3626c149c4bc690143b01f044`
- Supersedes: none
- Evidence: User-requested correction after the NVO Yahoo source diagnostic on 2026-07-31.

## Update — 2026-07-31 — DEC-0049

- Type: implementation
- Implementation commit: `b835f42b50446bbbda8f6573c224506e0bf2d5a3` — fix(fetch): reject non-feed source responses
- Superseded by: none
- Note: Non-feed XML roots now return an invalid-feed error, so Watchlist discovery retries rather than caching a false empty success.

## DEC-0050 — Require the latest completed NYSE close for Watchlist quotes

- Date: 2026-08-03
- Owner: shared
- Status at record: active
- Decision: Accept Watchlist quotes and quote-cache fallbacks only when their close date equals the latest completed regular NYSE session; test editions neither write nor read the production quote cache.
- Rationale: A displayed Watchlist edition must not mix stale per-ticker closes with current session closes, while morning, weekend, and holiday runs still use the legitimate preceding session.
- Scope: Quote providers, fallback selection, quote cache, test-revision isolation, and diagnostics.
- Implementation: pending
- Recorded against HEAD: `33ea9e9d33b3b7b992e3a257cb94b63924924894`
- Supersedes: none
- Evidence: User-requested implementation following the 2026-07-31 mixed-date Watchlist diagnosis and read-only adversarial review.

## Update — 2026-08-03 — DEC-0050

- Type: implementation
- Implementation commit: `db5386d7241be311d80e01e84207cfb63d98843f` — fix(watchlist): reject stale quote dates
- Superseded by: none
- Note: Date-validated provider fallback, exact-date cache reads, test cache isolation, NYSE-session selection, and rejection diagnostics are implemented with boundary coverage.

## DEC-0051 — Treat bare Brookfield as an accepted BN family-level relationship

- Date: 2026-08-03
- Owner: user
- Status at record: active
- Decision: Include material stories naming Brookfield in BN's Watchlist as a family-level relationship, with wording that does not assert Brookfield Corporation itself acted.
- Rationale: The owner accepted the Brookfield-to-BN family relationship while retaining entity-specific attribution safeguards.
- Scope: BN entity mapping, relationship-review seed state, Watchlist classification, and rendering.
- Implementation: pending
- Recorded against HEAD: `a816ecd5c080d89fbf4d70c7d1a3c65fe0956714`
- Supersedes: none
- Evidence: User-approved local relationship review and configuration update on 2026-08-03.

## Update — 2026-08-03 — DEC-0051

- Type: implementation
- Implementation commit: `6f6009ccb80952728858902cc48fe021be995430` — feat(watchlist): accept Brookfield family coverage for BN
- Superseded by: none
- Note: The review seed is accepted and a regression test preserves hedged family-level BN classification for bare Brookfield stories.

## DEC-0052 — Show live Watchlist prices during regular market hours

- Date: 2026-08-03
- Owner: user
- Status at record: active
- Decision: During regular NYSE market hours, render Watchlist prices from the same Yahoo Finance live-price source used by the Finance section; outside those hours, render the latest completed NYSE close.
- Rationale: The Watchlist should show current prices while trading is open and retain a clear final-price fallback after the market closes.
- Scope: Watchlist quote selection, quote labels, cache behavior, and price consistency with the Finance section.
- Implementation: pending
- Recorded against HEAD: `2e9a3bd512cccf9b993de936e60c4574ea91166c`
- Supersedes: none
- Evidence: User-approved Watchlist live-price request on 2026-08-03.

## DEC-0053 — Remove ticker-price display from the Finance section

- Date: 2026-08-04
- Owner: user
- Status at record: active
- Decision: Do not render stock ticker prices in the Finance section; retain Watchlist prices and their supporting quote retrieval.
- Rationale: The Finance briefing should contain only editorial stories, while the Watchlist remains the dedicated place for tracked-price information.
- Scope: Finance-section construction and its ticker-price display code only; Watchlist and alert behavior remain unchanged.
- Implementation: pending
- Recorded against HEAD: `eeb5feaf8fdee6a077ff3071fd629847c8a117e2`
- Supersedes: none
- Evidence: User-requested Finance-section simplification on 2026-08-04.
- Privacy waivers: none

## Update — 2026-08-04 — DEC-0052

- Type: implementation
- Implementation commit: `eeb5feaf8fdee6a077ff3071fd629847c8a117e2` — Refactor news agent implementation
- Superseded by: none
- Note: The Watchlist now uses the Finance quote snapshot during regular NYSE hours, labels those values live, and preserves completed-close behavior otherwise.
- Privacy waivers: none

## Update — 2026-08-04 — DEC-0053

- Type: implementation
- Implementation commit: `c4aa2764d763eefc71e3147f46a3350033d9367a` — feat(newsletter): simplify finance section
- Superseded by: none
- Note: Finance no longer receives ticker-price lead lines, while the Watchlist quote snapshot and alerts remain available.
- Privacy waivers: none

## DEC-0054 — Evaluate Form 8-K Item 7.01 by official content

- Date: 2026-08-04
- Owner: shared
- Status at record: active
- Decision: Keep the deterministic Form 8-K materiality allowlist for directly qualifying items, but treat Item 7.01 as indeterminate and render it only when the official filing text establishes a configured material event.
- Rationale: CuriosityStream disclosed a completed acquisition under Items 7.01 and 9.01, so blanket exclusion caused a material miss; content review captures such events without admitting every routine Regulation FD notice.
- Scope: EDGAR Form 8-K materiality classification, filing-body retrieval, Watchlist dispositions, and regression tests.
- Implementation: pending
- Recorded against HEAD: `efcefa859325f4d551ee6e4d29808434767fa0f9`
- Supersedes: DEC-0008
- Evidence: User-reported CuriosityStream miss on 2026-08-04 and SEC accession 0001628280-26-047438.
- Privacy waivers: none

## Update — 2026-08-04 — DEC-0008

- Type: supersession
- Implementation commit: not applicable
- Superseded by: DEC-0054
- Note: The fixed item allowlist remains the direct-accept path, while Item 7.01 now receives fail-closed official-content review.
- Privacy waivers: none

## DEC-0055 — Count general-news stories as sent only after SMTP acceptance

- Date: 2026-08-04
- Owner: user
- Status at record: active
- Decision: Count a general-news story as sent in newsletter quality metrics only when its edition reached SMTP acceptance; retain selected and non-accepted outcomes only for operational diagnostics.
- Rationale: Reader-facing relevance measurements must describe stories that actually reached the mail provider, not merely stories selected during an unsuccessful delivery attempt.
- Scope: Newsletter review candidate delivery states, sent-story denominators, metrics, and reporting.
- Implementation: pending
- Recorded against HEAD: `efcefa859325f4d551ee6e4d29808434767fa0f9`
- Supersedes: none
- Evidence: User-selected first grilling decision on 2026-08-04.
- Privacy waivers: none

## DEC-0056 — Keep newsletter review material local by default

- Date: 2026-08-04
- Owner: user
- Status at record: active
- Decision: Keep raw newsletter review material, source links, and reviewer notes local only; never automatically export, commit, or push them, and permit repository fixtures only after separate privacy review removes raw text, URLs, and notes.
- Rationale: Human evaluation needs durable local context without publishing source-derived material or reviewer notes through the repository.
- Scope: Newsletter review retention, exports, regression fixtures, and Git workflow.
- Implementation: pending
- Recorded against HEAD: `efcefa859325f4d551ee6e4d29808434767fa0f9`
- Supersedes: none
- Evidence: User-approved second grilling decision on 2026-08-04.
- Privacy waivers: none

## DEC-0057 — Judge filtered newsletter candidates against the finished daily deck

- Date: 2026-08-04
- Owner: user
- Status at record: active
- Decision: Label a filtered general-news candidate relevant only when it deserved a place in that day's finished newsletter after considering the stories selected that day; factual truth alone does not make it relevant.
- Rationale: The quality gate should be measured against reader value and finite daily capacity, not against whether every discarded article contains true information.
- Scope: Newsletter review rubric, false-negative labels, manual evaluation, and quality metrics.
- Implementation: pending
- Recorded against HEAD: `efcefa859325f4d551ee6e4d29808434767fa0f9`
- Supersedes: none
- Evidence: User-approved third grilling decision on 2026-08-04.
- Privacy waivers: none

## DEC-0058 — Include strict-filter candidates in newsletter review sampling

- Date: 2026-08-04
- Owner: user
- Status at record: active
- Decision: Include deeply filtered and hard-rejected general-news candidates in the initial randomized review sample at their combined 20 percent share.
- Rationale: Low-frequency review of the strictest filters is needed to expose serious false negatives that a near-miss-only sample would hide.
- Scope: Newsletter review strata, sample composition, false-negative measurement, and reviewer workload.
- Implementation: pending
- Recorded against HEAD: `efcefa859325f4d551ee6e4d29808434767fa0f9`
- Supersedes: none
- Evidence: User-approved fourth grilling decision on 2026-08-04.
- Privacy waivers: none

## DEC-0059 — Require a reason for clear newsletter review labels

- Date: 2026-08-04
- Owner: user
- Status at record: active
- Decision: Require a fixed reason code for every relevant or irrelevant newsletter review label, while allowing unclear labels without a reason.
- Rationale: Aggregate labels show whether the quality gate disagrees with the reviewer, but required reason codes identify what should be investigated or changed.
- Scope: Newsletter review CLI, adjudication schema, reviewer workflow, metrics, and controlled quality-gate improvements.
- Implementation: pending
- Recorded against HEAD: `efcefa859325f4d551ee6e4d29808434767fa0f9`
- Supersedes: none
- Evidence: User-approved fifth grilling decision on 2026-08-04.
- Privacy waivers: none

## DEC-0060 — Limit newsletter review-content retention

- Date: 2026-08-04
- Owner: user
- Status at record: active
- Decision: Retain raw newsletter review excerpts and free-text notes for 30 days only, and retain non-text candidate metadata and structured review labels for up to one year.
- Rationale: Thirty days provides enough context for active review while limiting retained source-derived text; one year preserves enough structured measurement history for controlled quality-gate evaluation.
- Scope: Newsletter review retention jobs, local database records, manual examples, and exports.
- Implementation: pending
- Recorded against HEAD: `efcefa859325f4d551ee6e4d29808434767fa0f9`
- Supersedes: none
- Evidence: User-approved sixth grilling decision on 2026-08-04.
- Privacy waivers: none

## DEC-0061 — Require owner-approved shadow evaluation before newsletter gate changes

- Date: 2026-08-04
- Owner: user
- Status at record: active
- Decision: Do not apply a newsletter quality-gate change to daily emails until it has been shadow-tested against the reviewed corpus, its improvements and regressions have been shown to the owner, and the owner explicitly approves it.
- Rationale: Review labels should improve the gate through measured, reversible human-approved changes rather than unobserved automatic tuning.
- Scope: Newsletter review metrics, regression fixtures, quality-gate change workflow, and delivery safety.
- Implementation: pending
- Recorded against HEAD: `efcefa859325f4d551ee6e4d29808434767fa0f9`
- Supersedes: none
- Evidence: User-approved seventh grilling decision on 2026-08-04.
- Privacy waivers: none

## DEC-0062 — Do not fail EDGAR retrieval for unavailable Item 7.01 text

- Date: 2026-08-04
- Owner: shared
- Status at record: active
- Decision: Review Form 8-K Item 7.01 by official content when available, but skip it and advance the EDGAR watermark when its document cannot be fetched; continue treating an indeterminate Form 6-K without qualifying metadata or readable content as incomplete.
- Rationale: An optional Regulation FD content check must not let a transient document outage block the whole ticker, while the existing Form 6-K completeness guarantee remains intact.
- Scope: EDGAR filing retrieval, Watchlist dispositions, source health, and watermark advancement.
- Implementation: pending
- Recorded against HEAD: `efcefa859325f4d551ee6e4d29808434767fa0f9`
- Supersedes: DEC-0054
- Evidence: Claude review finding and regression test in tests/test_watchlist_reliability.py on 2026-08-04.
- Privacy waivers: none

## Update — 2026-08-04 — DEC-0054

- Type: supersession
- Implementation commit: not applicable
- Superseded by: DEC-0062
- Note: Item 7.01 remains subject to official-content review, with unavailable documents now treated as an optional skipped filing rather than a ticker failure.
- Privacy waivers: none

## Update — 2026-08-04 — DEC-0062

- Type: implementation
- Implementation commit: `e4be4e4335baf3e5fd4b54e5a05167f3676fa3fa` — feat(newsletter): refine watchlist and review workflow
- Superseded by: none
- Note: Unavailable Item 7.01 documents now skip only that filing and permit the EDGAR watermark to advance; regression coverage passed.
- Privacy waivers: none

## DEC-0063 — Derive newsletter delivery exposure from edition state

- Date: 2026-08-05
- Owner: shared
- Status at record: active
- Decision: Determine whether a selected newsletter candidate was sent by joining its run to the existing production edition state, and do not duplicate delivery state on candidate rows.
- Rationale: The existing edition and per-recipient delivery records already define SMTP acceptance, while a second candidate-level state machine could drift and introduced an unsupported repair state.
- Scope: Newsletter candidate schema, edition persistence, SMTP outcome attribution, sent-story metrics, and operational diagnostics.
- Implementation: pending
- Recorded against HEAD: `99e502309063417db257beb73b129ee7046f5501`
- Supersedes: none
- Evidence: `Newsletter_trainplan.md` §§3.2, 4, 8.1, and 8.3, revised after the user-requested design review on 2026-08-05.
- Privacy waivers: none

## DEC-0064 — Calibrate newsletter review sampling from a production pilot

- Date: 2026-08-05
- Owner: shared
- Status at record: active
- Decision: Replace fixed newsletter review-slot percentages with a seven-production-day pilot, frozen per-stratum population counts, and explicit conclusive-label targets while retaining every nonempty strict-filter stratum.
- Rationale: Available production logs show that a fixed five-percent hard-reject allocation can underpower a materially large stratum and conflict with the minimum denominators required for a population estimate.
- Scope: Newsletter review batches, strata, reviewer workload, false-negative estimation, confidence reporting, and Phase N7 completion.
- Implementation: pending
- Recorded against HEAD: `99e502309063417db257beb73b129ee7046f5501`
- Supersedes: DEC-0058
- Evidence: `Newsletter_trainplan.md` §§3.5, 6.2, 7.2, and 7.5, revised after the user-requested design review on 2026-08-05.
- Privacy waivers: none

## DEC-0065 — Purge raw newsletter review fields after 30 days

- Date: 2026-08-05
- Owner: shared
- Status at record: active
- Decision: Null source-derived newsletter titles, excerpts, delivered text, direct source URLs, manual-example rationale, and reviewer notes after 30 days while retaining one-way URL or content hashes and structured metadata for their approved longer periods.
- Rationale: The clarified boundary preserves occurrence matching and aggregate evaluation without retaining raw review material longer than the active review window.
- Scope: Newsletter candidate and manual-example schema, retention cleanup, matching, exports, fixtures, and privacy tests.
- Implementation: pending
- Recorded against HEAD: `99e502309063417db257beb73b129ee7046f5501`
- Supersedes: none
- Evidence: `Newsletter_trainplan.md` §§3.2, 3.4, 6.4, 8.2, and 11.1, revised after the user-requested design review on 2026-08-05.
- Privacy waivers: none

## Update — 2026-08-05 — DEC-0058

- Type: supersession
- Implementation commit: not applicable
- Superseded by: DEC-0064
- Note: Strict-filter candidates remain mandatory, but pilot-calibrated targets replace the fixed combined twenty-percent allocation.
- Privacy waivers: none

## DEC-0066 — Gate newsletter SMTP on resumable review and history state

- Date: 2026-08-05
- Owner: shared
- Status at record: active
- Decision: Persist each production newsletter, its review frame, and a hash-checked history outbox before SMTP; retry only bounded SQLite lock failures, resume a complete edition within the same briefing date, and abandon any still-pending edition at date rollover.
- Rationale: Review persistence must not create an unmeasurable send, while transient database or history acknowledgement failures should reuse the already-rendered edition without repeating model work or risking a stale delivery.
- Scope: Newsletter preparation transactions, story history, SMTP and resend guards, CLI error handling, launchd retry behaviour, retention, and reliability tests.
- Implementation: pending
- Recorded against HEAD: `b88534311b6322f11893f4ea2fb06c653fc8ced0`
- Supersedes: none
- Evidence: `Newsletter_trainplan.md` §§3.1, 4, 8.2, 8.3, and 9, revised after the post-revision review on 2026-08-05.
- Privacy waivers: none

## DEC-0067 — Freeze the first filtered review frame before labelling

- Date: 2026-08-05
- Owner: shared
- Status at record: active
- Decision: Allow sent-story and manual-example review during the seven-day newsletter pilot, but reject every filtered-candidate adjudication until an eligible version-scoped pilot completes and an explicit immutable randomized batch is frozen.
- Rationale: Early ad hoc filtered labels could consume or bias candidates that must remain eligible for the first population-estimating sample.
- Scope: Newsletter review CLI, pilot progress, batch creation, adjudication validation, sampling, and false-negative metrics.
- Implementation: pending
- Recorded against HEAD: `b88534311b6322f11893f4ea2fb06c653fc8ced0`
- Supersedes: none
- Evidence: `Newsletter_trainplan.md` §§5, 6.2, 9, and 10, revised after the post-revision review on 2026-08-05.
- Privacy waivers: none

## DEC-0068 — Hide newsletter filter diagnostics before the initial verdict

- Date: 2026-08-05
- Owner: shared
- Status at record: active
- Decision: Present the accepted daily deck on demand during newsletter review while hiding terminal and legacy filter diagnostics until the reviewer explicitly requests details.
- Rationale: The comparative deck is required to judge finite newsletter capacity, whereas showing the pipeline's rejection reason first can anchor the human verdict on the rule under evaluation.
- Scope: Newsletter review prompt, reviewer commands, rubric application, and interaction tests.
- Implementation: pending
- Recorded against HEAD: `b88534311b6322f11893f4ea2fb06c653fc8ced0`
- Supersedes: none
- Evidence: `Newsletter_trainplan.md` §§5.1, 6.1, 9, and 16, revised after the post-revision review on 2026-08-05.
- Privacy waivers: none

## DEC-0069 — Freeze the briefing date and commit a delivery lease with history

- Date: 2026-08-05
- Owner: shared
- Status at record: active
- Decision: Capture one briefing date under the production build lock, use it for every dated artifact, abandon the edition if that date expires before history installation, and treat durable history acknowledgement as the commit point after which the exact edition proceeds to SMTP without another date check.
- Rationale: A single clock snapshot prevents mixed-date rows and files, while the delivery lease avoids abandoning an edition after history has already been mutated but before SMTP begins.
- Scope: Production CLI clock handling, pipeline inputs, dated diagnostics, newsletter runs, history outbox, rollover abandonment, SMTP, and fault tests.
- Implementation: pending
- Recorded against HEAD: `9ed7eab60c3afd8b49d14c671560a7ae7e83e7fd`
- Supersedes: none
- Evidence: `Newsletter_trainplan.md` §§3.1, 4, 8.3, 9, and 17, revised after the third-pass review on 2026-08-05.
- Privacy waivers: none

## DEC-0070 — Keep newsletter metrics scoped to the producing pipeline version

- Date: 2026-08-05
- Owner: shared
- Status at record: active
- Decision: Never pool sent, filtered, or manual-example newsletter metrics across pipeline or rubric versions; retain earlier labels as version-scoped historical evidence and stamp resolved manual examples with both versions.
- Rationale: The reviewer rubric may remain stable, but false-positive, false-negative, and miss rates measure the selector that produced or missed each story, so cross-version pooling would obscure regressions and invalidate attribution.
- Scope: Newsletter manual-example schema and review, pilot resets, metric grouping, minimum denominators, reports, retention, and Phase N7 scheduling.
- Implementation: pending
- Recorded against HEAD: `9ed7eab60c3afd8b49d14c671560a7ae7e83e7fd`
- Supersedes: none
- Evidence: `Newsletter_trainplan.md` §§3.4, 6.2, 6.4, 7, 9, 10, and 17, revised after the third-pass review on 2026-08-05.
- Privacy waivers: none

## DEC-0071 — Capture newsletter review outcomes at existing terminal decision points

- Date: 2026-08-05
- Owner: user
- Status at record: active
- Decision: Refactor only the existing quality, history, evidence, classification, duplicate, and final-selection discard points to emit durable review decision events, without redesigning briefing scoring, selection, or rendering.
- Rationale: Durable metrics require the exact terminal reason for every reviewed occurrence, but the pipeline currently discards some candidates before the final result can observe them.
- Scope: Pipeline decision-event plumbing, newsletter candidate records, persistence, and tests.
- Implementation: pending
- Recorded against HEAD: `b1b2b853d8532582fd24c17d91bca3c38c6835c4`
- Supersedes: none
- Evidence: User direction following the Newsletter training-plan implementation review on 2026-08-05.
- Privacy waivers: none

## DEC-0072 — Prefer Culture source-cap reason when both Culture constraints bind

- Date: 2026-08-06
- Owner: user
- Status at record: active
- Decision: Record `selection_source_cap` for a Culture candidate when both source and lane capacity bind; record `selection_culture_lane_cap` only when source capacity remains.
- Rationale: This preserves the selector's established constraint-check order and yields one deterministic terminal reason for review metrics.
- Scope: Final Culture selection outcomes, candidate review records, and selection precedence tests.
- Implementation: pending
- Recorded against HEAD: `72adcb70e9037de6523dbf0c307a1abf662db1e6`
- Supersedes: none
- Evidence: User confirmation on 2026-08-06; `Newsletter_trainplan.md` §4.
- Privacy waivers: none

## DEC-0073 — Order non-metric newsletter diagnostics oldest first

- Date: 2026-08-06
- Owner: user
- Status at record: active
- Decision: Present the separate non-metric newsletter diagnostic queue oldest first, while keeping population metrics limited to immutable randomized batches.
- Rationale: Oldest-first review is simple, predictable, and preserves the randomized sampling frame from diagnostic-order bias.
- Scope: Newsletter review pending queries and CLI ordering; it does not alter frozen-batch membership or metric calculations.
- Implementation: pending
- Recorded against HEAD: `72adcb70e9037de6523dbf0c307a1abf662db1e6`
- Supersedes: none
- Evidence: User direction on 2026-08-06; `Newsletter_trainplan.md` §§6.2 and 14.
- Privacy waivers: none

## DEC-0074 — Sample sent-story reviews across briefing days

- Date: 2026-08-06
- Owner: user
- Status at record: active
- Decision: Sample SMTP-accepted sent stories across multiple briefing days instead of requiring complete review of selected daily decks.
- Rationale: Sampling across days provides broader coverage and avoids treating one highly correlated day's deck as a representative precision sample.
- Scope: Sent-story pending queries, review-loop ordering, review limits, and newsletter false-positive metrics.
- Implementation: pending
- Recorded against HEAD: `72adcb70e9037de6523dbf0c307a1abf662db1e6`
- Supersedes: none
- Evidence: User direction on 2026-08-06; `Newsletter_trainplan.md` §14.
- Privacy waivers: none

## DEC-0075 — Start newsletter review history clean

- Date: 2026-08-06
- Owner: user
- Status at record: active
- Decision: Do not import historical newsletter JSON logs into the review corpus; begin with durable candidate records captured by the current pipeline.
- Rationale: Older logs lack reliable terminal decision stages and occurrence identities, so importing them would weaken review attribution even if excluded from metrics.
- Scope: Newsletter backfill, review corpus eligibility, pilot framing, and import commands.
- Implementation: pending
- Recorded against HEAD: `72adcb70e9037de6523dbf0c307a1abf662db1e6`
- Supersedes: none
- Evidence: User direction on 2026-08-06; `Newsletter_trainplan.md` §§3.6 and 14.
- Privacy waivers: none

## DEC-0076 — Keep the initial per-category sent-story label floor at 15

- Date: 2026-08-06
- Owner: user
- Status at record: active
- Decision: Require at least 15 sent-story labels per category initially, including Culture, and reassess only after a version-scoped pilot.
- Rationale: A common initial floor keeps the first review window comparable; Culture's lane-diversity behavior should justify a higher threshold with observed evidence rather than speculation.
- Scope: Sent-story review coverage, per-category false-positive reporting, pilot thresholds, and future metric-policy changes.
- Implementation: pending
- Recorded against HEAD: `72adcb70e9037de6523dbf0c307a1abf662db1e6`
- Supersedes: none
- Evidence: User direction on 2026-08-06; `Newsletter_trainplan.md` §§7.5 and 14.
- Privacy waivers: none

## DEC-0077 — Persist production sends made with OpenAI disabled

- Date: 2026-08-06
- Owner: user
- Status at record: active
- Decision: Persist and tag production newsletter sends made with `--openai-mode off` for review rather than omitting them.
- Rationale: Budget-constrained or degraded-mode days are important operational evidence; version-scoped metrics prevent them from being inappropriately pooled with incompatible runs.
- Scope: Newsletter run persistence, candidate capture, review eligibility, version tagging, and reports.
- Implementation: pending
- Recorded against HEAD: `72adcb70e9037de6523dbf0c307a1abf662db1e6`
- Supersedes: none
- Evidence: User direction on 2026-08-06; `Newsletter_trainplan.md` §14.
- Privacy waivers: none

## DEC-0078 — Discard generated August 5 newsletter run artifacts

- Date: 2026-08-06
- Owner: user
- Status at record: active
- Decision: Discard the generated August 5 newsletter run artifacts and restore the generated lock and story-history files to their tracked state.
- Rationale: These local run outputs are not part of the intended implementation change and should not remain as ambiguous working-tree state.
- Scope: August 5 category-assignment, compression-audit, quality-rejection, skipped-story, lock, and story-history artifacts.
- Implementation: pending
- Recorded against HEAD: `72adcb70e9037de6523dbf0c307a1abf662db1e6`
- Supersedes: none
- Evidence: User direction on 2026-08-06; prior handoff outstanding task.
- Privacy waivers: none

## Update — 2026-08-06 — DEC-0063

- Type: implementation
- Implementation commit: `9952128ff5cae1a20abe1f4bc536d41eaf2c29fa` — feat: implement newsletter review workflow
- Superseded by: none
- Note: Candidate review eligibility derives reader exposure by joining the linked production edition state.
- Privacy waivers: none

## Update — 2026-08-06 — DEC-0064

- Type: implementation
- Implementation commit: `9952128ff5cae1a20abe1f4bc536d41eaf2c29fa` — feat: implement newsletter review workflow
- Superseded by: none
- Note: The store freezes version-scoped filtered populations, targets, seed, and sampled candidate IDs after a seven-day pilot.
- Privacy waivers: none

## Update — 2026-08-06 — DEC-0065

- Type: implementation
- Implementation commit: `9952128ff5cae1a20abe1f4bc536d41eaf2c29fa` — feat: implement newsletter review workflow
- Superseded by: none
- Note: Retention redacts raw review fields after 30 days and expires dependent newsletter records safely.
- Privacy waivers: none

## Update — 2026-08-06 — DEC-0066

- Type: implementation
- Implementation commit: `9952128ff5cae1a20abe1f4bc536d41eaf2c29fa` — feat: implement newsletter review workflow
- Superseded by: none
- Note: Newsletter preparation, history acknowledgement, and stale-history abandonment remain guarded before delivery.
- Privacy waivers: none

## Update — 2026-08-06 — DEC-0067

- Type: implementation
- Implementation commit: `9952128ff5cae1a20abe1f4bc536d41eaf2c29fa` — feat: implement newsletter review workflow
- Superseded by: none
- Note: Filtered labels are accepted only for candidates in an explicit frozen batch.
- Privacy waivers: none

## Update — 2026-08-06 — DEC-0068

- Type: implementation
- Implementation commit: `9952128ff5cae1a20abe1f4bc536d41eaf2c29fa` — feat: implement newsletter review workflow
- Superseded by: none
- Note: The review loop exposes accepted deck context and filtered diagnostics only on explicit commands.
- Privacy waivers: none

## Update — 2026-08-06 — DEC-0069

- Type: implementation
- Implementation commit: `9952128ff5cae1a20abe1f4bc536d41eaf2c29fa` — feat: implement newsletter review workflow
- Superseded by: none
- Note: The committed workflow retains the frozen-date history lease and rollover cleanup path.
- Privacy waivers: none

## Update — 2026-08-06 — DEC-0071

- Type: implementation
- Implementation commit: `9952128ff5cae1a20abe1f4bc536d41eaf2c29fa` — feat: implement newsletter review workflow
- Superseded by: none
- Note: Existing terminal pipeline decisions are persisted as durable newsletter review records.
- Privacy waivers: none

## Update — 2026-08-06 — DEC-0072

- Type: implementation
- Implementation commit: `9952128ff5cae1a20abe1f4bc536d41eaf2c29fa` — feat: implement newsletter review workflow
- Superseded by: none
- Note: Final selection captures one deterministic terminal reason, including the settled Culture precedence.
- Privacy waivers: none

## Update — 2026-08-06 — DEC-0073

- Type: implementation
- Implementation commit: `9952128ff5cae1a20abe1f4bc536d41eaf2c29fa` — feat: implement newsletter review workflow
- Superseded by: none
- Note: Non-metric pending evaluation rows are ordered oldest first.
- Privacy waivers: none

## Update — 2026-08-06 — DEC-0075

- Type: implementation
- Implementation commit: `9952128ff5cae1a20abe1f4bc536d41eaf2c29fa` — feat: implement newsletter review workflow
- Superseded by: none
- Note: The review corpus begins only with durable current-pipeline candidate records; no historical-log backfill is added.
- Privacy waivers: none

## Update — 2026-08-06 — DEC-0077

- Type: implementation
- Implementation commit: `9952128ff5cae1a20abe1f4bc536d41eaf2c29fa` — feat: implement newsletter review workflow
- Superseded by: none
- Note: Production newsletter runs retain their OpenAI mode for review and reporting eligibility decisions.
- Privacy waivers: none

## DEC-0079 — Canonical URLs define clustering deduplication identity

- Date: 2026-08-30
- Owner: agent
- Status at record: active
- Decision: Cluster deduplication will use each article's normalized canonical URL when available, falling back to its feed URL.
- Rationale: The newsletter review store identifies candidates by the normalized canonical URL set, so clustering must use the same identity to prevent duplicate candidate rows from aborting delivery.
- Scope: General-news clustering and newsletter candidate persistence reliability.
- Implementation: pending
- Recorded against HEAD: `429cc6ee8a5e943977f56bf96fae1d9430fcd7a1`
- Supersedes: none
- Evidence: Failed scheduled-run trace on 2026-08-30 and `src/news_agent/cluster.py`.
- Privacy waivers: none

## DEC-0080 — Candidate persistence coalesces duplicate logical occurrences

- Date: 2026-08-30
- Owner: agent
- Status at record: active
- Decision: Newsletter candidate construction will keep one record for each `(candidate_kind, story_key)` identity within a run.
- Rationale: The durable schema intentionally permits only one occurrence of a logical candidate per run; duplicate feed aliases, including pre-clustering hard rejections, must not abort delivery.
- Scope: Newsletter review candidate construction and email-delivery reliability.
- Implementation: pending
- Recorded against HEAD: `94d22f64470701a9c35c7b60225b544b8a360868`
- Supersedes: none
- Evidence: Failed scheduled-run trace on 2026-08-30 and `src/news_agent/newsletter_review.py`.
- Privacy waivers: none

## DEC-0081 — Place the Watchlist immediately after the briefing index

- Date: 2026-09-01
- Owner: user
- Status at record: active
- Decision: Render the compact Watchlist immediately after `In This Briefing` and before all editorial news sections.
- Rationale: Tracked prices and ticker-specific disclosures are a priority view and should be visible near the top without repeated quiet-day prose.
- Scope: Morning Briefing HTML ordering and Watchlist presentation.
- Implementation: pending
- Recorded against HEAD: `eef14221078c9ee08cdf97cdd2edd9ba05bfe9be`
- Supersedes: none
- Evidence: User-approved Watchlist formatting and placement in the Codex task on 2026-09-01.
- Privacy waivers: none

## DEC-0082 — Write native email dry runs as formatted HTML previews

- Date: 2026-09-01
- Owner: user
- Status at record: active
- Decision: Native email dry runs will write the complete production-rendered newsletter HTML, including the Watchlist, to `preview.html` while retaining the plain-text console output and sending no email.
- Rationale: The full newsletter layout must be inspectable during a dry run without requiring delivery to an email client.
- Scope: Native email CLI dry-run output and local preview workflow.
- Implementation: pending
- Recorded against HEAD: `eef14221078c9ee08cdf97cdd2edd9ba05bfe9be`
- Supersedes: none
- Evidence: User-requested dry-run formatting behavior in the Codex task on 2026-09-01.
- Privacy waivers: none

## DEC-0083 — Link briefing index entries to their sections

- Date: 2026-09-01
- Owner: user
- Status at record: active
- Decision: Each category label and lead headline in `In This Briefing` will link to a stable internal anchor immediately before the matching newsletter section.
- Rationale: Readers should be able to jump directly from the briefing index to the category that interests them while retaining a harmless static fallback in clients that ignore internal anchors.
- Scope: Morning Briefing HTML index and category section headings.
- Implementation: pending
- Recorded against HEAD: `66bd7cfb21e8eeb02c2dc87556e3d1922c4e9114`
- Supersedes: none
- Evidence: User-approved internal category navigation design in the Codex task on 2026-09-01.
- Privacy waivers: none

## DEC-0084 — Use a non-interactive compact presentation on mobile

- Date: 2026-09-01
- Owner: user
- Status at record: active
- Decision: At viewport widths of 600 pixels or less, the newsletter will hide internal briefing-index links and the jump prompt, show equivalent plain-text index entries, and render story headlines at the 14-pixel body-text size while preserving the existing desktop presentation.
- Rationale: Gmail mobile does not reliably navigate internal email anchors, and smaller headlines improve mobile readability without changing the desktop newsletter.
- Scope: Morning Briefing responsive HTML, briefing index interaction, and story-headline typography.
- Implementation: pending
- Recorded against HEAD: `252393afbc8351d47246e821961bf0dd3510ecb4`
- Supersedes: none
- Evidence: User-approved mobile newsletter design in the Codex task on 2026-09-01 and `tests/test_mailer.py`.
- Privacy waivers: none

## Update — 2026-09-01 — DEC-0083

- Type: implementation
- Implementation commit: `252393afbc8351d47246e821961bf0dd3510ecb4` — Link briefing index entries to newsletter sections
- Superseded by: none
- Note: The committed renderer links briefing labels and lead headlines to stable category-section anchors.
- Privacy waivers: none

## DEC-0085 — Enforce a $100 monthly hard cap for NewsAgent API usage

- Date: 2026-09-01
- Owner: user
- Status at record: active
- Decision: The OpenAI NewsAgent project will enforce a $100 monthly API spend limit.
- Rationale: A project-level hard cap limits exposure if its API key is leaked while preserving sufficient capacity for normal daily briefings.
- Scope: OpenAI Platform project billing controls and NewsAgent API-key exposure.
- Implementation: pending
- Recorded against HEAD: `652086a455dafd18f277800f0cc015d959bdfc13`
- Supersedes: none
- Evidence: User direction in this Codex task on 2026-09-01.
- Privacy waivers: none

## DEC-0086 — Lead Watchlist disclosures with the underlying event

- Date: 2026-09-01
- Owner: user
- Status at record: active
- Decision: Watchlist disclosure rows will lead with a plain-English description derived from official filing content, combine same-day filings that represent the same event, and display the SEC form and acceptance time only as secondary metadata.
- Rationale: Form labels such as `6-K` and generic phrases such as `material filing` do not tell average readers what the company actually announced.
- Scope: EDGAR material-filing discovery, disclosure deduplication, Watchlist HTML and plain-text rendering, stored run metadata, and legacy rerender compatibility.
- Implementation: pending
- Recorded against HEAD: `652086a455dafd18f277800f0cc015d959bdfc13`
- Supersedes: none
- Evidence: User-approved Watchlist disclosure wording in the Codex task on 2026-09-01 and related renderer and discovery tests.
- Privacy waivers: none

## Update — 2026-09-01 — DEC-0084

- Type: implementation
- Implementation commit: `652086a455dafd18f277800f0cc015d959bdfc13` — Improve mobile newsletter presentation
- Superseded by: none
- Note: The committed renderer provides mobile-only static briefing entries, hides the jump prompt, and reduces story-headline size while preserving desktop behavior.
- Privacy waivers: none

## DEC-0087 — Stack briefing-index headlines below categories on mobile

- Date: 2026-09-01
- Owner: user
- Status at record: active
- Decision: At viewport widths of 600 pixels or less, each `In This Briefing` entry will show its non-clickable category label followed by the lead story headline beneath it in one stacked block, while desktop retains the linked two-column index.
- Rationale: Long lead headlines are easier to scan on narrow screens when they use the full width beneath their category instead of starting in a narrow adjacent column.
- Scope: Morning Briefing responsive HTML and mobile briefing-index presentation.
- Implementation: pending
- Recorded against HEAD: `a277094c375cd0423535cc02eff8075e5f869697`
- Supersedes: none
- Evidence: User-approved bounded design in the Codex task on 2026-09-01 and the mobile briefing-index regression test.
- Privacy waivers: none

## DEC-0088 — Skip completed scheduled editions before billable pipeline work

- Date: 2026-09-01
- Owner: user
- Status at record: active
- Decision: A scheduled email invocation must exit before news retrieval and OpenAI processing when a production edition for the current briefing date has SMTP acceptance for every configured recipient, while incomplete deliveries remain eligible for retry and pending Gate A failure alerts retain precedence.
- Rationale: The four scheduled retry triggers should not repeat potentially billable pipeline work after the daily newsletter has already reached every recipient.
- Scope: Scheduled email CLI orchestration, production-delivery state queries, recurring API cost, and regression tests.
- Implementation: pending
- Recorded against HEAD: `539c30e9353c28608ee1451a77586ce97a57d941`
- Supersedes: none
- Evidence: User-approved bounded design in the Codex task on 2026-09-01 and the scheduled-delivery regressions in `tests/test_cli.py` and `tests/test_mailer.py`.
- Privacy waivers: none

## DEC-0089 — Use an explicit dark newsletter theme

- Date: 2026-09-01
- Owner: user
- Status at record: active
- Decision: Normal newsletter rendering will use an explicit dark palette on desktop and mobile, declare a dark email color scheme, and convert stored light-theme watchlist fragments when they are re-rendered.
- Rationale: The newsletter should have the same intentional dark appearance across desktop and mobile instead of depending on inconsistent Gmail color inversion.
- Scope: Newsletter HTML colors, responsive presentation, Gmail color-scheme hints, stored-edition format previews, and renderer regression tests.
- Implementation: pending
- Recorded against HEAD: `3fb07b4cee8a9aebbe5858a709d3451782478ce0`
- Supersedes: none
- Evidence: User-approved bounded design in the Codex task on 2026-09-01 and the dark-theme regression in `tests/test_mailer.py`.
- Privacy waivers: none

## DEC-0090 — Let email clients select presentation from a light base

- Date: 2026-09-01
- Owner: user
- Status at record: active
- Decision: Normal newsletter rendering will use the approved light palette without forced color-scheme metadata so Gmail and other email clients can apply their own user-selected theme behavior.
- Rationale: Email HTML cannot reliably read a recipient's Gmail theme, and the user prefers client-controlled presentation over a newsletter that forces dark colors everywhere.
- Scope: Newsletter HTML colors, Gmail theme behavior, responsive desktop and mobile presentation, preview rendering, and renderer regression tests.
- Implementation: pending
- Recorded against HEAD: `03a8f534d9c3a8e3ad9caadd614e1c22cc8bdeb7`
- Supersedes: DEC-0089
- Evidence: User-approved bounded reversal in the Codex task on 2026-09-01 and the light-base renderer regression in `tests/test_mailer.py`.
- Privacy waivers: none

## Update — 2026-09-01 — DEC-0089

- Type: supersession
- Implementation commit: not applicable
- Superseded by: DEC-0090
- Note: The forced dark newsletter theme is replaced by a light base that leaves theme adaptation to the recipient's email client.
- Privacy waivers: none
