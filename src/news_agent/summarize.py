from __future__ import annotations

import json
import os
from typing import Any

from news_agent.models import AgentConfig, BriefingItem, BriefingText, StockSnapshot, StoryCluster


BRIEFING_ORDER = ("business_tech", "domestic", "global", "culture", "finance", "overall")


BRIEFING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "briefings": {
            "type": "array",
            "minItems": 6,
            "maxItems": 6,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "category": {"type": "string"},
                    "title": {"type": "string"},
                    "items": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 10,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "headline": {"type": "string"},
                                "summary": {"type": "string"},
                                "why_it_matters": {"type": "string"},
                                "next_watch": {"type": "string"},
                                "sources": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "minItems": 1,
                                    "maxItems": 5,
                                },
                                "urls": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "minItems": 0,
                                    "maxItems": 5,
                                },
                            },
                            "required": ["headline", "summary", "why_it_matters", "next_watch", "sources", "urls"],
                        },
                    },
                },
                "required": ["category", "title", "items"],
            },
        }
    },
    "required": ["briefings"],
}


def _cluster_payload(category: str, clusters: list[StoryCluster]) -> dict[str, Any]:
    return {
        "category": category,
        "stories": [
            {
                "candidate_headline": cluster.title,
                "representative_summary": cluster.representative_summary[:700],
                "score": round(cluster.total_score, 2),
                "source_count": len(cluster.sources),
                "sources": cluster.sources[:5],
                "urls": list(cluster.urls[:5]),
                "latest_publication_time": cluster.latest_published_at.isoformat(),
                "category_candidates": list(cluster.category_candidates),
                "watchlist_matches": list(cluster.watchlist_matches),
                "update_note": cluster.update_note,
                "article_samples": [
                    {
                        "source": article.source,
                        "title": article.title,
                        "summary": article.summary[:500],
                        "url": article.url,
                    }
                    for article in cluster.articles[:4]
                ],
            }
            for cluster in clusters
        ],
    }


def _quote_payload(snapshot: StockSnapshot | None, symbol: str) -> dict[str, Any]:
    if snapshot is None:
        return {"symbol": symbol}
    quote = snapshot.quote_for(symbol)
    return {
        "symbol": symbol,
        "price": quote.price,
        "change_percent": quote.change_percent,
        "open_price": quote.open_price,
        "volume": quote.volume,
        "as_of": quote.as_of,
        "provider": quote.provider,
    }


def _stock_payload(snapshot: StockSnapshot | None) -> dict[str, Any]:
    if snapshot is None:
        return {"news_mentioned_stocks": [], "mega_cap_stocks": []}
    return {
        "news_mentioned_stocks": [
            {
                "symbol": mention.symbol,
                "mention_count": mention.mention_count,
                "headlines": list(mention.headlines),
                "sources": list(mention.sources),
                "quote": _quote_payload(snapshot, mention.symbol),
            }
            for mention in snapshot.news_mentions[:20]
        ],
        "mega_cap_stocks": [
            _quote_payload(snapshot, symbol)
            for symbol in snapshot.mega_caps
        ],
    }


def build_prompt(
    category_clusters: dict[str, list[StoryCluster]],
    config: AgentConfig,
    stock_snapshot: StockSnapshot | None = None,
) -> str:
    category_labels = {name: category.label for name, category in config.categories.items()}
    payload = {
        "briefing_order": BRIEFING_ORDER,
        "category_labels": category_labels | {"overall": "What matters most today"},
        "market_snapshot": _stock_payload(stock_snapshot),
        "category_clusters": [
            _cluster_payload(category, clusters)
            for category, clusters in category_clusters.items()
        ],
    }
    return json.dumps(payload, ensure_ascii=False)


