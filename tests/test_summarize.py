from __future__ import annotations

from news_agent.summarize import parse_briefings


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
