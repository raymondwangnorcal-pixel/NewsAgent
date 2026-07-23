from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from news_agent.models import Article, CategoryAssignment, ImportanceConfig, StoryCluster
from news_agent.scoring import apply_importance, importance_from_total_score


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "importance_selection_replay.json"


def _band_bounds(label: str) -> tuple[float, float]:
    low, high = label.split("-")
    return float(low), float(high)


def test_importance_replay_fixture_matches_locked_bands_and_anchor_order() -> None:
    rows = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    config = ImportanceConfig()
    clusters: list[StoryCluster] = []
    assignments: dict[str, CategoryAssignment] = {}
    by_title: dict[str, StoryCluster] = {}
    for index, row in enumerate(rows):
        story = StoryCluster(
            key=f"fixture-{index}",
            title=row["title"],
            category=row["category"],
            culture_lane=row["culture_lane"],
            total_score=row["total_score"],
            evidence_score=row["evidence_score"],
            articles=[Article(
                title=row["title"],
                url=f"https://example.com/{index}",
                source=row["primary_source"],
                published_at=datetime.now(timezone.utc),
            )],
        )
        clusters.append(story)
        by_title[story.title] = story
        assignments[story.key] = CategoryAssignment(
            category=story.category,
            rationale="fixture",
            llm_importance=row["llm_importance"],
        )

    apply_importance(clusters, assignments, config)

    assert len(clusters) == 30
    for row, story in zip(rows, clusters, strict=True):
        low, high = _band_bounds(row["expected_final_importance_band"])
        assert low <= story.importance <= high, (story.title, story.importance)
    assert by_title["Tariffs raise national fuel costs"].importance > by_title["Scheduled movie release"].importance
    assert by_title["War escalation threatens neighboring states"].importance > by_title["Ordinary product launch"].importance
    assert by_title["Quiet standards decision transforms an industry"].importance >= 70
    assert by_title["Legitimate zero-importance trade item"].importance < importance_from_total_score(8.0, config)
