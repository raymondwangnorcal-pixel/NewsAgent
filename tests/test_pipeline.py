from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest

import news_agent.pipeline as pipeline
from news_agent.models import (
    AgentConfig,
    Article,
    BriefingParagraph,
    BriefingSection,
    CategoryAssignment,
    QualityGateConfig,
    StockSnapshot,
    StoryCluster,
    SourceTierConfig,
)


def cluster(key: str, title: str, total_score: float = 0.0, category: str = "") -> StoryCluster:
    return StoryCluster(key=key, title=title, total_score=total_score, category=category)


def minimal_config() -> AgentConfig:
    return AgentConfig(
        feeds=(),
        categories={},
        lookback_hours=30,
        max_articles=50,
        quality_gate=QualityGateConfig(),
    )


def make_article(
    title: str,
    url: str,
    summary: str,
    source: str = "Reuters",
) -> Article:
    return Article(
        title=title,
        url=url,
        source=source,
        published_at=datetime.now(timezone.utc),
        summary=summary,
    )


def _patch_fetch_and_stock(monkeypatch: pytest.MonkeyPatch, articles: list[Article]) -> None:
    async def fake_fetch_all_feeds(*args: object, **kwargs: object) -> list[Article]:
        return articles

    async def fake_build_stock_snapshot(*args: object, **kwargs: object) -> StockSnapshot:
        return StockSnapshot(news_mentions=(), mega_caps=(), quotes={})

    monkeypatch.setattr(pipeline, "fetch_all_feeds", fake_fetch_all_feeds)
    monkeypatch.setattr(pipeline, "build_stock_snapshot", fake_build_stock_snapshot)


# --- Selection pool bounding -----------------------------------------------------


def test_select_classification_candidates_bounds_pool_size() -> None:
    clusters = [cluster(f"c{i}", f"Story {i}", total_score=float(100 - i)) for i in range(80)]

    candidates = pipeline.select_classification_candidates(clusters, pool_size=50)

    assert len(candidates) == 50
    # highest-importance clusters first, category-agnostic
    assert candidates[0].key == "c0"
    assert candidates[-1].key == "c49"


def test_select_classification_candidates_excludes_skipped_clusters() -> None:
    keep = cluster("keep", "Keep me", total_score=10)
    skipped = cluster("skip", "Skip me", total_score=20)
    skipped.skip_reason = "stale/repeated from yesterday"

    candidates = pipeline.select_classification_candidates([keep, skipped], pool_size=50)

    assert candidates == [keep]


# --- Category assignment application ----------------------------------------------


def test_apply_category_assignments_sets_cluster_category() -> None:
    story = cluster("story-1", "Some story")
    assignments = {"story-1": CategoryAssignment(category="finance", rationale="Market story.")}

    pipeline.apply_category_assignments([story], assignments)

    assert story.category == "finance"


def test_apply_category_assignments_leaves_unassigned_clusters_uncategorized() -> None:
    story = cluster("story-1", "Some story")

    pipeline.apply_category_assignments([story], {})

    assert story.category == ""


def test_apply_source_tier_scoring_boosts_primary_and_secondary_not_specialist() -> None:
    config = minimal_config()
    config = AgentConfig(
        feeds=config.feeds,
        categories=config.categories,
        lookback_hours=config.lookback_hours,
        max_articles=config.max_articles,
        source_tiers={
            "finance": SourceTierConfig("Bloomberg", "Reuters", "Financial Times")
        },
    )
    primary = cluster("primary", "Primary", total_score=10, category="finance")
    primary.articles = [make_article("Primary", "https://example.com/p", "Summary", source="Bloomberg")]
    secondary = cluster("secondary", "Secondary", total_score=10, category="finance")
    secondary.articles = [make_article("Secondary", "https://example.com/s", "Summary", source="Reuters")]
    specialist = cluster("specialist", "Specialist", total_score=10, category="finance")
    specialist.articles = [make_article("Specialist", "https://example.com/f", "Summary", source="Financial Times")]

    pipeline.apply_source_tier_scoring([primary, secondary, specialist], config)

    assert primary.total_score == pytest.approx(10 + pipeline.PRIMARY_SOURCE_TIER_BOOST)
    assert secondary.total_score == pytest.approx(10 + pipeline.SECONDARY_SOURCE_TIER_BOOST)
    assert specialist.total_score == 10
    assert specialist.specialist_article_urls == ("https://example.com/f",)


def test_apply_source_tier_scoring_skips_explicit_wire_syndication() -> None:
    wire = make_article("Event", "https://reuters.com/event", "Wire summary", source="Reuters")
    copy = make_article("Event - Reuters", "https://local.test/event", "Wire summary", source="Local Gazette")
    story = cluster("event", "Event", category="global")
    story.articles = [wire, copy]

    pipeline.apply_source_tier_scoring([story], minimal_config())

    assert story.source_count == 2
    assert story.corroboration_status == "single_source"
    assert story.skip_reason == "single wire-syndicated source only"
    assert story.sources == ["Reuters", "Local Gazette"]


