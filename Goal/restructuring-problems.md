# NewsAgent: Restructuring Problems

## Purpose

This document records concerns with the repository restructuring proposed in
[`mobile-split-email.md`](mobile-split-email.md), evaluated against the current
codebase and against the revised direction in
[`folk-style-pivot.md`](folk-style-pivot.md). The product strategy in both
planning documents is sound. The concerns here are almost entirely about
*execution risk in how the restructure is carried out*, not about the direction
of the pivot.

## Current State

Today the project is a single flat Python package, `src/news_agent/`, with
roughly 27 modules (sourcing, enrichment, clustering, scoring, formatting,
pipeline, SMS, and so on) and about 28 test files that import those module
paths directly. There is no `personalization`, `folk_agent`, `email_newsletter`,
`api`, or `apps/web` package yet. The restructure therefore is not a small
addition; it moves and renames essentially the whole codebase.

`mobile-split-email.md` proposes moving this package into a shared
`news_agent_core/` and splitting the rest across new top-level packages:
`personalization/`, `folk_agent/`, `email_newsletter/`, `api/`, and a separate
`apps/web/`.

## What the Revised folk-style-pivot.md Resolved

Two earlier cross-document concerns are now closed and are recorded here only so
they are not re-raised:

- **Build order no longer contradicts.** Both documents now sequence work the
  same way: structured editions, then the shared data model and subscriber
  website / preferences, then channel delivery. Earlier the two documents
  disagreed about whether Telegram or the website came first.
- **Product boundary is clean.** Email is now explicitly a separate product and
  not a Folk delivery channel. The `users` record dropped its `email` field, and
  the delivery tiers no longer list email. The two products share an editorial
  foundation and a subscriber profile but have distinct presentations.

The remaining problems below are unaffected by that revision.

## Concern 1: A Big-Bang Move With No Migration Path

The proposal moves the entire existing pipeline into `news_agent_core/` and
simultaneously splits it across five new packages. Implementation step 1 also
bundles a behavior change — persisting structured editions instead of formatted
strings — into the same step as the move.

Mixing a large mechanical rename with a semantic change is the hardest kind of
change to review and the easiest to break silently. There is no incremental or
strangler path described: the codebase is expected to arrive in the new shape in
essentially one motion.

Recommended instead: introduce the structured-edition model inside the current
`src/news_agent/` package first and prove it with one channel (Telegram) before
carving out `news_agent_core/` and the product folders. Separate the "change
behavior" commits from the "move files" commits so each can be reviewed and
reverted independently.

## Concern 2: All Tests Break at Once

The roughly 28 existing test files import the current module paths. A move of
this size breaks the entire suite simultaneously, and the proposal does not
describe how tests migrate, whether a compatibility shim is provided, or how the
suite stays green during the transition.

The test suite is the safety net that makes a move this large survivable. It
should be migrated incrementally alongside the code, or preserved through
temporary import shims, rather than broken in a single step.

## Concern 3: `personalization/` Should Not Be a Shared Root

`mobile-split-email.md` places `personalization/` as a mandatory layer between
`news_agent_core` and *both* products
(`core -> personalization -> folk / email`). With email now fully separated as
the only product that needs watchlists and entity dossiers, this shared layer is
misplaced.

The core issue is that "personalization" means two different things in the two
products:

- **Folk personalization is selection.** Given the one shared edition, filter to
  a subscriber's enabled sections, topics, exclusions, and verbosity. It is
  cheap, deterministic, in-memory, and reads data that already exists.
- **Email personalization is generation.** Aggregate watchlist entities, run
  per-entity retrieval or scraping, build entity dossiers, deduplicate against
  the corpus, and reserve section slots. It can call external sources, costs
  money, and carries rate-limit risk.

Putting both under one shared root causes four problems:

1. **It over-couples Folk to machinery it never runs.** A shared root declares
   personalization a stable foundation both products build on, but watchlists
   and dossiers are an email feature. Folk would import a package whose surface
   is mostly email code it never calls.
2. **It shares a blast radius and release cadence.** Once Folk depends on the
   shared layer, an email-driven change (a new dossier schema, a watchlist
   scoring tweak) forces Folk to be retested and redeployed even though its
   behavior did not change.
3. **It hides the cost boundary the pivot depends on.** The watchlist retrieval
   process is the single largest new cost surface, and it scales with the number
   of tracked entities rather than users. Burying it in a "shared" layer makes it
   look free to call from anywhere. It belongs inside `email_newsletter/`, next
   to the one consumer that pays for it.
4. **It muddies the layering claim.** A shared root implies both products
   genuinely build on the layer. Only email does. Promoting a product feature
   into the foundation is precisely the premature abstraction this document
   warns against.

What the two products actually share is *data*, not *logic*: one subscriber
profile and one set of preferences. Share that in `news_agent_core` — the
`users`, `preferences`, `delivery_channels`, and `tracked_entities` models, plus
the thin section/topic selection. Put watchlist retrieval, entity dossiers, and
email assembly under `email_newsletter/`. If Folk later grows real watchlist
needs, promote that logic into a shared layer *then*, when a second consumer
actually exists.

## Concern 4: Watchlist Retrieval Is an Unsized Cost Surface

`mobile-split-email.md` correctly flags that custom emails must not run a full
scraper per subscriber, and proposes aggregating unique entities and retrieving
once per entity. That mitigation is sound, but it is itself a whole new
retrieval subsystem whose cost scales with the number of distinct tracked
entities, not users.

The safeguard "cap the number of active tracked entities per subscriber and
globally" is named but not sized. For a product whose entire premise is
$1.50/user economics, an unbounded per-entity search cost is the same class of
risk the Folk pivot spent pages avoiding on SMS. The global entity cap, the
per-run retrieval budget, and the cache TTL should be concrete numbers before
this subsystem is built.

## Concern 5: Boundary and Concurrency Details

Two smaller issues:

- **Mixed module conventions.** `api/` is placed inside `src/` while
  `apps/web/` sits at the repository root. The web app is a separate deployable
  but the API is inside the Python source tree, which blurs the boundary between
  the two.
- **Concurrent preference writes.** Both documents now state that the website
  form and inbound conversational commands update the same preference records.
  That shared-profile design is intentional and good, but nothing addresses what
  happens when a website edit and a "only send tech tomorrow" Telegram command
  write concurrently. The conflict and ordering behavior should be defined.

## Summary

The pivot strategy is sound and the two planning documents are now consistent
with each other. The open risks are all in *how* `mobile-split-email.md` carries
out the restructure:

1. A single large move bundled with a behavior change, with no incremental path.
2. The whole test suite breaking at once, with no migration plan.
3. A `personalization/` layer wrongly shared between products when only its data
   is common and its heavy logic is email-only.
4. A watchlist retrieval subsystem whose cost caps are named but not sized.
5. Minor module-boundary and concurrent-write details left undefined.

A safer path is to add the structured-edition model inside the current package,
prove one channel against it, keep heavy personalization inside the email
product, and only then carve out the shared core and product folders
incrementally — migrating tests alongside the code rather than all at once.
