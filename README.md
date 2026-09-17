# Morning News Agent

A scheduled AI briefing agent that gathers signals from reputable news sources every morning, ranks stories by frequency and expected impact, and sends five concise briefing messages:

1. Business and technology
2. Domestic U.S. news
3. Global news
4. Culture, social, and media trends
5. Financial news

## Website subscriber integration

The optional Supabase subscriber connection, safe import, public-list activation, unsubscribe behavior, and no-send checks are documented in [docs/subscriber-integration.md](docs/subscriber-integration.md). It remains off until `NEWSAGENT_SUBSCRIBERS_ENABLED=true` is configured.

## Website digest preview

After a production send, the agent can publish that briefing for the live plate on `gaplesslabs.com/newsagent`. It is off until `NEWSAGENT_PUBLISH_DIGEST=true`, publishes only editions that actually reached recipients, and never fails a send when publishing fails. See [docs/digest-publish.md](docs/digest-publish.md).
