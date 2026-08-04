from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal


CategoryName = str
DEFAULT_CATEGORY_FETCH_RESERVES: dict[CategoryName, int] = {
    "business_tech": 40,
    "domestic": 40,
    "global": 40,
    "finance": 40,
    "culture": 30,
}
CultureLane = Literal["", "film_tv", "music", "sports", "gaming", "media_creators", "internet_culture"]
OpenAIMode = Literal["full", "classify-only", "off"]
EnrichmentStatus = Literal[
    "not_attempted", "feed_content", "extracted", "metadata_only",
    "not_permitted", "blocked", "failed", "too_thin",
]
DraftStatus = Literal["llm", "fallback_disabled", "fallback_error", "fallback_missing"]
CompressionStatus = Literal[
    "compressed",
    "kept_original_no_gain",
    "kept_original_guard_failed",
    "kept_original_error",
    "kept_original_budget_exhausted",
    "skipped_short",
    "skipped_fallback_draft",
    "disabled",
]


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
    culture_lane: CultureLane = ""


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
    include_links_telegram: bool = False


@dataclass(frozen=True)
class ExtractionPolicyConfig:
    id: str
    allowed_domains: tuple[str, ...]
    policy: Literal["disabled", "metadata_only", "article_text"] = "disabled"
    source_role: Literal["primary", "publisher"] = "publisher"


@dataclass(frozen=True)
class EnrichmentConfig:
    enabled: bool = True
    max_clusters_per_run: int = 60
    global_cluster_slots: int = 20
    reserved_clusters_per_category: int = 8
    max_articles_per_cluster: int = 2
    max_pages_per_run: int = 50
    request_timeout_seconds: float = 8.0
    max_response_bytes: int = 2_000_000
    max_extracted_chars: int = 6_000
    minimum_extracted_chars: int = 300
    minimum_story_evidence_score: float = 1.2
    policies: tuple[ExtractionPolicyConfig, ...] = ()


@dataclass(frozen=True)
class QualityGateConfig:
    min_summary_chars: int = 80
    summary_duplicate_threshold: float = 0.85
    ambiguous_penalty_weight: float = 0.4
    clear_bad_penalty_weight: float = 1.5
    max_content_quality_penalty: float = 2.5
    low_content_quality_skip_threshold: float = 1.0


@dataclass(frozen=True)
class CategorySelectionLimit:
    floor: int
    ceiling: int
    big_day_max: int


DEFAULT_CATEGORY_SELECTION_LIMITS: dict[CategoryName, CategorySelectionLimit] = {
    "business_tech": CategorySelectionLimit(3, 5, 6),
    "domestic": CategorySelectionLimit(3, 5, 6),
    "global": CategorySelectionLimit(3, 5, 6),
    "finance": CategorySelectionLimit(3, 5, 6),
    "culture": CategorySelectionLimit(2, 5, 6),
}


@dataclass(frozen=True)
class ImportanceConfig:
    enabled: bool = True
    logistic_midpoint: float = 12.0
    logistic_steepness: float = 0.30
    llm_weight: float = 0.65
    clamp_down: float = 25.0
    clamp_up: float = 100.0
    deck_target: int = 25
    big_day_importance_threshold: float = 70.0
    big_day_requires_corroboration: bool = True
    calibration_version: str = "total-score-v1-2026-07-21"


@dataclass(frozen=True)
class OpenAICostConfig:
    enabled: bool = True
    model: str = "gpt-5.6-terra"
    max_cost_usd_per_run: float = 1.00
    input_cost_usd_per_million_tokens: float = 2.50
    output_cost_usd_per_million_tokens: float = 15.00
    # The production config reserves funds for email Watchlist work. Keep this
    # neutral for programmatic/test construction that does not enable email.
    watchlist_reserve_usd: float = 0.0


@dataclass(frozen=True)
class DraftingConfig:
    model: str = "gpt-5.6-terra"
    max_output_tokens_per_batch: int = 6000


@dataclass(frozen=True)
class CompressionConfig:
    enabled: bool = False
    model: str = "gpt-5.6-terra"
    min_words_to_compress: int = 40
    min_words_floor: int = 20
    compress_fallback_drafts: bool = False
    guard_entities: bool = True
    max_output_tokens_per_batch: int = 1200


@dataclass(frozen=True)
class DuplicateGateConfig:
    enabled: bool = True
    candidate_window_hours: float = 24.0
    candidate_title_jaccard_threshold: float = 0.20
    max_clusters_per_request: int = 40
    max_output_tokens_per_request: int = 2000
    reasoning_effort: str = "medium"
    max_component_size: int = 4
    summary_truncate_chars: int = 250


@dataclass(frozen=True)
class AgentConfig:
    feeds: tuple[FeedConfig, ...]
    categories: dict[CategoryName, CategoryConfig]
    lookback_hours: int
    max_articles: int
    category_fetch_reserves: dict[CategoryName, int] = field(
        default_factory=lambda: dict(DEFAULT_CATEGORY_FETCH_RESERVES)
    )
    importance: ImportanceConfig = field(default_factory=ImportanceConfig)
    openai_costs: OpenAICostConfig = field(default_factory=OpenAICostConfig)
    drafting: DraftingConfig = field(default_factory=DraftingConfig)
    duplicate_gate: DuplicateGateConfig = field(default_factory=DuplicateGateConfig)
    category_selection_limits: dict[CategoryName, CategorySelectionLimit] = field(
        default_factory=lambda: dict(DEFAULT_CATEGORY_SELECTION_LIMITS)
    )
    compression: CompressionConfig = field(default_factory=CompressionConfig)
    max_per_source_per_category: int = 2
    big_day_source_cap: int = 3
    formatting: FormattingConfig = field(default_factory=FormattingConfig)
    quality_gate: QualityGateConfig = field(default_factory=QualityGateConfig)
    enrichment: EnrichmentConfig = field(default_factory=EnrichmentConfig)


