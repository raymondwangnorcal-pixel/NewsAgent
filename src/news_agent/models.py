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
    source_type: str = "general"
    region: str = "global"
    quality_weight: float = 1.0
    political_leaning: str = ""


@dataclass(frozen=True)
class CategoryConfig:
    name: CategoryName
    label: str


@dataclass(frozen=True)
class FormattingConfig:
    max_chars_per_message_sms: int = 1400
    max_stories_per_category_sms: int = 5
    max_sources_per_story: int = 3
    include_links_sms: bool = False
    include_links_telegram: bool = True


@dataclass(frozen=True)
class QualityGateConfig:
    min_summary_chars: int = 80
    summary_duplicate_threshold: float = 0.85
    ambiguous_penalty_weight: float = 0.4
    clear_bad_penalty_weight: float = 1.5
    max_content_quality_penalty: float = 2.5
    low_content_quality_skip_threshold: float = 1.0


@dataclass(frozen=True)
class AgentConfig:
    feeds: tuple[FeedConfig, ...]
    categories: dict[CategoryName, CategoryConfig]
    lookback_hours: int
    max_articles: int
    formatting: FormattingConfig = field(default_factory=FormattingConfig)
    quality_gate: QualityGateConfig = field(default_factory=QualityGateConfig)


@dataclass(frozen=True)
class Article:
    title: str
    url: str
    source: str
    published_at: datetime
    summary: str = ""
    reputation: float = 0.7
    feed_categories: tuple[CategoryName, ...] = ()
    feed_source_type: str = "general"
    content_quality_penalty: float = 0.0

    @property
    def text(self) -> str:
        return f"{self.title}. {self.summary}".strip()


@dataclass
class StoryCluster:
    key: str
    title: str
    articles: list[Article] = field(default_factory=list)
    category: CategoryName = ""
    """Set once by the classification stage (news_agent.classify) -- exactly one
    category per cluster, never a keyword-derived score dict. Empty string means
    not yet classified or classified as not fitting any category."""
    impact_score: float = 0.0
    frequency_score: float = 0.0
    recency_score: float = 0.0
    quality_score: float = 0.0
    source_balance_score: float = 0.0
    watchlist_score: float = 0.0
    total_score: float = 0.0
    why_it_matters: str = ""
    watchlist_matches: tuple[str, ...] = ()
    is_update: bool = False
    update_note: str = ""
    confidence: str = ""
    skip_reason: str = ""
    content_quality_penalty: float = 0.0

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
    def representative_summary(self) -> str:
        for article in sorted(self.articles, key=lambda item: item.reputation, reverse=True):
            if article.summary:
                return article.summary
        return self.title

    @property
    def urls(self) -> tuple[str, ...]:
        seen: set[str] = set()
        ordered: list[str] = []
        for article in self.articles:
            if article.url not in seen:
                ordered.append(article.url)
                seen.add(article.url)
        return tuple(ordered)

    @property
    def source_count(self) -> int:
        return len(self.sources)

    @property
    def merged_text(self) -> str:
        samples = []
        for article in self.articles[:5]:
            samples.append(f"{article.source}: {article.title} {article.summary}".strip())
        return "\n".join(samples)


@dataclass(frozen=True)
class CategoryAssignment:
    category: CategoryName
    rationale: str
    outlier_urls: tuple[str, ...] = ()


@dataclass(frozen=True)
class BriefingParagraph:
    story_id: str
    category: CategoryName
    paragraph: str
    sources: tuple[str, ...]
    urls: tuple[str, ...] = ()


@dataclass(frozen=True)
class BriefingSection:
    category: CategoryName
    label: str
    paragraphs: tuple[BriefingParagraph, ...]
    lead_lines: tuple[str, ...] = ()
    """Optional factual, non-prose preamble lines rendered above the paragraphs
    (e.g. finance's live market-quote ticker). Never LLM-generated -- this is
    for real numeric/reference data a drafting model shouldn't be trusted to
    state from memory, kept structurally separate from editorial paragraphs."""


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
class MarketMover:
    symbol: str
    name: str
    asset_type: str
    latest_price: float
    previous_close: float
    absolute_change: float
    percent_change: float
    volume: int | None = None
    as_of: str = ""
    importance: float = 1.0
    threshold: float = 0.0
    move_reason: str = ""
    reason_confidence: str = "low"
    reason_sources: tuple[str, ...] = ()
    watchlist_matches: tuple[str, ...] = ()


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
    market_movers: tuple[MarketMover, ...] = ()

    def quote_for(self, symbol: str) -> StockQuote:
        return self.quotes.get(symbol, StockQuote(symbol=symbol))
