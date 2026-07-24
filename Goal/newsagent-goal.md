# NewsAgent Goal

## Purpose

NewsAgent should be a dependable daily news briefing system for a busy reader who wants to understand the most important developments quickly without reading a feed full of duplicate headlines, unsupported takes, or long articles.

Its job is not to report everything. Its job is to identify the small set of stories worth knowing, explain each one clearly, and deliver a useful briefing in a few minutes of reading.

## The Reader Experience

The final product delivers one morning briefing organized into five predictable categories:

1. Business and technology
2. U.S. news
3. Global news
4. Culture, social, and media trends
5. Financial news

Each category contains a limited number of distinct, high-value stories. A reader should be able to scan the briefing on Telegram, SMS, or the terminal and come away with a confident answer to three questions for every story:

- What happened?
- What is the most useful evidence or context?
- Why does it matter now or what is likely to happen next?

The briefing should feel like a knowledgeable, credible person explaining the news to a friend: direct, readable, analytical, and never academic or sensational.

## Editorial Standard

Accuracy and conciseness are NewsAgent's highest priorities.

- Accuracy comes first. NewsAgent must not change a fact, remove material qualification, overstate a claim, or add unsupported speculation merely to make a story shorter or more entertaining.
- Conciseness comes second, but is still universal. Every delivered story should be compact, information-dense prose rather than a rewritten headline or a mini article.
- Relevance matters. Stories should earn their place through significance, trustworthy evidence, corroboration where available, reader-interest signals, and freshness.
- Clarity matters. The reader should not need specialist knowledge to understand why a story is included.
- Variety matters. The system should avoid duplicate stories, excessive reliance on one publisher, and decks that crowd out an entire category.

The intended final story format is one stand-alone paragraph, normally two or three sentences. It leads with the event, adds the strongest concrete fact or comparison, and ends with a grounded explanation of why the development matters. Final length should be set to a single measured editorial range before compression is enabled; the draft and compression prompts must use the same range.

## Expected Final System

### 1. Source intake and normalization

NewsAgent gathers news from a maintained, configuration-driven set of reputable RSS and news sources. It applies the configured lookback window, normalizes article metadata, retains feed/category provenance, and limits the working pool to a practical size while reserving capacity for every major category.

The final system favors source diversity and does not allow a topic to appear important simply because one feed repeats it. Source additions, category assignments, reputation settings, and extraction policies live in versioned configuration rather than being embedded in code.

### 2. Clustering, evidence, and quality control

Related coverage is clustered into one candidate story. The system scores each cluster using corroboration, source reputation, evidence quality, timeliness, topic relevance, and expected impact. It enriches selected candidates with bounded article extraction when source metadata alone is insufficient.

The quality-control gate rejects or deprioritizes weak candidates, including stale items, thin one-source stories when corroboration is needed, duplicate clusters, low-evidence stories, and items that do not meet the category's standard. It records why stories were skipped so the system can be tuned from evidence rather than guesswork.

The system then selects a balanced deck under explicit category floors, ceilings, and source caps. It should make a reasonable effort to meet category targets, including culture, without lowering quality merely to fill space.

### 3. Drafting

For each selected cluster, NewsAgent produces structured story drafts using the configured GPT-5.6 drafting model. The model receives only the bounded, selected evidence needed for the story and returns a schema-validated paragraph per story.

The drafting instructions require:

- A direct lead identifying the actors, action, and immediate outcome.
- One or two strong figures, comparisons, or concrete details when supported by reporting.
- A final, sourced explanation of significance, risk, or next step.
- Informal but credible language for a general reader.
- No clickbait, filler, headline repetition, dramatic slang, excessive background, or unsupported prediction.

If the drafting call fails, the system produces a deterministic fallback instead of dropping the story. Fallback output remains visible and is clearly tracked internally as a fallback.

### 4. Second-pass compression

After a successful LLM draft, NewsAgent runs a dedicated compression pass over the drafted paragraph, not over the source articles. This pass uses `gpt-5.6-terra` independently of the drafting-model setting.

