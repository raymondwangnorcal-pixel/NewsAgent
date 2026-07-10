from __future__ import annotations

import asyncio

import pytest

import news_agent.pipeline as pipeline
from news_agent.models import BriefingItem, BriefingText


def sample_briefings(title: str = "1/6 Business and technology") -> list[BriefingText]:
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
