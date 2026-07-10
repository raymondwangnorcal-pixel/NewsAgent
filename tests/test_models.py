from __future__ import annotations

from news_agent.models import BriefingItem, BriefingText


def test_briefing_text_message_uses_readable_sections() -> None:
    briefing = BriefingText(
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
            BriefingItem(
                headline="Oil slips",
                summary="Crude prices moved lower.",
                why_it_matters="Energy prices affect inflation expectations.",
                sources=("MarketWatch",),
            ),
        ),
    )

    message = briefing.to_message()

    assert message.startswith("💸 FINANCE")
    assert "Key headlines" in message
    assert "• Markets rise" in message
    assert "What happened: Stocks rose after inflation data." in message
    assert "Why it matters: Rate expectations shifted." in message
    assert "Sources: Reuters, CNBC" in message
    assert "\n• Oil slips" in message
