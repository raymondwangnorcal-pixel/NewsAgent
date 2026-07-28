# NewsAgent: Conversational Delivery and Personalized Email Digest

> **Status:** Superseded for V1 implementation by
> [email-restructuring.md](../plans/email-restructuring.md). This document
> remains the long-term product vision only.

## Purpose

NewsAgent is being extended from a single daily briefing pipeline into two
distinct subscriber experiences that share the same editorial foundation:

1. **Folk-style NewsAgent**: a concise, conversational experience delivered
   through Telegram, SMS, and potentially an invite-only iMessage integration.
   It should feel like a familiar contact who sends a morning briefing and can
   answer follow-up questions in the same conversation.
2. **Email Newsletter**: a richer daily newsletter delivered to an inbox. It
   can be longer, include more context and source links, and become highly
   personalized around a subscriber's interests and watchlist.

The two experiences should live in the same repository. They should *not*
duplicate the logic that finds, enriches, clusters, scores, and stores news.
The product difference is primarily in personalization, presentation, delivery,
and interaction.

## Product Direction

The Folk-style product follows the direction described in the repository's
current channel strategy:

- Telegram can provide a low-cost, full chat-native briefing.
- SMS should normally be a compact GSM-safe teaser plus a link to the full
  briefing, because sending a full edition by SMS is too expensive.
- iMessage is an experimental, opt-in future channel rather than a dependency;
  it does not have a standard broadly available API appropriate for a daily
  broadcast product.
- Users can reply to ask for an explanation, request context, or change their
  preferences.

Email is a complementary product, not merely an export of the mobile message.
It has a much lower marginal delivery cost and can therefore include deeper
story context, more links, longer sections, and subscriber-specific coverage.

## Repository Structure and Migration Principle

Use a monorepo with a shared editorial foundation and separate product-facing
folders. However, do **not** perform a big-bang rename of the existing
`src/news_agent/` package. The repository currently has one working package
and an established test suite; the restructuring must preserve both while new
behavior is introduced and proven.

The first implementation stages keep the current `src/news_agent/` import path
and add structured-edition behavior inside it. The layout below is the intended
end state, reached by incremental extraction only after the corresponding code
is stable and covered by tests.

```text
src/
  news_agent_core/                 # Shared editorial and data foundation
    sourcing/                      # RSS/API retrieval and article normalization
    enrichment/                    # Article extraction and source metadata
    clustering/                    # Story grouping and duplicate handling
    scoring/                       # Quality, impact, category, and watchlist scores
    editions/                      # Structured daily edition generation and storage
    models/                        # Shared Story, Edition, Article, and entity models
    profiles/                      # Subscriber records, channels, and preferences
    selection/                     # Thin deterministic topic/section filtering

  folk_agent/                      # Concise conversational product
    delivery/                      # Telegram, SMS/Telnyx, future iMessage adapters
    rendering/                     # Channel-specific short formats and teaser links
    conversations/                 # Inbound webhooks, retrieval, and reply routing

  email_newsletter/                # Long-form personalized digest product
    rendering/                     # HTML/text email templates and section layouts
    delivery/                      # Email provider integration, send and bounce events
    digest/                        # Email-specific depth, layout, and content rules
    watchlists/                    # Entity retrieval, dossiers, and email assembly

apps/
  api/                             # Python API deployable; routes, auth, and webhooks
  web/                             # Separate website deployable; settings and briefings

tests/
  ...
```

The dependency direction should remain one-way:

```text
news_agent_core -> folk_agent
                -> email_newsletter

apps/api -> news_agent_core, folk_agent, email_newsletter
apps/web -> apps/api
```

Neither product imports the other's rendering, delivery, or product-specific
assembly code. Both consume the same structured edition and profile data. The
only shared personalization logic is inexpensive, deterministic selection of
enabled sections, topics, exclusions, and verbosity. Entity retrieval,
watchlist scoring, dossier generation, and email assembly remain inside
`email_newsletter/`; they are not a dependency of Folk.

