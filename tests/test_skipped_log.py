from __future__ import annotations

from datetime import datetime, timezone
import json

from news_agent.models import Article, StoryCluster
from news_agent.skipped_log import build_skipped_stories, write_skipped_log


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
        category_scores={"business_tech": 1.0},
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
