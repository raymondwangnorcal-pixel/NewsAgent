from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


CategoryName = str


@dataclass(frozen=True)
class FeedConfig:
    name: str
    url: str
    reputation: float
    categories: tuple[CategoryName, ...]


@dataclass(frozen=True)
class CategoryConfig:
    name: CategoryName
    label: str
    keywords: tuple[str, ...]
    impact_terms: tuple[str, ...]


@dataclass(frozen=True)
class AgentConfig:
    feeds: tuple[FeedConfig, ...]
    categories: dict[CategoryName, CategoryConfig]
    lookback_hours: int
    max_articles: int


@dataclass(frozen=True)
class Article:
    title: str
    url: str
    source: str
    published_at: datetime
    summary: str = ""
    reputation: float = 0.7
    feed_categories: tuple[CategoryName, ...] = ()

    @property
    def text(self) -> str:
        return f"{self.title}. {self.summary}".strip()


@dataclass
class StoryCluster:
    key: str
    title: str
    articles: list[Article] = field(default_factory=list)
    category_scores: dict[CategoryName, float] = field(default_factory=dict)
    impact_score: float = 0.0
    frequency_score: float = 0.0
    recency_score: float = 0.0
    total_score: float = 0.0

    @property
    def sources(self) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for article in sorted(self.articles, key=lambda item: item.reputation, reverse=True):
            if article.source not in seen:
                ordered.append(article.source)
                seen.add(article.source)
        return ordered

    @property
    def latest_published_at(self) -> datetime:
        if not self.articles:
            return datetime.now(timezone.utc)
        return max(article.published_at for article in self.articles)

    @property
    def merged_text(self) -> str:
        samples = []
        for article in self.articles[:5]:
            samples.append(f"{article.source}: {article.title} {article.summary}".strip())
        return "\n".join(samples)


@dataclass(frozen=True)
class BriefingItem:
    headline: str
    summary: str
    why_it_matters: str
    sources: tuple[str, ...]
    next_watch: str = ""


@dataclass(frozen=True)
class BriefingText:
    category: str
    title: str
    items: tuple[BriefingItem, ...]

    def to_message(self, max_sources: int = 3) -> str:
        lines = [self.title, ""]
        for index, item in enumerate(self.items, start=1):
            sources = ", ".join(item.sources[:max_sources])
            lines.append(f"{index}. {item.headline}")
            lines.append(f"Summary: {item.summary}")
            lines.append(f"Why it matters: {item.why_it_matters}")
            if item.next_watch:
                lines.append(f"Watch: {item.next_watch}")
            if sources:
                lines.append(f"Sources: {sources}")
            if index < len(self.items):
                lines.append("")
        return "\n".join(lines).strip()

    def to_sms(self, max_sources: int = 3) -> str:
        return self.to_message(max_sources=max_sources)


@dataclass(frozen=True)
class StockQuote:
    symbol: str
    price: float | None = None
    change_percent: float | None = None
    open_price: float | None = None
    volume: int | None = None
    as_of: str = ""
    provider: str = "Yahoo Finance"

    def compact(self) -> str:
        if self.price is None:
            return f"{self.symbol}: quote unavailable"
        change = ""
        if self.change_percent is not None:
            change = f" ({self.change_percent:+.2f}%)"
        return f"{self.symbol} {self.price:.2f}{change}"


@dataclass(frozen=True)
class StockMention:
    symbol: str
    mention_count: int
    headlines: tuple[str, ...]
    sources: tuple[str, ...]


@dataclass(frozen=True)
class StockSnapshot:
    news_mentions: tuple[StockMention, ...]
    mega_caps: tuple[str, ...]
    quotes: dict[str, StockQuote]

    def quote_for(self, symbol: str) -> StockQuote:
        return self.quotes.get(symbol, StockQuote(symbol=symbol))
