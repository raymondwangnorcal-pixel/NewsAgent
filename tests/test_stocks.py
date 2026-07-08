from __future__ import annotations

from datetime import datetime, timezone

from news_agent.models import Article
from news_agent.stocks import collect_stock_mentions, extract_tickers_from_headline


def test_extract_tickers_from_explicit_headline_patterns() -> None:
    headline = "Nvidia (NVDA) jumps as $TSLA slides and NASDAQ:MSFT holds steady"

    assert extract_tickers_from_headline(headline) == {"NVDA", "TSLA", "MSFT"}


def test_collect_stock_mentions_counts_sources() -> None:
    articles = [
        Article(
            title="Apple unveils new iPhone as AAPL shares rise",
            url="https://example.com/1",
            source="Reuters",
            published_at=datetime.now(timezone.utc),
        ),
        Article(
            title="$AAPL stock climbs again",
            url="https://example.com/2",
            source="CNBC",
            published_at=datetime.now(timezone.utc),
        ),
    ]

    mentions = collect_stock_mentions(articles)

    assert mentions[0].symbol == "AAPL"
    assert mentions[0].mention_count == 2
    assert mentions[0].sources == ("CNBC", "Reuters")
