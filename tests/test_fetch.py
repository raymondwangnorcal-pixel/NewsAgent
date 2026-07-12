from __future__ import annotations

from news_agent.fetch import parse_feed
from news_agent.models import FeedConfig


def feed() -> FeedConfig:
    return FeedConfig(
        name="Google News Culture",
        url="https://example.com/feed",
        reputation=0.7,
        categories=("culture",),
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
