from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from news_agent.classify import (
    CATEGORY_NAMES,
    classify_clusters,
    classify_clusters_fallback,
    default_category_assignments_path,
    load_category_guidelines,
    write_category_assignments,
)
from news_agent.models import Article, StoryCluster


def make_article(
    *,
    title: str = "Sample headline",
    summary: str = "Sample summary describing what happened.",
    url: str = "https://example.com/story",
    source: str = "Example News",
    feed_categories: tuple[str, ...] = (),
) -> Article:
    return Article(
        title=title,
        url=url,
        source=source,
        published_at=datetime.now(timezone.utc),
        summary=summary,
        feed_categories=feed_categories,
    )


def make_cluster(key: str, title: str, articles: list[Article] | None = None) -> StoryCluster:
    return StoryCluster(key=key, title=title, articles=articles or [make_article(title=title)])


# --- Fake OpenAI client, mirroring the pattern in test_quality_gate.py -----------


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
    FakeOpenAI._shared_responses = fake_responses
    monkeypatch.setitem(sys.modules, "openai", _FakeOpenAIModule())


def assignment_payload(entries: list[dict]) -> str:
    return json.dumps({"assignments": entries})


# --- Guideline loading -------------------------------------------------------------


def test_load_category_guidelines_reads_real_file() -> None:
    text = load_category_guidelines()
    assert "Business + Tech" in text
    assert "U.S. News" in text
    assert "Global News" in text
    assert "Culture + Media" in text
    assert "Finance" in text


# --- Category inclusion rules (one representative fixture per category) -----------


def test_classify_includes_business_tech_ai_infrastructure_story(monkeypatch: pytest.MonkeyPatch) -> None:
    cluster = make_cluster(
        "meta-datacenter",
        "Meta expands planned data center to five gigawatts",
        [
            make_article(
                title="Meta expands planned data center to five gigawatts",
                summary="Meta is expanding its planned data center, with more than $50 billion in "
                "projected investment as AI infrastructure spending accelerates.",
                url="https://example.com/meta-datacenter",
                source="Reuters",
            )
        ],
    )
    fake_responses = FakeResponses(
        outputs=[
            assignment_payload(
                [
                    {
                        "cluster_id": "meta-datacenter",
                        "category": "business_tech",
                        "rationale": "Company infrastructure investment, not a stock-price story.",
                        "outlier_urls": [],
                    }
                ]
            )
        ]
    )
    install_fake_openai(monkeypatch, fake_responses)

    result = classify_clusters([cluster], openai_mode="full")

    assert result["meta-datacenter"].category == "business_tech"
    assert result["meta-datacenter"].rationale


def test_classify_includes_domestic_federal_ruling_story(monkeypatch: pytest.MonkeyPatch) -> None:
    cluster = make_cluster(
        "irs-ruling",
        "Federal judge blocks IRS settlement",
        [
            make_article(
                title="Federal judge blocks proposed IRS settlement",
                summary="A federal judge ruled the settlement improperly sought personal benefit "
                "and referred several lawyers to disciplinary authorities.",
                url="https://example.com/irs-ruling",
                source="AP News",
            )
        ],
    )
    fake_responses = FakeResponses(
        outputs=[
            assignment_payload(
                [
                    {
                        "cluster_id": "irs-ruling",
                        "category": "domestic",
                        "rationale": "Federal court ruling on US government conduct.",
                        "outlier_urls": [],
                    }
                ]
            )
        ]
    )
    install_fake_openai(monkeypatch, fake_responses)

    result = classify_clusters([cluster], openai_mode="full")

    assert result["irs-ruling"].category == "domestic"


