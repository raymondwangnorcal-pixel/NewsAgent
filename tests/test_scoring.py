from __future__ import annotations

from datetime import datetime, timezone

from news_agent.models import AgentConfig, Article, CategoryConfig, FeedConfig, StoryCluster
from news_agent.scoring import score_clusters, top_for_category
from news_agent.cluster import cluster_articles


def _basic_config() -> AgentConfig:
    return AgentConfig(
        feeds=(),
        categories={},
        lookback_hours=30,
        max_articles=20,
    )


def _article(url: str, *, penalty: float, source: str = "Some Source") -> Article:
    return Article(
        title=f"Story about something {url}",
        url=url,
        source=source,
        published_at=datetime.now(timezone.utc),
        summary="A reasonably long summary describing the event in some detail.",
        reputation=0.8,
        content_quality_penalty=penalty,
    )


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


def test_aggregator_feed_category_tag_is_discounted_without_keyword_signal() -> None:
    config = AgentConfig(
        feeds=(),
        categories={
            "business_tech": CategoryConfig(
                "business_tech", "Business and technology", ("ai", "startup", "chip"), ("layoffs",)
            ),
            "domestic": CategoryConfig("domestic", "Domestic", ("federal", "policy"), ("federal",)),
        },
        lookback_hours=30,
        max_articles=20,
    )
    off_topic = cluster_articles(
        [
            Article(
                title="Opinion: The cost of Trump's war on science will be measured in Alaska",
                url="https://example.com/opinion",
                source="Anchorage Daily",
                published_at=datetime.now(timezone.utc),
                summary="A researcher argues federal funding cuts will hurt Alaska research.",
                reputation=0.75,
                feed_categories=("business_tech",),
                feed_source_type="aggregator",
            )
        ]
    )[0]
    on_topic = cluster_articles(
        [
            Article(
                title="Startup raises new funding for AI chip design",
                url="https://example.com/startup",
                source="TechCrunch",
                published_at=datetime.now(timezone.utc),
                summary="The startup will use the funding to expand its chip design team.",
                reputation=0.85,
                feed_categories=("business_tech",),
                feed_source_type="dedicated",
            )
        ]
    )[0]

    clusters = score_clusters([off_topic, on_topic], config)
    off_topic_scored = next(c for c in clusters if c.key == off_topic.key)
    on_topic_scored = next(c for c in clusters if c.key == on_topic.key)

    assert off_topic_scored.category_scores["business_tech"] < on_topic_scored.category_scores["business_tech"]
    assert off_topic_scored.category_scores["business_tech"] < 0.2


def test_single_article_cluster_penalty_equals_article_penalty() -> None:
    config = _basic_config()
    clear_bad_weight = config.quality_gate.clear_bad_penalty_weight
    cluster = StoryCluster(
        key="single",
        title="Story about something",
        articles=[_article("https://example.com/1", penalty=clear_bad_weight)],
    )

    scored = score_clusters([cluster], config)

    assert scored[0].content_quality_penalty == clear_bad_weight


def test_one_bad_one_clean_article_moderately_dilutes_penalty() -> None:
    config = _basic_config()
    clear_bad_weight = config.quality_gate.clear_bad_penalty_weight
    cluster = StoryCluster(
        key="mixed",
        title="Story about something",
        articles=[
            _article("https://example.com/bad", penalty=clear_bad_weight, source="Bad Source"),
            _article("https://example.com/good", penalty=0.0, source="Good Source"),
        ],
    )

    scored = score_clusters([cluster], config)

    expected = clear_bad_weight / 2
    assert abs(scored[0].content_quality_penalty - expected) < 1e-9
    # Moderate dilution, not zeroed: penalty should sit meaningfully below the
    # full clear_bad weight but well above zero.
    assert 0 < scored[0].content_quality_penalty < clear_bad_weight


def test_majority_junk_cluster_stays_close_to_clear_bad_weight() -> None:
    """One clean corroborator shouldn't fully absolve a mostly-junk cluster.

    This is the exact scenario ADR-0001 was written to fix: under the
    originally-proposed MIN-aggregation, a single clean article would zero out
    the whole cluster's penalty regardless of how much junk surrounded it.
    Mean-aggregation instead keeps the penalty close to the clear_bad weight
    when the cluster is majority junk.
    """
    config = _basic_config()
    clear_bad_weight = config.quality_gate.clear_bad_penalty_weight
    articles = [
        _article(f"https://example.com/bad{i}", penalty=clear_bad_weight, source=f"Bad Source {i}")
        for i in range(5)
    ] + [_article("https://example.com/good", penalty=0.0, source="Good Source")]
    cluster = StoryCluster(key="majority-junk", title="Story about something", articles=articles)

    scored = score_clusters([cluster], config)

    expected = (5 * clear_bad_weight) / 6
    assert abs(scored[0].content_quality_penalty - expected) < 1e-9
    # Stays close to the clear_bad weight, not diluted away toward zero.
    assert scored[0].content_quality_penalty > 0.8 * clear_bad_weight


def test_all_clean_cluster_has_zero_content_quality_penalty() -> None:
    config = _basic_config()
    cluster = StoryCluster(
        key="clean",
        title="Story about something",
        articles=[
            _article("https://example.com/1", penalty=0.0, source="Source A"),
            _article("https://example.com/2", penalty=0.0, source="Source B"),
        ],
    )

    scored = score_clusters([cluster], config)

    assert scored[0].content_quality_penalty == 0
