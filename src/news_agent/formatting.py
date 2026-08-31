from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Literal

from news_agent.time import briefing_today

from news_agent.models import BriefingParagraph, BriefingSection, FormattingConfig


FormatMode = Literal["sms", "telegram", "console", "email"]

CATEGORY_HEADERS = {
    "business_tech": "🧠 BUSINESS + TECH",
    "domestic": "🇺🇸 U.S. NEWS",
    "global": "🌍 GLOBAL NEWS",
    "culture": "🎭 CULTURE + MEDIA",
    "finance": "💸 FINANCE",
}
SOURCE_ALIASES = {
    "ap": "AP",
    "associated press": "AP",
    "ap news": "AP",
    "bloomberg": "Bloomberg",
    "cnbc": "CNBC",
    "financial times": "FT",
    "ft": "FT",
    "marketwatch": "MarketWatch",
    "new york times": "NYT",
    "nytimes": "NYT",
    "npr": "NPR",
    "reuters": "Reuters",
    "reuters news": "Reuters",
    "the new york times": "NYT",
    "the wall street journal": "WSJ",
    "wall street journal": "WSJ",
    "washington post": "WaPo",
    "the washington post": "WaPo",
    "wsj": "WSJ",
    "yahoo finance": "Yahoo Finance",
}
HOST_PREFIX_RE = re.compile(r"^(?:https?://)?(?:www\.)?", re.IGNORECASE)
DOMAIN_SUFFIX_RE = re.compile(r"\.(?:com|org|net|co|io|gov|edu)(?:/.*)?$", re.IGNORECASE)
MORE_OMITTED_LINE_RE = re.compile(r"^\+ \d+ more stories omitted for length\.$", re.MULTILINE)


@dataclass(frozen=True)
class FormatOptions:
    mode: FormatMode = "telegram"
    max_chars_per_message_sms: int = 1400
    max_stories_per_category_sms: int = 5
    max_sources_per_story: int = 3
    include_links_sms: bool = False
    include_links_telegram: bool = False
    today: date | None = None

    @classmethod
    def from_config(
        cls,
        config: FormattingConfig,
        mode: FormatMode = "telegram",
    ) -> "FormatOptions":
        return cls(
            mode=mode,
            max_chars_per_message_sms=config.max_chars_per_message_sms,
            max_stories_per_category_sms=config.max_stories_per_category_sms,
            max_sources_per_story=config.max_sources_per_story,
            include_links_sms=config.include_links_sms,
            include_links_telegram=config.include_links_telegram,
        )

    @property
    def max_chars(self) -> int | None:
        if self.mode == "sms":
            return self.max_chars_per_message_sms
        if self.mode == "telegram":
            return 3600
        return None

    @property
    def story_limit(self) -> int | None:
        if self.mode == "sms":
            return self.max_stories_per_category_sms
        return None

    @property
    def include_links(self) -> bool:
        if self.mode == "sms":
            return self.include_links_sms
        if self.mode == "telegram":
            return self.include_links_telegram
        if self.mode == "email":
            return True
        return True


@dataclass(frozen=True)
class FormattedMessage:
    title: str
    text: str
    omitted_count: int = 0
    category: str = ""

    @property
    def char_count(self) -> int:
        return len(self.text)

    @property
    def sms_segments(self) -> int:
        if not self.text:
            return 0
        return (len(self.text) + 159) // 160


def format_briefing_messages(
    briefing: BriefingSection | list[BriefingSection] | tuple[BriefingSection, ...],
    options: FormatOptions | None = None,
) -> list[str]:
    return [message.text for message in format_briefing_previews(briefing, options=options)]


def format_briefing_previews(
    briefing: BriefingSection | list[BriefingSection] | tuple[BriefingSection, ...],
    options: FormatOptions | None = None,
) -> list[FormattedMessage]:
    options = options or FormatOptions()
    sections = (briefing,) if isinstance(briefing, BriefingSection) else tuple(briefing)
    return [format_category_message(item, options=options) for item in sections]


def format_category_message(section: BriefingSection, options: FormatOptions | None = None) -> FormattedMessage:
    """Render one category section as compact paragraphs. Char-limit fitting
    drops whole stories from the end -- it never truncates a paragraph
    mid-sentence, since a chopped paragraph is worse than one fewer story."""
    options = options or FormatOptions()
    story_limit = options.story_limit or len(section.paragraphs)
    include_sources = True
    visible_count = min(len(section.paragraphs), story_limit)

    while visible_count >= 0:
        omitted_count = len(section.paragraphs) - visible_count
        text = build_section_text(
            section,
            section.paragraphs[:visible_count],
            options,
            omitted_count=omitted_count,
            include_sources=include_sources,
        )
        if fits_message(text, options):
            return FormattedMessage(
                title=header_title(section.category, options.today),
                text=text,
                omitted_count=omitted_count,
                category=section.category,
            )
        if include_sources:
            include_sources = False
            continue
        visible_count -= 1

    text = truncate_message(build_header(section.category, options), options.max_chars or 1400)
    return FormattedMessage(
        title=header_title(section.category, options.today),
        text=text,
        omitted_count=len(section.paragraphs),
    )


