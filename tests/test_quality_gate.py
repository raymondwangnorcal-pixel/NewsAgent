from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from news_agent.models import Article, QualityGateConfig
from news_agent.quality_gate import (
    apply_quality_gate,
    default_quality_gate_rejections_path,
    format_quality_gate_rejections,
    hard_reject_reason,
    judge_ambiguous_articles,
    triggered_heuristics,
    write_quality_gate_rejections,
)


def make_article(
    title: str = "Fed cuts interest rates by a quarter point amid cooling inflation",
    url: str = "https://example.com/story",
    source: str = "Reuters",
    summary: str = (
        "The Federal Reserve cut its benchmark interest rate by a quarter percentage "
        "point on Wednesday, citing signs that inflation is continuing to cool while "
        "the labor market remains resilient."
    ),
) -> Article:
    return Article(
        title=title,
        url=url,
        source=source,
        published_at=datetime.now(timezone.utc),
        summary=summary,
    )


def default_config() -> QualityGateConfig:
    return QualityGateConfig()


# --- Hard rejection -----------------------------------------------------------


def test_empty_summary_is_hard_rejected() -> None:
    article = make_article(summary="")
    reason = hard_reject_reason(article, default_config())
    assert reason == "empty_summary"


def test_whitespace_only_summary_is_hard_rejected() -> None:
    article = make_article(summary="   \n\t  ")
    reason = hard_reject_reason(article, default_config())
    assert reason == "empty_summary"


def test_exact_duplicate_summary_and_title_is_hard_rejected() -> None:
    article = make_article(
        title="Stocks rally on Fed rate cut",
        summary="Stocks rally on Fed rate cut",
    )
    reason = hard_reject_reason(article, default_config())
    assert reason == "summary_duplicates_title"


def test_near_duplicate_summary_above_threshold_is_hard_rejected() -> None:
    article = make_article(
        title="Stocks rally sharply after Fed cuts interest rates today",
        summary="Stocks rally sharply after Fed cuts interest rates on Wednesday",
    )
    reason = hard_reject_reason(article, default_config())
    assert reason == "summary_duplicates_title"


def test_distinct_summary_is_not_hard_rejected() -> None:
    article = make_article()
    assert hard_reject_reason(article, default_config()) is None


def test_apply_quality_gate_drops_hard_rejections_from_survivors() -> None:
    good = make_article(url="https://example.com/good")
    empty = make_article(url="https://example.com/empty", summary="")

    survivors, hard_rejections, _ambiguous = apply_quality_gate([good, empty], default_config())

    assert [a.url for a in survivors] == ["https://example.com/good"]
    assert len(hard_rejections) == 1
    assert hard_rejections[0][0].url == "https://example.com/empty"
    assert hard_rejections[0][1] == "empty_summary"


# --- Soft-scoring bucketing ----------------------------------------------------


def test_zero_triggers_is_clear_good_with_zero_penalty() -> None:
    article = make_article()
    assert triggered_heuristics(article, default_config()) == []

    survivors, hard_rejections, ambiguous = apply_quality_gate([article], default_config())
    assert hard_rejections == []
    assert ambiguous == []
    assert survivors[0].content_quality_penalty == 0.0


def test_one_trigger_is_ambiguous_with_ambiguous_weight_penalty() -> None:
    config = default_config()
    # Thin summary (< min_summary_chars) but non-empty, and not a title duplicate.
    article = make_article(summary="Rates fall slightly.")

    triggers = triggered_heuristics(article, config)
    assert triggers == ["thin_summary"]

    survivors, hard_rejections, ambiguous = apply_quality_gate([article], config)
    assert hard_rejections == []
    assert len(ambiguous) == 1
    assert survivors[0].content_quality_penalty == pytest.approx(config.ambiguous_penalty_weight)


