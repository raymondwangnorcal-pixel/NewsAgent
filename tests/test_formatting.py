from __future__ import annotations

from datetime import date

from news_agent.formatting import (
    FormatOptions,
    format_briefing_previews,
    format_category_message,
    format_console_preview,
    format_sources,
    omitted_story_count,
)
from news_agent.models import BriefingParagraph, BriefingSection


def paragraph(
    story_id: str = "story-1",
    category: str = "business_tech",
    text: str = "OpenAI announced a new model with major performance gains, and Microsoft said "
    "it would integrate the model into its productivity suite within weeks.",
    sources: tuple[str, ...] = ("Reuters", "The Verge", "Bloomberg"),
    urls: tuple[str, ...] = ("https://example.com/story-1",),
) -> BriefingParagraph:
    return BriefingParagraph(story_id=story_id, category=category, paragraph=text, sources=sources, urls=urls)


def section(
    category: str = "business_tech",
    label: str = "Business + Tech",
    paragraphs: tuple[BriefingParagraph, ...] | None = None,
    lead_lines: tuple[str, ...] = (),
) -> BriefingSection:
    return BriefingSection(
        category=category,
        label=label,
        paragraphs=paragraphs if paragraphs is not None else (paragraph(),),
        lead_lines=lead_lines,
    )


# --- Header / category naming ---------------------------------------------------------


def test_category_header_formatting() -> None:
    options = FormatOptions(mode="telegram", today=date(2026, 7, 10))
    message = format_category_message(section(), options=options)
    assert message.text.startswith("🧠 BUSINESS + TECH · July 10")


def test_finance_header_uses_finance_emoji() -> None:
    options = FormatOptions(mode="telegram", today=date(2026, 7, 10))
    message = format_category_message(section(category="finance", label="Finance"), options=options)
    assert message.text.startswith("💸 FINANCE · July 10")


# --- Paragraph rendering (no bullets, no fragmented fields) ----------------------------


def test_story_renders_as_a_plain_paragraph_no_bullet_prefix() -> None:
    options = FormatOptions(mode="telegram")
    message = format_category_message(section(), options=options)
    assert "• " not in message.text
    assert "OpenAI announced a new model" in message.text


def test_story_output_has_no_labeled_sections() -> None:
    options = FormatOptions(mode="telegram")
    message = format_category_message(section(), options=options)
    assert "What happened:" not in message.text
    assert "Why it matters:" not in message.text
    assert "Sources:" not in message.text


def test_story_includes_source_attribution_line() -> None:
    options = FormatOptions(mode="telegram")
    message = format_category_message(section(), options=options)
    assert "(via Reuters, The Verge, Bloomberg)" in message.text


def test_multiple_paragraphs_render_in_order_separated_by_blank_line() -> None:
    paragraphs = (
        paragraph(story_id="a", text="First story paragraph text here."),
        paragraph(story_id="b", text="Second story paragraph text here."),
    )
    options = FormatOptions(mode="telegram")
    message = format_category_message(section(paragraphs=paragraphs), options=options)
    first_index = message.text.index("First story paragraph")
    second_index = message.text.index("Second story paragraph")
    assert first_index < second_index
    # blank line between stories
    between = message.text[first_index:second_index]
    assert "\n\n" in between


# --- Lead lines (finance market-check ticker, kept separate from prose) ----------------


def test_lead_lines_render_above_paragraphs() -> None:
    options = FormatOptions(mode="telegram")
    sec = section(
        category="finance",
        label="Finance",
        lead_lines=("AAPL 210.34 (+1.2%)", "NVDA 118.02 (-0.4%)"),
    )
    message = format_category_message(sec, options=options)
    lead_index = message.text.index("AAPL 210.34")
    paragraph_index = message.text.index("OpenAI announced")
    assert lead_index < paragraph_index


def test_no_lead_lines_when_section_has_none() -> None:
    options = FormatOptions(mode="telegram")
    message = format_category_message(section(), options=options)
    # no stray blank market-check block for non-finance categories
    assert message.text.count("\n\n") == 1  # header blank line + one paragraph block


# --- Source formatting ------------------------------------------------------------------


def test_source_cleanup_and_deduplication() -> None:
    result = format_sources(("Reuters", "reuters.com", "  AP News  "), max_sources=3)
    assert result == "Reuters, AP"


# --- Links per channel -------------------------------------------------------------------


def test_telegram_and_sms_omit_links_by_default() -> None:
    telegram_options = FormatOptions(mode="telegram")
    sms_options = FormatOptions(mode="sms")
    telegram_message = format_category_message(section(), options=telegram_options)
    sms_message = format_category_message(section(), options=sms_options)
    assert "https://example.com/story-1" not in telegram_message.text
    assert "https://example.com/story-1" not in sms_message.text


def test_telegram_can_include_links_when_explicitly_enabled() -> None:
    options = FormatOptions(mode="telegram", include_links_telegram=True)
    message = format_category_message(section(), options=options)
    assert "https://example.com/story-1" in message.text


# --- Char-limit fitting: drops whole stories, never truncates mid-sentence --------------


def test_sms_length_limit_drops_whole_stories_not_mid_sentence() -> None:
    long_paragraphs = tuple(
        paragraph(story_id=f"story-{i}", text=f"Story number {i} paragraph text that describes an event in full sentences.")
        for i in range(10)
    )
    options = FormatOptions(mode="sms", max_chars_per_message_sms=250)
    message = format_category_message(section(paragraphs=long_paragraphs), options=options)

    assert message.char_count <= 250
    assert message.omitted_count > 0
    assert "+ " in message.text and "more stories omitted for length." in message.text
    # every rendered paragraph ends on a real sentence boundary, not a mid-word cut
    for line in message.text.splitlines():
        if line.startswith("Story number"):
            assert line.rstrip().endswith(".")


def test_sms_story_limit_caps_visible_stories() -> None:
    paragraphs = tuple(paragraph(story_id=f"story-{i}", text=f"Story {i} text.") for i in range(8))
    options = FormatOptions(mode="sms", max_stories_per_category_sms=3, max_chars_per_message_sms=4000)
    message = format_category_message(section(paragraphs=paragraphs), options=options)
    assert message.omitted_count == 5


# --- Empty category ------------------------------------------------------------------------


def test_empty_category_renders_header_only() -> None:
    options = FormatOptions(mode="telegram")
    message = format_category_message(section(paragraphs=()), options=options)
    assert message.text.strip().startswith("🧠 BUSINESS + TECH")
    assert message.omitted_count == 0


# --- Multi-section preview + console preview -------------------------------------------


def test_format_briefing_previews_handles_multiple_sections() -> None:
    sections = (section(category="business_tech", label="Business + Tech"), section(category="finance", label="Finance"))
    options = FormatOptions(mode="telegram")
    messages = format_briefing_previews(sections, options=options)
    assert len(messages) == 2
    assert messages[0].title.startswith("🧠 BUSINESS + TECH")
    assert messages[1].title.startswith("💸 FINANCE")


def test_console_dry_run_preview_includes_counts() -> None:
    options = FormatOptions(mode="console")
    messages = format_briefing_previews((section(),), options=options)
    preview = format_console_preview(messages)
    assert "===== TEXT 1/1 =====" in preview
    assert "===== Summary =====" in preview
    assert "Total messages: 1" in preview


def test_omitted_story_count_parses_summed_omitted_lines() -> None:
    text = "Header\n\n+ 3 more stories omitted for length.\n\nOther\n\n+ 2 more stories omitted for length."
    assert omitted_story_count(text) == 5
