from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from news_agent import duplicate_gate
from news_agent.duplicate_gate import apply_duplicate_gate
from news_agent.models import (
    AgentConfig,
    Article,
    CategoryAssignment,
    DuplicateGateConfig,
    OpenAICostConfig,
    StoryCluster,
)
from news_agent.openai_budget import OpenAIBudget
from news_agent.openai_client import StructuredResponseOutcome


def cluster(
    key: str,
    title: str,
    source: str,
    *,
    category: str = "business_tech",
    importance: float = 50.0,
    reputation: float = 0.8,
) -> StoryCluster:
    article = Article(
        title=title,
        url=f"https://example.com/{key}",
        source=source,
        published_at=datetime.now(timezone.utc),
        summary=f"Reporting about {title}.",
        reputation=reputation,
        evidence_score=2.0,
    )
    return StoryCluster(
        key=key,
        title=title,
        articles=[article],
        category=category,
        importance=importance,
        total_score=importance,
    )


def config(max_component_size: int = 4) -> AgentConfig:
    return AgentConfig(
        feeds=(),
        categories={},
        lookback_hours=30,
        max_articles=50,
        duplicate_gate=replace(
            DuplicateGateConfig(),
            candidate_title_jaccard_threshold=0.10,
            max_component_size=max_component_size,
        ),
    )


def outcome(*sets: list[str]) -> StructuredResponseOutcome:
    return StructuredResponseOutcome(
        response=SimpleNamespace(
            output_text=json.dumps(
                {
                    "same_event_sets": [
                        {"cluster_ids": cluster_ids} for cluster_ids in sets
                    ]
                }
            )
        )
    )


def test_duplicate_gate_merges_anthropic_pair_and_preserves_destination_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stronger = cluster(
        "anthropic-strong",
        "Anthropic CEO Dario Amodei says company is not advocating an open-weight ban",
        "CNBC",
        importance=80,
    )
    weaker = cluster(
        "anthropic-weak",
        "Anthropic's Dario Amodei responds on open-weight model restrictions",
        "TechCrunch",
        importance=60,
    )
    assignments = {
        stronger.key: CategoryAssignment("business_tech", "technology", ("https://outlier/one",)),
        weaker.key: CategoryAssignment("business_tech", "technology", ("https://outlier/two",)),
    }
    monkeypatch.setattr(
        duplicate_gate,
        "request_structured_response",
        lambda **_kwargs: outcome(["c0", "c1"]),
    )

    merged, removed, stats = apply_duplicate_gate(
        {"business_tech": [stronger, weaker]},
        config(),
        assignments=assignments,
        budget=OpenAIBudget(OpenAICostConfig()),
    )

    assert merged["business_tech"] == [stronger]
    assert removed == [weaker]
    assert stronger.key == "anthropic-strong"
    assert stronger.merged_from == ("anthropic-weak",)
    assert stronger.sources == ["CNBC", "TechCrunch"]
    assert assignments[stronger.key].outlier_urls == (
        "https://outlier/one",
        "https://outlier/two",
    )
    assert stats.sets_merged == 1
    assert stats.clusters_removed == 1


def test_duplicate_gate_merges_d4vd_reports_and_avoids_timeline_headline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bbc = cluster(
        "d4vd-bbc",
        "US singer D4vd to go on trial for murder in death of 14-year-old",
        "BBC World",
        reputation=0.95,
    )
    deadline = cluster(
        "d4vd-deadline",
        "D4vd ordered to stand trial in Celeste Rivas Hernandez murder case",
        "Deadline",
    )
    timeline = cluster(
        "d4vd-timeline",
        "D4vd Murder Case: A Timeline of the Investigation & Charges",
        "Billboard",
    )
    monkeypatch.setattr(
        duplicate_gate,
        "request_structured_response",
        lambda **_kwargs: outcome(["c0", "c1", "c2"]),
    )

    merged, _removed, _stats = apply_duplicate_gate(
        {"culture": [bbc, deadline, timeline]},
        config(),
        assignments={},
        budget=OpenAIBudget(OpenAICostConfig()),
    )

    assert len(merged["culture"]) == 1
    assert merged["culture"][0].title == bbc.title
    assert set(merged["culture"][0].sources) == {"BBC World", "Deadline", "Billboard"}


def test_duplicate_gate_keeps_all_clusters_when_model_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    left = cluster("left", "Acme launches a new electric vehicle", "A")
    right = cluster("right", "Acme electric vehicle launch reaches dealers", "B")
    monkeypatch.setattr(
        duplicate_gate,
        "request_structured_response",
        lambda **_kwargs: StructuredResponseOutcome(error_code="duplicate_gate_api_error"),
    )

    original = {"business_tech": [left, right]}
    merged, removed, stats = apply_duplicate_gate(
        original,
        config(),
        assignments={},
        budget=OpenAIBudget(OpenAICostConfig()),
    )

    assert merged == original
    assert removed == []
    assert stats.request_made is True
    assert stats.clusters_removed == 0