The compressor makes prose shorter only when it can preserve the story's facts and meaning. It must retain named entities, numbers, dates, attribution, negation, uncertainty, causal relationships, and the closing consequence. It never receives sources, so it cannot use outside material to introduce a new claim.

Before accepting a compressed paragraph, NewsAgent compares protected numbers, currencies, percentages, and dates in both directions. If compression changes, removes, or adds a protected fact, or fails to create a meaningful reduction, the original draft is delivered unchanged. Non-LLM fallback drafts are never sent through the compressor, preventing a second LLM rewrite after a drafting failure.

Compression is a universal editorial treatment: the accepted concise paragraph is used by SMS, Telegram, and console output. Finance market lead lines remain untouched. The compressor has its own per-run cost ceiling and output-token cap, and it can be disabled immediately through configuration, environment, or CLI controls.

### 5. Delivery and presentation

The same selected briefing is rendered appropriately for console, Telegram, and SMS. Links are omitted by default in delivered messages, while source attribution remains concise and useful. A dry run should make it easy to inspect the exact message body, character counts, omissions, diagnostics, and skipped-story reasons before a real send.

SMS prioritizes the highest-value stories within its character budget. It drops whole low-priority trailing stories rather than truncating a story mid-thought. Telegram provides readable spacing and a fuller mobile-friendly presentation. Console output supports inspection and debugging without changing what a real send would contain.

NewsAgent maintains history so unchanged stories are not repeatedly sent, while meaningful updates remain eligible. Breaking alerts stay separate from the scheduled morning briefing and use their own thresholds and cooldowns.

### 6. Cost, reliability, and observability

The final system is economical by design. It uses bounded article enrichment, batched structured LLM calls, small targeted prompts, configurable model selection, and deterministic fallbacks. It should spend API budget only on the candidates and transformations that improve the reader's final briefing.

Compression has a strict per-run dollar ceiling, configured current token rates, and actual usage/cost diagnostics. The system never fails a whole briefing because a feed, extractor, model call, or notification provider fails; it degrades gracefully, preserves available content, and reports what happened.

Every run exposes useful diagnostics, including:

- Fetched, clustered, enriched, qualified, selected, and skipped counts.
- Category coverage and underfill reasons.
- Source distribution and duplicate suppression.
- Draft and compression outcomes, guard failures, reduction rates, and estimated API cost.
- Delivery character counts, omissions, and provider outcomes.

For 30 days, NewsAgent retains a local audit artifact for each run containing source evidence identifiers, the original draft, delivered text, compression/guard status, model usage, and estimated cost. This allows a questionable story to be traced back to sourcing, drafting, compression, or validation.

## Definition of Done

NewsAgent reaches its intended final version when a normal scheduled run reliably produces a balanced, high-quality five-category briefing that a reader can trust and finish quickly, while meeting these conditions:

- Every delivered story is distinct, relevant, source-grounded, and easy to understand.
- The selection process is transparent, configurable, and resistant to duplicates, weak evidence, category starvation, and source overconcentration.
- AI-generated drafts follow the editorial structure and tone consistently, with deterministic fallbacks when AI is unavailable.
- Compression is enabled only after a shadow evaluation of 100 stories, including manual comparison of at least 25 original/compressed pairs, finds zero material fidelity errors and shows a meaningful reduction in prose and SMS omissions.
- Concision never overrides accuracy; anything that cannot be safely compressed keeps its original draft.
- Telegram, SMS, console dry runs, history suppression, diagnostics, and alerting all function predictably from the same core briefing result.
- The operator can change sources, category limits, links, delivery settings, model configuration, and compression controls without code edits.
- API costs are bounded, visible per run, and proportional to the value added by AI.
- Automated tests cover the core pipeline, configuration, quality gate, drafting/fallback behavior, formatting, delivery boundaries, compression guard, and retention cleanup.

## Ongoing Product Principles

NewsAgent should stay opinionated about quality but flexible about policy. New sources, categories, scoring rules, or delivery channels should be added only when they improve the reader's briefing, preserve explainability, and fit within the system's reliability and cost boundaries.

The north star is simple: every morning, deliver the few stories the reader most needs to know, explained accurately and concisely enough to be useful immediately.