def test_two_triggers_is_clear_bad_with_clear_bad_weight_penalty() -> None:
    config = default_config()
    # Thin summary + teaser title => 2 triggers.
    article = make_article(
        title="Here's what to know about the market today?",
        summary="Rates fall.",
    )

    triggers = triggered_heuristics(article, config)
    assert len(triggers) >= 2

    survivors, hard_rejections, ambiguous = apply_quality_gate([article], config)
    assert hard_rejections == []
    assert ambiguous == []  # 2+ triggers skip the LLM-judge candidate list
    assert survivors[0].content_quality_penalty == pytest.approx(config.clear_bad_penalty_weight)


def test_teaser_title_question_mark_triggers_heuristic() -> None:
    article = make_article(title="Is the Fed about to cut rates again?")
    assert "teaser_title" in triggered_heuristics(article, default_config())


def test_catalystless_stock_tip_triggers_heuristic() -> None:
    article = make_article(
        title="7 stocks to buy right now",
        summary="Analysts highlight several names investors may want to consider this week.",
    )
    assert "catalystless_stock_tip" in triggered_heuristics(article, default_config())


def test_stock_tip_with_catalyst_does_not_trigger_heuristic() -> None:
    article = make_article(
        title="7 stocks to buy after strong earnings guidance",
        summary="Several companies raised earnings guidance this week, boosting investor confidence.",
    )
    assert "catalystless_stock_tip" not in triggered_heuristics(article, default_config())


def test_penalty_is_capped_at_max_content_quality_penalty() -> None:
    config = QualityGateConfig(clear_bad_penalty_weight=10.0, max_content_quality_penalty=2.5)
    article = make_article(
        title="Here's what to know about 7 stocks to buy today?",
        summary="Rates fall.",
    )
    survivors, _hard_rejections, _ambiguous = apply_quality_gate([article], config)
    assert survivors[0].content_quality_penalty == pytest.approx(2.5)


# --- Rejection logging ----------------------------------------------------------


def test_default_quality_gate_rejections_path_uses_iso_date() -> None:
    path = default_quality_gate_rejections_path(today=datetime(2026, 7, 15).date())
    assert path == Path("data") / "quality_gate_rejections_2026-07-15.json"


def test_format_quality_gate_rejections_matches_expected_shape() -> None:
    article = make_article(
        title="Some title",
        url="https://example.com/x",
        source="Example Source",
        summary="",
    )
    formatted = format_quality_gate_rejections([(article, "empty_summary")])

    assert formatted == [
        {
            "title": "Some title",
            "source": "Example Source",
            "url": "https://example.com/x",
            "reason": "empty_summary",
        }
    ]


def test_format_quality_gate_rejections_shape_matches_production_log() -> None:
    production_log_path = Path(__file__).resolve().parents[1] / "data" / "quality_gate_rejections_2026-07-12.json"
    production_entries = json.loads(production_log_path.read_text(encoding="utf-8"))
    assert production_entries, "expected at least one production rejection to compare shape against"

    article = make_article(summary="")
    formatted = format_quality_gate_rejections([(article, "empty_summary")])[0]

    assert set(formatted.keys()) == set(production_entries[0].keys())


def test_write_quality_gate_rejections_writes_expected_json(tmp_path: Path) -> None:
    article = make_article(summary="")
    path = tmp_path / "quality_gate_rejections_2026-07-15.json"

    result_path = write_quality_gate_rejections([(article, "empty_summary")], path=path)

    assert result_path == path
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == [
        {
            "title": article.title,
            "source": article.source,
            "url": article.url,
            "reason": "empty_summary",
        }
    ]


def test_write_quality_gate_rejections_creates_parent_dirs(tmp_path: Path) -> None:
    nested_path = tmp_path / "nested" / "dir" / "quality_gate_rejections_2026-07-15.json"
    write_quality_gate_rejections([], path=nested_path)
    assert nested_path.exists()
    assert json.loads(nested_path.read_text(encoding="utf-8")) == []


# --- Task D: LLM judge for ambiguous articles ------------------------------------


class FakeResponse:
    def __init__(self, output_text: str) -> None:
        self.output_text = output_text


class FakeResponses:
    def __init__(self, outputs: list[str] | None = None, error: Exception | None = None) -> None:
        self._outputs = outputs or []
        self._error = error
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        index = len(self.calls) - 1
        output_text = self._outputs[index] if index < len(self._outputs) else self._outputs[-1]
        return FakeResponse(output_text)


