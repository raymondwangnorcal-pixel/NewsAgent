from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import re

from news_agent.models import Article, MarketMover


CAUSAL_KEYWORDS = {
    "acquisition",
    "antitrust",
    "approval",
    "buyback",
    "downgrade",
    "earnings",
    "export controls",
    "fed",
    "guidance",
    "inflation",
    "ipo",
    "lawsuit",
    "merger",
    "oil supply",
    "rate",
    "rates",
    "regulation",
    "tariff",
    "upgrade",
}
ASSET_ALIASES = {
    "AAPL": ("apple",),
    "MSFT": ("microsoft",),
    "NVDA": ("nvidia",),
    "TSLA": ("tesla",),
    "AMZN": ("amazon",),
    "META": ("meta", "facebook"),
    "GOOGL": ("alphabet", "google"),
    "AVGO": ("broadcom",),
    "AMD": ("advanced micro devices", "amd"),
    "NFLX": ("netflix",),
    "BTC": ("bitcoin", "crypto"),
    "ETH": ("ethereum", "crypto"),
    "SPY": ("s&p 500", "stocks", "market"),
    "QQQ": ("nasdaq", "tech stocks"),
    "DIA": ("dow",),
    "IWM": ("russell 2000", "small caps"),
}


def article_mentions_mover(article: Article, mover: MarketMover) -> bool:
    text = f"{article.title} {article.summary}"
    if re.search(rf"(?<![A-Z0-9])\$?{re.escape(mover.symbol)}(?![A-Z0-9])", text):
        return True
    lowered = text.lower()
    return any(re.search(rf"\b{re.escape(alias)}\b", lowered) for alias in ASSET_ALIASES.get(mover.symbol, ()))


def causal_score(article: Article) -> int:
    text = f"{article.title} {article.summary}".lower()
    return sum(1 for keyword in CAUSAL_KEYWORDS if keyword in text)


def recency_hours(article: Article) -> float:
    published = article.published_at
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    return max((datetime.now(timezone.utc) - published).total_seconds() / 3600, 0.0)


def explain_mover(mover: MarketMover, articles: list[Article]) -> MarketMover:
    candidates = [
        article
        for article in articles
        if article_mentions_mover(article, mover) and recency_hours(article) <= 48
    ]
    candidates.sort(key=lambda article: (causal_score(article), article.reputation, -recency_hours(article)), reverse=True)
    reason_sources: list[str] = []
    for article in candidates:
        if causal_score(article) <= 0:
            continue
        if article.source not in reason_sources:
            reason_sources.append(article.source)
    if len(reason_sources) >= 2:
        best = candidates[0]
        return replace(
            mover,
            move_reason=best.summary or best.title,
            reason_confidence="high",
            reason_sources=tuple(reason_sources[:5]),
        )
    if reason_sources:
        best = candidates[0]
        return replace(
            mover,
            move_reason=best.summary or best.title,
            reason_confidence="medium",
            reason_sources=tuple(reason_sources[:5]),
        )
    return replace(
        mover,
        move_reason="Shares moved sharply, but the immediate catalyst was not clear from major headlines.",
        reason_confidence="low",
        reason_sources=tuple(article.source for article in candidates[:3]),
    )


def explain_movers(movers: tuple[MarketMover, ...], articles: list[Article]) -> tuple[MarketMover, ...]:
    return tuple(explain_mover(mover, articles) for mover in movers)
