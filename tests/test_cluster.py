from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from news_agent.cluster import (
    article_cluster_similarity,
    cluster_articles,
    cluster_event_terms,
    cluster_tokens,
    clusters_are_different_developments,
    duplicate_gate_candidates,
    jaccard,
    normalize_title,
    specific_shared_entities,
)
from news_agent.models import Article, DuplicateGateConfig, StoryCluster


def article(title: str, source: str = "Source", hours_ago: int = 0) -> Article:
    return Article(
        title=title,
        url=f"https://example.com/{hash(title)}",
        source=source,
        published_at=datetime.now(timezone.utc) - timedelta(hours=hours_ago),
    )


def story_cluster(title: str, source: str = "Source", hours_ago: int = 0) -> StoryCluster:
    item = article(title, source, hours_ago)
    item = replace(item, summary=f"substantive reporting about {title}.")
    return StoryCluster(key=title.casefold(), title=title, articles=[item])


def test_cluster_articles_groups_similar_headlines() -> None:
    clusters = cluster_articles(
        [
            article("Fed signals rate cuts as inflation cools", "A"),
            article("Inflation cools as Fed signals possible rate cuts", "B"),
            article("Streaming service launches a new comedy slate", "C"),
        ]
    )

    assert len(clusters) == 2
    assert max(len(cluster.articles) for cluster in clusters) == 2


def test_normalize_title_removes_source_punctuation_and_filler() -> None:
    assert normalize_title("LIVE: Fed signals rate cuts, Reuters", "Reuters") == "fed signals rate cuts"


def test_cluster_articles_groups_same_event_across_outlets() -> None:
    clusters = cluster_articles(
        [
            article("Nvidia shares jump after earnings beat and higher forecast", "Reuters"),
            article("Nvidia stock rises as profit forecast tops estimates", "CNBC"),
            article("Apple growers face heat damage across Washington", "AP"),
        ]
    )

    grouped = [cluster for cluster in clusters if "Nvidia" in cluster.title]

    assert len(grouped) == 1
    assert len(grouped[0].articles) == 2
    assert grouped[0].source_count == 2


def test_cluster_articles_keeps_same_company_different_events_separate() -> None:
    clusters = cluster_articles(
        [
            article("Tesla shares rise after earnings beat", "Reuters"),
            article("Tesla recalls vehicles after safety probe", "AP"),
        ]
    )

    assert len(clusters) == 2


def test_cluster_articles_keeps_unrelated_similar_keywords_separate() -> None:
    clusters = cluster_articles(
        [
            article("Apple launches new iPhone with AI tools", "The Verge"),
            article("Apple growers face crop losses after extreme heat", "AP"),
        ]
    )

    assert len(clusters) == 2


def test_cluster_articles_merges_different_feed_urls_with_one_canonical_url() -> None:
    canonical_url = "https://publisher.example.com/news/canonical-story"
    first = replace(
        article("Company announces new factory", "Reuters"),
        canonical_url=canonical_url,
    )
    second = replace(
        article("Local officials approve industrial project", "AP"),
        canonical_url=canonical_url,
    )

    clusters = cluster_articles([first, second])

    assert len(clusters) == 1
    assert clusters[0].source_count == 2


def test_duplicate_gate_candidates_include_real_anthropic_pair() -> None:
    left = story_cluster(
        "Anthropic's Dario Amodei responds: doesn't oppose open-weight models, but fears Chinese AI",
        "TechCrunch",
    )
    right = story_cluster(
        "Anthropic CEO Dario Amodei says AI company isn't advocating for ban of open-weight models",
        "CNBC",
    )
    right.articles[0] = replace(right.articles[0], summary="")
    left.articles[0] = replace(
        left.articles[0],
        summary="Anthropic does not support a ban on open-weight AI models.",
    )
    right.articles[0] = replace(
        right.articles[0],
        summary="Anthropic is not advocating a ban on open-weight AI models.",
    )

    pairs = duplicate_gate_candidates([left, right], DuplicateGateConfig())

    assert jaccard(cluster_tokens(left), cluster_tokens(right)) == pytest.approx(0.3529, abs=0.0001)
    assert specific_shared_entities(left, right) == {"anthropic"}
    assert not clusters_are_different_developments(left, right, pairs[0].title_jaccard)
    assert [(pair.left, pair.right) for pair in pairs] == [(left, right)]


def test_real_anthropic_pair_remains_below_first_pass_threshold() -> None:
    left = article(
        "Anthropic's Dario Amodei responds: doesn't oppose open-weight models, but fears Chinese AI",
        "TechCrunch",
    )
    right = story_cluster(
        "Anthropic CEO Dario Amodei says AI company isn't advocating for ban of open-weight models",
        "CNBC",
    )

    assert article_cluster_similarity(left, right) == pytest.approx(0.3275, abs=0.0001)
    assert article_cluster_similarity(left, right) < 0.43


def test_duplicate_gate_rejects_pair_sharing_only_generic_entities() -> None:
    left = story_cluster("AI CEO discusses cloud capacity", "A")
    right = story_cluster("AI CEO discusses labor negotiations", "B")

    assert duplicate_gate_candidates([left, right], DuplicateGateConfig()) == []


def test_duplicate_gate_rejects_sentence_starters_as_shared_entities() -> None:
    left = story_cluster("Film studio wins a bidding war for a horror script", "A")
    right = story_cluster("Iran war enters a new phase", "B")
    left.articles[0] = replace(
        left.articles[0],
        summary="Both companies said they would discuss the deal later.",
    )
    right.articles[0] = replace(
        right.articles[0],
        summary="Both governments said they would discuss the war later.",
    )

    assert specific_shared_entities(left, right) == set()
    assert duplicate_gate_candidates([left, right], DuplicateGateConfig()) == []


def test_duplicate_gate_vetoes_distinct_tesla_developments() -> None:
    earnings = story_cluster("Tesla earnings beat", "A")
    recall = story_cluster("Tesla safety recall", "B")

    assert jaccard(cluster_tokens(earnings), cluster_tokens(recall)) == pytest.approx(0.2)
    assert cluster_event_terms(earnings) == {"earnings"}
    assert cluster_event_terms(recall) == {"recall"}
    assert clusters_are_different_developments(earnings, recall, 0.2)
    assert duplicate_gate_candidates([earnings, recall], DuplicateGateConfig()) == []


def test_duplicate_gate_includes_d4vd_trial_pair_without_event_terms() -> None:
    left = story_cluster("US singer D4vd to go on trial for murder in death of 14-year-old", "BBC")
    right = story_cluster("D4vd ordered to stand trial in Celeste Rivas Hernandez murder case", "Deadline")

    assert not cluster_event_terms(left)
    assert not cluster_event_terms(right)
    assert len(duplicate_gate_candidates([left, right], DuplicateGateConfig())) == 1


def test_duplicate_gate_rejects_pair_outside_time_window() -> None:
    left = story_cluster("Acme launches new electric car", "A")
    right = story_cluster("Acme electric car launch reaches dealers", "B", hours_ago=30)

    assert duplicate_gate_candidates([left, right], DuplicateGateConfig()) == []


def test_duplicate_gate_empty_deck_produces_no_candidates() -> None:
    clusters = [story_cluster(f"organization {index} announces topic {index}") for index in range(25)]

    assert duplicate_gate_candidates(clusters, DuplicateGateConfig()) == []