def test_apply_source_tier_scoring_uncertain_similarity_never_collapses_count() -> None:
    summary = "Officials announced a detailed policy change affecting millions of households next year."
    wire = make_article("Event", "https://reuters.com/event", summary, source="Reuters")
    similar = make_article("Event", "https://local.test/event", summary, source="Local Gazette")
    story = cluster("event", "Event", category="global")
    story.articles = [wire, similar]

    pipeline.apply_source_tier_scoring([story], minimal_config())

    assert story.corroboration_status == "confirmed"
    assert story.skip_reason == ""
    assert story.source_attributions[1]["confidence"] == "uncertain"


# --- select_unique_category_clusters: structural dedup ----------------------------


def test_select_unique_category_clusters_each_story_in_exactly_one_category() -> None:
    business = cluster("nvidia-export", "Nvidia falls on export restrictions", total_score=20, category="business_tech")
    finance = cluster("fed-rates", "Fed rate outlook moves markets", total_score=18, category="finance")
    global_story = cluster("global-conflict", "Conflict escalates overseas", total_score=17, category="global")

    selected = pipeline.select_unique_category_clusters([business, finance, global_story])

    assert business in selected["business_tech"]
    assert business not in selected["finance"]
    assert finance in selected["finance"]
    assert set(selected) == {"business_tech", "domestic", "global", "culture", "finance"}


def test_select_unique_category_clusters_respects_per_category_limit() -> None:
    clusters = [
        cluster(f"finance-{i}", f"Finance story {i}", total_score=float(10 - i), category="finance") for i in range(10)
    ]

    selected = pipeline.select_unique_category_clusters(clusters)

    assert len(selected["finance"]) == pipeline.CATEGORY_LIMITS["finance"]


def test_selected_clusters_deduplicates_by_story_identity() -> None:
    original = cluster("same-story", "Original headline", 10, category="business_tech")
    duplicate = cluster("same-story", "Duplicate headline", 9, category="finance")

    selected = pipeline.selected_clusters({"business_tech": [original], "finance": [duplicate]})

    assert selected == [original]


# --- build_draft_candidates: outlier filtering -------------------------------------


def test_build_draft_candidates_drops_outlier_articles() -> None:
    keep_article = make_article("Kept story", "https://example.com/keep", "Summary text.")
    outlier_article = make_article("Unrelated story", "https://example.com/outlier", "Other summary.")
    story = cluster("mixed", "Mixed cluster")
    story.articles = [keep_article, outlier_article]

    assignments = {
        "mixed": CategoryAssignment(
            category="business_tech",
            rationale="Kept the main story.",
            outlier_urls=("https://example.com/outlier",),
        )
    }

    candidates = pipeline.build_draft_candidates({"business_tech": [story]}, assignments)

    assert len(candidates) == 1
    assert [a.url for a in candidates[0].articles] == ["https://example.com/keep"]


def test_build_draft_candidates_keeps_all_articles_when_no_outliers() -> None:
    articles = [make_article("A", "https://example.com/a", "Summary A")]
    story = cluster("clean", "Clean cluster")
    story.articles = articles

    candidates = pipeline.build_draft_candidates({"business_tech": [story]}, {})

    assert len(candidates[0].articles) == 1


def test_build_draft_candidates_propagates_corroboration_and_specialist_flags() -> None:
    specialist = make_article("Analysis", "https://ft.com/analysis", "Context", source="Financial Times")
    story = cluster("context", "Context")
    story.articles = [specialist]
    story.corroboration_status = "single_source"
    story.specialist_article_urls = (specialist.url,)

    candidates = pipeline.build_draft_candidates({"finance": [story]}, {})

    assert candidates[0].corroboration_status == "single_source"
    assert candidates[0].specialist_article_urls == (specialist.url,)


def test_build_draft_candidates_falls_back_to_all_articles_if_outliers_cover_everything() -> None:
    # Defensive: never produce a zero-article candidate just because the
    # classifier flagged every article as an outlier.
    article = make_article("A", "https://example.com/a", "Summary A")
    story = cluster("all-outliers", "All flagged")
    story.articles = [article]
    assignments = {
        "all-outliers": CategoryAssignment(
            category="business_tech", rationale="", outlier_urls=("https://example.com/a",)
        )
    }

    candidates = pipeline.build_draft_candidates({"business_tech": [story]}, assignments)

    assert len(candidates[0].articles) == 1


# --- build_briefing_sections: grouping + finance lead lines ------------------------


