from __future__ import annotations

from datetime import datetime, timezone

from news_agent.history import apply_history, save_story_history
from news_agent.models import Article, StoryCluster


def cluster(title: str, source: str = "Reuters", summary: str = "A summary.") -> StoryCluster:
    item = StoryCluster(
        key=title.lower(),
        title=title,
        articles=[
            Article(
                title=title,
                url=f"https://example.com/{hash(title + source)}",
                source=source,
                published_at=datetime.now(timezone.utc),
                summary=summary,
                reputation=1.0,
            )
        ],
        category_scores={"finance": 1.0},
        total_score=5.0,
    )
    return item


def test_history_marks_repeated_unchanged_story_as_skipped(tmp_path) -> None:
    path = tmp_path / "story_history.json"
    original = cluster("Nvidia rises after earnings beat", summary="Nvidia raised guidance after earnings.")
    save_story_history([original], path)
    repeated = cluster("Nvidia rises after earnings beat", summary="Nvidia raised guidance after earnings.")

    apply_history([repeated], path)

    assert repeated.skip_reason == "stale/repeated from yesterday"


def test_history_marks_same_story_with_new_source_as_update(tmp_path) -> None:
    path = tmp_path / "story_history.json"
    original = cluster("Nvidia rises after earnings beat", summary="Nvidia raised guidance after earnings.")
    save_story_history([original], path)
    updated = cluster("Nvidia rises after earnings beat", summary="Nvidia raised guidance after earnings.")
    updated.articles.append(
        Article(
            title="NVDA jumps as earnings top estimates",
            url="https://example.com/cnbc",
            source="CNBC",
            published_at=datetime.now(timezone.utc),
            summary="CNBC also reported the earnings beat.",
            reputation=0.9,
        )
    )

    apply_history([updated], path)

    assert updated.is_update is True
    assert updated.update_note
    assert not updated.skip_reason


def test_history_keeps_same_company_different_story(tmp_path) -> None:
    path = tmp_path / "story_history.json"
    original = cluster("Tesla shares rise after earnings beat", summary="Tesla beat earnings expectations.")
    save_story_history([original], path)
    different = cluster("Tesla recalls vehicles after safety probe", summary="Tesla issued a recall after a probe.")

    apply_history([different], path)

    assert not different.skip_reason
    assert different.is_update is False
