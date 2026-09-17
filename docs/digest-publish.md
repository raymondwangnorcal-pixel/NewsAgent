# Publishing the briefing to the website

After a production briefing is delivered, the agent can publish that same
briefing for the live plate on `gaplesslabs.com/newsagent`. It is off by
default and it never affects delivery.

## What is published

`src/news_agent/mailer/digest_publish.py` builds the payload from the
`BriefingSection` objects the email was formatted from — not from the rendered
HTML and not by parsing the plain text back apart. Each story is split into a
headline and a body with `render._extract_headline`, the same call the email
makes, so the plate and the email break every story in the same place and a
change to that heuristic moves both together.

Each category carries its key, its display label and its stories; each story
carries its headline, its body, and its sources as name and URL. Only `http`
and `https` URLs travel; a source whose link fails that check keeps its name
and loses its link rather than publishing a bad one.

Two exclusions are structural rather than filtered. Watchlist content is
assembled on a different path and is never passed to the publisher, so no
configuration can make it public. A section's `lead_lines` — finance's live
quote preamble — are dropped, because they are end-of-day figures that would
read as stale on a page somebody opens in the afternoon.

## When it publishes

`publish_quietly` runs from `cli.py` immediately after a successful send, in
both the `email` and `both` delivery paths. It publishes only when:

- `NEWSAGENT_PUBLISH_DIGEST` is `true` (a repository variable, default false);
- the edition is a production edition, never a test revision or a gate alert;
- at least one recipient reached SMTP acceptance.

A failure prints a warning to stderr and returns. The briefing is the product
and the website is downstream of it: a publish failure must not turn a
delivered briefing into a failed run. The site then keeps showing the last
published edition, with its date visible, until the next successful publish.

## Where it goes

`newsagent_publish_edition` in Supabase upserts the day's row and deletes
anything older than seven days in the same call. The table is private: no
browser role can read it, the website reads through its own server-side
endpoint, and that endpoint only ever returns the newest row. There is no
public archive and no date parameter anywhere in the read path.

Credentials are the ones the send already uses — `SUPABASE_URL` and
`SUPABASE_SECRET_KEY`. Upstream error bodies are never logged, since they can
carry both.

## Turning it off

Set the `NEWSAGENT_PUBLISH_DIGEST` repository variable to `false`. The next run
stops publishing; the site keeps serving the last edition until its cache
expires, and the plate withdraws once the table is emptied.