def system_prompt() -> str:
    return (
        "You write concise morning news briefings. Use only the supplied article data. "
        "Prioritize stories mentioned by multiple reputable sources, stories with real-world "
        "market/social/policy impact, and stories likely to matter over the next few days. "
        "Avoid clickbait, duplicate stories, speculation beyond the supplied data, and niche items "
        "unless the source frequency or impact score is high. In the financial briefing, include "
        "notable news-mentioned stocks from the market snapshot and the mega-cap watchlist "
        "(AAPL, MSFT, NVDA, TSLA, AMZN, META, GOOGL), especially large moves or repeated mentions. "
        "Each item needs a headline, a 2-3 sentence summary with concrete context, a short "
        "relevance note, what to watch next, the main sources, and source URLs when supplied. "
        "Keep language direct and skimmable."
    )


def polish_system_prompt() -> str:
    return (
        "You polish already-selected morning briefing drafts. Use only the facts, sources, "
        "tickers, prices, categories, and watch items present in the supplied drafts. Do not "
        "add new facts, sources, causal claims, names, dates, numbers, or URLs. Preserve the "
        "six briefing categories, keep source names attached to the same items, and return "
        "the same structured briefing shape, including source URLs. Make summaries more "
        "informative while staying skimmable, keep relevance notes concise, and remove awkward repetition."
    )


def _briefing_payload(briefings: list[BriefingText]) -> dict[str, Any]:
    return {
        "draft_briefings": [
            {
                "category": briefing.category,
                "title": briefing.title,
                "items": [
                    {
                        "headline": item.headline,
                        "summary": item.summary,
                        "why_it_matters": item.why_it_matters,
                        "next_watch": item.next_watch,
                        "sources": list(item.sources),
                        "watchlist_matches": list(item.watchlist_matches),
                        "update_note": item.update_note,
                        "urls": list(item.urls),
                    }
                    for item in briefing.items
                ],
            }
            for briefing in briefings
        ]
    }


def build_polish_prompt(draft_briefings: list[BriefingText]) -> str:
    payload = {
        "briefing_order": BRIEFING_ORDER,
        "instructions": (
            "Polish these drafts only. Keep the same categories, item structure, and source "
            "attribution. Do not introduce facts not already present in the draft."
        ),
        **_briefing_payload(draft_briefings),
    }
    return json.dumps(payload, ensure_ascii=False)


def _generate_structured_briefings(
    input_messages: list[dict[str, str]],
    model: str | None = None,
) -> list[BriefingText]:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("Install provider dependencies with: python -m pip install -e '.[providers]'") from exc

    client = OpenAI()
    selected_model = model or os.getenv("OPENAI_MODEL", "gpt-5.5")
    response = client.responses.create(
        model=selected_model,
        input=input_messages,
        text={
            "format": {
                "type": "json_schema",
                "name": "morning_news_briefings",
                "strict": True,
                "schema": BRIEFING_SCHEMA,
            }
        },
    )
    data = json.loads(response.output_text)
    return parse_briefings(data)


def generate_briefings_with_openai(
    category_clusters: dict[str, list[StoryCluster]],
    config: AgentConfig,
    stock_snapshot: StockSnapshot | None = None,
    model: str | None = None,
) -> list[BriefingText]:
    return _generate_structured_briefings(
        [
            {"role": "system", "content": system_prompt()},
            {"role": "user", "content": build_prompt(category_clusters, config, stock_snapshot)},
        ],
        model=model,
    )


def generate_polished_briefings_with_openai(
    draft_briefings: list[BriefingText],
    model: str | None = None,
) -> list[BriefingText]:
    return _generate_structured_briefings(
        [
            {"role": "system", "content": polish_system_prompt()},
            {"role": "user", "content": build_polish_prompt(draft_briefings)},
        ],
        model=model,
    )


def parse_briefings(data: dict[str, Any]) -> list[BriefingText]:
    briefings: list[BriefingText] = []
    for briefing in data["briefings"]:
        items = tuple(
            BriefingItem(
                headline=item["headline"],
                summary=item["summary"],
                why_it_matters=item["why_it_matters"],
                next_watch=item.get("next_watch", ""),
                sources=tuple(item["sources"]),
                urls=tuple(item.get("urls", ())),
            )
            for item in briefing["items"]
        )
        briefings.append(
            BriefingText(
                category=briefing["category"],
                title=briefing["title"],
                items=items,
            )
        )
    return briefings


