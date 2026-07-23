from __future__ import annotations

from datetime import datetime, timezone
import json

from news_agent.models import Article, QualityGateConfig, StoryCluster
from news_agent.skipped_log import build_skipped_stories, skip_reason, write_skipped_log


def test_skipped_stories_include_reason_sources_url_and_watchlist(tmp_path) -> None:
    cluster = StoryCluster(
        key="ai funding",
        title="AI startup raises funding",
        articles=[
            Article(
                title="AI startup raises funding",
                url="https://example.com/ai",
                source="TechCrunch",
                published_at=datetime.now(timezone.utc),
                reputation=0.85,
            )
        ],
        category="business_tech",
        total_score=2.5,
        watchlist_matches=("AI",),
        skip_reason="score below threshold",
    )

    skipped = build_skipped_stories([cluster], selected_clusters=[])
    path = write_skipped_log(skipped, tmp_path / "skipped.json")
    data = json.loads(path.read_text(encoding="utf-8"))

    assert data[0]["headline"] == "AI startup raises funding"
    assert data[0]["reason_skipped"] == "score below threshold"
    assert data[0]["source_names"] == ["TechCrunch"]
    assert data[0]["url"] == "https://example.com/ai"
    assert data[0]["watchlist_match"] == ["AI"]
    assert data[0]["importance"] == 0.0


def test_skipped_log_sorts_by_importance_before_legacy_score() -> None:
    high_score = _multi_source_cluster("high-score", "High score", content_quality_penalty=0)
    high_score.total_score = 99
    high_score.importance = 20
    important = _multi_source_cluster("important", "Important", content_quality_penalty=0)
    important.total_score = 1
    important.importance = 80

    skipped = build_skipped_stories([high_score, important], selected_clusters=[])

    assert [item.headline for item in skipped] == ["Important", "High score"]


def _single_source_cluster(key: str, title: str, *, quality_score: float, content_quality_penalty: float) -> StoryCluster:
    return StoryCluster(
        key=key,
        title=title,
        articles=[
            Article(
                title=title,
                url=f"https://example.com/{key}",
                source="LonelyBlog",
                published_at=datetime.now(timezone.utc),
                reputation=0.4,
            )
        ],
        category="business_tech",
        quality_score=quality_score,
        impact_score=1.0,
        total_score=1.0,
        content_quality_penalty=content_quality_penalty,
    )


def _multi_source_cluster(key: str, title: str, *, content_quality_penalty: float) -> StoryCluster:
    return StoryCluster(
        key=key,
        title=title,
        articles=[
            Article(
                title=title,
                url=f"https://example.com/{key}-a",
                source="Reuters",
                published_at=datetime.now(timezone.utc),
                reputation=0.9,
            ),
            Article(
                title=title,
                url=f"https://example.com/{key}-b",
                source="AP",
                published_at=datetime.now(timezone.utc),
                reputation=0.9,
            ),
        ],
        category="business_tech",
        quality_score=0.9,
        impact_score=5.0,
        total_score=4.0,
        content_quality_penalty=content_quality_penalty,
    )


def test_low_content_quality_distinct_from_low_source_quality() -> None:
    threshold = QualityGateConfig().low_content_quality_skip_threshold

    low_source_cluster = _single_source_cluster(
        "low-source", "Thin single-source story", quality_score=0.3, content_quality_penalty=0.0
    )
    low_content_cluster = _multi_source_cluster(
        "low-content", "Well-sourced but junky story", content_quality_penalty=threshold
    )

    assert skip_reason(low_source_cluster, selected_ids=set()) == "low source quality"
    assert skip_reason(low_content_cluster, selected_ids=set()) == "low content quality"


def test_low_content_quality_wins_when_both_conditions_trip() -> None:
    threshold = QualityGateConfig().low_content_quality_skip_threshold

    both_cluster = _single_source_cluster(
        "both-trip", "Thin single-source junky story", quality_score=0.3, content_quality_penalty=threshold
    )

    assert skip_reason(both_cluster, selected_ids=set()) == "low content quality"


def test_format_skipped_table_distinguishes_low_content_from_low_source_quality() -> None:
    threshold = QualityGateConfig().low_content_quality_skip_threshold

    low_source_cluster = _single_source_cluster(
        "low-source", "Thin single-source story", quality_score=0.3, content_quality_penalty=0.0
    )
    low_content_cluster = _multi_source_cluster(
        "low-content", "Well-sourced but junky story", content_quality_penalty=threshold
    )

    skipped = build_skipped_stories([low_source_cluster, low_content_cluster], selected_clusters=[])

    from news_agent.skipped_log import format_skipped_table

    table = format_skipped_table(skipped)

    assert "low content quality" in table
    assert "low source quality" in table

    lines_by_headline = {line.split(" | ")[3]: line for line in table.splitlines() if " | " in line}
    assert "low content quality" in lines_by_headline["Well-sourced but junky story"]
    assert "low source quality" in lines_by_headline["Thin single-source story"]