`apps/api` and `apps/web` are sibling deployables. The API owns authentication,
website-facing profile endpoints, and incoming channel webhooks; the website is
a client of that API. This makes the service boundary explicit rather than
placing one deployable inside the shared Python source tree and the other at the
repository root.

## Safe Incremental Restructure

The target layout is not the first commit. Separate behavioral changes from
mechanical moves and keep the suite green throughout.

1. **Establish a baseline.** Record the existing test results and representative
   Telegram/SMS output fixtures before moving production modules.
2. **Add structured editions in place.** Introduce `Edition`, `Story`, source,
   and entity models under the existing `src/news_agent/` package. Keep current
   renderers working by deriving their existing formatted strings from the
   structured result. This is a behavior change with no package rename.
3. **Prove one existing channel.** Run the current Telegram path against the
   persisted structured edition, compare it with approved output fixtures, and
   retain the current CLI contract.
4. **Add profiles and the website/API contract in place.** Add subscriber,
   channel, and preference storage without moving the sourcing pipeline.
5. **Add email as an isolated product module.** Implement its renderer and
   watchlist enrichment behind its own feature/configuration boundary. It must
   consume the persisted edition rather than fork the pipeline.
6. **Extract only stable seams.** Move a cohesive, well-tested area into
   `news_agent_core`, `folk_agent`, or `email_newsletter` in a dedicated
   mechanical commit. Migrate its tests in the same change.
7. **Retire compatibility imports deliberately.** Temporary re-export shims
   preserve old `news_agent.*` imports during migration. Remove a shim only
   after all internal callers and tests have moved, in a separately reviewable
   cleanup change.

This sequence allows every change to be reviewed, tested, and reverted
independently. It also avoids tying the structured-edition model to an
all-at-once filesystem reorganization.

### Test Migration Rules

- Existing tests continue to import the current module paths until the module
  they cover is moved.
- Every new structured-edition behavior has focused unit tests plus renderer
  regression/snapshot tests for the existing Telegram and SMS formats.
- A file move migrates its tests in the same pull request; it must not leave a
  temporarily broken suite behind.
- Compatibility shims have their own small import tests and a tracked removal
  condition, so they do not silently become permanent architecture.

## Shared Editorial Foundation

The current sourcing and scoring pipeline remains a single system. It should
generate and persist a structured daily edition rather than only final message
strings. A story should retain enough information for multiple formats:

```text
story
  id, edition_id, section, title, short_blurb, detailed_context,
  why_it_matters, source_urls, source_metadata, tags, entities,
  published_at, quality_score, importance_score
```

This produces one reliable daily news corpus and one set of editorial decisions
that all experiences can build upon. It avoids a situation where email and
mobile subscribers receive inconsistent rankings or where improvements to
source quality must be implemented twice.

## Folk-Style Experience

The Folk-style product is optimized for immediate consumption and conversation:

- **Telegram:** can receive a fuller chat-native briefing at near-zero delivery
  cost.
- **SMS:** receives a short, GSM-safe summary and a branded link to the daily
  briefing page. SMS is a paid or limited fallback because segment costs grow
  quickly with message length.
- **iMessage:** may be tested as an opt-in beta only after a reliable and
  permitted two-way transport is proven.
- **Replies:** a subscriber can ask about a story, request deeper context,
  compare it with yesterday, pause a topic, or change the next delivery time.

The conversational retrieval layer should first answer from the current and
recent saved editions. Expensive live research should be optional, metered, and
clearly separated from the included briefing experience.

The Folk renderer uses concise fields such as the title, short blurb, and a
link. It should not attempt to place a full 5--10 minute edition in an SMS.

## Personalized Email Newsletter

The email newsletter can be longer because delivery is inexpensive. It should
not simply send every story to every reader; it should combine a broadly
important daily edition with personalized coverage.

For example, a subscriber who tracks Apple can receive the normal important
finance stories plus a dedicated **Apple Watch** block containing relevant
coverage of Apple developments, a short explanation of what changed, and links
to the sources.

Suggested email content layers:

1. **Top stories:** major stories selected from the shared edition.
2. **Section coverage:** a subscriber-specific selection for Finance, Tech,
   U.S., Global, and Culture according to their enabled sections and topic
   preferences.
