# NewsAgent: Conversational Delivery and Personalized Email Digest

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

The Folk-style product follows the direction described in
[`folk-style-pivot.md`](folk-style-pivot.md):

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

## Repository Structure

Use a monorepo with a shared core and separate product-facing folders. The
current pipeline should become the shared core rather than being placed inside
either delivery product.

```text
src/
  news_agent_core/                 # Shared editorial and data foundation
    sourcing/                      # RSS/API retrieval and article normalization
    enrichment/                    # Article extraction and source metadata
    clustering/                    # Story grouping and duplicate handling
    scoring/                       # Quality, impact, category, and watchlist scores
    editions/                      # Structured daily edition generation and storage
    models/                        # Shared Story, Edition, Article, and entity models

  personalization/                 # Shared subscriber-specific assembly
    preferences/                   # Topics, exclusions, verbosity, schedules
    watchlists/                    # Entity tracking and relevance matching
    assembly/                      # Builds a subscriber's selected story set

  folk_agent/                      # Concise conversational product
    delivery/                      # Telegram, SMS/Telnyx, future iMessage adapters
    rendering/                     # Channel-specific short formats and teaser links
    conversations/                 # Inbound webhooks, retrieval, and reply routing

  email_newsletter/                # Long-form personalized digest product
    rendering/                     # HTML/text email templates and section layouts
    delivery/                      # Email provider integration, send and bounce events
    digest/                        # Email-specific depth, layout, and content rules

  api/                             # Authenticated API used by the website and webhooks

apps/
  web/                             # Subscriber website for settings and daily briefings

tests/
  ...
```

The dependency direction should remain one-way:

```text
news_agent_core -> personalization -> folk_agent
                                   -> email_newsletter
                                   -> api / web
```

Neither `folk_agent` nor `email_newsletter` should import each other's
rendering or delivery code. Both should consume the same structured edition and
the same saved user preferences.

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
watchlist retrieval process supplements it with targeted searches or feeds for
entities currently tracked by at least one active subscriber.

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

Suggested core tables:

```text
users
  id, email, timezone, plan, created_at

delivery_channels
  id, user_id, channel, address_or_chat_id, opted_in, verified, enabled

preferences
  user_id, enabled_sections, topic_boosts, excluded_topics,
  preferred_delivery_time, folk_verbosity, email_verbosity, email_story_limit

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

## Recommended Implementation Order

1. Refactor the existing pipeline so it persists structured editions, stories,
   sources, and entities rather than only formatted messages.
2. Introduce the shared data model for users, delivery channels, preferences,
   and watchlists.
3. Add the shared personalization/assembly layer and watchlist enrichment
   process.
4. Build the authenticated website and API for profile, channel, and watchlist
   management.
5. Implement personalized email rendering and delivery, including the
   watchlist-aware section blocks.
6. Implement the Folk delivery adapters and conversational preference commands.
7. Add the grounded retrieval assistant and, later, optional metered live-news
   research.
8. Treat Telnyx SMS and iMessage as later transport work; neither should block
   the shared core, email, website, or Telegram paths.

## Architectural Principle

NewsAgent should have one editorial brain and one subscriber profile, but two
deliberately different product presentations. The email product can offer a
personalized research-style digest. The Folk product can remain compact,
low-friction, and conversational. Shared sourcing, scoring, structured edition
storage, preference data, and entity dossiers keep those products consistent
and economical as they grow.
