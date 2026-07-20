from __future__ import annotations

from datetime import datetime, timezone

import news_agent.enrichment as enrichment
from news_agent.models import Article, EnrichmentConfig, ExtractionPolicyConfig, StoryCluster


def config(policy: str = "article_text") -> EnrichmentConfig:
    return EnrichmentConfig(
        minimum_extracted_chars=40,
        policies=(ExtractionPolicyConfig(id="example", allowed_domains=("example.com",), policy=policy),),
    )


def article(url: str = "https://example.com/story") -> Article:
    return Article(title="A story", url=url, source="Example", published_at=datetime.now(timezone.utc), summary="Short summary")


def test_enrich_article_extracts_json_ld_body_when_extractor_unavailable(monkeypatch) -> None:
    body = b'''<html><head><link rel="canonical" href="https://example.com/canonical">
    <script type="application/ld+json">{"@type":"NewsArticle","articleBody":"A detailed report says the company will invest $2 billion. Construction begins next year and 4,000 workers will be hired."}</script></head></html>'''
    monkeypatch.setattr(enrichment, "_extract_text", lambda html_text, url: "")

    result = enrichment.enrich_article(
        article(),
        config(),
        lambda url, cfg: enrichment.FetchedPage("https://example.com/story", "text/html", body),
    )

    assert result.enrichment_status == "extracted"
    assert result.canonical_url == "https://example.com/canonical"
    assert "$2 billion" in result.extracted_text


def test_enrich_article_metadata_policy_does_not_retain_body() -> None:
    result = enrichment.enrich_article(
        article(),
        config("metadata_only"),
        lambda url, cfg: enrichment.FetchedPage("https://example.com/story", "text/html", b"<p>Article body</p>"),
    )

    assert result.enrichment_status == "metadata_only"
    assert result.extracted_text == ""


def test_enrich_article_rejects_unconfigured_domain_without_fetching() -> None:
    called = False

    def fetcher(url, cfg):
        nonlocal called
        called = True
        raise AssertionError("must not fetch")

    result = enrichment.enrich_article(article("https://untrusted.example/story"), config(), fetcher)

    assert result.enrichment_status == "not_permitted"
    assert called is False


def test_enrich_clusters_replaces_original_article_with_enriched_result(monkeypatch) -> None:
    original = article()
    cluster = StoryCluster(key="story", title=original.title, articles=[original])
    body = b'<script type="application/ld+json">{"@type":"NewsArticle","articleBody":"This detailed report contains enough facts to pass extraction and explain what happened clearly."}</script>'
    monkeypatch.setattr(enrichment, "_extract_text", lambda html_text, url: "")

    clusters, stats = enrichment.enrich_clusters(
        [cluster],
        config(),
        lambda url, cfg: enrichment.FetchedPage(url, "text/html", body),
    )

    assert clusters[0].articles[0].enrichment_status == "extracted"
    assert stats.pages_extracted == 1