def test_build_briefing_sections_groups_paragraphs_and_covers_all_categories() -> None:
    paragraphs = [
        BriefingParagraph(story_id="s1", category="finance", paragraph="Markets moved.", sources=("Reuters",)),
        BriefingParagraph(story_id="s2", category="culture", paragraph="A film opened.", sources=("Variety",)),
    ]
    config = minimal_config()

    sections = pipeline.build_briefing_sections(paragraphs, config, StockSnapshot(news_mentions=(), mega_caps=(), quotes={}))

    by_category = {section.category: section for section in sections}
    assert set(by_category) == {"business_tech", "domestic", "global", "culture", "finance"}
    assert len(by_category["finance"].paragraphs) == 1
    assert len(by_category["business_tech"].paragraphs) == 0


def test_build_briefing_sections_finance_gets_lead_lines_other_categories_dont() -> None:
    class FakeQuote:
        def __init__(self, text: str) -> None:
            self._text = text

        def compact(self) -> str:
            return self._text

    class FakeSnapshot:
        mega_caps = ("AAPL", "NVDA")

        def quote_for(self, symbol: str) -> FakeQuote:
            return FakeQuote(f"{symbol} 100.00 (+1.0%)")

    config = minimal_config()
    sections = pipeline.build_briefing_sections([], config, FakeSnapshot())
    by_category = {section.category: section for section in sections}

    assert by_category["finance"].lead_lines == ("AAPL 100.00 (+1.0%)", "NVDA 100.00 (+1.0%)")
    assert by_category["business_tech"].lead_lines == ()


# --- collect_pipeline_context: quality gate + classification wiring ---------------


