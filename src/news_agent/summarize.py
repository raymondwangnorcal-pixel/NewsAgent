from __future__ import annotations

import json
import os
import re
from typing import Any

from news_agent.models import AgentConfig, BriefingItem, BriefingText, StockSnapshot, StoryCluster


BRIEFING_ORDER = ("business_tech", "domestic", "global", "culture", "finance")


BRIEFING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "briefings": {
            "type": "array",
            "minItems": 5,
            "maxItems": 5,
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
        "category_labels": category_labels,
        "market_snapshot": _stock_payload(stock_snapshot),
        "category_clusters": [
            _cluster_payload(category, clusters)
            for category, clusters in category_clusters.items()
        ],
    }
    return json.dumps(payload, ensure_ascii=False)


VOICE_GUIDE = (
    "Write like you're texting a friend the news, not anchoring a broadcast. Ground rules:\n"
    "- Keep it snappy. Short sentences. No big paragraphs, no academic or corporate tone.\n"
    "- Skip fluff, intros, and formal transitions ('furthermore', 'in conclusion', 'it is worth noting'). "
    "Get straight to the point.\n"
    "- Natural, low-stakes slang is fine where it actually fits, like a sharp 20-something in 2026 texting a "
    "buddy. Don't force it or overdo it.\n"
    "- Emoji are rare. Only drop one when it genuinely adds a laugh, a shock, or nails the mood. Never spam them.\n"
    "- If a story is tense, wild, or unhinged, match that energy. A little irreverence is fine. Don't flatten "
    "everything into the same even-keeled tone.\n"
    "- Never use an em dash (—). Never write in the 'not just X but Y' pattern. Both scream AI. Use plain "
    "commas, periods, or 'and' instead.\n"
    "- Do not label your own reasoning. Don't write 'why it matters:' or 'what happened:' inside the text "
    "itself, just say the thing.\n"
    "Style reference (match this energy and directness, not these specific words):\n"
    '"Tensions are rising in the Gulf after Iran launched strikes on U.S. regional bases in Kuwait, Qatar, '
    "and Bahrain, following U.S. strikes earlier this week. Trump said the ceasefire is no longer in effect, "
    "although he also claimed he does not expect the conflict to escalate into a full-scale war. Israel has "
    'also threatened to re-enter the conflict on the U.S. side."'
)


def system_prompt() -> str:
    return (
        f"{VOICE_GUIDE}\n\n"
        "You write a morning news briefing. Use only the supplied article data. Prioritize stories "
        "mentioned by multiple reputable sources, stories with real-world market/social/policy impact, "
        "and stories likely to matter over the next few days. Skip clickbait, duplicate stories, "
        "speculation beyond the supplied data, and niche items unless the source frequency or impact "
        "score is high. In the financial briefing, call out notable news-mentioned stocks from the "
        "market snapshot and the mega-cap watchlist (AAPL, MSFT, NVDA, TSLA, AMZN, META, GOOGL), "
        "especially large moves or repeated mentions.\n\n"
        "Each item needs: a headline, a summary that's a quick 2-3 sentence recap in the voice above "
        "(fold in why it actually matters as part of the flow, don't bolt it on as a separate formal "
        "clause), a short why_it_matters gut-check line, a next_watch one-liner on what to keep an eye on, "
        "the main sources, and source URLs when supplied. Be concrete: if Nvidia falls on export "
        "restriction news, say the chip curbs could hit Nvidia's China sales and ding chip stocks broadly, "
        "don't just say 'this could affect the tech sector.'"
    )