FALLBACK_WATCH_LINES = {
    "business_tech": "Company updates, regulatory response, customer reaction, or market follow-through.",
    "domestic": "New filings, official statements, court action, or policy response.",
    "global": "Diplomatic response, security developments, energy prices, or market spillover.",
    "culture": "Audience reaction, league/company response, ratings, or follow-up coverage.",
    "finance": "Price action, Fed commentary, earnings updates, and broader market reaction.",
    "overall": "Whether follow-up reporting confirms the signal and moves markets or policy.",
}
TRAILING_FILLER_WORDS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "but",
    "for",
    "from",
    "his",
    "her",
    "in",
    "into",
    "of",
    "on",
    "or",
    "that",
    "the",
    "their",
    "to",
    "with",
}


def compact_text(value: str, max_chars: int = 240) -> str:
    text = " ".join(value.split())
    for punctuation in (",", ".", ";", ":", "!", "?"):
        text = text.replace(f" {punctuation}", punctuation)
    text = text.replace(" 's", "'s")
    text = text.replace("Why it matters:", "").replace("Why it matters", "").strip()
    if len(text) <= max_chars:
        return text

    sentence_end = max(text.rfind(".", 0, max_chars + 1), text.rfind("!", 0, max_chars + 1), text.rfind("?", 0, max_chars + 1))
    if sentence_end >= min(80, max_chars // 2):
        return text[: sentence_end + 1]

    shortened = text[: max_chars + 1].rsplit(" ", 1)[0].rstrip(" ,;:")
    words = shortened.split()
    while words and words[-1].strip(".,;:!?").lower() in TRAILING_FILLER_WORDS:
        words.pop()
    shortened = " ".join(words)
    if shortened.endswith((".", "!", "?")):
        return shortened
    return f"{shortened}."


def clean_fallback_summary(cluster: StoryCluster) -> str:
    article = cluster.articles[0]
    summary = article.summary or article.title
    summary = compact_text(summary, max_chars=420)
    for source in (article.source, *cluster.sources):
        suffix = f" {source}"
        if source and summary.endswith(suffix):
            summary = summary[: -len(suffix)].rstrip()
    if summary.casefold() == cluster.title.casefold():
        return "No additional source summary was available."
    return summary or "No additional source summary was available."


def fallback_why_it_matters(
    source_count: int,
    category: str = "overall",
    watchlist_matches: tuple[str, ...] = (),
) -> str:
    watchlist_note = " It also matches your watchlist." if watchlist_matches else ""
    category_templates = {
        "business_tech": "Could affect company strategy, AI/product competition, regulation, funding, or customer adoption.",
        "domestic": "Could shape policy, legal risk, public safety, economic conditions, or social debate.",
        "global": "Could affect diplomacy, trade, conflict risk, humanitarian conditions, or energy markets.",
        "culture": "Could shift public attention, platform incentives, creator economics, entertainment, or sports business.",
        "finance": "Could move investors, sectors, earnings expectations, rates, IPOs, or risk appetite.",
        "overall": "Could have broader consequences across markets, policy, public attention, or follow-up coverage.",
    }
    if source_count >= 3:
        return f"Confirmed by {source_count} sources. {category_templates.get(category, category_templates['overall'])}{watchlist_note}".strip()
    if source_count == 2:
        return f"Covered by two sources. {category_templates.get(category, category_templates['overall'])}{watchlist_note}".strip()
    return f"Single-source but high-signal. {category_templates.get(category, category_templates['overall'])}{watchlist_note}".strip()


def generate_fallback_briefings(
    category_clusters: dict[str, list[StoryCluster]],
    config: AgentConfig,
    stock_snapshot: StockSnapshot | None = None,
) -> list[BriefingText]:
    titles = {
        "business_tech": "1/6 Business and technology",
        "domestic": "2/6 Domestic U.S. news",
        "global": "3/6 Global news",
        "culture": "4/6 Culture, social, and media trends",
        "finance": "5/6 Financial news",
        "overall": "6/6 What matters most today",
    }
    briefings: list[BriefingText] = []
    for category in BRIEFING_ORDER:
        clusters = category_clusters.get(category, [])[:6 if category == "overall" else 5]
        items = []
        if category == "finance" and stock_snapshot is not None:
            items.extend(stock_snapshot_items(stock_snapshot))
        for cluster in clusters:
            source_count = len(cluster.sources)
            items.append(
                BriefingItem(
                    headline=cluster.title,
                    summary=clean_fallback_summary(cluster),
                    why_it_matters=cluster.why_it_matters
                    or fallback_why_it_matters(source_count, category, cluster.watchlist_matches),
                    next_watch=FALLBACK_WATCH_LINES[category],
                    sources=tuple(cluster.sources[:5]),
                    watchlist_matches=cluster.watchlist_matches,
                    update_note=cluster.update_note,
                    urls=cluster.urls[:5],
                )
            )
        if not items:
            items.append(
                BriefingItem(
                    headline="No dominant story detected",
                    summary="The configured sources did not surface a high-confidence item for this category.",
                    why_it_matters="Skipping weak signals helps keep the briefing high-signal.",
                    next_watch="Check source configuration if this category is often empty.",
                    sources=("Configured feeds",),
                )
            )
        briefings.append(BriefingText(category=category, title=titles[category], items=tuple(items)))
    return briefings


def stock_snapshot_items(snapshot: StockSnapshot) -> list[BriefingItem]:
    items: list[BriefingItem] = []
    if snapshot.market_movers:
        top_movers = snapshot.market_movers[:5]
        summary = "; ".join(
            (
                f"{mover.symbol} {mover.percent_change:+.1f}%"
                f" ({mover.move_reason if mover.reason_confidence != 'low' else 'catalyst unclear from major headlines'})"
            )
            for mover in top_movers
        )
        sources = tuple(sorted({source for mover in top_movers for source in mover.reason_sources})) or ("Stooq",)
        watchlist_matches = tuple(
            sorted({match for mover in top_movers for match in mover.watchlist_matches})
        )
        items.append(
            BriefingItem(
                headline="Explained market movers",
                summary=summary,
                why_it_matters="Large asset moves with a credible catalyst can set the tone for the rest of the session.",
                next_watch="Watch whether the moves hold after U.S. market liquidity deepens.",
                sources=sources[:5],
                watchlist_matches=watchlist_matches,
            )
        )

    if snapshot.news_mentions:
        top_mentions = snapshot.news_mentions[:8]
        summary = "; ".join(
            f"{mention.symbol} ({mention.mention_count} mention{'s' if mention.mention_count != 1 else ''}, "
            f"{snapshot.quote_for(mention.symbol).compact()})"
            for mention in top_mentions
        )
        sources = tuple(sorted({source for mention in top_mentions for source in mention.sources})) or ("Headlines",)
        items.append(
            BriefingItem(
                headline="News-mentioned stocks",
                summary=summary,
                why_it_matters="These tickers appeared in the morning headline flow and may be reacting to fresh company, policy, earnings, or market news.",
                next_watch="Look for repeated mentions across more outlets and outsized moves after the market opens.",
                sources=sources[:5],
            )
        )

    mega_summary = "; ".join(snapshot.quote_for(symbol).compact() for symbol in snapshot.mega_caps)
    items.append(
        BriefingItem(
            headline="Mega-cap watchlist",
            summary=mega_summary,
            why_it_matters="These names carry heavy index weight and often explain broad Nasdaq/S&P moves.",
            next_watch="Watch whether mega-cap moves confirm or fade the broader market direction.",
            sources=("Yahoo Finance",),
        )
    )
    return items
