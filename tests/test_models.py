from __future__ import annotations

from news_agent.models import BriefingParagraph, BriefingSection, CategoryAssignment


def test_category_assignment_defaults() -> None:
    assignment = CategoryAssignment(category="finance", rationale="Market-moving story.")
    assert assignment.outlier_urls == ()


def test_category_assignment_can_carry_outlier_urls() -> None:
    assignment = CategoryAssignment(
        category="business_tech",
        rationale="Kept the AI story, dropped the unrelated lawsuit article.",
        outlier_urls=("https://example.com/unrelated",),
    )
    assert assignment.outlier_urls == ("https://example.com/unrelated",)


def test_briefing_paragraph_has_no_headline_or_why_it_matters_fields() -> None:
    paragraph = BriefingParagraph(
        story_id="story-1",
        category="finance",
        paragraph="Markets fell after new inflation data came in above expectations.",
        sources=("Reuters", "CNBC"),
        urls=("https://example.com/markets",),
    )
    assert not hasattr(paragraph, "headline")
    assert not hasattr(paragraph, "why_it_matters")
    assert not hasattr(paragraph, "next_watch")
    assert paragraph.sources == ("Reuters", "CNBC")


def test_briefing_section_groups_paragraphs_by_category() -> None:
    section = BriefingSection(
        category="finance",
        label="Finance",
        paragraphs=(
            BriefingParagraph(
                story_id="story-1",
                category="finance",
                paragraph="Markets moved lower.",
                sources=("Reuters",),
            ),
        ),
    )
    assert section.category == "finance"
    assert len(section.paragraphs) == 1
    assert section.lead_lines == ()


def test_briefing_section_lead_lines_default_empty_but_settable() -> None:
    section = BriefingSection(
        category="finance",
        label="Finance",
        paragraphs=(),
        lead_lines=("AAPL 210.34 (+1.2%)",),
    )
    assert section.lead_lines == ("AAPL 210.34 (+1.2%)",)
