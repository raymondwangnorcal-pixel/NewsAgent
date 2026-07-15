from __future__ import annotations

from datetime import datetime, timezone

from news_agent.models import Article, BriefingItem, BriefingText, StoryCluster
from news_agent.summarize import (
    build_polish_prompt,
    clean_fallback_summary,
    compact_text,
    event_why_it_matters,
    fallback_why_it_matters,
    parse_briefings,
)


def test_parse_briefings_maps_structured_output() -> None:
    briefings = parse_briefings(
        {
            "briefings": [
                {
                    "category": "finance",
                    "title": "5/5 Financial news",
                    "items": [
                        {
                            "headline": "Markets rise",
                            "summary": "Stocks rose after inflation data.",
                            "why_it_matters": "Rate expectations shifted.",
                            "next_watch": "Fed speakers.",
                            "sources": ["Reuters", "CNBC"],
                        }
                    ],
                }
            ]
        }
    )

    assert briefings[0].items[0].sources == ("Reuters", "CNBC")
    assert "Markets rise" in briefings[0].to_sms()


def test_build_polish_prompt_uses_compact_draft_payload() -> None:
    prompt = build_polish_prompt(
        [
            BriefingText(
                category="finance",
                title="5/5 Financial news",
                items=(
                    BriefingItem(
                        headline="Markets rise",
                        summary="Stocks rose after inflation data.",
                        why_it_matters="Rate expectations shifted.",
                        next_watch="Fed speakers.",
                        sources=("Reuters", "CNBC"),
                    ),
                ),
            )
        ]
    )

    assert "draft_briefings" in prompt
    assert "Markets rise" in prompt
    assert "Reuters" in prompt
    assert "article_samples" not in prompt
    assert "category_clusters" not in prompt
    assert "market_snapshot" not in prompt
    assert "https://example.com" not in prompt


def test_fallback_helpers_keep_copy_clean() -> None:
    text = "Why it matters: " + " ".join(["market"] * 80)

    compact = compact_text(text, max_chars=40)

    assert compact == "market market market market market."
    assert fallback_why_it_matters(1).startswith("Single source, but high-signal")
    assert fallback_why_it_matters(3).startswith("3 outlets are on this")


def test_clean_fallback_summary_omits_source_suffix_and_headline_duplicates() -> None:
    cluster = StoryCluster(
        key="market",
        title="Markets rise",
        articles=[
            Article(
                title="Markets rise",
                url="https://example.com",
                source="Yahoo Finance",
                published_at=datetime.now(timezone.utc),
                summary="Markets rise Yahoo Finance",
            )
        ],
    )

    assert clean_fallback_summary(cluster) == ""


def test_clean_fallback_summary_uses_detail_from_another_cluster_article() -> None:
    cluster = StoryCluster(
        key="market",
        title="Markets rise",
        articles=[
            Article(
                title="Markets rise",
                url="https://example.com/first",
                source="Reuters",
                published_at=datetime.now(timezone.utc),
                summary="Markets rise Reuters",
            ),
            Article(
                title="Stocks rally after inflation data",
                url="https://example.com/second",
                source="CNBC",
                published_at=datetime.now(timezone.utc),
                summary="Stocks rose after inflation data eased concerns about further rate increases.",
            ),
        ],
    )

    assert clean_fallback_summary(cluster) == "Stocks rose after inflation data eased concerns about further rate increases."


def test_clean_fallback_summary_preserves_more_article_detail() -> None:
    long_summary = (
        "Markets rose after investors parsed inflation data, bond yields, and early earnings commentary from "
        "large companies. Strategists said the move looked broad rather than limited to a few mega-cap names, "
        "with cyclical shares and defensive sectors both drawing buyers. Traders were also watching whether "
        "late-session demand would hold into the next round of central bank remarks."
    )
    cluster = StoryCluster(
        key="market",
        title="Markets rise after inflation data",
        articles=[
            Article(
                title="Markets rise after inflation data",
                url="https://example.com",
                source="Reuters",
                published_at=datetime.now(timezone.utc),
                summary=long_summary,
            )
        ],
    )

    summary = clean_fallback_summary(cluster)

    assert "late-session demand" in summary
    assert len(summary) > 240


