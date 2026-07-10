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


def test_high_quality_confirmation_beats_low_quality_repetition() -> None:
    config = AgentConfig(
        feeds=(),
        categories={
            "domestic": CategoryConfig("domestic", "Domestic", ("court", "policy"), ("court", "policy")),
        },
        lookback_hours=30,
        max_articles=20,
    )
    articles = [
        Article(
            title="Supreme Court ruling changes federal policy",
            url="https://example.com/reuters",
            source="Reuters",
            published_at=datetime.now(timezone.utc),
            reputation=1.0,
            feed_categories=("domestic",),
        ),
        Article(
            title="Federal policy shifts after Supreme Court ruling",
            url="https://example.com/ap",
            source="AP",
            published_at=datetime.now(timezone.utc),
            reputation=1.0,
            feed_categories=("domestic",),
        ),
        Article(
            title="Rumor blog says court policy drama grows",
            url="https://example.com/blog1",
            source="Unknown Blog 1",
            published_at=datetime.now(timezone.utc),
            reputation=0.2,
            feed_categories=("domestic",),
        ),
        Article(
            title="Another rumor blog says court policy drama grows",
            url="https://example.com/blog2",
            source="Unknown Blog 2",
            published_at=datetime.now(timezone.utc),
            reputation=0.2,
            feed_categories=("domestic",),
        ),
    ]

    clusters = score_clusters(cluster_articles(articles), config)

    assert clusters[0].quality_score >= 0.9
    assert "Supreme Court" in clusters[0].title or "Federal policy" in clusters[0].title


def test_top_for_category_limits_single_source_overrepresentation() -> None:
    clusters = []
    for index in range(4):
        item = cluster_articles(
            [
                Article(
                    title=f"Market story {index}",
                    url=f"https://example.com/{index}",
                    source="Same Source",
                    published_at=datetime.now(timezone.utc),
                    reputation=0.8,
                )
            ]
        )[0]
        item.category_scores["finance"] = 10 - index
        item.total_score = 10 - index
        clusters.append(item)
    diverse = cluster_articles(
        [
            Article(
                title="Market story from another source",
                url="https://example.com/diverse",
                source="Other Source",
                published_at=datetime.now(timezone.utc),
                reputation=0.8,
            )
        ]
    )[0]
    diverse.category_scores["finance"] = 6
    diverse.total_score = 6
    clusters.append(diverse)

    selected = top_for_category(clusters, "finance", 3)

    assert any(cluster.sources == ["Other Source"] for cluster in selected)