def test_duplicate_gate_makes_no_request_without_eligible_pairs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        duplicate_gate,
        "request_structured_response",
        lambda **_kwargs: pytest.fail("gate request should not be made"),
    )

    original = {
        "business_tech": [
            cluster("acme", "Acme launches a product", "A"),
            cluster("beta", "Beta reports quarterly results", "B"),
        ]
    }
    merged, removed, stats = apply_duplicate_gate(
        original,
        config(),
        assignments={},
        budget=OpenAIBudget(OpenAICostConfig()),
    )

    assert merged == original
    assert removed == []
    assert stats.request_made is False


def test_duplicate_gate_keeps_deck_on_malformed_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    left = cluster("left", "Acme launches electric vehicle", "A")
    right = cluster("right", "Acme electric vehicle launch reaches dealers", "B")
    monkeypatch.setattr(
        duplicate_gate,
        "request_structured_response",
        lambda **_kwargs: StructuredResponseOutcome(
            response=SimpleNamespace(output_text="{not json")
        ),
    )
    budget = OpenAIBudget(OpenAICostConfig())

    merged, removed, stats = apply_duplicate_gate(
        {"business_tech": [left, right]},
        config(),
        assignments={},
        budget=budget,
    )

    assert merged["business_tech"] == [left, right]
    assert removed == []
    assert stats.sets_merged == 0
    assert budget.stage_outcomes()["duplicate_gate"]["reasons"] == {
        "duplicate_gate_malformed_response": 1
    }


def test_duplicate_gate_rejects_second_set_that_reuses_cluster(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clusters = [
        cluster("one", "Acme launches electric vehicle", "A"),
        cluster("two", "Acme electric vehicle launch reaches dealers", "B"),
        cluster("three", "Acme launches electric vehicle nationwide", "C"),
    ]
    monkeypatch.setattr(
        duplicate_gate,
        "request_structured_response",
        lambda **_kwargs: outcome(["c0", "c1"], ["c1", "c2"]),
    )

    merged, _removed, stats = apply_duplicate_gate(
        {"business_tech": clusters},
        config(),
        assignments={},
        budget=OpenAIBudget(OpenAICostConfig()),
    )

    assert len(merged["business_tech"]) == 2
    assert stats.sets_merged == 1
    assert stats.sets_rejected == 1


def test_duplicate_gate_rejects_set_larger_than_component_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clusters = [
        cluster(
            f"story-{index}",
            f"Acme launches electric vehicle model {index}",
            f"Source {index}",
        )
        for index in range(5)
    ]
    monkeypatch.setattr(
        duplicate_gate,
        "request_structured_response",
        lambda **_kwargs: outcome(["c0", "c1", "c2", "c3", "c4"]),
    )

    merged, removed, stats = apply_duplicate_gate(
        {"business_tech": clusters},
        config(max_component_size=4),
        assignments={},
        budget=OpenAIBudget(OpenAICostConfig()),
    )

    assert merged["business_tech"] == clusters
    assert removed == []
    assert stats.sets_rejected == 1
    assert stats.sets_merged == 0


def test_duplicate_gate_rejects_set_spanning_components_but_merges_valid_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = cluster("first", "Acme launches electric vehicle", "A")
    second = cluster("second", "Acme electric vehicle launch reaches dealers", "B")
    third = cluster("third", "D4vd ordered to stand trial in murder case", "C", category="culture")
    fourth = cluster("fourth", "D4vd murder case advances to trial", "D", category="culture")
    monkeypatch.setattr(
        duplicate_gate,
        "request_structured_response",
        lambda **_kwargs: outcome(["c0", "c2"], ["c0", "c1"]),
    )

    merged, _removed, stats = apply_duplicate_gate(
        {"business_tech": [first, second], "culture": [third, fourth]},
        config(),
        assignments={},
        budget=OpenAIBudget(OpenAICostConfig()),
    )

    assert len(merged["business_tech"]) == 1
    assert len(merged["culture"]) == 2
    assert stats.sets_returned == 2
    assert stats.sets_rejected == 1
    assert stats.sets_merged == 1


def test_duplicate_gate_cross_category_merge_keeps_stronger_category(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    business = cluster(
        "business",
        "Acme announces acquisition of Beta",
        "A",
        category="business_tech",
        importance=90,
    )
    finance = cluster(
        "finance",
        "Acme acquisition of Beta reshapes its portfolio",
        "B",
        category="finance",
        importance=70,
    )
    monkeypatch.setattr(
        duplicate_gate,
        "request_structured_response",
        lambda **_kwargs: outcome(["c0", "c1"]),
    )

    merged, _removed, stats = apply_duplicate_gate(
        {"business_tech": [business], "finance": [finance]},
        config(),
        assignments={},
        budget=OpenAIBudget(OpenAICostConfig()),
    )

    assert merged["business_tech"] == [business]
    assert merged["finance"] == []
    assert stats.cross_category_merges == 1
