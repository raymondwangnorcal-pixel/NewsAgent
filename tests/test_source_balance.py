from datetime import datetime, timezone

from news_agent.models import Article, StoryCluster
from news_agent.source_balance import (
    cluster_source_attributions,
    resolve_source_attribution,
    resolve_source_name,
    source_quality,
)


def article(source: str, *, title: str = "A major event happened", summary: str = "") -> Article:
    return Article(
        title=title,
        url=f"https://example.com/{source.replace(' ', '-').lower()}",
        source=source,
        published_at=datetime.now(timezone.utc),
        summary=summary,
    )


def test_short_aliases_use_word_boundaries() -> None:
    assert resolve_source_name("AP News") == "Associated Press"
    assert resolve_source_name("Daily Capital") is None
    assert source_quality("Daily Capital", fallback=0.42) == 0.42


def test_known_publisher_and_title_credit_are_confirmed() -> None:
    reuters = article("Reuters")
    syndication = article("Local Gazette", title="A major event happened - Reuters")

    assert resolve_source_attribution(reuters, [reuters]).resolved_source == "Reuters"
    attribution = resolve_source_attribution(syndication, [syndication])
    assert attribution.resolved_source == "Reuters"
    assert attribution.confidence == "confirmed"
    assert attribution.display_source == "Local Gazette"


def test_body_similarity_is_uncertain_and_keeps_display_identity() -> None:
    summary = "Officials announced a detailed policy change affecting millions of households next year."
    reuters = article("Reuters", summary=summary)
    local = article("Local Gazette", summary=summary)

    attribution = resolve_source_attribution(local, [reuters, local])

    assert attribution.resolved_source == "Reuters"
    assert attribution.confidence == "uncertain"
    assert attribution.display_source == "Local Gazette"


def test_cluster_attribution_does_not_overwrite_display_sources() -> None:
    wire = article("Reuters")
    copy = article("Local Gazette", title="A major event happened - Reuters")
    cluster = StoryCluster(key="event", title="Event", articles=[wire, copy])

    attributions = cluster_source_attributions(cluster)

    assert {item.resolved_source for item in attributions} == {"Reuters"}
    assert cluster.sources == ["Reuters", "Local Gazette"]
