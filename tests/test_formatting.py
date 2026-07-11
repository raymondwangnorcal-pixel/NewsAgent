from __future__ import annotations

from datetime import date

from news_agent.formatting import (
    FormatOptions,
    clean_source_name,
    format_briefing_previews,
    format_category_message,
    format_console_preview,
    format_sources,
)
from news_agent.models import BriefingItem, BriefingText


def item(
    headline: str = "OpenAI announces new enterprise tools",
    summary: str = "OpenAI introduced new tools aimed at helping companies build internal AI workflows.",
    why: str = "Enterprise adoption is becoming the main battleground for AI platforms.",
    sources: tuple[str, ...] = ("Reuters News", "The Verge", "Bloomberg"),
    urls: tuple[str, ...] = (),
) -> BriefingItem:
    return BriefingItem(
        headline=headline,
        summary=summary,
        why_it_matters=why,
        next_watch="Watch customer adoption.",
        sources=sources,
        urls=urls,
    )


def briefing(category: str = "business_tech", *items: BriefingItem) -> BriefingText:
    return BriefingText(
        category=category,
        title="unused",
        items=items or (item(),),
    )


def test_category_header_formatting() -> None:
    message = format_category_message(
        briefing(),
        options=FormatOptions(mode="telegram", today=date(2026, 7, 10)),
    ).text

    assert message.startswith("🧠 BUSINESS + TECH — July 10\n\n")
    assert "AI, startups, Big Tech, regulation" not in message


def test_story_bullet_formatting() -> None:
    message = format_category_message(
        briefing(),
        options=FormatOptions(mode="telegram", today=date(2026, 7, 10)),
    ).text

    assert "• OpenAI announces new enterprise tools" in message
    assert "  What happened: OpenAI introduced new tools" in message
    assert "  Why it matters: Enterprise adoption is becoming" in message
    assert "  Sources: Reuters, The Verge, Bloomberg" in message


def test_finance_snapshot_and_movers_skip_empty_sections() -> None:
    finance = BriefingText(
        category="finance",
        title="unused",
        items=(
            BriefingItem(
                headline="Mega-cap watchlist",
                summary="AAPL 123.45 (+1.20%); MSFT: quote unavailable; NVDA 200.00 (+5.60%)",
                why_it_matters="Mega-caps can steer indexes.",
                sources=("Yahoo Finance",),
            ),
            BriefingItem(
                headline="Explained market movers",
                summary="NVDA +5.6% (earnings beat expectations); TSLA -4.2% (catalyst unclear from major headlines)",
                why_it_matters="Large asset moves can set the tone.",
                sources=("Reuters", "CNBC"),
            ),
        ),
    )

    message = format_category_message(finance, options=FormatOptions(today=date(2026, 7, 10))).text

    assert "Market snapshot\n• AAPL: 123.45 (+1.20%)\n• NVDA: 200.00 (+5.60%)" in message
    assert "Big movers\n• NVDA +5.6% — earnings beat expectations" in message
    assert "No clear catalyst found in major headlines." in message
    assert "Key headlines" not in message


def test_source_cleanup_and_deduplication() -> None:
    assert clean_source_name("www.reuters.com") == "Reuters"
    assert clean_source_name("The New York Times") == "NYT"
    assert clean_source_name("Wall Street Journal") == "WSJ"
    assert clean_source_name("Financial Times") == "FT"
    assert clean_source_name("Associated Press") == "AP"
    assert clean_source_name("Washington Post") == "WaPo"
    assert format_sources(("Reuters News", "www.reuters.com", "The New York Times"), max_sources=3) == "Reuters, NYT"


def test_telegram_can_include_links_but_sms_omits_them_by_default() -> None:
    linked = briefing("business_tech", item(urls=("https://example.com/story",)))

    telegram = format_category_message(
        linked,
        options=FormatOptions(mode="telegram", include_links_telegram=True),
    ).text
    sms = format_category_message(
        linked,
        options=FormatOptions(mode="sms", include_links_sms=False),
    ).text

    assert "Link: https://example.com/story" in telegram
    assert "https://example.com/story" not in sms


def test_sms_length_truncation_adds_omitted_line() -> None:
    long_items = tuple(
        item(
            headline=f"Long story {index} with enough words to take space",
            summary=" ".join(["summary"] * 40),
            why=" ".join(["impact"] * 25),
        )
        for index in range(8)
    )

    formatted = format_category_message(
        briefing("business_tech", *long_items),
        options=FormatOptions(mode="sms", max_chars_per_message_sms=500, max_stories_per_category_sms=8),
    )

    assert formatted.char_count <= 500
    assert "+ " in formatted.text
    assert "more stories omitted for length." in formatted.text


def test_brief_mode_uses_one_line_stories() -> None:
    message = format_category_message(
        briefing(),
        options=FormatOptions(mode="sms", brief=True, today=date(2026, 7, 10)),
    ).text

    assert "• OpenAI announces new enterprise tools — Enterprise adoption" in message
    assert "What happened:" not in message


def test_console_dry_run_preview_includes_counts() -> None:
    previews = format_briefing_previews(
        [briefing()],
        options=FormatOptions(mode="console", today=date(2026, 7, 10)),
    )

    preview = format_console_preview(previews)

    assert "==============================" in preview
    assert "TEXT 1/1: BUSINESS + TECH" in preview
    assert "Total messages: 1" in preview
    assert "approx" in preview
    assert "Omitted stories: 0" in preview
