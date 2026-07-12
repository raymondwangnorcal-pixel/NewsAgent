from __future__ import annotations

from news_agent.models import BriefingItem, BriefingText


def test_briefing_text_message_uses_readable_sections() -> None:
    briefing = BriefingText(
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
    assert "Also happening" in message
    assert "• Markets rise" in message
    assert "Stocks rose after inflation data. Rate expectations shifted." in message
    assert "(via Reuters, CNBC)" in message
    assert "\n• Oil slips" in message