class FakeOpenAI:
    _shared_responses: FakeResponses | None = None

    def __init__(self, *args, **kwargs) -> None:
        self.responses = FakeOpenAI._shared_responses


class _FakeOpenAIModule:
    OpenAI = FakeOpenAI


def install_fake_openai(monkeypatch: pytest.MonkeyPatch, fake_responses: FakeResponses) -> None:
    # judge_ambiguous_articles() does `from openai import OpenAI` inline (mirroring
    # summarize.py's pattern), so the fake must be installed as the `openai` module
    # itself, following the class-swap pattern used for FakeTelegramSender in
    # tests/test_notifications.py.
    FakeOpenAI._shared_responses = fake_responses
    monkeypatch.setitem(sys.modules, "openai", _FakeOpenAIModule())


def test_judge_ambiguous_articles_returns_empty_dict_for_no_articles() -> None:
    assert judge_ambiguous_articles([]) == {}


def test_judge_ambiguous_articles_success_path(monkeypatch: pytest.MonkeyPatch) -> None:
    articles = [
        make_article(url="https://example.com/1", title="Article one"),
        make_article(url="https://example.com/2", title="Article two"),
    ]
    payload = json.dumps(
        {
            "verdicts": [
                {"url": "https://example.com/1", "verdict": "good"},
                {"url": "https://example.com/2", "verdict": "junk"},
            ]
        }
    )
    fake_responses = FakeResponses(outputs=[payload])
    install_fake_openai(monkeypatch, fake_responses)

    verdicts = judge_ambiguous_articles(articles)

    assert verdicts == {
        "https://example.com/1": "good",
        "https://example.com/2": "junk",
    }
    assert len(fake_responses.calls) == 1


def test_judge_ambiguous_articles_degrades_gracefully_on_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    articles = [make_article(url="https://example.com/1")]
    fake_responses = FakeResponses(error=RuntimeError("boom"))
    install_fake_openai(monkeypatch, fake_responses)

    verdicts = judge_ambiguous_articles(articles)

    assert verdicts == {}
    assert len(fake_responses.calls) == 1


def test_judge_ambiguous_articles_chunks_batches_over_forty_articles(monkeypatch: pytest.MonkeyPatch) -> None:
    articles = [make_article(url=f"https://example.com/{i}", title=f"Article {i}") for i in range(85)]

    def payload_for(chunk_articles: list[Article]) -> str:
        return json.dumps(
            {"verdicts": [{"url": a.url, "verdict": "good"} for a in chunk_articles]}
        )

    # 85 articles -> chunks of 40, 40, 5 => three calls.
    outputs = [
        payload_for(articles[0:40]),
        payload_for(articles[40:80]),
        payload_for(articles[80:85]),
    ]
    fake_responses = FakeResponses(outputs=outputs)
    install_fake_openai(monkeypatch, fake_responses)

    verdicts = judge_ambiguous_articles(articles)

    assert len(fake_responses.calls) > 1
    assert len(fake_responses.calls) == 3
    for call in fake_responses.calls[:-1]:
        # each non-final chunk request should carry at most 40 articles worth of content
        assert call["model"]
    assert len(verdicts) == 85


def test_judge_ambiguous_articles_partial_chunk_failure_keeps_successful_chunk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    articles = [make_article(url=f"https://example.com/{i}", title=f"Article {i}") for i in range(45)]

    good_payload = json.dumps(
        {"verdicts": [{"url": a.url, "verdict": "good"} for a in articles[0:40]]}
    )

    class TwoChunkResponses(FakeResponses):
        def create(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                return FakeResponse(good_payload)
            raise RuntimeError("second chunk failed")

    fake_responses = TwoChunkResponses()
    install_fake_openai(monkeypatch, fake_responses)

    verdicts = judge_ambiguous_articles(articles)

    assert len(fake_responses.calls) == 2
    assert len(verdicts) == 40
    assert all(verdicts[a.url] == "good" for a in articles[0:40])
    assert all(a.url not in verdicts for a in articles[40:45])