def test_event_why_it_matters_explains_nvidia_export_restriction_relevance() -> None:
    cluster = StoryCluster(
        key="nvidia export",
        title="Nvidia falls 6% after new export restriction news",
        articles=[
            Article(
                title="Nvidia falls 6% after new export restriction news",
                url="https://example.com/nvda",
                source="Reuters",
                published_at=datetime.now(timezone.utc),
                summary="New AI chip export restrictions raised concern about Nvidia sales in China.",
            )
        ],
    )

    why = event_why_it_matters(cluster, "finance")

    assert why == "Chip export curbs could hit Nvidia's China sales hard and drag down the rest of the semiconductor trade."


def test_event_why_it_matters_explains_inflation_relevance() -> None:
    cluster = StoryCluster(
        key="inflation",
        title="Inflation cools more than expected",
        articles=[
            Article(
                title="Inflation cools more than expected",
                url="https://example.com/inflation",
                source="CNBC",
                published_at=datetime.now(timezone.utc),
                summary="Consumer prices rose less than economists expected.",
            )
        ],
    )

    why = event_why_it_matters(cluster, "finance")

    assert "rate-cut bets" in why
    assert "equity valuations" in why


def test_event_why_it_matters_explains_conflict_relevance() -> None:
    cluster = StoryCluster(
        key="conflict",
        title="Missile attack escalates regional conflict",
        articles=[
            Article(
                title="Missile attack escalates regional conflict",
                url="https://example.com/conflict",
                source="BBC",
                published_at=datetime.now(timezone.utc),
                summary="The attack raised fears of a broader war.",
            )
        ],
    )

    why = event_why_it_matters(cluster, "global")

    assert "humanitarian risk" in why
    assert "energy" in why


def test_event_why_it_matters_treats_opinion_pieces_as_low_confidence() -> None:
    cluster = StoryCluster(
        key="opinion-alaska",
        title="Opinion: The cost of Trump's war on science will be measured in Alaska",
        articles=[
            Article(
                title="Opinion: The cost of Trump's war on science will be measured in Alaska",
                url="https://example.com/opinion",
                source="Anchorage Daily",
                published_at=datetime.now(timezone.utc),
                summary="A researcher argues federal science funding cuts will hurt Alaska.",
            )
        ],
    )

    why = event_why_it_matters(cluster, "business_tech")

    assert "humanitarian" not in why
    assert "diplomacy" not in why
    assert why.startswith("Single source, but high-signal")


def test_event_why_it_matters_ignores_war_on_science_idiom_even_without_opinion_label() -> None:
    cluster = StoryCluster(
        key="war-on-science",
        title="Trump's war on science funding hits research labs",
        articles=[
            Article(
                title="Trump's war on science funding hits research labs",
                url="https://example.com/science-funding",
                source="Some Outlet",
                published_at=datetime.now(timezone.utc),
                summary="Federal grant cuts are forcing labs to lay off researchers.",
            )
        ],
    )

    why = event_why_it_matters(cluster, "domestic")

    assert "humanitarian risk" not in why
    assert "diplomacy" not in why


def test_event_why_it_matters_handles_central_bank_forecast_without_company_leak() -> None:
    cluster = StoryCluster(
        key="boj-forecast",
        title="BOJ may raise growth forecast, maintain vigilance to inflation risk, sources say",
        articles=[
            Article(
                title="BOJ may raise growth forecast, maintain vigilance to inflation risk, sources say",
                url="https://example.com/boj",
                source="Yahoo! Finance Canada",
                published_at=datetime.now(timezone.utc),
                summary="The Bank of Japan is weighing an upgrade to its growth outlook.",
            )
        ],
    )

    why = event_why_it_matters(cluster, "finance")

    assert "The company" not in why
    assert "Central bank" in why


def test_event_why_it_matters_handles_forecast_without_named_company() -> None:
    cluster = StoryCluster(
        key="japan-forecasts",
        title="More Japanese Companies Choose Not to Publish Earnings Forecasts Amid Oil Uncertainty",
        articles=[
            Article(
                title="More Japanese Companies Choose Not to Publish Earnings Forecasts Amid Oil Uncertainty",
                url="https://example.com/japan-earnings",
                source="Nippon",
                published_at=datetime.now(timezone.utc),
                summary="Companies cited oil price swings as a reason to withhold guidance.",
            )
        ],
    )

    why = event_why_it_matters(cluster, "business_tech")

    assert "The company" not in why
    assert "guidance" in why
