from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest

import news_agent.pipeline as pipeline
from news_agent.models import (
    AgentConfig,
    Article,
    BriefingItem,
    BriefingText,
    QualityGateConfig,
    StockSnapshot,
    StoryCluster,
)


def sample_briefings(title: str = "1/5 Business and technology") -> list[BriefingText]:
    return [
        BriefingText(
            category="business_tech",
            title=title,
            items=(
                BriefingItem(
                    headline="AI startup raises funding",
                    summary="A startup raised a large round.",
                    why_it_matters="It may shape the AI market.",
                    next_watch="Watch hiring and customer growth.",
                    sources=("Reuters",),
                ),
            ),
        )
    ]


def cluster(
    key: str,
    title: str,
    total_score: float,
    category_scores: dict[str, float],
) -> StoryCluster:
    return StoryCluster(
        key=key,
        title=title,
        total_score=total_score,
        category_scores=category_scores,
    )


def test_build_briefings_polish_generates_draft_then_polishes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    calls: list[str] = []
    draft = sample_briefings("draft")
    polished = sample_briefings("polished")

    async def fake_collect_pipeline_context(*args: object, **kwargs: object) -> pipeline.PipelineContext:
        calls.append("collect")
        return pipeline.PipelineContext(category_clusters={}, stock_snapshot=object(), all_clusters=[])

    def fake_fallback(category_clusters: object, config: object, stock_snapshot: object) -> list[BriefingText]:
        calls.append("fallback")
        return draft

    def fake_polish(draft_briefings: list[BriefingText]) -> list[BriefingText]:
        calls.append("polish")
        assert draft_briefings == draft
        return polished

    monkeypatch.setattr(pipeline, "collect_pipeline_context", fake_collect_pipeline_context)
    monkeypatch.setattr(pipeline, "generate_fallback_briefings", fake_fallback)
    monkeypatch.setattr(pipeline, "generate_polished_briefings_with_openai", fake_polish)

    result = asyncio.run(
        pipeline.build_briefings(
            openai_mode="polish",
            config=object(),
            watchlist_path=None,
            history_path=tmp_path / "history.json",
            persist_history=False,
            skipped_log_path=tmp_path / "skipped.json",
        )
    )

    assert result == polished
    assert calls == ["collect", "fallback", "polish"]


def test_build_briefings_off_returns_fallback_without_polish(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    draft = sample_briefings("draft")

    async def fake_collect_pipeline_context(*args: object, **kwargs: object) -> pipeline.PipelineContext:
        return pipeline.PipelineContext(category_clusters={}, stock_snapshot=object(), all_clusters=[])

    monkeypatch.setattr(pipeline, "collect_pipeline_context", fake_collect_pipeline_context)
    monkeypatch.setattr(pipeline, "generate_fallback_briefings", lambda *args: draft)
    monkeypatch.setattr(
        pipeline,
        "generate_polished_briefings_with_openai",
        lambda *args: (_ for _ in ()).throw(AssertionError("polish should not run")),
    )

    result = asyncio.run(
        pipeline.build_briefings(
            openai_mode="off",
            config=object(),
            watchlist_path=None,
            history_path=tmp_path / "history.json",
            persist_history=False,
            skipped_log_path=tmp_path / "skipped.json",
        )
    )

    assert result == draft


def test_select_unique_category_clusters_prevents_cross_message_repeats() -> None:
    shared_story = cluster(
        "nvidia-export",
        "Nvidia falls on export restrictions",
        20,
        {"business_tech": 10, "finance": 10},
    )
    finance_story = cluster(
        "fed-rates",
        "Fed rate outlook moves markets",
        18,
        {"finance": 9},
    )
    global_story = cluster(
        "global-conflict",
        "Conflict escalates overseas",
        17,
        {"global": 1},
    )

    selected = pipeline.select_unique_category_clusters([shared_story, finance_story, global_story])

    assert shared_story in selected["business_tech"]
    assert shared_story not in selected["finance"]
    assert finance_story in selected["finance"]
    assert set(selected) == {"business_tech", "domestic", "global", "culture", "finance"}


def test_selected_clusters_deduplicates_by_story_identity() -> None:
    original = cluster("same-story", "Original headline", 10, {"business_tech": 1})
    duplicate = cluster("same-story", "Duplicate headline", 9, {"finance": 1})

    selected = pipeline.selected_clusters(
        {
            "business_tech": [original],
            "finance": [duplicate],
        }
    )

    assert selected == [original]


# --- Task G: quality-gate wiring into collect_pipeline_context --------------


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
        )
    )

    assert len(context.quality_gate_rejections) == 1
    rejected_article, reason = context.quality_gate_rejections[0]
    assert rejected_article.url == junk_article.url
    assert reason == "empty_summary"

    # The hard-rejected article must never reach clustering.
    all_urls = {url for cluster in context.all_clusters for url in cluster.urls}
    assert junk_article.url not in all_urls
    assert good_article.url in all_urls

    # Rejection log was written in the narrowed hard-reject format.
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
    # Thin (but non-empty) summary trips exactly one regex heuristic -> ambiguous
    # bucket, soft-penalized, NOT hard-rejected.
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
        )
    )

    assert context.quality_gate_rejections == ()
    all_urls = {url for cluster in context.all_clusters for url in cluster.urls}
    assert thin_article.url in all_urls

    matching = [cluster for cluster in context.all_clusters if thin_article.url in cluster.urls]
    assert len(matching) == 1
    assert matching[0].content_quality_penalty == pytest.approx(QualityGateConfig().ambiguous_penalty_weight)


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

    context = asyncio.run(
        pipeline.collect_pipeline_context(
            minimal_config(),
            openai_mode="full",
            quality_gate_log_path=tmp_path / "quality_gate_rejections.json",
        )
    )

    matching = [cluster for cluster in context.all_clusters if thin_article.url in cluster.urls]
    assert len(matching) == 1
    assert matching[0].content_quality_penalty == pytest.approx(0.0)


def test_build_briefing_result_threads_quality_gate_config_threshold(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    heavily_penalized = cluster("penalized-story", "Heavily penalized story", 5.0, {"business_tech": 1})
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
    monkeypatch.setattr(pipeline, "generate_fallback_briefings", lambda *args: sample_briefings())
    monkeypatch.setattr(pipeline, "generate_polished_briefings_with_openai", lambda *args: sample_briefings())

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
