from __future__ import annotations

import asyncio

import pytest

import news_agent.pipeline as pipeline
from news_agent.models import BriefingItem, BriefingText, StoryCluster


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