def test_classify_includes_global_conflict_story(monkeypatch: pytest.MonkeyPatch) -> None:
    cluster = make_cluster(
        "iran-strikes",
        "Iran and US exchange strikes, Strait of Hormuz threatened",
        [
            make_article(
                title="Iran threatens Strait of Hormuz shipping after exchange of strikes",
                summary="Iran and the United States exchanged missile and drone attacks, raising "
                "fears of a broader regional war and disruption to global energy supplies.",
                url="https://example.com/iran-strikes",
                source="Reuters",
            )
        ],
    )
    fake_responses = FakeResponses(
        outputs=[
            assignment_payload(
                [
                    {
                        "cluster_id": "iran-strikes",
                        "category": "global",
                        "rationale": "Battlefield and international-consequences focus, not a US policy decision.",
                        "outlier_urls": [],
                    }
                ]
            )
        ]
    )
    install_fake_openai(monkeypatch, fake_responses)

    result = classify_clusters([cluster], openai_mode="full")

    assert result["iran-strikes"].category == "global"


def test_classify_includes_culture_media_acquisition_lawsuit(monkeypatch: pytest.MonkeyPatch) -> None:
    cluster = make_cluster(
        "paramount-wbd-suit",
        "States sue to block Paramount-Warner Bros. Discovery deal",
        [
            make_article(
                title="Twelve states sue to block Paramount's acquisition of Warner Bros. Discovery",
                summary="States argue the roughly $110 billion deal would reduce competition in "
                "Hollywood and weaken workers' negotiating power.",
                url="https://example.com/paramount-wbd-suit",
                source="Variety",
            )
        ],
    )
    fake_responses = FakeResponses(
        outputs=[
            assignment_payload(
                [
                    {
                        "cluster_id": "paramount-wbd-suit",
                        "category": "culture",
                        "rationale": "Central issue is entertainment-industry competition, not corporate strategy alone.",
                        "outlier_urls": [],
                    }
                ]
            )
        ]
    )
    install_fake_openai(monkeypatch, fake_responses)

    result = classify_clusters([cluster], openai_mode="full")

    assert result["paramount-wbd-suit"].category == "culture"


def test_classify_includes_finance_market_reaction_story(monkeypatch: pytest.MonkeyPatch) -> None:
    cluster = make_cluster(
        "oil-spike",
        "Oil jumps 9% as markets react to Gulf tensions",
        [
            make_article(
                title="Oil prices jump 9% as investors flee risk assets",
                summary="Escalating tensions between the US and Iran pushed investors away from "
                "risk assets; tech stocks and crypto declined.",
                url="https://example.com/oil-spike",
                source="Bloomberg",
            )
        ],
    )
    fake_responses = FakeResponses(
        outputs=[
            assignment_payload(
                [
                    {
                        "cluster_id": "oil-spike",
                        "category": "finance",
                        "rationale": "Main significance is the market/price reaction, not the geopolitical event itself.",
                        "outlier_urls": [],
                    }
                ]
            )
        ]
    )
    install_fake_openai(monkeypatch, fake_responses)

    result = classify_clusters([cluster], openai_mode="full")

    assert result["oil-spike"].category == "finance"


# --- Category exclusion / boundary rules -------------------------------------------


def test_classify_excludes_stock_drop_from_business_tech(monkeypatch: pytest.MonkeyPatch) -> None:
    # Per the guidelines: a story about Tesla stock falling belongs in Finance
    # unless the important development is the business event that caused it.
    cluster = make_cluster(
        "tesla-drop",
        "Tesla shares fall 15%",
        [
            make_article(
                title="Tesla shares fall 15% amid broader market selloff",
                summary="Tesla shares dropped 15% as part of a broad market decline, with no "
                "company-specific news driving the move.",
                url="https://example.com/tesla-drop",
                source="CNBC",
            )
        ],
    )
    fake_responses = FakeResponses(
        outputs=[
            assignment_payload(
                [
                    {
                        "cluster_id": "tesla-drop",
                        "category": "finance",
                        "rationale": "Mentions a company but the significance is the price move, not a business event.",
                        "outlier_urls": [],
                    }
                ]
            )
        ]
    )
    install_fake_openai(monkeypatch, fake_responses)

    result = classify_clusters([cluster], openai_mode="full")

    assert result["tesla-drop"].category == "finance"
    assert result["tesla-drop"].category != "business_tech"