3. **Watchlist updates:** company, person, industry, place, or ticker-specific
   stories such as Apple/AAPL.
4. **Context:** detailed story summaries, `why it matters`, relevant history,
   and source links.
5. **Daily briefing link:** a private web version with the full edition and
   controls for changing preferences.

Per-channel settings should allow a single user to choose a terse Folk
experience and a deep email experience at the same time. Examples include
`folk_verbosity`, `email_verbosity`, and `email_story_limit`.

## Watchlist-Aware Retrieval and Scraping

Custom emails should not cause the system to run a full scraper independently
for every subscriber. Instead, use a shared watchlist enrichment process:

```text
Subscriber watchlists
  -> aggregate unique tracked entities (Apple/AAPL, Nvidia, Federal Reserve)
  -> retrieve relevant coverage once per unique entity
  -> normalize, deduplicate, score, and cache the articles
  -> produce a daily entity dossier
  -> assemble each subscriber's personalized email
```

The normal general-news pipeline remains responsible for broad reporting. The
email product's watchlist retrieval process supplements it with targeted
searches or feeds for entities currently tracked by at least one active
subscriber. It remains inside `email_newsletter/watchlists/` because it is a
cost-bearing email feature, not a shared Folk dependency.

Relevant data entities include:

```text
tracked_entities
  id, canonical_name, entity_type, ticker, aliases

user_watchlists
  user_id, entity_id, importance, enabled, section

entity_articles
  entity_id, article_id, relevance_score, topic, discovered_at

entity_dossiers
  entity_id, date, summary, prior_context, generated_at
```

Important safeguards:

- Retrieve and summarize each unique tracked entity only once per run.
- Deduplicate entity results against the general daily corpus and across query
  aliases such as `Apple` and `AAPL`.
- Cap the number of active tracked entities per subscriber and globally.
- Only include a watchlist section when there is enough credible and materially
  relevant recent coverage; do not fill newsletters with empty updates.
- Preserve recent entity history (for example, 7--30 days) so explanations of
  what changed are grounded in earlier coverage.
- Reserve a limited number of personalized slots per section so a watchlist
  interest supplements, rather than crowds out, major news.

### Retrieval Budget and Admission Controls

Watchlist enrichment is an external-cost and rate-limit boundary. It must be
disabled by default until the following controls are configured and enforced:

```text
MAX_TRACKED_ENTITIES_PER_USER
MAX_FRESH_ENTITY_RETRIEVALS_PER_RUN
MAX_WATCHLIST_RETRIEVAL_COST_PER_RUN_USD
MAX_CANDIDATE_ARTICLES_PER_ENTITY
ENTITY_RETRIEVAL_CACHE_TTL_HOURS
ENTITY_DOSSIER_HISTORY_DAYS
```

The suggested initial operating values are intentionally conservative:

| Control | Suggested launch value | Behavior at the limit |
|---|---:|---|
| Entities per subscriber | 10 | Require a subscriber to remove an entity before adding another. |
| Fresh entity retrievals per run | 100 | Defer lower-priority entities to the next run; do not exceed the cap. |
| Candidate articles per entity | 10 | Score and retain the best credible matches only. |
| Retrieval cache TTL | 24 hours | Reuse the day's result across all subscribers. |
| Dossier history | 30 days | Retain enough context for a meaningful "what changed" summary. |

The dollar budget cannot be responsibly fixed until the selected retrieval
providers and their pricing are known. Before enabling the feature, set a hard
per-run dollar cap and make the worker stop gracefully when it is exhausted.
The cap, provider mix, and overflow policy require product approval; see
**Open Decisions Requiring Approval** below.

## Subscriber Website

Build one authenticated website for both products. It is the subscriber's
control center and should update a shared profile rather than separate mobile
and email settings stores.

The website should support:

- Selecting enabled sections and topics.
- Adding entities to track: companies, tickers, people, industries, or places.
- Choosing watchlist importance: `must include`, `more coverage`, or `major
  developments only`.
