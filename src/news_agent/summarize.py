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
                            },
                            "required": ["headline", "summary", "why_it_matters", "next_watch", "sources"],
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
                "score": round(cluster.total_score, 2),
                "source_count": len(cluster.sources),
                "sources": cluster.sources[:5],
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
        "Each item needs a headline, a 1-2 "
        "sentence summary, why it matters, what to watch next, and the main sources. Keep language "
        "direct and skimmable."
    )


def generate_briefings_with_openai(
    category_clusters: dict[str, list[StoryCluster]],
    config: AgentConfig,
    stock_snapshot: StockSnapshot | None = None,
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
        input=[
            {"role": "system", "content": system_prompt()},
            {"role": "user", "content": build_prompt(category_clusters, config, stock_snapshot)},
        ],
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
        clusters = category_clusters.get(category, [])[:10 if category == "overall" else 5]
        items = []
        if category == "finance" and stock_snapshot is not None:
            items.extend(stock_snapshot_items(stock_snapshot))
        for cluster in clusters:
            summary = cluster.articles[0].summary or cluster.articles[0].title
            summary = summary.replace("Why it matters:", "").replace("Why it matters", "").strip()
            items.append(
                BriefingItem(
                    headline=cluster.title,
                    summary=summary[:220],
                    why_it_matters=(
                        f"Covered by {len(cluster.sources)} source(s); scored high on "
                        f"frequency/impact/recency signals."
                    ),
                    next_watch="Watch for follow-up coverage, policy response, market reaction, or new filings.",
                    sources=tuple(cluster.sources[:5]),
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