@dataclass(frozen=True)
class Article:
    title: str
    url: str
    source: str
    published_at: datetime
    summary: str = ""
    canonical_url: str = ""
    feed_content: str = ""
    extracted_text: str = ""
    enrichment_status: EnrichmentStatus = "not_attempted"
    enrichment_error_code: str = ""
    evidence_score: float = 0.0
    reputation: float = 0.7
    feed_categories: tuple[CategoryName, ...] = ()
    feed_source_type: str = "general"
    feed_culture_lane: CultureLane = ""
    feed_timestamp_valid: bool = True
    content_quality_penalty: float = 0.0

    @property
    def text(self) -> str:
        return f"{self.title}. {self.best_available_text}".strip()

    @property
    def best_available_text(self) -> str:
        return self.extracted_text or self.feed_content or self.summary


@dataclass
class StoryCluster:
    key: str
    title: str
    articles: list[Article] = field(default_factory=list)
    category: CategoryName = ""
    culture_lane: CultureLane = ""
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
    importance: float = 0.0
    why_it_matters: str = ""
    watchlist_matches: tuple[str, ...] = ()
    is_update: bool = False
    update_note: str = ""
    confidence: str = ""
    skip_reason: str = ""
    content_quality_penalty: float = 0.0
    evidence_score: float = 0.0
    merged_from: tuple[str, ...] = ()

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
        for article in sorted(self.articles, key=lambda item: (item.evidence_score, item.reputation), reverse=True):
            if article.best_available_text:
                return article.best_available_text
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
            samples.append(f"{article.source}: {article.title} {article.best_available_text}".strip())
        return "\n".join(samples)


@dataclass(frozen=True)
class CategoryAssignment:
    category: CategoryName
    rationale: str
    outlier_urls: tuple[str, ...] = ()
    culture_lane: CultureLane = ""
    llm_importance: int | None = None


@dataclass(frozen=True)
class OpenAICapabilities:
    judge_quality: bool
    classify: bool
    draft: bool


@dataclass(frozen=True)
class BriefingParagraph:
    story_id: str
    category: CategoryName
    paragraph: str
    sources: tuple[str, ...]
    urls: tuple[str, ...] = ()
    draft_status: DraftStatus = "llm"
    draft_error_code: str = ""
    full_paragraph: str = ""
    compression_status: CompressionStatus | str = ""
    compression_ratio: float = 0.0
    is_merged: bool = False


@dataclass(frozen=True)
class PipelineDiagnostics:
    articles_fetched: int = 0
    pages_attempted: int = 0
    pages_extracted: int = 0
    pages_blocked: int = 0
    pages_failed: int = 0
    feed_content_articles: int = 0
    llm_drafts: int = 0
    fallback_drafts: int = 0
    fallback_story_ids: tuple[str, ...] = ()
    drafting_input_tokens: int = 0
    drafting_output_tokens: int = 0
    drafting_cost_usd: float = 0.0
    drafting_budget_exhausted: bool = False
    fetched_articles_by_feed_hint: dict[str, int] = field(default_factory=dict)
    preliminary_clusters_by_feed_hint: dict[str, int] = field(default_factory=dict)
    enrichment_clusters_by_feed_hint: dict[str, int] = field(default_factory=dict)
    classification_pool_by_feed_hint: dict[str, int] = field(default_factory=dict)
    history_suppressed_by_feed_hint: dict[str, int] = field(default_factory=dict)
    insufficient_context_by_feed_hint: dict[str, int] = field(default_factory=dict)
    classified_clusters_by_category: dict[str, int] = field(default_factory=dict)
    backfill_candidates_by_category: dict[str, int] = field(default_factory=dict)
    selected_stories_by_category: dict[str, int] = field(default_factory=dict)
    underfilled_reason_by_category: dict[str, str] = field(default_factory=dict)
    importance_by_category: dict[str, dict[str, float | int]] = field(default_factory=dict)
    floor_selected_by_category: dict[str, int] = field(default_factory=dict)
    remainder_selected_by_category: dict[str, int] = field(default_factory=dict)
    big_day_selected_by_category: dict[str, int] = field(default_factory=dict)
    source_cap_relaxed_by_category: dict[str, int] = field(default_factory=dict)
    deck_target: int = 0
    deck_selected: int = 0
    deck_underfilled_reason: str = ""
    duplicate_gate_deck_size: int = 0
    duplicate_gate_eligible_pairs: int = 0
    duplicate_gate_candidate_components: int = 0
    duplicate_gate_clusters_offered: int = 0
    duplicate_gate_components_dropped: int = 0
    duplicate_gate_sets_returned: int = 0
    duplicate_gate_sets_rejected: int = 0
    duplicate_gate_sets_merged: int = 0
    duplicate_gate_clusters_removed: int = 0
    duplicate_gate_cross_category_merges: int = 0
    compressed_count: int = 0
    compression_status_counts: dict[str, int] = field(default_factory=dict)
    median_compression_ratio: float = 0.0
    guard_failures: int = 0
    entity_warnings: int = 0
    compression_cost_usd: float = 0.0
    compression_budget_exhausted: bool = False
    openai_input_tokens: int = 0
    openai_output_tokens: int = 0
    openai_cost_usd: float = 0.0
    openai_budget_exhausted: bool = False
    openai_cost_by_stage: dict[str, float] = field(default_factory=dict)
    openai_stage_outcomes: dict[str, dict[str, object]] = field(default_factory=dict)


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
    previous_close: float | None = None
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
