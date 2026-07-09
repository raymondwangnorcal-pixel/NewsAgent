from __future__ import annotations

from datetime import datetime, timezone

from news_agent.models import Article, BriefingItem, BriefingText, StoryCluster
from news_agent.summarize import (
    build_polish_prompt,
    clean_fallback_summary,
    compact_text,
    fallback_why_it_matters,
    parse_briefings,
)


def test_parse_briefings_maps_structured_output() -> None:
    briefings = parse_briefings(
        {
            "briefings": [
                {
                    "category": "finance",
                    "title": "5/6 Financial news",
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
                title="5/6 Financial news",
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
    assert fallback_why_it_matters(1).startswith("A single-source item")
    assert fallback_why_it_matters(3).startswith("Confirmed by 3 sources")


def test_clean_fallback_summary_removes_source_suffix_and_headline_duplicates() -> None:
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

    assert clean_fallback_summary(cluster) == "No additional source summary was available."