def polish_system_prompt() -> str:
    return (
        f"{VOICE_GUIDE}\n\n"
        "You polish already-selected morning briefing drafts. Use only the facts, sources, tickers, "
        "prices, categories, and watch items present in the supplied drafts. Do not add new facts, "
        "sources, causal claims, names, dates, numbers, or URLs. Preserve the five briefing categories, "
        "keep source names attached to the same items, and return the same structured briefing shape, "
        "including source URLs.\n\n"
        "Rewrite every summary and why_it_matters line into the casual voice above: strip out any stiff "
        "or formal phrasing left over from the draft, cut repetition, and make it read like a quick "
        "text instead of a press release. Keep it accurate to the source draft, just change how it's said."
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
    "business_tech": "Watching for the company's next move and how regulators and customers react.",
    "domestic": "Watching for new filings, official statements, or a court/policy response.",
    "global": "Watching for the diplomatic fallout, security moves, and any hit to energy prices.",
    "culture": "Watching for how audiences and the league or company respond, plus follow-up coverage.",
    "finance": "Watching price action, Fed chatter, and earnings for the next signal.",
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
COMPANY_ALIASES = {
    "aapl": "Apple",
    "alphabet": "Alphabet",
    "amazon": "Amazon",
    "amzn": "Amazon",
    "apple": "Apple",
    "googl": "Alphabet",
    "google": "Google",
    "meta": "Meta",
    "microsoft": "Microsoft",
    "msft": "Microsoft",
    "nvidia": "Nvidia",
    "nvda": "Nvidia",
    "tesla": "Tesla",
    "tsla": "Tesla",
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
    titles = {cluster.title.casefold(), *(article.title.casefold() for article in cluster.articles)}
    for article in cluster.articles:
        if not article.summary:
            continue

        summary = compact_text(article.summary, max_chars=420)
        for source in (article.source, *cluster.sources):
            suffix = f" {source}"
            if source and summary.casefold().endswith(suffix.casefold()):
                summary = summary[: -len(suffix)].rstrip()

        if summary and summary.casefold() not in titles:
            return summary

    return ""


def fallback_why_it_matters(
    source_count: int,
    category: str = "general",
    watchlist_matches: tuple[str, ...] = (),
) -> str:
    watchlist_note = " Also on your watchlist." if watchlist_matches else ""
    category_templates = {
        "business_tech": "Could shake up company strategy, the AI/product race, regulation, funding, or how fast people adopt this.",
        "domestic": "Could move policy, legal risk, public safety, the economy, or just spark a big debate.",
        "global": "Could ripple into diplomacy, trade, conflict risk, humanitarian stuff, or energy markets.",
        "culture": "Could shift where people's attention goes, creator money, entertainment, or sports business.",
        "finance": "Could move investors, whole sectors, earnings expectations, rates, IPOs, or risk appetite.",
        "general": "Could have knock-on effects across markets, policy, public attention, or the next few days of coverage.",
    }
    if source_count >= 3:
        return f"{source_count} outlets are on this. {category_templates.get(category, category_templates['general'])}{watchlist_note}".strip()
    if source_count == 2:
        return f"Two outlets have it. {category_templates.get(category, category_templates['general'])}{watchlist_note}".strip()
    return f"Single source, but high-signal. {category_templates.get(category, category_templates['general'])}{watchlist_note}".strip()


def contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(re.search(rf"\b{re.escape(term)}\b", text) for term in terms)


def detected_company(text: str) -> str:
    for alias, company in COMPANY_ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}\b", text):
            return company
    return "The company"


def event_why_it_matters(
    cluster: StoryCluster,
    category: str,
    watchlist_matches: tuple[str, ...] = (),
) -> str:
    text = f"{cluster.title} {cluster.representative_summary}".lower()
    company = detected_company(text)

    if contains_any(text, ("export restriction", "export restrictions", "export control", "export controls", "chip restriction")):
        if contains_any(text, ("nvidia", "nvda", "ai chip", "chips", "semiconductor")):
            return "Chip export curbs could hit Nvidia's China sales hard and drag down the rest of the semiconductor trade."
        return "Export controls could scramble sales, supply chains, and the whole U.S.-China tech rivalry."

    if contains_any(text, ("earnings", "guidance", "forecast", "profit", "revenue")):
        if contains_any(text, ("beat", "tops", "raises", "raised", "strong", "surge", "jump")):
            return f"{company} just gave investors a reason to feel good, and peers in the sector could ride the wave too."
        if contains_any(text, ("miss", "cuts", "cut", "weak", "falls", "fell", "drops", "plunges", "slumps")):
            return f"{company}'s weak outlook could hit its valuation and stir up demand worries across the sector."
        return f"{company}'s numbers can reset the mood for the whole sector, not just its own stock."

    if contains_any(text, ("fed", "federal reserve", "rate cut", "interest rate", "treasury yield", "yields")):
        return "Rate expectations move borrowing costs, bond yields, bank stocks, housing, basically everyone's risk appetite."

    if contains_any(text, ("inflation", "cpi", "consumer prices", "prices")):
        return "Inflation prints can flip rate-cut bets, jolt bond yields, and shake up equity valuations fast."

    if contains_any(text, ("tariff", "tariffs", "trade war")):
        return "Tariffs mean higher costs for companies and consumers, plus more stress on global supply chains."

    if contains_any(text, ("lawsuit", "sues", "court", "judge", "antitrust", "doj", "ftc", "probe", "investigation")):
        if category == "domestic":
            return "This case could set precedent and force policy or enforcement changes well beyond this one dispute."
        return "Legal heat like this can box in company strategy, raise compliance costs, and set the tone for the sector."

    if contains_any(text, ("merger", "acquisition", "acquires", "takeover", "deal")):
        return "A deal like this can reshuffle competition, pricing power, and how investors size up the whole market."

    if contains_any(text, ("ipo", "public listing", "go public")):
        return "IPO momentum is basically a vibe check on risk appetite, and it can move valuations for similar private companies."

    if contains_any(text, ("startup", "funding", "venture", "vc", "raises", "seed round")):
        return "This funding shows where investors think the real demand is, and it could shape the next round of startup fights."

    if contains_any(text, ("layoff", "layoffs", "job cuts", "cuts jobs")):
        return "Job cuts usually mean cost pressure, and they can hint at weaker demand or a bigger strategic reset."

    if contains_any(text, ("ai", "artificial intelligence", "model", "chip", "semiconductor")):
        return "This kind of AI/chip move can shift product roadmaps, cloud spending, and demand across the whole stack."

    if contains_any(text, ("supreme court", "ruling", "appeals court", "federal court")):
        return "This ruling could set precedent and force policy or business changes far beyond this one case."

    if contains_any(text, ("election", "vote", "campaign", "ballot", "primary")):
        return "The outcome here could shift policy, regulation, spending, and market expectations depending on who wins."

    if contains_any(text, ("immigration", "healthcare", "education", "student loan", "school")):
        return "This policy shift could hit household costs, public services, and state or federal budgets."

    if contains_any(text, ("war", "missile", "attack", "conflict", "invasion", "escalation")):
        return "Escalation like this raises real humanitarian risk and can ripple into energy, trade, defense, and diplomacy fast."

    if contains_any(text, ("ceasefire", "peace talks", "truce")):
        return "A ceasefire could ease the humanitarian pressure and change where this conflict heads next."

    if contains_any(text, ("sanction", "sanctions")):
        return "Sanctions can choke off trade flows, rattle energy markets, and shift diplomatic leverage."

    if contains_any(text, ("oil", "energy", "gas", "opec")):
        return "Energy swings like this feed straight into inflation, consumer costs, and earnings for transport and industrial names."

    if contains_any(text, ("bitcoin", "btc", "ethereum", "eth", "crypto", "etf")):
        return "Crypto moves are a decent risk-appetite gauge and can pull flows into ETFs, exchanges, and related stocks."

    if contains_any(text, ("streaming", "netflix", "disney", "youtube", "tiktok", "platform")):
        return "Platform shifts like this can move audience attention, subscription trends, ad spend, and creator money."

    if contains_any(text, ("nfl", "nba", "mlb", "sports", "league", "championship")):
        return "Big sports news like this can move media rights, sponsorships, fan attention, and league money."

    if watchlist_matches:
        return f"This touches your watchlist topic {watchlist_matches[0]}, worth keeping an eye on."

    return fallback_why_it_matters(len(cluster.sources), category, watchlist_matches)


def generate_fallback_briefings(
    category_clusters: dict[str, list[StoryCluster]],
    config: AgentConfig,
    stock_snapshot: StockSnapshot | None = None,
) -> list[BriefingText]:
    titles = {
        "business_tech": "1/5 Business and technology",
        "domestic": "2/5 Domestic U.S. news",
        "global": "3/5 Global news",
        "culture": "4/5 Culture, social, and media trends",
        "finance": "5/5 Financial news",
    }
    briefings: list[BriefingText] = []
    for category in BRIEFING_ORDER:
        clusters = category_clusters.get(category, [])[:5]
        items = []
        if category == "finance" and stock_snapshot is not None:
            items.extend(stock_snapshot_items(stock_snapshot))
        for cluster in clusters:
            items.append(
                BriefingItem(
                    headline=cluster.title,
                    summary=clean_fallback_summary(cluster),
                    why_it_matters=cluster.why_it_matters
                    or event_why_it_matters(cluster, category, cluster.watchlist_matches),
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
                f" ({mover.move_reason if mover.reason_confidence != 'low' else 'no clear reason yet'})"
            )
            for mover in top_movers
        )
        sources = tuple(sorted({source for mover in top_movers for source in mover.reason_sources})) or ("Stooq",)
        watchlist_matches = tuple(
            sorted({match for mover in top_movers for match in mover.watchlist_matches})
        )
        items.append(
            BriefingItem(
                headline="Biggest moves",
                summary=summary,
                why_it_matters="Moves this size with a real catalyst behind them can set the tone for the whole session.",
                next_watch="Watch if these hold once U.S. trading really gets going.",
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
                headline="Stocks in the headlines",
                summary=summary,
                why_it_matters="These tickers are all over this morning's headlines, probably reacting to fresh news.",
                next_watch="Watch for more outlets picking these up and bigger moves once the market opens.",
                sources=sources[:5],
            )
        )

    mega_summary = "; ".join(snapshot.quote_for(symbol).compact() for symbol in snapshot.mega_caps)
    items.append(
        BriefingItem(
            headline="Mega-cap check",
            summary=mega_summary,
            why_it_matters="These names carry so much index weight that they basically move the Nasdaq/S&P on their own.",
            next_watch="Watch if the mega-caps confirm or fade whatever the broader market is doing.",
            sources=("Yahoo Finance",),
        )
    )
    return items
