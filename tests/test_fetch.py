from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import news_agent.fetch as fetch
from news_agent.fetch import fetch_feed_with_status, parse_feed, select_articles_with_category_reserves
from news_agent.models import Article, FeedConfig


def feed() -> FeedConfig:
    return FeedConfig(
        name="Google News Culture",
        url="https://example.com/feed",
        reputation=0.7,
        categories=("culture",),
        culture_lane="internet_culture",
    )


def test_parse_feed_rejects_spam_streaming_title() -> None:
    xml = """<rss><channel><item>
        <title>!+[Here's Way To Watch]!@! England v Norway Match Ｌｉｖｅ Ｓｔｒｅａｍｉｎｇ Ｆｒｅｅ ＯＮ Ｔｖ Ｃｈａｎｎｅｌ</title>
        <link>https://example.com/spam</link>
        <description>Spam promotion.</description>
    </item></channel></rss>"""

    assert parse_feed(xml, feed()) == []


def test_parse_feed_normalizes_small_amounts_of_fullwidth_typography() -> None:
    xml = """<rss><channel><item>
        <title>ＡI startup raises new funding - Google News Culture</title>
        <link>https://example.com/funding</link>
        <description>The company raised funding for its AI products.</description>
    </item></channel></rss>"""

    articles = parse_feed(xml, feed())

    assert len(articles) == 1
    assert articles[0].title == "AI startup raises new funding"


def test_parse_feed_reads_namespaced_content_encoded() -> None:
    xml = """<rss xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel><item>
        <title>Company announces a major expansion</title>
        <link>https://example.com/expansion</link>
        <description>Short summary.</description>
        <content:encoded><![CDATA[The company will invest $2 billion and add 4,000 jobs. Construction begins next year.]]></content:encoded>
    </item></channel></rss>"""

    article = parse_feed(xml, feed())[0]

    assert "$2 billion" in article.feed_content
    assert article.enrichment_status == "feed_content"


def test_parse_feed_reads_nested_atom_xhtml() -> None:
    xml = """<feed xmlns="http://www.w3.org/2005/Atom" xmlns:xhtml="http://www.w3.org/1999/xhtml">
      <entry><title>Detailed Atom report</title><link href="https://example.com/atom"/>
      <content type="xhtml"><xhtml:div><xhtml:p>First substantive paragraph.</xhtml:p><xhtml:p>Second contextual paragraph.</xhtml:p></xhtml:div></content>
      </entry></feed>"""

    article = parse_feed(xml, feed())[0]

    assert "First substantive paragraph" in article.feed_content
    assert "Second contextual paragraph" in article.feed_content


def test_parse_feed_copies_culture_lane() -> None:
    xml = """<rss><channel><item><title>Culture report</title>
    <link>https://example.com/culture</link><description>A substantive report.</description>
    </item></channel></rss>"""

    article = parse_feed(xml, feed())[0]

    assert article.feed_culture_lane == "internet_culture"
    assert article.feed_timestamp_valid is False


def test_parse_feed_marks_valid_feed_timestamp() -> None:
    xml = """<rss><channel><item><title>Timestamped report</title>
    <link>https://example.com/timestamped</link><description>A report.</description>
    <pubDate>Tue, 21 Jul 2026 12:00:00 GMT</pubDate></item></channel></rss>"""

    article = parse_feed(xml, feed())[0]

    assert article.feed_timestamp_valid is True


def test_fetch_feed_rejects_a_valid_html_provider_error_page(monkeypatch: pytest.MonkeyPatch) -> None:
    class Response:
        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return b"<html><head><title>Yahoo</title></head><body>Will be right back</body></html>"

    monkeypatch.setattr(fetch.urllib.request, "urlopen", lambda *_args, **_kwargs: Response())

    articles, error = fetch_feed_with_status(feed())

    assert articles == []
    assert error == "invalid_feed"


def _article(key: str, minutes_old: int, categories: tuple[str, ...]) -> Article:
    return Article(
        title=key,
        url=f"https://example.com/{key}",
        source="Example",
        published_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_old),
        feed_categories=categories,
    )


def test_fetch_selection_reserves_categories_before_global_recency_cutoff() -> None:
    reserves = {"business_tech": 2, "domestic": 2, "global": 2, "finance": 2, "culture": 1}
    newest_culture = [_article(f"culture-{index}", index, ("culture",)) for index in range(10)]
    reserved = [
        *[_article(f"business-{index}", 20 + index, ("business_tech",)) for index in range(2)],
        *[_article(f"domestic-{index}", 30 + index, ("domestic",)) for index in range(2)],
        *[_article(f"global-{index}", 40 + index, ("global",)) for index in range(2)],
        *[_article(f"finance-{index}", 50 + index, ("finance",)) for index in range(2)],
    ]

    selected = select_articles_with_category_reserves([*newest_culture, *reserved], 12, reserves)

    assert len(selected) == 12
    for category, minimum in reserves.items():
        assert sum(category in article.feed_categories for article in selected) >= minimum


def test_fetch_selection_dual_tag_article_counts_twice_but_returns_once() -> None:
    dual = _article("dual", 1, ("business_tech", "culture"))

    selected = select_articles_with_category_reserves(
        [dual, _article("other", 2, ())],
        2,
        {"business_tech": 1, "culture": 1},
    )

    assert selected.count(dual) == 1
    assert len(selected) == 2


def test_fetch_selection_releases_unfilled_reserve_to_global_remainder() -> None:
    articles = [_article(f"general-{index}", index, ()) for index in range(5)]

    selected = select_articles_with_category_reserves(articles, 3, {"domestic": 3})

    assert [article.title for article in selected] == ["general-0", "general-1", "general-2"]