- Excluding unwanted topics.
- Setting timezone, delivery days, and delivery time.
- Enabling or disabling email, Telegram, SMS, and future push/iMessage channels.
- Choosing per-channel depth: `short`, `standard`, or `deep` for email; and
  `teaser`, `short`, or `standard` for the Folk experience.
- Pausing delivery, resuming, and managing channel opt-in/out status.
- Viewing the private current and recent daily briefing pages.

The API backing the website is also the authority for inbound conversational
commands. For example, a Telegram message reading "Only send tech and finance
tomorrow" should update the same preference records as the website form.

### Preference Write Consistency

Preference changes are field-level patches, not full-record replacements.
Disjoint changes merge: a website update to delivery time must not overwrite a
Telegram change to enabled sections. For changes to the same field, the API
must provide deterministic ordering:

- Store a monotonically increasing `version` and server-side `updated_at` on
  each preference record.
- Website updates include the version they last read. A stale update receives a
  conflict response and the current value, allowing the interface to ask the
  subscriber to confirm or reload.
- Inbound commands are serialized per user and recorded as preference events.
  The response sent to the user states the resulting setting.
- A temporary instruction such as "only send tech tomorrow" is stored as a
  dated override, not as an irreversible replacement of the user's normal
  sections.

The product must choose the final policy for two simultaneous updates to the
same field. The recommended default is last server-received write wins for
channel commands, while interactive website conflicts require confirmation.

Suggested core tables:

```text
users
  id, timezone, plan, created_at

authentication_identities
  id, user_id, provider, external_subject, verified_at

delivery_channels
  id, user_id, channel, address_or_chat_id, opted_in, verified, enabled

preferences
  user_id, enabled_sections, topic_boosts, excluded_topics,
  preferred_delivery_time, folk_verbosity, email_verbosity, email_story_limit,
  version, updated_at

preference_events
  id, user_id, source, patch, created_at, applied_version

preference_overrides
  id, user_id, starts_at, ends_at, patch, created_at

daily_editions
  id, date, published_url, generated_at

stories
  id, edition_id, ...structured story fields...

conversations
messages
```

## Delivery and Assembly Flow

```text
Sources and targeted entity retrieval
  -> normalized article corpus
  -> clustering, quality, and importance scoring
  -> persisted structured daily edition and entity dossiers
  -> subscriber preference + watchlist selection
  -> channel-specific rendering
      -> concise Folk delivery and replies
      -> longer personalized email
      -> private web briefing page
```

## Long-Term Implementation Order (Post-V1)

This is the roadmap for the multi-user two-product system. It applies only
after the V1 single-user email release below; it is not a prerequisite for V1.

1. Establish output and test baselines; do not rename packages yet.
2. Add and persist structured editions, stories, sources, and entities inside
   the existing package, while retaining current rendered output.
3. Prove the structured edition with the existing Telegram path and regression
   tests.
4. Add user/profile storage and the API contract used by the website and
   channel webhooks.
5. Build the authenticated website for profile, channel, and preference
   management.
6. Implement the email renderer and delivery path as an isolated product
   module, initially using only the shared edition.
7. After cost limits are approved, add email-local watchlist retrieval and
   personalized entity dossiers.
8. Add or enhance Folk delivery adapters and conversational preference commands.
9. Extract stable modules incrementally into the target package layout; migrate
   their tests and retain temporary compatibility shims during each move.
10. Add the grounded retrieval assistant and later optional metered live-news
    research. Treat Telnyx SMS and iMessage as later transport work.

## Open Decisions Requiring Approval

The following choices affect product behavior or external cost and should be
made explicitly before implementation proceeds beyond the shared-edition and
website work:

1. **Watchlist retrieval budget:** Which retrieval/search providers will the
   email product use, and what is the maximum dollar budget per daily run?
   The suggested initial non-price limits are 10 entities per subscriber and
   100 fresh entity retrievals per run.
2. **Capacity overflow:** When the daily unique-entity cap is reached, should
   NewsAgent defer lower-priority entities to the next run (recommended), show
   a "not checked today" status, or offer a paid higher-priority tier?