def test_classify_netflix_acquisition_is_business_tech_not_culture(monkeypatch: pytest.MonkeyPatch) -> None:
    cluster = make_cluster(
        "netflix-acquisition",
        "Netflix acquires streaming rival for $2 billion",
        [
            make_article(
                title="Netflix to acquire streaming rival for $2 billion",
                summary="The deal is centered on corporate strategy and market consolidation in streaming.",
                url="https://example.com/netflix-acquisition",
                source="TechCrunch",
            )
        ],
    )
    fake_responses = FakeResponses(
        outputs=[
            assignment_payload(
                [
                    {
                        "cluster_id": "netflix-acquisition",
                        "category": "business_tech",
                        "rationale": "Central issue is corporate strategy, per the Netflix boundary rule.",
                        "outlier_urls": [],
                    }
                ]
            )
        ]
    )
    install_fake_openai(monkeypatch, fake_responses)

    result = classify_clusters([cluster], openai_mode="full")

    assert result["netflix-acquisition"].category == "business_tech"


# --- Ambiguous / boundary cases -----------------------------------------------------


def test_classify_us_authorized_strikes_is_domestic_when_focus_is_us_decision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cluster = make_cluster(
        "us-strikes-decision",
        "President authorizes strikes against Iran",
        [
            make_article(
                title="President authorizes strikes against Iran after congressional briefing",
                summary="The decision followed a closed-door briefing with congressional leaders "
                "and sparked domestic political debate over war powers.",
                url="https://example.com/us-strikes-decision",
                source="AP News",
            )
        ],
    )
    fake_responses = FakeResponses(
        outputs=[
            assignment_payload(
                [
                    {
                        "cluster_id": "us-strikes-decision",
                        "category": "domestic",
                        "rationale": "Focus is the US government's decision and domestic political consequences.",
                        "outlier_urls": [],
                    }
                ]
            )
        ]
    )
    install_fake_openai(monkeypatch, fake_responses)

    result = classify_clusters([cluster], openai_mode="full")

    assert result["us-strikes-decision"].category == "domestic"


def test_classify_reports_outlier_urls_for_incoherent_cluster(monkeypatch: pytest.MonkeyPatch) -> None:
    cluster = make_cluster(
        "mixed-apple",
        "Apple developments",
        [
            make_article(title="Apple releases new AI model", url="https://example.com/apple-ai"),
            make_article(title="Apple store employee wins lawsuit", url="https://example.com/apple-lawsuit"),
        ],
    )
    fake_responses = FakeResponses(
        outputs=[
            assignment_payload(
                [
                    {
                        "cluster_id": "mixed-apple",
                        "category": "business_tech",
                        "rationale": "Kept the AI model story; the lawsuit is an unrelated development.",
                        "outlier_urls": ["https://example.com/apple-lawsuit"],
                    }
                ]
            )
        ]
    )
    install_fake_openai(monkeypatch, fake_responses)

    result = classify_clusters([cluster], openai_mode="full")

    assert result["mixed-apple"].outlier_urls == ("https://example.com/apple-lawsuit",)


# --- Degraded fallback (feed-tag voting, no LLM) ------------------------------------


def test_classify_clusters_fallback_uses_feed_tag_voting() -> None:
    cluster = make_cluster(
        "fallback-vote",
        "Some story",
        [
            make_article(feed_categories=("finance",)),
            make_article(feed_categories=("finance",)),
            make_article(feed_categories=("culture",)),
        ],
    )

    result = classify_clusters_fallback([cluster])

    assert result["fallback-vote"].category == "finance"
    assert "degraded mode" in result["fallback-vote"].rationale


def test_classify_clusters_fallback_drops_when_no_feed_signal() -> None:
    cluster = make_cluster("no-signal", "Untagged story", [make_article(feed_categories=())])

    result = classify_clusters_fallback([cluster])

    assert result["no-signal"].category == ""
    assert "degraded mode" in result["no-signal"].rationale


# --- openai_mode="off" skips the LLM entirely ---------------------------------------


def test_classify_clusters_off_mode_never_calls_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    cluster = make_cluster("off-mode", "Some story", [make_article(feed_categories=("global",))])
    fake_responses = FakeResponses(error=AssertionError("LLM should not be called in off mode"))
    install_fake_openai(monkeypatch, fake_responses)

    result = classify_clusters([cluster], openai_mode="off")

    assert result["off-mode"].category == "global"
    assert fake_responses.calls == []