def test_collect_pipeline_context_hard_rejects_articles_before_clustering(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    junk_article = make_article(
        title="Fed cuts interest rates by a quarter point",
        url="https://example.com/junk",
        summary="",  # empty summary -> hard reject
    )
    good_article = make_article(
        title="Central bank lowers benchmark lending rate to ease credit conditions",
        url="https://example.com/good",
        summary=(
            "The central bank lowered its benchmark lending rate by a quarter point on "
            "Wednesday, citing cooling inflation and a resilient but slowing labor market "
            "as it continues a gradual path toward easier credit conditions nationwide."
        ),
    )
    _patch_fetch_and_stock(monkeypatch, [junk_article, good_article])

    context = asyncio.run(
        pipeline.collect_pipeline_context(
            minimal_config(),
            openai_mode="off",
            quality_gate_log_path=tmp_path / "quality_gate_rejections.json",
            category_assignments_log_path=tmp_path / "category_assignments.json",
        )
    )

    assert len(context.quality_gate_rejections) == 1
    rejected_article, reason = context.quality_gate_rejections[0]
    assert rejected_article.url == junk_article.url
    assert reason == "empty_summary"

    all_urls = {url for c in context.all_clusters for url in c.urls}
    assert junk_article.url not in all_urls
    assert good_article.url in all_urls

    logged = json.loads((tmp_path / "quality_gate_rejections.json").read_text())
    assert logged == [
        {
            "title": junk_article.title,
            "source": junk_article.source,
            "url": junk_article.url,
            "reason": "empty_summary",
        }
    ]


def test_collect_pipeline_context_soft_penalized_articles_reach_clustering(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    thin_article = make_article(
        title="Regional airline announces new routes",
        url="https://example.com/thin",
        summary="Short update from a wire service.",
    )
    _patch_fetch_and_stock(monkeypatch, [thin_article])

    context = asyncio.run(
        pipeline.collect_pipeline_context(
            minimal_config(),
            openai_mode="off",
            quality_gate_log_path=tmp_path / "quality_gate_rejections.json",
            category_assignments_log_path=tmp_path / "category_assignments.json",
        )
    )

    assert context.quality_gate_rejections == ()
    all_urls = {url for c in context.all_clusters for url in c.urls}
    assert thin_article.url in all_urls

    matching = [c for c in context.all_clusters if thin_article.url in c.urls]
    assert len(matching) == 1
    assert matching[0].content_quality_penalty == pytest.approx(QualityGateConfig().ambiguous_penalty_weight)


def test_collect_pipeline_context_off_mode_never_calls_classify_llm(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    article = make_article(
        title="Some story", url="https://example.com/a", summary="A reasonably detailed summary of the event."
    )
    _patch_fetch_and_stock(monkeypatch, [article])

    context = asyncio.run(
        pipeline.collect_pipeline_context(
            minimal_config(),
            openai_mode="off",
            quality_gate_log_path=tmp_path / "quality_gate_rejections.json",
            category_assignments_log_path=tmp_path / "category_assignments.json",
        )
    )

    # off mode -> classify_clusters_fallback only, every cluster gets *some*
    # assignment (possibly "" if no feed-tag signal), never a crash.
    assert len(context.category_assignments) == len(context.all_clusters)


def test_collect_pipeline_context_writes_category_assignments_log(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    article = make_article(
        title="Some story",
        url="https://example.com/a",
        summary="A reasonably detailed summary of the event.",
    )
    _patch_fetch_and_stock(monkeypatch, [article])
    log_path = tmp_path / "category_assignments.json"

    asyncio.run(
        pipeline.collect_pipeline_context(
            minimal_config(),
            openai_mode="off",
            quality_gate_log_path=tmp_path / "quality_gate_rejections.json",
            category_assignments_log_path=log_path,
        )
    )

    assert log_path.exists()
    logged = json.loads(log_path.read_text())
    assert isinstance(logged, list)


def test_collect_pipeline_context_applies_llm_verdicts_to_ambiguous_articles(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    thin_article = make_article(
        title="Regional airline announces new routes",
        url="https://example.com/thin",
        summary="Short update from a wire service.",
    )
    _patch_fetch_and_stock(monkeypatch, [thin_article])

    def fake_judge(articles: list[Article], model: str | None = None) -> dict[str, str]:
        assert [a.url for a in articles] == [thin_article.url]
        return {thin_article.url: "good"}

    monkeypatch.setattr(pipeline, "judge_ambiguous_articles", fake_judge)
    monkeypatch.setattr(pipeline, "classify_clusters", lambda candidates, openai_mode: {})

    context = asyncio.run(
        pipeline.collect_pipeline_context(
            minimal_config(),
            openai_mode="full",
            quality_gate_log_path=tmp_path / "quality_gate_rejections.json",
            category_assignments_log_path=tmp_path / "category_assignments.json",
        )
    )

    matching = [c for c in context.all_clusters if thin_article.url in c.urls]
    assert len(matching) == 1
    assert matching[0].content_quality_penalty == pytest.approx(0.0)


def test_build_briefing_result_threads_quality_gate_config_threshold(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    heavily_penalized = cluster("penalized-story", "Heavily penalized story", total_score=5.0)
    heavily_penalized.content_quality_penalty = 0.9

    async def fake_collect_pipeline_context(*args: object, **kwargs: object) -> pipeline.PipelineContext:
        return pipeline.PipelineContext(
            category_clusters={},
            stock_snapshot=object(),
            all_clusters=[heavily_penalized],
            quality_gate_rejections=(),
            quality_gate_log_path=tmp_path / "quality_gate_rejections.json",
        )

    monkeypatch.setattr(pipeline, "collect_pipeline_context", fake_collect_pipeline_context)

    config = AgentConfig(
        feeds=(),
        categories={},
        lookback_hours=30,
        max_articles=50,
        quality_gate=QualityGateConfig(low_content_quality_skip_threshold=0.5),
    )

    result = asyncio.run(
        pipeline.build_briefing_result(
            openai_mode="off",
            config=config,
            watchlist_path=None,
            history_path=tmp_path / "history.json",
            persist_history=False,
            skipped_log_path=tmp_path / "skipped.json",
        )
    )

    assert len(result.skipped_stories) == 1
    assert result.skipped_stories[0].reason_skipped == "low content quality"
    assert result.quality_gate_log_path == tmp_path / "quality_gate_rejections.json"


# --- End-to-end: fetch -> ... -> BriefingSection, fully mocked at the network edges --


def test_build_briefing_result_end_to_end_off_mode(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    article = make_article(
        title="OpenAI releases a new model",
        url="https://example.com/openai",
        summary=(
            "OpenAI released a new flagship model on Tuesday, saying it outperforms prior "
            "versions on reasoning and coding benchmarks while cutting inference costs."
        ),
        source="TechCrunch",
    )
    _patch_fetch_and_stock(monkeypatch, [article])

    result = asyncio.run(
        pipeline.build_briefing_result(
            openai_mode="off",
            config=minimal_config(),
            watchlist_path=None,
            history_path=tmp_path / "history.json",
            persist_history=False,
            skipped_log_path=tmp_path / "skipped.json",
            quality_gate_log_path=tmp_path / "quality_gate_rejections.json",
            category_assignments_log_path=tmp_path / "category_assignments.json",
        )
    )

    assert isinstance(result.briefings, list)
    assert all(isinstance(section, BriefingSection) for section in result.briefings)
    assert {section.category for section in result.briefings} == set(pipeline.CATEGORY_LIMITS)
    # off mode -> classify_clusters_fallback with no feed_categories signal ->
    # category "" -> the article doesn't land in any of the 5 sections, but the
    # pipeline still runs end-to-end without crashing and every section exists.
    assert result.skipped_log_path == tmp_path / "skipped.json"