3. **Same-field preference conflict:** Confirm whether the recommended policy
   is acceptable: website edits detect stale versions and require confirmation;
   concurrent channel commands use last server-received write wins, with an
   audit event and confirmation reply.
4. **Website/API technology and deployment:** The plan defines two deployable
   boundaries but does not select a web framework, authentication provider,
   database, or hosting platform. These choices should be made before creating
   the applications, rather than assumed during the package restructure.

## Architectural Principle

NewsAgent should have one editorial brain and one subscriber profile, but two
deliberately different product presentations. The email product can offer a
personalized research-style digest. The Folk product can remain compact,
low-friction, and conversational. The products share sourcing, scoring,
structured edition storage, and profile data; email alone owns the costly
watchlist retrieval and dossier machinery. The transition to this structure is
incremental, test-protected, and reversible.

## Approved V1: Single-User Personalized Email

This section records the approved first-release scope. Its requirements and
implementation order override the long-term roadmap above for V1. The website,
multi-user profile system, mobile channels, and richer watchlist infrastructure
remain planned future work and are not prerequisites for this release.

### Scope

- Deliver one personalized email to the project owner only.
- Schedule the email for **8:15 AM America/New_York**, every calendar day.
  Each edition is keyed to its local calendar date and uses the latest completed
  regular-session close; on weekends and market holidays, that is the most
  recent trading day's close.
- Run the job on the owner's own machine. If the machine starts or wakes after
  8:15 AM, it may send the *current local date's* unsent edition once. It must
  never send an unsent edition from a prior local calendar date. This prevents
  a late catch-up from arriving immediately before the next scheduled edition.
- Use personal Gmail SMTP for delivery. The initial workflow must support a
  dry run that renders the exact HTML and plain-text message and validates
  links; a separate explicit email send action performs a live delivery. Add a
  new email mode without changing existing Telegram/SMS behavior:

  ```bash
  news-briefing --email --dry-run
  news-briefing --email --send
  ```

  `--email` is mutually exclusive with the existing `--channel` delivery
  selection. Existing invocations of `news-briefing --send` retain their
  current configured Telegram/SMS behavior.
- The local, uncommitted `.env` is configured with the Gmail SMTP connection
  shape: `GMAIL_SMTP_HOST`, `GMAIL_SMTP_PORT`, `GMAIL_SMTP_USERNAME`,
  `GMAIL_SMTP_APP_PASSWORD`, `EMAIL_FROM`, and `EMAIL_TO`. Credential values
  and recipient addresses must never be copied into source, tests,
  documentation, or commits.
- `EMAIL_TO` supports a comma-separated list for the initial two-recipient
  test. Send one independently addressed copy per recipient rather than
  placing multiple recipients in a shared `To:` or `Cc:` field. Report the
  number of successful deliveries and verify each recipient independently.
- Do not build the website, authentication, generalized profiles, Telegram,
  SMS, iMessage, multi-user delivery, paid data services, or generic
  watchlist infrastructure in V1.

### Finance and Watchlist Content

- Keep the existing general-finance section unchanged.
- Add a separate **Watchlist** section after general finance.
- The initial checked-in YAML watchlist contains the following canonical
  selections, with aliases supported where useful:

  | Ticker | Company |
  |---|---|
  | `AAPL` | Apple |
  | `NVO` | Novo Nordisk |
  | `META` | Meta Platforms |

- V1 supports at most three selected companies. A selected company always
  receives a quote row, even when it has no qualifying news.
- Retrieve targeted coverage for each selected company/ticker in addition to
  the normal general-news corpus. Use targeted Google News RSS searches for
  discovery, then preserve and show the original publisher/source links in the
  email.
- A selected ticker receives a drafted summary only for an event plausibly
  capable of changing an investor's view: earnings or guidance, M&A,
  regulation or litigation, a major product or strategy shift, or a market
  move explained by a credible article. Do not independently infer an unusual
  market move from price data in V1.
- Keep each ticker block concise: synthesize the one or two most important
  qualifying events and briefly mention additional material events only when
  necessary. Include an explicit, grounded `why it matters` sentence and
  direct source links.
