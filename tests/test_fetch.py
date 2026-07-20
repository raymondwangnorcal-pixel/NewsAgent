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
