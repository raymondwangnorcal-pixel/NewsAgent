from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

import news_agent.pipeline as pipeline
from news_agent.draft import DraftCandidate
from news_agent.models import (
    AgentConfig,
    Article,
    BriefingParagraph,
    BriefingSection,
    CategoryAssignment,
    QualityGateConfig,
    StockSnapshot,
    StoryCluster,
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


def hinted_cluster(key: str, category: str, score: float, evidence: float = 2.0) -> StoryCluster:
    story = cluster(key, key, total_score=score)
    story.evidence_score = evidence
    story.articles = [Article(
        title=key,
        url=f"https://example.com/{key}",
        source="Example",
        published_at=datetime.now(timezone.utc),
        summary="A substantive summary with enough facts and context for classification.",
        feed_categories=(category,),
        evidence_score=evidence,
    )]
    return story


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


def test_cluster_feed_hints_uses_union_and_canonical_order() -> None:
    story = hinted_cluster("dual", "culture", 10)
    story.articles.append(Article(
        title="dual", url="https://example.com/dual-2", source="Other",
        published_at=datetime.now(timezone.utc), feed_categories=("business_tech",),
    ))

    assert pipeline.cluster_feed_hints(story) == ("business_tech", "culture")


def test_classification_pool_reserves_evidence_qualified_culture_candidates() -> None:
    finance = [hinted_cluster(f"finance-{i}", "finance", 200 - i) for i in range(40)]
    culture = [hinted_cluster(f"culture-{i}", "culture", 100 - i) for i in range(12)]
    thin = hinted_cluster("culture-thin", "culture", 500, evidence=0.5)

    selected = pipeline.select_classification_candidates(
        [thin, *finance, *culture], minimum_evidence_score=1.2,
    )

    assert sum("culture" in pipeline.cluster_feed_hints(item) for item in selected) == 10
    assert thin not in selected


def test_openai_capability_matrix() -> None:
    assert pipeline.openai_capabilities("full") == pipeline.OpenAICapabilities(True, True, True)
    assert pipeline.openai_capabilities("classify-only") == pipeline.OpenAICapabilities(True, True, False)
    assert pipeline.openai_capabilities("off") == pipeline.OpenAICapabilities(False, False, False)


def test_backfill_uses_matching_hints_and_deduplicates() -> None:
    initial = [hinted_cluster("initial", "finance", 10)]
    dual = hinted_cluster("dual-backfill", "culture", 9)
    dual.articles[0] = Article(
        title="dual", url="https://example.com/dual", source="Example",
        published_at=datetime.now(timezone.utc), summary="Substantive reporting.",
        feed_categories=("culture", "business_tech"), evidence_score=2.0,
    )

    result = pipeline.select_backfill_candidates(
        [*initial, dual], initial, ["business_tech", "culture"], 1.2,
    )

    assert result == [dual]


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


def test_apply_evidence_gate_excludes_context_poor_story() -> None:
    thin = cluster("thin", "Headline only")
    thin.evidence_score = 0.8
    rich = cluster("rich", "Substantive highlight")
    rich.evidence_score = 3.0

    pipeline.apply_evidence_gate([thin, rich], minimum_score=1.6)

    assert thin.skip_reason == "insufficient story context"
    assert rich.skip_reason == ""


# --- importance deck selection ----------------------------------------------------


def test_select_importance_deck_places_each_story_in_exactly_one_category() -> None:
    business = cluster("nvidia-export", "Nvidia falls on export restrictions", total_score=20, category="business_tech")
    finance = cluster("fed-rates", "Fed rate outlook moves markets", total_score=18, category="finance")
    global_story = cluster("global-conflict", "Conflict escalates overseas", total_score=17, category="global")

    selected = pipeline.select_importance_deck([business, finance, global_story], minimal_config()).category_clusters

    assert business in selected["business_tech"]
    assert business not in selected["finance"]
    assert finance in selected["finance"]
    assert set(selected) == {"business_tech", "domestic", "global", "culture", "finance"}


def test_select_importance_deck_respects_normal_category_ceiling() -> None:
    clusters = [
        cluster(f"finance-{i}", f"Finance story {i}", total_score=float(10 - i), category="finance") for i in range(10)
    ]

    selected = pipeline.select_importance_deck(clusters, minimal_config()).category_clusters

    assert len(selected["finance"]) == 5


def test_select_importance_deck_keeps_culture_lane_diversity() -> None:
    lanes = ("film_tv", "music", "sports", "gaming")
    clusters = []
    for index, lane in enumerate(lanes):
        story = hinted_cluster(f"culture-{index}", "culture", 10 - index)
        story.category = "culture"
        story.culture_lane = lane  # type: ignore[assignment]
        story.articles[0] = Article(
            title=story.title,
            url=f"https://example.com/culture-{index}",
            source=f"Publisher {index}",
            published_at=datetime.now(timezone.utc),
            summary="A substantive culture story with enough verified context to publish.",
            feed_categories=("culture",),
            evidence_score=2.0,
        )
        clusters.append(story)

    selected = pipeline.select_importance_deck(clusters, minimal_config(), minimum_evidence_score=1.2).category_clusters

    assert len(selected["culture"]) == 4
    assert len({story.culture_lane for story in selected["culture"][:2]}) == 2


def test_two_culture_stories_meet_the_configured_floor() -> None:
    config = minimal_config()
    selected = {category: [] for category in pipeline.CATEGORY_NAMES}
    selected["culture"] = [cluster(f"culture-{index}", f"Culture {index}") for index in range(2)]

    assert pipeline.underfilled_categories(selected, config.category_selection_limits) == [
        "business_tech",
        "domestic",
        "global",
        "finance",
    ]


def test_floor_relaxes_non_culture_source_cap_only_as_needed() -> None:
    stories = []
    for index in range(3):
        story = cluster(f"finance-{index}", f"Finance {index}", 20 - index, category="finance")
        story.articles = [make_article(story.title, f"https://example.com/{index}", "Substantive context", "Reuters")]
        stories.append(story)

    result = pipeline.select_importance_deck(stories, minimal_config())

    assert len(result.category_clusters["finance"]) == 3
    assert result.source_cap_relaxed_by_category["finance"] == 1


def test_big_day_phase_never_exceeds_deck_target_or_category_maximum() -> None:
    stories: list[StoryCluster] = []
    for category in ("business_tech", "domestic", "global", "finance"):
        for index in range(6):
            story = cluster(f"{category}-{index}", f"{category} {index}", 30 - index, category=category)
            story.importance = 90 - index
            story.articles = [make_article(
                story.title, f"https://{category}-{index}.example/story", "Substantive context", f"Source {index}"
            )]
            stories.append(story)
    culture = cluster("culture-only", "Culture only", 20, category="culture")
    culture.importance = 80
    culture.culture_lane = "music"
    culture.articles = [make_article("Culture only", "https://culture.example/story", "Context", "Culture Source")]
    stories.append(culture)

    result = pipeline.select_importance_deck(stories, minimal_config())

    assert result.selected_count == 25
    assert all(len(items) <= 6 for items in result.category_clusters.values())
    assert sum(result.big_day_selected_by_category.values()) == 4


def test_llm_only_big_day_elevation_requires_corroboration() -> None:
    config = minimal_config()
    stories: list[StoryCluster] = []
    for category in pipeline.CATEGORY_NAMES:
        count = 6 if category != "culture" else 1
        for index in range(count):
            story = cluster(f"{category}-{index}", f"{category} {index}", 12.0, category=category)
            story.importance = 90.0
            story.culture_lane = "music" if category == "culture" else ""
            story.articles = [make_article(
                story.title, f"https://{category}-{index}.example/story", "Context", f"Source {index}"
            )]
            if category == "business_tech" and index == 0:
                story.articles.append(make_article(
                    story.title,
                    "https://corroborator.example/story",
                    "Independent corroboration",
                    "Corroborating Source",
                ))
            stories.append(story)

    result = pipeline.select_importance_deck(stories, config)

    assert result.selected_count == 22
    assert result.big_day_selected_by_category["business_tech"] == 1
    assert sum(result.big_day_selected_by_category.values()) == 1


def _ordered_story(
    key: str,
    *,
    importance: float,
    total_score: float,
    published_at: datetime,
) -> StoryCluster:
    story = cluster(key, key, total_score=total_score, category="finance")
    story.importance = importance
    story.articles = [Article(
        title=key,
        url=f"https://example.com/{key}",
        source=f"Source {key}",
        published_at=published_at,
        summary="Substantive context for deterministic presentation ordering.",
    )]
    return story


def _drafted_paragraph(story: StoryCluster) -> BriefingParagraph:
    return BriefingParagraph(
        story_id=story.key,
        category=story.category,
        paragraph=f"Draft for {story.key}.",
        sources=tuple(story.sources),
    )


def test_presentation_order_ranks_by_importance_not_total_score() -> None:
    now = datetime(2026, 7, 22, tzinfo=timezone.utc)
    important = _ordered_story("important", importance=90, total_score=10, published_at=now)
    high_legacy_score = _ordered_story("legacy", importance=40, total_score=30, published_at=now)

    result = pipeline.select_importance_deck([high_legacy_score, important], minimal_config())
    draft_order = result.category_clusters["finance"]
    presentation = pipeline.order_paragraphs_for_presentation(
        [_drafted_paragraph(story) for story in draft_order],
        result.category_clusters,
    )

    assert draft_order == [high_legacy_score, important]
    assert [paragraph.story_id for paragraph in presentation] == ["important", "legacy"]


def test_presentation_order_ties_break_by_total_score_then_recency_then_identity() -> None:
    now = datetime(2026, 7, 22, tzinfo=timezone.utc)
    stories = [
        _ordered_story("beta", importance=70, total_score=11, published_at=now - timedelta(hours=1)),
        _ordered_story("recent", importance=70, total_score=11, published_at=now),
        _ordered_story("score-wins", importance=70, total_score=12, published_at=now - timedelta(days=1)),
        _ordered_story("alpha", importance=70, total_score=11, published_at=now - timedelta(hours=1)),
    ]

    result = pipeline.select_importance_deck(stories, minimal_config())
    presentation = pipeline.order_paragraphs_for_presentation(
        [_drafted_paragraph(story) for story in result.category_clusters["finance"]],
        result.category_clusters,
    )

    assert [paragraph.story_id for paragraph in presentation] == [
        "score-wins", "recent", "alpha", "beta",
    ]


def test_presentation_order_matches_total_score_when_importance_disabled() -> None:
    now = datetime(2026, 7, 22, tzinfo=timezone.utc)
    config = minimal_config()
    config = replace(config, importance=replace(config.importance, enabled=False))
    stories = [
        _ordered_story("middle", importance=0, total_score=20, published_at=now),
        _ordered_story("highest", importance=0, total_score=30, published_at=now),
        _ordered_story("lowest", importance=0, total_score=10, published_at=now),
    ]

    result = pipeline.select_importance_deck(stories, config)
    presentation = pipeline.order_paragraphs_for_presentation(
        [_drafted_paragraph(story) for story in result.category_clusters["finance"]],
        result.category_clusters,
    )

    assert [paragraph.story_id for paragraph in presentation] == ["highest", "middle", "lowest"]


def test_build_result_reorders_only_after_drafting(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    now = datetime(2026, 7, 22, tzinfo=timezone.utc)
    important = _ordered_story("important", importance=90, total_score=10, published_at=now)
    high_legacy_score = _ordered_story("legacy", importance=40, total_score=30, published_at=now)
    category_clusters = {category: [] for category in pipeline.CATEGORY_NAMES}
    category_clusters["finance"] = [high_legacy_score, important]
    context = pipeline.PipelineContext(
        category_clusters=category_clusters,
        stock_snapshot=StockSnapshot(news_mentions=(), mega_caps=(), quotes={}),
        all_clusters=[high_legacy_score, important],
    )
    drafted_ids: list[str] = []

    async def fake_collect(*args: object, **kwargs: object) -> pipeline.PipelineContext:
        return context

    def fake_draft(candidates: list[DraftCandidate], **kwargs: object) -> pipeline.DraftRunResult:
        drafted_ids.extend(candidate.story_id for candidate in candidates)
        return pipeline.DraftRunResult([
            BriefingParagraph(
                story_id=candidate.story_id,
                category=candidate.category,
                paragraph=f"Draft for {candidate.story_id}.",
                sources=(),
            )
            for candidate in candidates
        ])

    monkeypatch.setattr(pipeline, "collect_pipeline_context", fake_collect)
    monkeypatch.setattr(pipeline, "draft_paragraphs_result", fake_draft)

    result = asyncio.run(pipeline.build_briefing_result(
        config=minimal_config(),
        openai_mode="off",
        watchlist_path=None,
        persist_history=False,
        skipped_log_path=tmp_path / "skipped.json",
    ))

    finance = next(section for section in result.briefings if section.category == "finance")
    assert drafted_ids == ["legacy", "important"]
    assert [paragraph.story_id for paragraph in finance.paragraphs] == ["important", "legacy"]


def test_pipeline_compresses_between_drafting_and_presentation_order(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    now = datetime(2026, 7, 22, tzinfo=timezone.utc)
    important = _ordered_story("important", importance=90, total_score=10, published_at=now)
    high_legacy_score = _ordered_story("legacy", importance=40, total_score=30, published_at=now)
    category_clusters = {category: [] for category in pipeline.CATEGORY_NAMES}
    category_clusters["finance"] = [high_legacy_score, important]
    context = pipeline.PipelineContext(
        category_clusters=category_clusters,
        stock_snapshot=StockSnapshot(news_mentions=(), mega_caps=(), quotes={}),
        all_clusters=[high_legacy_score, important],
    )
    stage_events: list[tuple[str, list[str]]] = []
    budget_ids: list[int] = []

    async def fake_collect(*args: object, **kwargs: object) -> pipeline.PipelineContext:
        budget = kwargs["openai_budget"]
        budget_ids.append(id(budget))
        budget.record("quality_judging", 100, 10)
        budget.record("classification", 200, 20)
        return context

    def fake_draft(candidates: list[DraftCandidate], **kwargs: object) -> pipeline.DraftRunResult:
        budget = kwargs["budget"]
        budget_ids.append(id(budget))
        budget.record("drafting", 100, 50)
        stage_events.append(("draft", [candidate.story_id for candidate in candidates]))
        return pipeline.DraftRunResult([
            BriefingParagraph(
                story_id=candidate.story_id,
                category=candidate.category,
                paragraph=f"Full draft for {candidate.story_id}.",
                sources=(),
            )
            for candidate in candidates
        ], input_tokens=100, output_tokens=50, cost_usd=0.00175)

    def fake_compress(
        paragraphs: list[BriefingParagraph],
        *args: object,
        **kwargs: object,
    ) -> pipeline.CompressionRunResult:
        budget = kwargs["budget"]
        budget_ids.append(id(budget))
        budget.record("compression", 20, 10)
        stage_events.append(("compress", [paragraph.story_id for paragraph in paragraphs]))
        compressed = [
            replace(
                paragraph,
                full_paragraph=paragraph.paragraph,
                paragraph=f"Short {paragraph.story_id}.",
                compression_status="compressed",
                compression_ratio=0.5,
            )
            for paragraph in paragraphs
        ]
        return pipeline.CompressionRunResult(
            compressed,
            input_tokens=20,
            output_tokens=10,
            cost_usd=0.001,
        )

    monkeypatch.setattr(pipeline, "collect_pipeline_context", fake_collect)
    monkeypatch.setattr(pipeline, "draft_paragraphs_result", fake_draft)
    monkeypatch.setattr(pipeline, "compress_paragraphs_result", fake_compress)

    result = asyncio.run(pipeline.build_briefing_result(
        config=minimal_config(),
        openai_mode="full",
        watchlist_path=None,
        persist_history=False,
        skipped_log_path=tmp_path / "skipped.json",
    ))

    finance = next(section for section in result.briefings if section.category == "finance")
    assert stage_events == [
        ("draft", ["legacy", "important"]),
        ("compress", ["legacy", "important"]),
    ]
    assert len(set(budget_ids)) == 1
    assert [paragraph.story_id for paragraph in finance.paragraphs] == ["important", "legacy"]
    assert [paragraph.paragraph for paragraph in finance.paragraphs] == [
        "Short important.",
        "Short legacy.",
    ]
    assert result.diagnostics.compressed_count == 2
    assert result.diagnostics.drafting_input_tokens == 100
    assert result.diagnostics.drafting_output_tokens == 50
    assert result.diagnostics.drafting_cost_usd == 0.00175
    assert result.diagnostics.compression_status_counts == {"compressed": 2}
    assert result.diagnostics.median_compression_ratio == 0.5
    assert result.diagnostics.openai_cost_usd == pytest.approx(0.0024)
    assert set(result.diagnostics.openai_cost_by_stage) == {
        "quality_judging",
        "classification",
        "drafting",
        "compression",
    }
    assert result.diagnostics.compression_cost_usd == 0.001


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

    # Evidence filtering now precedes classification, so this thin cluster never
    # consumes a classification slot in off mode.
    assert context.category_assignments == {}
    assert context.all_clusters[0].skip_reason == "insufficient story context"


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

    def fake_judge(
        articles: list[Article],
        model: str | None = None,
        **kwargs: object,
    ) -> dict[str, str]:
        assert [a.url for a in articles] == [thin_article.url]
        return {thin_article.url: "good"}

    monkeypatch.setattr(pipeline, "judge_ambiguous_articles", fake_judge)
    monkeypatch.setattr(pipeline, "classify_clusters", lambda candidates, **kwargs: {})

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
    assert {section.category for section in result.briefings} == set(pipeline.CATEGORY_NAMES)
    # off mode -> classify_clusters_fallback with no feed_categories signal ->
    # category "" -> the article doesn't land in any of the 5 sections, but the
    # pipeline still runs end-to-end without crashing and every section exists.
    assert result.skipped_log_path == tmp_path / "skipped.json"