- An edition considers articles in the interval beginning at the last
  SMTP-accepted email edition's stored `article_window_end` and ending when the
  current edition starts. A dry run, a failed attempt, or an indeterminate SMTP
  attempt does not advance that watermark or mark articles as sent.

### Source and Summary Integrity

- Apply this policy to the new ticker-watchlist section only; it does not yet
  change the existing general-finance section.
- Treat targeted Google News RSS as a discovery mechanism, not an authority.
  The displayed citation and link must identify the original publisher.
- Admit sources using an explicit policy:
  - **Tier 1:** SEC filings, company investor-relations releases and earnings
    materials, and regulator or court releases.
  - **Tier 2:** Reuters, Associated Press, Financial Times, Wall Street
    Journal, Bloomberg, and CNBC.
  - Other sources may be discovery leads only and require corroboration by a
    Tier 1 or Tier 2 source before use.
- A single Tier 1 source or a single Tier 2 report can support a qualifying
  event. Do not present model inference as fact.
- The OpenAI-backed summarization path is allowed, but it may use only
  retrieved source text and its citations as factual input. If source text
  cannot be extracted sufficiently, or the model fails, show the linked
  qualifying headline and publisher marked **"Summary unavailable"** rather
  than generating a recap from a headline or inventing content.
- If targeted retrieval itself fails for a ticker, explicitly say that its
  news search was unavailable. Do not silently treat a failed search as no
  news.
- Include a permanent informational-only footer and prohibit buy/sell/hold or
  other investment recommendations anywhere in the digest.

### Price Data, Delivery Safety, and State

- Show every watched ticker's latest completed regular-session close, daily
  percentage change, and the actual close date.
- Use **Tiingo Free** as the primary end-of-day quote provider and **EODHD
  Free** as the backup. The backup is queried only after a primary failure.
- Retry quote retrieval for up to five minutes. If neither provider succeeds,
  use the last successfully stored close and label it with its actual date;
  never imply that a cached value is current.
- Use local SQLite to persist edition windows, seen articles, quote cache,
  delivery records, and idempotency keys. The local-date edition key has a
  database uniqueness constraint, and a process-wide file lock prevents a
  scheduled invocation and a manual invocation from preparing the same edition
  concurrently.
- Use explicit delivery states: `prepared`, `sending`, `smtp_accepted`,
  `failed`, and `indeterminate`. Persist `prepared` before work begins and set
  `sending` immediately before the SMTP call. On confirmed SMTP acceptance,
  atomically record `smtp_accepted` and advance the article watermark/seen
  state. A definite SMTP rejection becomes `failed` and may be retried within
  the same local date. A process crash or timeout during `sending` is
  `indeterminate`; do not automatically resend it because Gmail acceptance
  cannot be known. A manual resend is available only through an explicit
  `--email --resend DATE` command that warns about possible duplicate delivery.
- A dry run does not acquire an edition delivery claim, write a sent-content
  watermark, or affect later live delivery.
- Render multipart email: HTML with clickable citations and a plain-text
  alternative.

### V1 Implementation Order

1. Add the checked-in three-company YAML watchlist and focused configuration
   validation.
2. Add the email renderer and `--email` CLI mode, keeping the existing
   Telegram/SMS paths and their CLI semantics unchanged.
3. Add a Gmail SMTP sender, multipart rendering, dry-run output, and an
   explicit `--email --send` path.
4. Add the SQLite schema, local-date edition key, process lock, delivery-state
   machine, and article-window watermark rules before enabling scheduled sends.
5. Add Tiingo/EODHD end-of-day quote adapters, cache/fallback behavior, and
   quote-date labeling.
6. Add targeted Google News RSS discovery, source-tier validation, extraction,
   and the watchlist summary fallback rules.
7. Add regression, state-machine, scheduler, quote-fallback, source-integrity,
   and SMTP-failure tests; then enable the local 8:15 AM scheduler.

### Future Cost Note

V1 prioritizes zero marginal API cost because the recipient is the owner. Keep
the quote-provider boundary small so a later multi-user/commercial release can
migrate to the lowest reasonable-cost provider with appropriate display and
redistribution licensing.
