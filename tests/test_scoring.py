from __future__ import annotations

from datetime import datetime, timezone

from news_agent.models import AgentConfig, Article, CategoryConfig, FeedConfig
from news_agent.scoring import score_clusters, top_for_category
from news_agent.cluster import cluster_articles


def test_finance_story_scores_for_finance_category() -> None:
    config = AgentConfig(
        feeds=(FeedConfig("Reuters", "https://example.com", 1.0, ("finance",)),),
        categories={
            "finance": CategoryConfig(
                name="finance",
                label="Financial news",
                keywords=("fed", "inflation", "stock", "earnings"),
                impact_terms=("fed", "inflation"),
            ),
            "culture": CategoryConfig(
                name="culture",
                label="Culture",
                keywords=("film", "music"),
                impact_terms=("viral",),
            ),
        },
        lookback_hours=30,
        max_articles=20,
    )
    articles = [
        Article(
            title="Fed decision lifts stocks as inflation cools",
            url="https://example.com/fed",
            source="Reuters",
            published_at=datetime.now(timezone.utc),
            summary="Investors expect interest rate cuts after new inflation data.",
            reputation=1.0,
            feed_categories=("finance",),
        )
    ]

    clusters = score_clusters(cluster_articles(articles), config)

    assert top_for_category(clusters, "finance", 1)[0].title == "Fed decision lifts stocks as inflation cools"
    assert clusters[0].category_scores["finance"] > clusters[0].category_scores["culture"]
