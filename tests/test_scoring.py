from __future__ import annotations

from datetime import datetime, timezone

from news_agent.models import AgentConfig, Article, StoryCluster
from news_agent.scoring import score_clusters, top_for_category, top_for_culture
from news_agent.cluster import cluster_articles
from news_agent.skipped_log import skip_reason


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


def test_top_for_category_selects_by_assigned_category_not_score() -> None:
    # Category assignment now happens once, upstream, via news_agent.classify --
    # score_clusters no longer computes any per-category score at all.
    config = _basic_config()
    finance_cluster = cluster_articles(
        [
            Article(
                title="Fed decision lifts stocks as inflation cools",
                url="https://example.com/fed",
                source="Reuters",
                published_at=datetime.now(timezone.utc),
                summary="Investors expect interest rate cuts after new inflation data.",
                reputation=1.0,
            )
        ]
    )[0]
    culture_cluster = cluster_articles(
        [
            Article(
                title="A major film breaks box-office records",
                url="https://example.com/film",
                source="Variety",
                published_at=datetime.now(timezone.utc),
                summary="The film topped the weekend box office by a wide margin.",
                reputation=1.0,
            )
        ]
    )[0]
    finance_cluster.category = "finance"
    culture_cluster.category = "culture"

    clusters = score_clusters([finance_cluster, culture_cluster], config)

    assert top_for_category(clusters, "finance", 1)[0].title == "Fed decision lifts stocks as inflation cools"
    assert top_for_category(clusters, "culture", 1)[0].title == "A major film breaks box-office records"
    assert not any(c.category == "finance" for c in top_for_category(clusters, "culture", 5))


def test_high_quality_confirmation_beats_low_quality_repetition() -> None:
    config = _basic_config()
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
    config = _basic_config()
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
        item.category = "finance"
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
    diverse.category = "finance"
    clusters.append(diverse)

    scored = score_clusters(clusters, config)
    selected = top_for_category(scored, "finance", 3)

    assert any(cluster.sources == ["Other Source"] for cluster in selected)


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


def _culture_story(key: str, source: str, lane: str, score: float, evidence: float = 2.0) -> StoryCluster:
    story = StoryCluster(
        key=key,
        title=key,
        category="culture",
        culture_lane=lane,  # type: ignore[arg-type]
        total_score=score,
        evidence_score=evidence,
        articles=[_article(f"https://example.com/{key}", penalty=0.0, source=source)],
    )
    return story


def test_culture_selector_prefers_lanes_and_hard_caps_source() -> None:
    stories = [
        _culture_story("film-1", "Publisher A", "film_tv", 10),
        _culture_story("film-2", "Publisher A", "film_tv", 9),
        _culture_story("film-3", "Publisher A", "film_tv", 8),
        _culture_story("music", "Publisher B", "music", 7),
        _culture_story("sports", "Publisher C", "sports", 6),
        _culture_story("gaming", "Publisher D", "gaming", 5),
    ]

    selected = top_for_culture(stories)

    assert len({story.culture_lane for story in selected}) >= 3
    assert sum(story.sources[0] == "Publisher A" for story in selected) == 2


def test_culture_selector_never_uses_below_threshold_story() -> None:
    thin = _culture_story("thin", "Publisher", "music", 100, evidence=1.19)

    assert top_for_culture([thin], minimum_evidence_score=1.2) == []


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


def test_max_penalty_cluster_is_downranked_and_flagged_not_dropped() -> None:
    """Task H1 — compounding-penalty regression test.

    ADR-0001 replaces the old hard-reject-before-clustering behavior with a soft
    penalty subtracted from `total_score`. That penalty (capped at
    `max_content_quality_penalty`) compounds with two other independent
    mechanisms that can fire on the same single-source, low-signal cluster: the
    `source_count == 1 and impact_score < 3.0` total_score penalty in
    `score_clusters`, and the `quality_score < 0.55` skip check in
    `skipped_log.py`. The ADR calls out that this compounding is intentional but
    needs an explicit test rather than being assumed safe — specifically, that a
    maximally-penalized cluster is *downranked and observable*, not silently
    dropped from the clusters list the way a pre-clustering hard-reject would
    have dropped it.
    """
    config = _basic_config()
    max_penalty = config.quality_gate.max_content_quality_penalty

    bad_cluster = StoryCluster(
        key="max-penalty-single-source",
        title="Story about something bad",
        articles=[_article("https://example.com/bad", penalty=max_penalty, source="Junk Source")],
    )
    clean_cluster = StoryCluster(
        key="clean-single-source",
        title="Story about something clean",
        articles=[_article("https://example.com/clean", penalty=0.0, source="Clean Source")],
    )

    scored = score_clusters([bad_cluster, clean_cluster], config)

    # (1) Soft-suppressed, not hard-dropped: score_clusters has no mechanism to
    # remove clusters, but this is the exact contract this test protects, so
    # assert it explicitly rather than trusting that implicitly.
    assert len(scored) == 2
    assert any(cluster is bad_cluster for cluster in scored)

    # (2) The mean-aggregated penalty is correctly reflected post-aggregation
    # (single article, so the cluster penalty equals the article penalty).
    assert bad_cluster.content_quality_penalty == max_penalty

    # (3) The penalty measurably suppresses ranking rather than being a no-op:
    # same article shape, only the content_quality_penalty differs.
    assert bad_cluster.total_score < clean_cluster.total_score
    assert clean_cluster.total_score - bad_cluster.total_score >= max_penalty * 0.9

    # (4) Downranked-but-observable, verified via the actual reporting path:
    # a maximally-penalized cluster gets tagged with a skip reason instead of
    # crashing or vanishing from the skipped-stories report. Since
    # max_content_quality_penalty (2.5 by default) exceeds
    # low_content_quality_skip_threshold (1.0 by default), this resolves to
    # "low content quality" specifically, ahead of the other fallback reasons.
    reason = skip_reason(bad_cluster, selected_ids=set())
    assert reason
    if max_penalty >= config.quality_gate.low_content_quality_skip_threshold:
        assert reason == "low content quality"