def build_section_text(
    section: BriefingSection,
    paragraphs: tuple[BriefingParagraph, ...],
    options: FormatOptions,
    omitted_count: int = 0,
    include_sources: bool = True,
) -> str:
    lines = [build_header(section.category, options)]
    if section.lead_lines:
        lines.append("")
        lines.extend(section.lead_lines)
    for paragraph in paragraphs:
        lines.append("")
        lines.append(format_paragraph_item(paragraph, options=options, include_sources=include_sources))
    if omitted_count > 0:
        lines.append("")
        lines.append(f"+ {omitted_count} more stories omitted for length.")
    return "\n".join(lines).strip()


def format_paragraph_item(
    story: BriefingParagraph,
    options: FormatOptions | None = None,
    include_sources: bool = True,
) -> str:
    options = options or FormatOptions()
    text = normalize_text(story.paragraph)
    source_limit = (
        max(options.max_sources_per_story, 5)
        if story.is_merged
        else options.max_sources_per_story
    )
    sources = format_sources(story.sources, max_sources=source_limit)

    lines = [text]
    if (include_sources or story.is_merged) and sources:
        lines.append(f"(via {sources})")
    if options.include_links and story.urls:
        link_limit = source_limit if story.is_merged and options.mode == "email" else 1
        lines.append(format_links(story.urls, max_links=link_limit))
    return "\n".join(lines)


def format_sources(sources: tuple[str, ...] | list[str], max_sources: int = 3) -> str:
    cleaned: list[str] = []
    seen: set[str] = set()
    for source in sources:
        display = clean_source_name(source)
        if display and display not in seen:
            cleaned.append(display)
            seen.add(display)
        if len(cleaned) == max_sources:
            break
    return ", ".join(cleaned)


def format_links(urls: tuple[str, ...] | list[str], max_links: int = 1) -> str:
    links: list[str] = []
    seen: set[str] = set()
    for url in urls:
        cleaned = url.strip()
        if cleaned and cleaned not in seen:
            links.append(cleaned)
            seen.add(cleaned)
        if len(links) == max_links:
            break
    return ", ".join(links)


def clean_source_name(source: str) -> str:
    value = " ".join(source.strip().split())
    value = HOST_PREFIX_RE.sub("", value)
    value = DOMAIN_SUFFIX_RE.sub("", value)
    value = value.replace("-", " ").replace("_", " ").strip()
    lowered = value.lower()
    if lowered in SOURCE_ALIASES:
        return SOURCE_ALIASES[lowered]
    for key, display in SOURCE_ALIASES.items():
        if key in lowered:
            return display
    if lowered.endswith(" news"):
        value = value[:-5]
    return value.title() if value.islower() else value


def build_header(category: str, options: FormatOptions) -> str:
    return header_title(category, options.today)


def header_title(category: str, today: date | None = None) -> str:
    title = CATEGORY_HEADERS.get(category, category.upper())
    selected_day = today or briefing_today()
    return f"{title} · {selected_day.strftime('%B')} {selected_day.day}"


def normalize_text(value: str) -> str:
    return " ".join(value.split()).strip()


def fits_message(text: str, options: FormatOptions) -> bool:
    return options.max_chars is None or len(text) <= options.max_chars


def truncate_message(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    suffix = "\n+ More omitted for length."
    limit = max(0, max_chars - len(suffix))
    return text[:limit].rstrip() + suffix


def format_console_preview(messages: list[FormattedMessage]) -> str:
    total = len(messages)
    lines: list[str] = []
    for index, message in enumerate(messages, start=1):
        lines.append(f"===== TEXT {index}/{total} =====")
        lines.append(message.text)
        lines.append("")

    lines.append("===== Summary =====")
    lines.append(f"Total messages: {total}")
    for index, message in enumerate(messages, start=1):
        segment_label = "segment" if message.sms_segments == 1 else "segments"
        lines.append(f"Message {index}: {message.char_count} chars, approx {message.sms_segments} SMS {segment_label}")
    omitted = sum(message.omitted_count for message in messages)
    lines.append(f"Omitted stories: {omitted}")
    return "\n".join(lines).rstrip()


def omitted_story_count(text: str) -> int:
    total = 0
    for match in MORE_OMITTED_LINE_RE.finditer(text):
        number = re.search(r"\d+", match.group(0))
        if number:
            total += int(number.group(0))
    return total