def test_classify_clusters_empty_list_skips_llm_entirely(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_responses = FakeResponses(error=AssertionError("LLM should not be called for an empty pool"))
    install_fake_openai(monkeypatch, fake_responses)

    result = classify_clusters([], openai_mode="full")

    assert result == {}
    assert fake_responses.calls == []


# --- Malformed model responses / graceful degradation -------------------------------


def test_classify_clusters_degrades_gracefully_on_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    cluster = make_cluster("errors-out", "Some story", [make_article(feed_categories=("domestic",))])
    fake_responses = FakeResponses(error=RuntimeError("boom"))
    install_fake_openai(monkeypatch, fake_responses)

    result = classify_clusters([cluster], openai_mode="full")

    # LLM call failed entirely -> falls back to feed-tag heuristic for every cluster.
    assert result["errors-out"].category == "domestic"
    assert "degraded mode" in result["errors-out"].rationale


def test_classify_clusters_malformed_json_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    cluster = make_cluster("malformed", "Some story", [make_article(feed_categories=("culture",))])
    fake_responses = FakeResponses(outputs=["not valid json {{{"])
    install_fake_openai(monkeypatch, fake_responses)

    result = classify_clusters([cluster], openai_mode="full")

    assert result["malformed"].category == "culture"
    assert "degraded mode" in result["malformed"].rationale


def test_classify_clusters_partial_response_fills_gap_with_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    covered = make_cluster("covered", "Covered story", [make_article(feed_categories=("finance",))])
    uncovered = make_cluster("uncovered", "Uncovered story", [make_article(feed_categories=("global",))])
    fake_responses = FakeResponses(
        outputs=[
            assignment_payload(
                [
                    {
                        "cluster_id": "covered",
                        "category": "finance",
                        "rationale": "Clear market story.",
                        "outlier_urls": [],
                    }
                ]
            )
        ]
    )
    install_fake_openai(monkeypatch, fake_responses)

    result = classify_clusters([covered, uncovered], openai_mode="full")

    assert result["covered"].category == "finance"
    assert result["covered"].rationale == "Clear market story."
    assert result["uncovered"].category == "global"
    assert "degraded mode" in result["uncovered"].rationale


# --- Batching -------------------------------------------------------------------------


def test_classify_clusters_chunks_batches_over_forty_clusters(monkeypatch: pytest.MonkeyPatch) -> None:
    clusters = [make_cluster(f"cluster-{i}", f"Story {i}") for i in range(85)]

    def payload_for(chunk_clusters: list[StoryCluster]) -> str:
        return assignment_payload(
            [
                {"cluster_id": c.key, "category": "global", "rationale": "batch", "outlier_urls": []}
                for c in chunk_clusters
            ]
        )

    outputs = [payload_for(clusters[0:40]), payload_for(clusters[40:80]), payload_for(clusters[80:85])]
    fake_responses = FakeResponses(outputs=outputs)
    install_fake_openai(monkeypatch, fake_responses)

    result = classify_clusters(clusters, openai_mode="full")

    assert len(fake_responses.calls) == 3
    assert len(result) == 85


# --- Logging / persistence -----------------------------------------------------------


def test_write_category_assignments_creates_parent_dirs(tmp_path: Path) -> None:
    nested_path = tmp_path / "nested" / "dir" / "category_assignments_2026-07-15.json"
    from news_agent.models import CategoryAssignment

    write_category_assignments(
        {"story-1": CategoryAssignment(category="finance", rationale="test")},
        path=nested_path,
    )

    assert nested_path.exists()
    data = json.loads(nested_path.read_text(encoding="utf-8"))
    assert data == [{"cluster_id": "story-1", "category": "finance", "rationale": "test", "outlier_urls": []}]


def test_default_category_assignments_path_uses_todays_date() -> None:
    from datetime import date

    path = default_category_assignments_path(today=date(2026, 7, 15))
    assert path == Path("data") / "category_assignments_2026-07-15.json"


def test_category_names_has_exactly_five_categories() -> None:
    assert len(CATEGORY_NAMES) == 5
