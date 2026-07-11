from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Literal

from news_agent.models import BriefingItem, BriefingText, FormattingConfig


FormatMode = Literal["sms", "telegram", "console"]

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
SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+")
MORE_OMITTED_LINE_RE = re.compile(r"^\+ \d+ more stories omitted for length\.$", re.MULTILINE)


@dataclass(frozen=True)
class FormatOptions:
    mode: FormatMode = "telegram"
    brief: bool = False
    max_chars_per_message_sms: int = 1400
    max_stories_per_category_sms: int = 5
    max_sources_per_story: int = 3
    include_links_sms: bool = False
    include_links_telegram: bool = True
    today: date | None = None

    @classmethod
    def from_config(
        cls,
        config: FormattingConfig,
        mode: FormatMode = "telegram",
        brief: bool = False,
    ) -> "FormatOptions":
        return cls(
            mode=mode,
            brief=brief,
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
        return True


@dataclass(frozen=True)
class FormattedMessage:
    title: str
    text: str
    omitted_count: int = 0

    @property
    def char_count(self) -> int:
        return len(self.text)

    @property
    def sms_segments(self) -> int:
        if not self.text:
            return 0
        return (len(self.text) + 159) // 160


def format_briefing_messages(
    briefing: BriefingText | list[BriefingText] | tuple[BriefingText, ...],
    options: FormatOptions | None = None,
) -> list[str]:
    return [message.text for message in format_briefing_previews(briefing, options=options)]


def format_briefing_previews(
    briefing: BriefingText | list[BriefingText] | tuple[BriefingText, ...],
    options: FormatOptions | None = None,
) -> list[FormattedMessage]:
    options = options or FormatOptions()
    briefings = (briefing,) if isinstance(briefing, BriefingText) else tuple(briefing)
    return [format_category_message(item, options=options) for item in briefings]


def format_category_message(
    briefing: BriefingText,
    emoji: str | None = None,
    stories: tuple[BriefingItem, ...] | None = None,
    options: FormatOptions | None = None,
) -> FormattedMessage:
    options = options or FormatOptions()
    category = briefing.category
    items = stories or briefing.items
    if category == "finance":
        return format_finance_message(briefing, options)
    return fit_category_message(category, items, options, numbered=False, emoji=emoji)


def fit_category_message(
    category: str,
    items: tuple[BriefingItem, ...],
    options: FormatOptions,
    numbered: bool = False,
    emoji: str | None = None,
) -> FormattedMessage:
    story_limit = options.story_limit or len(items)
    visible_count = min(len(items), story_limit)
    include_sources = True
    compact = options.mode == "sms"

    while visible_count >= 0:
        omitted_count = len(items) - visible_count
        text = build_category_text(
            category,
            items[:visible_count],
            options,
            omitted_count=omitted_count,
            numbered=numbered,
            include_sources=include_sources,
            compact=compact,
            emoji=emoji,
        )
        if fits_message(text, options):
            return FormattedMessage(title=header_title(category, options.today, emoji=emoji), text=text, omitted_count=omitted_count)
        if compact is False:
            compact = True
            continue
        if include_sources:
            include_sources = False
            continue
        visible_count -= 1

    text = truncate_message(build_header(category, options, emoji=emoji), options.max_chars or 1400)
    return FormattedMessage(title=header_title(category, options.today, emoji=emoji), text=text, omitted_count=len(items))


def build_category_text(
    category: str,
    items: tuple[BriefingItem, ...],
    options: FormatOptions,
    omitted_count: int = 0,
    numbered: bool = False,
    include_sources: bool = True,
    compact: bool = False,
    emoji: str | None = None,
) -> str:
    lines = [build_header(category, options, emoji=emoji)]
    for index, item in enumerate(items, start=1):
        lines.append("")
        lines.append(
            format_story_item(
                item,
                index=index if numbered else None,
                options=options,
                include_sources=include_sources,
                compact=compact,
            )
        )
    if omitted_count > 0:
        lines.append("")
        lines.append(f"+ {omitted_count} more stories omitted for length.")
    return "\n".join(lines).strip()


def format_story_item(
    story: BriefingItem,
    index: int | None = None,
    options: FormatOptions | None = None,
    include_sources: bool = True,
    compact: bool = False,
) -> str:
    options = options or FormatOptions()
    headline = truncate_headline(story.headline)
    why = compact_sentence(story.why_it_matters, max_chars=125 if compact else 170)
    summary = compact_sentence(story.summary, max_chars=130 if compact else 210)
    sources = format_sources(story.sources, max_sources=options.max_sources_per_story)
    prefix = f"{index}." if index is not None else "•"

    if options.brief:
        line = f"{prefix} {headline}"
        if why:
            line = f"{line} — {why}"
        if include_sources and sources and index is None:
            line = f"{line} Sources: {sources}"
        if options.include_links and story.urls and options.mode == "console":
            line = f"{line} Link: {story.urls[0]}"
        return line

    if index is not None:
        lines = [f"{index}. {headline}"]
        if why:
            lines.append(f"   Why it matters: {why}")
        if story.next_watch:
            lines.append(f"   Watch next: {compact_sentence(story.next_watch, max_chars=110)}")
        if options.include_links and story.urls and options.mode == "console":
            lines.append(f"   Link: {story.urls[0]}")
        return "\n".join(lines)

    lines = [f"• {headline}"]
    if summary:
        lines.append(f"  What happened: {summary}")
    if why:
        lines.append(f"  Why it matters: {why}")
    if include_sources and sources:
        lines.append(f"  Sources: {sources}")
    if options.include_links and story.urls:
        lines.append(f"  Link: {format_links(story.urls)}")
    if story.update_note and options.mode != "sms":
        lines.append(f"  Update: {compact_sentence(story.update_note, max_chars=110)}")
    if story.watchlist_matches and options.mode in {"telegram", "console"}:
        lines.append(f"  Watchlist: {', '.join(story.watchlist_matches[:5])}")
    return "\n".join(lines)


def format_finance_message(briefing: BriefingText, options: FormatOptions) -> FormattedMessage:
    story_limit = options.story_limit or len(briefing.items)
    include_sources = True
    compact = options.mode == "sms"
    visible_count = min(len(briefing.items), story_limit)

    while visible_count >= 0:
        visible_items = briefing.items[:visible_count]
        omitted_count = len(briefing.items) - visible_count
        text = build_finance_text(
            visible_items,
            options,
            omitted_count=omitted_count,
            include_sources=include_sources,
            compact=compact,
        )
        if fits_message(text, options):
            return FormattedMessage(title=header_title("finance", options.today), text=text, omitted_count=omitted_count)
        if compact is False:
            compact = True
            continue
        if include_sources:
            include_sources = False
            continue
        visible_count -= 1

    text = truncate_message(build_header("finance", options), options.max_chars or 1400)
    return FormattedMessage(title=header_title("finance", options.today), text=text, omitted_count=len(briefing.items))


def build_finance_text(
    items: tuple[BriefingItem, ...],
    options: FormatOptions,
    omitted_count: int = 0,
    include_sources: bool = True,
    compact: bool = False,
) -> str:
    mover_items = [item for item in items if "mover" in item.headline.lower()]
    snapshot_items = [item for item in items if item.headline.lower() == "mega-cap watchlist"]
    headline_items = [
        item
        for item in items
        if item not in mover_items and item not in snapshot_items
    ]
    lines = [build_header("finance", options)]

    snapshot_lines = format_market_snapshot(snapshot_items)
    if snapshot_lines:
        lines.extend(["", "Market snapshot", *snapshot_lines])

    mover_lines = format_big_movers(mover_items)
    if mover_lines:
        lines.extend(["", "Big movers", *mover_lines])

    if headline_items:
        lines.extend(["", "Key headlines"])
        for item in headline_items:
            lines.append(
                format_story_item(
                    item,
                    options=options,
                    include_sources=include_sources,
                    compact=compact,
                )
            )

    if omitted_count > 0:
        lines.extend(["", f"+ {omitted_count} more stories omitted for length."])
    return "\n".join(lines).strip()


def format_market_snapshot(items: list[BriefingItem]) -> list[str]:
    lines: list[str] = []
    for item in items:
        for raw_part in item.summary.split(";"):
            part = raw_part.strip()
            if not part or "unavailable" in part.lower():
                continue
            symbol, _, rest = part.partition(" ")
            display = f"{symbol.rstrip(':')}: {rest.strip()}" if rest else part
            lines.append(f"• {display}")
    return lines[:8]


def format_big_movers(items: list[BriefingItem]) -> list[str]:
    lines: list[str] = []
    for item in items:
        for raw_part in item.summary.split(";"):
            part = raw_part.strip()
            if not part:
                continue
            part = part.replace("catalyst unclear from major headlines", "No clear catalyst found in major headlines.")
            part = part.replace("(", "— ", 1).rstrip(")")
            lines.append(f"• {part}")
    return lines[:8]


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


def build_header(category: str, options: FormatOptions, emoji: str | None = None) -> str:
    return header_title(category, options.today, emoji=emoji)


def header_title(category: str, today: date | None = None, emoji: str | None = None) -> str:
    title = CATEGORY_HEADERS.get(category, category.upper())
    if emoji:
        title = f"{emoji} {title.split(' ', 1)[-1]}"
    selected_day = today or date.today()
    return f"{title} — {selected_day.strftime('%B')} {selected_day.day}"


def compact_sentence(value: str, max_chars: int = 180) -> str:
    text = normalize_text(value)
    if not text:
        return ""
    first_sentence = SENTENCE_END_RE.split(text, maxsplit=1)[0]
    if len(first_sentence) <= max_chars:
        return ensure_terminal_punctuation(first_sentence)
    return truncate_sentence(first_sentence, max_chars)


def truncate_headline(headline: str, max_chars: int = 90) -> str:
    text = normalize_text(headline)
    if len(text) <= max_chars:
        return text
    shortened = text[: max_chars - 1].rsplit(" ", 1)[0].rstrip(" ,;:")
    return f"{shortened}…"


def truncate_sentence(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return ensure_terminal_punctuation(text)
    shortened = text[: max_chars - 1].rsplit(" ", 1)[0].rstrip(" ,;:")
    return ensure_terminal_punctuation(f"{shortened}…")


def ensure_terminal_punctuation(text: str) -> str:
    text = text.strip()
    if not text or text.endswith((".", "!", "?", "…")):
        return text
    return f"{text}."


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
    lines: list[str] = []
    for index, message in enumerate(messages, start=1):
        lines.append("==============================")
        lines.append(f"TEXT {index}/{len(messages)}: {console_title(message.title)}")
        lines.append("==============================")
        lines.append(message.text)
        lines.append("")
    lines.append("Summary")
    lines.append(f"Total messages: {len(messages)}")
    for index, message in enumerate(messages, start=1):
        lines.append(
            f"Message {index}: {message.char_count} chars, approx {message.sms_segments} SMS segment"
            f"{'' if message.sms_segments == 1 else 's'}"
        )
    omitted = sum(message.omitted_count for message in messages)
    lines.append(f"Omitted stories: {omitted}")
    return "\n".join(lines).rstrip()


def console_title(title: str) -> str:
    heading = title.split("—", 1)[0].strip()
    return re.sub(r"^[^\w]+", "", heading).strip()


def omitted_story_count(text: str) -> int:
    total = 0
    for match in MORE_OMITTED_LINE_RE.finditer(text):
        number = re.search(r"\d+", match.group(0))
        if number:
            total += int(number.group(0))
    return total
