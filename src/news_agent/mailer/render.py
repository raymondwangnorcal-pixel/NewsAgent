from __future__ import annotations

import html
import re
from urllib.parse import urlparse
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from news_agent.formatting import FormattedMessage
from news_agent.time import briefing_today
from news_agent.mailer.quotes import EndOfDayQuote
from news_agent.mailer.watchlist_news import WatchlistStory

SYSTEM_FONT_STACK = (
    "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto,"
    " Helvetica, Arial, sans-serif"
)

# ---- Inline colour tokens (light theme, email-safe) ----
_INK = "#0F1419"
_SECONDARY = "#536471"
_DIVIDER = "#E1E5E8"
_SURFACE = "#FFFFFF"
_PAGE_BG = "#F5F6F8"
_LINK = "#1966D2"
_SOURCE_CLR = "#7A8793"
_GREEN = "#188038"
_RED = "#C5221F"
_QUIET = "#9AA0A6"

_RESPONSIVE_CSS = (
    "<style>"
    ".briefing-mobile-index{display:none;}"
    "@media screen and (max-width:600px){"
    ".briefing-desktop-only{display:none!important;}"
    ".briefing-desktop-index{display:none!important;}"
    ".briefing-index{padding-top:0!important;padding-left:0!important;"
    "padding-right:12px!important;padding-bottom:0!important;}"
    f".briefing-index-heading{{margin:0!important;padding:10px 0 10px 8px!important;"
    f"font-size:12.5px!important;font-weight:700!important;color:{_INK}!important;"
    f"border-left:3px solid {_INK}!important;}}"
    ".briefing-mobile-index{display:table!important;width:100%!important;}"
    ".story-headline{font-size:14px!important;}"
    "}"
    "</style>"
)

CATEGORY_ACCENT_COLORS: dict[str, str] = {
    "business_tech": "#1565C0",
    "domestic": "#C62828",
    "global": "#2E7D32",
    "culture": "#EF6C00",
    "finance": "#7B1FA2",
}

CATEGORY_LABELS: dict[str, str] = {
    "business_tech": "Business + Tech",
    "domestic": "U.S. News",
    "global": "Global News",
    "culture": "Culture + Media",
    "finance": "Finance",
}

# Sentence-end heuristic: period after a letter/digit/closing-punct,
# followed by whitespace then an uppercase letter or opening quote.
_SENTENCE_END_RE = re.compile(
    r"(?<=[a-zA-Z0-9,;\"\'\)\]’”%])\.\s+(?=[A-Z\"\'“‘(])"
)


@dataclass(frozen=True)
class RenderedEmail:
    subject: str
    plain_text: str
    html: str


# ------------------------------------------------------------------
# Parity renderer (plain-text wrap) — unchanged
# ------------------------------------------------------------------


def render_parity_email(messages: list[FormattedMessage], header: str) -> RenderedEmail:
    plain_text = "\n\n".join((header, *(message.text for message in messages))).strip() + "\n"
    subject = f"Morning Briefing — {briefing_today().isoformat()}"
    rendered_html = (
        f'<html><body style="font-family: {SYSTEM_FONT_STACK};">'
        f'<pre style="font-family: {SYSTEM_FONT_STACK}; white-space: pre-wrap;">'
        + html.escape(plain_text)
        + "</pre></body></html>"
    )
    return RenderedEmail(subject=subject, plain_text=plain_text, html=rendered_html)


# ------------------------------------------------------------------
# Redesigned newsletter renderer
# ------------------------------------------------------------------


def render_minimal_newsletter(
    messages: list[FormattedMessage],
    header: str,
    watchlist_html: str = "",
    watchlist_text: str = "",
) -> RenderedEmail:
    """Build the email newsletter with card layout and typographic hierarchy."""

    # ---- Plain text (unchanged) ----
    plain_parts = [header, *(message.text for message in messages)]
    if watchlist_text:
        plain_parts.append(watchlist_text)
    plain_parts.append("For informational purposes only; not investment advice.")
    plain_text = "\n\n".join(plain_parts).strip() + "\n"

    # ---- HTML ----
    today = briefing_today()
    date_line = (
        f"{today.strftime('%A')}, {today.strftime('%B')} {today.day}, {today.year}"
    )
    sections_html = "".join(_render_section(m) for m in messages)
    index_html = _build_headline_index(messages)
    wl_block = watchlist_html if watchlist_html else ""

    rendered_html = (
        f'<html><head><meta charset="utf-8">{_RESPONSIVE_CSS}</head>'
        f'<body style="margin:0;'
        f' padding:24px 16px; background:{_PAGE_BG};'
        f" font-family:{SYSTEM_FONT_STACK}; -webkit-font-smoothing:antialiased;\">"
        f'<div style="max-width:600px; margin:0 auto; background:{_SURFACE};'
        f' border-radius:6px; overflow:hidden;">'
        # Header
        f'<div style="padding:28px 28px 20px; border-bottom:1px solid {_DIVIDER};">'
        f'<h1 style="margin:0; font-size:22px; font-weight:700; letter-spacing:-0.3px;'
        f" color:{_INK}; font-family:{SYSTEM_FONT_STACK};\">"
        f"Morning Briefing</h1>"
        f'<p style="margin:4px 0 0; font-size:13px; color:{_SECONDARY};'
        f' font-weight:500;">{html.escape(date_line)}</p></div>'
        # Headline index (quick-scan summary)
        f"{index_html}"
        # Watchlist
        f"{wl_block}"
        # Sections
        f"{sections_html}"
        # Footer
        f'<div style="padding:16px 28px 20px; border-top:1px solid {_DIVIDER};">'
        f'<p style="margin:0; font-size:11.5px; color:{_QUIET};">'
        f"For informational purposes only; not investment advice.</p></div>"
        f"</div></body></html>"
    )
    subject = f"Morning Briefing — {briefing_today().isoformat()}"
    return RenderedEmail(subject=subject, plain_text=plain_text, html=rendered_html)


# ------------------------------------------------------------------
# Headline index (quick-scan summary)
# ------------------------------------------------------------------


def _build_headline_index(messages: list[FormattedMessage]) -> str:
    """Compact table-of-contents showing one lead headline per section."""
    items: list[tuple[str, str, str, str]] = []  # (label, accent, headline, anchor)
    for m in messages:
        category = getattr(m, "category", "") or _guess_category(m.title)
        label = CATEGORY_LABELS.get(category, _label_from_title(m.title))
        accent = CATEGORY_ACCENT_COLORS.get(category, _SECONDARY)
        blocks = [b for b in m.text.split("\n\n") if b.strip()]
        if len(blocks) < 2:
            continue
        # Extract first headline from first story block
        first_block = blocks[1]
        text_lines: list[str] = []
        for line in first_block.splitlines():
            if line.startswith("(via ") or _is_http_url(line.strip()):
                continue
            if line.startswith("+ ") and "omitted" in line:
                continue
            text_lines.append(line)
        full = " ".join(text_lines).strip()
        if not full:
            continue
        hl, _ = _extract_headline(full)
        items.append((label, accent, hl, _section_anchor(category, label)))

    if not items:
        return ""

    desktop_rows: list[str] = []
    mobile_rows: list[str] = []
    for item_index, (label, accent, hl, anchor) in enumerate(items):
        desktop_rows.append(
            f'<tr>'
            f'<td width="140" style="width:140px; padding:6px 12px 6px 8px;'
            f' border-left:3px solid {accent}; vertical-align:top;">'
            f'<span style="font-size:10.5px; font-weight:700; line-height:1.35;'
            f" text-transform:uppercase; letter-spacing:0.8px; color:{_SECONDARY};"
            f' font-family:{SYSTEM_FONT_STACK}; white-space:nowrap;">'
            f'<a href="#{anchor}" class="briefing-desktop-only"'
            f' style="color:{_SECONDARY}; text-decoration:underline;">'
            f"<b>{html.escape(label)}</b></a></span></td>"
            f'<td style="padding:6px 0; vertical-align:top;">'
            f'<span style="font-size:13.5px; font-weight:600; line-height:1.35; color:{_INK};'
            f' font-family:{SYSTEM_FONT_STACK};">'
            f'<a href="#{anchor}" class="briefing-desktop-only"'
            f' style="color:{_INK}; text-decoration:underline;">'
            f"<b>{html.escape(hl)}</b></a></span></td>"
            f"</tr>"
        )
        mobile_bottom_padding = "13px" if item_index == len(items) - 1 else "10px"
        mobile_rows.append(
            f'<tr><td style="padding:8px 0 {mobile_bottom_padding} 8px;'
            f' border-left:3px solid {accent};'
            f' vertical-align:top; font-family:{SYSTEM_FONT_STACK};">'
            f'<p style="margin:0; font-size:10.5px; font-weight:700; line-height:1.35;'
            f' text-transform:uppercase; letter-spacing:0.8px; color:{_SECONDARY};">'
            f"<b>{html.escape(label)}</b></p>"
            f'<p style="margin:4px 0 0; font-size:13.5px; font-weight:600;'
            f' line-height:1.4; color:{_INK};">'
            f"<b>{html.escape(hl)}</b></p></td></tr>"
        )

    return (
        f'<div class="briefing-index" style="padding:20px 28px; border-bottom:1px solid {_DIVIDER};'
        f' background:{_PAGE_BG};">'
        f'<p class="briefing-index-heading" style="margin:0 0 12px; font-size:10px; font-weight:700;'
        f" text-transform:uppercase; letter-spacing:1.5px; color:{_QUIET};"
        f' font-family:{SYSTEM_FONT_STACK};"><b>In This Briefing</b> '
        f'<span style="text-transform:none; text-decoration:underline;"'
        f' class="briefing-desktop-only">'
        f"(click to jump to category)</span></p>"
        f'<table class="briefing-desktop-index" cellpadding="0" cellspacing="0" border="0"'
        f' style="border-collapse:separate; border-spacing:0; width:100%;'
        f' table-layout:fixed;">'
        + "".join(desktop_rows)
        + '</table><table class="briefing-mobile-index" width="100%" cellpadding="0" cellspacing="0"'
        + ' border="0" style="display:none; border-collapse:separate; border-spacing:0; width:100%;">'
        + "".join(mobile_rows)
        + "</table></div>"
    )


# ------------------------------------------------------------------
# Section + story rendering
# ------------------------------------------------------------------


def _render_section(message: FormattedMessage) -> str:
    """Render one category section with accent bar and story cards."""
    category = getattr(message, "category", "") or _guess_category(message.title)
    accent = CATEGORY_ACCENT_COLORS.get(category, _SECONDARY)
    label = CATEGORY_LABELS.get(category, _label_from_title(message.title))

    blocks = [b for b in message.text.split("\n\n") if b]
    if len(blocks) < 2:
        return ""

    stories = "".join(_render_story_card(b) for b in blocks[1:])
    anchor = _section_anchor(category, label)
    return (
        f'<a id="{anchor}" name="{anchor}"></a>'
        f'<div style="padding:0 28px;">'
        # Section heading with accent bar
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0"'
        f' style="border-collapse:collapse;"><tr>'
        f'<td style="padding:28px 0 14px; border-bottom:1px solid {accent};">'
        f'<table cellpadding="0" cellspacing="0" border="0"'
        f' style="border-collapse:collapse;"><tr>'
        f'<td style="width:4px; height:20px; background:{accent};'
        f' border-radius:2px; font-size:0; line-height:0;">&nbsp;</td>'
        f'<td style="padding-left:10px; font-size:12.5px; font-weight:700;'
        f" text-transform:uppercase; letter-spacing:1.2px; color:{_INK};"
        f' font-family:{SYSTEM_FONT_STACK};">'
        f"<b>{html.escape(label)}</b></td>"
        f"</tr></table></td></tr></table>"
        # Stories
        f"{stories}</div>"
    )


def _section_anchor(category: str, label: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (category or label).casefold()).strip("-")
    return f"section-{slug or 'news'}"


def _render_story_card(block: str) -> str:
    """Render one story with headline / body / source attribution."""
    lines = block.splitlines()
    text_lines: list[str] = []
    source_html = ""
    index = 0

    while index < len(lines):
        line = lines[index]

        # ---- Multi-source with paired URLs ----
        if (
            line.startswith("(via ")
            and line.endswith(")")
            and index + 1 < len(lines)
        ):
            labels = [v.strip() for v in line[5:-1].split(",")]
            urls = [v.strip() for v in lines[index + 1].split(",")]
            if (
                len(urls) > 1
                and len(urls) == len(labels)
                and all(_is_http_url(u) for u in urls)
            ):
                linked = ", ".join(
                    f'<a href="{html.escape(u, quote=True)}"'
                    f' style="color:{_LINK}; text-decoration:none;">'
                    f"{html.escape(la)}</a>"
                    for la, u in zip(labels, urls, strict=True)
                )
                source_html = (
                    f'<p style="margin:8px 0 0; font-size:12.5px;'
                    f" color:{_SOURCE_CLR}; font-weight:500;"
                    f' font-family:{SYSTEM_FONT_STACK};">via {linked}</p>'
                )
                index += 2
                continue

        # ---- Single source with URL ----
        if (
            line.startswith("(via ")
            and line.endswith(")")
            and index + 1 < len(lines)
            and _is_http_url(lines[index + 1])
        ):
            name = line[5:-1]
            url = html.escape(lines[index + 1].strip(), quote=True)
            source_html = (
                f'<p style="margin:8px 0 0; font-size:12.5px;'
                f" color:{_SOURCE_CLR}; font-weight:500;"
                f' font-family:{SYSTEM_FONT_STACK};">via'
                f' <a href="{url}" style="color:{_LINK};'
                f' text-decoration:none;">{html.escape(name)}</a></p>'
            )
            index += 2
            continue

        # ---- Source without URL ----
        if line.startswith("(via ") and line.endswith(")"):
            name = line[5:-1]
            source_html = (
                f'<p style="margin:8px 0 0; font-size:12.5px;'
                f" color:{_SOURCE_CLR}; font-weight:500;"
                f' font-family:{SYSTEM_FONT_STACK};">via'
                f" {html.escape(name)}</p>"
            )
            index += 1
            continue

        # ---- Bare URL — skip ----
        if _is_http_url(line.strip()):
            index += 1
            continue

        # ---- Omitted notice — skip ----
        if line.startswith("+ ") and "omitted for length" in line:
            index += 1
            continue

        text_lines.append(line)
        index += 1

    full_text = " ".join(text_lines).strip()
    if not full_text:
        return ""

    headline, body = _extract_headline(full_text)
    margin_bottom = " 0 14px" if body else ""
    parts = [
        f'<p class="story-headline" style="margin:0{margin_bottom}; font-size:19px;'
        f" font-weight:700; line-height:1.3; color:{_INK};"
        f' font-family:{SYSTEM_FONT_STACK};">'
        f"<b>{html.escape(headline)}</b></p>"
    ]
    if body:
        parts.append(
            f'<p style="margin:0; font-size:14px; line-height:1.6;'
            f" color:{_SECONDARY}; font-family:{SYSTEM_FONT_STACK};\">"
            f"{html.escape(body)}</p>"
        )
    if source_html:
        parts.append(source_html)

    return (
        f'<div style="padding:22px 0; border-bottom:1px solid {_DIVIDER};">'
        + "".join(parts)
        + "</div>"
    )


# ------------------------------------------------------------------
# Headline extraction
# ------------------------------------------------------------------


def _extract_headline(text: str) -> tuple[str, str]:
    """Split *text* at the first robust sentence boundary into (headline, body).

    Returns the full text as headline with empty body when no clean split
    is found or the first sentence is unreasonably short (<20 chars).
    """
    for match in _SENTENCE_END_RE.finditer(text):
        dot_end = match.start() + 1  # include the period
        headline = text[:dot_end].strip()
        body = text[dot_end:].strip()
        if len(headline) >= 20:
            return headline, body
    return text, ""


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _guess_category(title: str) -> str:
    """Best-effort category key from a formatted title string."""
    upper = title.upper()
    if "BUSINESS" in upper or "TECH" in upper:
        return "business_tech"
    if "U.S." in upper or "DOMESTIC" in upper:
        return "domestic"
    if "GLOBAL" in upper:
        return "global"
    if "CULTURE" in upper or "MEDIA" in upper:
        return "culture"
    if "FINANCE" in upper:
        return "finance"
    return ""


def _label_from_title(title: str) -> str:
    """Extract a clean section label from a formatted title like
    ``'\\U0001f9e0 BUSINESS + TECH \\u00b7 Aug 30'``.
    """
    # Strip leading emoji / non-ASCII
    stripped = re.sub(r"^[^\w]+", "", title, flags=re.UNICODE).strip()
    # Strip date suffix after ·
    if "·" in stripped:
        stripped = stripped.split("·")[0].strip()
    if stripped == stripped.upper() and len(stripped) > 3:
        return stripped.title().replace("U.s.", "U.S.")
    return stripped


# ------------------------------------------------------------------
# Watchlist section
# ------------------------------------------------------------------


def render_watchlist_section(
    quotes: dict[str, EndOfDayQuote | None],
    stories: list[WatchlistStory],
    *,
    gate_state: str = "DISABLED",
    pending_relationships: int = 0,
    gate_progress_notice: str = "",
) -> tuple[str, str]:
    """Return ``(plain_text, html)`` for the watchlist section."""

    by_ticker = {story.ticker: story for story in stories}

    # ========== Plain text (unchanged logic) ==========
    lines: list[str] = ["WATCHLIST"]
    for ticker, quote in quotes.items():
        if quote is None:
            lines.append(f"{ticker}: quote unavailable")
        else:
            timing = "live" if quote.quote_kind == "live" else f"close {quote.close_date}"
            lines.append(
                f"{ticker}: {quote.close_price:.2f} ({quote.percent_change:+.2f}%) · {timing}"
            )

        story = by_ticker.get(ticker)
        has_content = False

        if story is not None and story.disclosures:
            has_content = True
            lines.append("  Disclosed")
            for filing in story.disclosures[:4]:
                headline, metadata = _filing_display_text(filing)
                url = str(getattr(filing, "url", ""))
                lines.extend((f"    {headline} ({metadata})", f"    {url}"))

        if story is not None and (story.summary or (story.summary_unavailable and story.articles)):
            has_content = True
            lines.append("  Reported")
            if story.summary:
                lines.append(f"    {story.summary}")
                if story.why_it_matters:
                    lines.append(f"    Why it matters: {story.why_it_matters}")
            else:
                lines.append(f"    Summary unavailable: {story.articles[0].title}")
            source_articles = story.articles[:2]
            source_names = ", ".join(dict.fromkeys(article.source for article in source_articles))
            if source_names:
                lines.append(f"    (via {source_names})")
            if story.relationship_label:
                lines.append(f"    {_relationship_wording(ticker, str(story.relationship_label))}")

        if story is not None and story.official_retrieval_failed and has_content:
            lines.append("Official filing retrieval failed.")
        if story is not None and story.official_retrieval_failed and not has_content:
            lines.append(
                "Official filing retrieval failed; no complete news determination was possible."
            )
        elif story is not None and story.classification_incomplete:
            lines.append("Watchlist classification incomplete; some candidates were not evaluated.")
        elif story is not None and story.search_error:
            warning = (
                "Some optional news sources failed."
                if has_content
                else "No verified news today (partial sources)."
            )
            lines.append(warning)
        elif not has_content and not (story is not None and story.official_retrieval_failed):
            lines.append("No verified news today.")

    if gate_state == "DISABLED":
        lines.append("Watchlist evaluation disabled.")
    if pending_relationships:
        lines.append(f"Watchlist review needed: {pending_relationships} relationship(s).")
    if gate_progress_notice:
        lines.append(gate_progress_notice)

    # ========== HTML (redesigned compact layout) ==========
    html_output = _build_watchlist_html(
        quotes, by_ticker, gate_state, pending_relationships, gate_progress_notice,
    )

    return "\n".join(lines), html_output


# ------------------------------------------------------------------
# Watchlist HTML builder
# ------------------------------------------------------------------


def _build_watchlist_html(
    quotes: dict[str, EndOfDayQuote | None],
    by_ticker: dict[str, WatchlistStory],
    gate_state: str,
    pending_relationships: int,
    gate_progress_notice: str,
) -> str:
    accent = CATEGORY_ACCENT_COLORS.get("finance", "#7C3AED")

    quiet: list[tuple[str, EndOfDayQuote | None, WatchlistStory | None]] = []
    news: list[tuple[str, EndOfDayQuote | None, WatchlistStory | None]] = []

    for ticker, quote in quotes.items():
        story = by_ticker.get(ticker)
        has_content = bool(
            story is not None
            and (
                story.disclosures
                or story.summary
                or (story.summary_unavailable and story.articles)
            )
        )
        if has_content:
            news.append((ticker, quote, story))
        else:
            quiet.append((ticker, quote, story))

    parts: list[str] = []

    # ---- Section header ----
    parts.append(
        f'<div style="padding:0 28px;">'
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0"'
        f' style="border-collapse:collapse;"><tr>'
        f'<td style="padding:28px 0 14px; border-bottom:1px solid {accent};">'
        f'<table cellpadding="0" cellspacing="0" border="0"'
        f' style="border-collapse:collapse;"><tr>'
        f'<td style="width:4px; height:20px; background:{accent};'
        f' border-radius:2px; font-size:0; line-height:0;">&nbsp;</td>'
        f'<td style="padding-left:10px; font-size:12.5px; font-weight:700;'
        f" text-transform:uppercase; letter-spacing:1.2px; color:{_INK};"
        f' font-family:{SYSTEM_FONT_STACK};"><b>Watchlist</b></td>'
        f"</tr></table></td></tr></table>"
    )

    # ---- 2-column grid for quiet tickers ----
    if quiet:
        parts.append(
            f'<table width="100%" cellpadding="0" cellspacing="0" border="0"'
            f' style="border-collapse:collapse; font-variant-numeric:tabular-nums;">'
        )
        for i in range(0, len(quiet), 2):
            parts.append("<tr>")
            for j in range(2):
                idx = i + j
                if idx < len(quiet):
                    t, q, _s = quiet[idx]
                    cell = _render_quote_cell(t, q)
                    br = f" border-right:1px solid {_DIVIDER};" if j == 0 else ""
                    pad = "padding:12px 14px 12px 0;" if j == 0 else "padding:12px 0 12px 14px;"
                    last_row = (i + 2) >= len(quiet)
                    bb = "" if last_row else f" border-bottom:1px solid {_DIVIDER};"
                    parts.append(
                        f'<td width="50%" style="{pad}{br}{bb}'
                        f' vertical-align:top;">{cell}</td>'
                    )
                else:
                    parts.append('<td width="50%"></td>')
            parts.append("</tr>")
        parts.append("</table>")

    # ---- Full-width rows for tickers with news ----
    for ticker, quote, story in news:
        parts.append(_render_watchlist_news_row(ticker, quote, story))

    # ---- Status notices ----
    notices: list[str] = []
    if gate_state == "DISABLED":
        notices.append("Watchlist evaluation disabled.")
    if pending_relationships:
        notices.append(f"Watchlist review needed: {pending_relationships} relationship(s).")
    if gate_progress_notice:
        notices.append(gate_progress_notice)
    for notice in notices:
        parts.append(
            f'<p style="margin:12px 0 0; font-size:12px; color:{_QUIET};'
            f' font-style:italic;">{html.escape(notice)}</p>'
        )

    parts.append("</div>")
    return "".join(parts)


def _render_quote_cell(ticker: str, quote: EndOfDayQuote | None) -> str:
    """Compact ticker + price + change for the watchlist grid."""
    if quote is None:
        return (
            f'<span style="font-size:13px; font-weight:700; color:{_INK};'
            f' font-family:{SYSTEM_FONT_STACK};">{html.escape(ticker)}</span> '
            f'<span style="font-size:12px; color:{_QUIET};">unavailable</span>'
        )
    color = _GREEN if quote.percent_change > 0 else _RED if quote.percent_change < 0 else _SECONDARY
    sign = "+" if quote.percent_change > 0 else ""
    return (
        f'<span style="font-size:13px; font-weight:700; color:{_INK};'
        f' font-family:{SYSTEM_FONT_STACK};">{html.escape(ticker)}</span> '
        f'<span style="font-size:13px; font-weight:500; color:{_INK};">'
        f"{quote.close_price:.2f}</span> "
        f'<span style="font-size:12px; font-weight:600; color:{color};">'
        f"{sign}{quote.percent_change:.2f}%</span>"
    )


def _render_watchlist_news_row(
    ticker: str,
    quote: EndOfDayQuote | None,
    story: WatchlistStory | None,
) -> str:
    """Full-width watchlist row for a ticker that has disclosures or news."""
    quote_cell = _render_quote_cell(ticker, quote)
    parts = [
        f'<div style="padding:14px 0; border-top:1px solid {_DIVIDER};">'
        f"<div>{quote_cell}</div>"
    ]

    if story is None:
        parts.append("</div>")
        return "".join(parts)

    # ---- Disclosures ----
    if story.disclosures:
        parts.append(
            f'<p style="margin:8px 0 4px; font-size:11px; font-weight:700;'
            f" text-transform:uppercase; letter-spacing:0.5px; color:{_SECONDARY};"
            f' font-family:{SYSTEM_FONT_STACK};">Disclosed</p>'
        )
        for filing in story.disclosures[:4]:
            filing_headline, metadata = _filing_display_text(filing)
            url = str(getattr(filing, "url", ""))
            parts.append(
                f'<p style="margin:2px 0; font-size:13px; line-height:1.4;">'
                f'<a href="{html.escape(url, quote=True)}" style="color:{_LINK};'
                f' text-decoration:none;">{html.escape(filing_headline)}</a> '
                f'<span style="font-size:11px; color:{_QUIET};">'
                f'{html.escape(metadata)}</span></p>'
            )

    # ---- Reported summary ----
    if story.summary or (story.summary_unavailable and story.articles):
        if story.summary:
            body = html.escape(story.summary)
            if story.why_it_matters:
                body += " " + html.escape(story.why_it_matters)
        else:
            body = "Summary unavailable: " + html.escape(story.articles[0].title)
        parts.append(
            f'<p style="margin:6px 0 0; font-size:13.5px; line-height:1.5;'
            f" color:{_SECONDARY}; font-family:{SYSTEM_FONT_STACK};\">"
            f"{body}</p>"
        )
        # Source links
        source_articles = story.articles[:2]
        if source_articles:
            links = ", ".join(
                f'<a href="{html.escape(a.canonical_url or a.url, quote=True)}"'
                f' style="color:{_LINK}; text-decoration:none;">'
                f"{html.escape(a.source)}</a>"
                for a in source_articles
            )
            parts.append(
                f'<p style="margin:5px 0 0; font-size:12px; color:{_SOURCE_CLR};'
                f' font-style:italic;">via {links}</p>'
            )

    # ---- Relationship ----
    if story.relationship_label:
        relation = _relationship_wording(ticker, str(story.relationship_label))
        if story.relationship_source:
            citation = html.escape(story.relationship_source, quote=True)
            parts.append(
                f'<p style="margin:5px 0 0; font-size:12px; color:{_SOURCE_CLR};'
                f' font-style:italic;">{html.escape(relation)}'
                f' <a href="{citation}" style="color:{_LINK};'
                f' text-decoration:none;">evidence</a></p>'
            )
        else:
            parts.append(
                f'<p style="margin:5px 0 0; font-size:12px; color:{_SOURCE_CLR};'
                f' font-style:italic;">{html.escape(relation)}</p>'
            )

    # ---- Error / warning notices ----
    has_content = bool(
        story.disclosures
        or story.summary
        or (story.summary_unavailable and story.articles)
    )
    if story.official_retrieval_failed:
        msg = (
            "Official filing retrieval failed."
            if has_content
            else "Official filing retrieval failed; no complete news determination was possible."
        )
        parts.append(
            f'<p style="margin:5px 0 0; font-size:12px; color:{_QUIET};'
            f' font-style:italic;">{html.escape(msg)}</p>'
        )
    elif story.classification_incomplete:
        parts.append(
            f'<p style="margin:5px 0 0; font-size:12px; color:{_QUIET};'
            f' font-style:italic;">Watchlist classification incomplete.</p>'
        )
    elif story.search_error:
        warning = (
            "Some optional news sources failed."
            if has_content
            else "No verified news today (partial sources)."
        )
        parts.append(
            f'<p style="margin:5px 0 0; font-size:12px; color:{_QUIET};'
            f' font-style:italic;">{html.escape(warning)}</p>'
        )

    parts.append("</div>")
    return "".join(parts)


def _filing_display_text(filing: object) -> tuple[str, str]:
    headline = str(getattr(filing, "headline", "")).strip()
    if not headline:
        description = str(getattr(filing, "description", "")).strip()
        headline = description if description else "Important company update"
    accepted = getattr(filing, "accepted_at", None)
    timestamp = (
        accepted.astimezone(ZoneInfo("America/New_York")).strftime("%H:%M ET")
        if accepted is not None and accepted.tzinfo is not None
        else str(getattr(filing, "filing_date", ""))
    )
    form = str(getattr(filing, "form", "Filing"))
    return headline, f"{form} · {timestamp}"


# ------------------------------------------------------------------
# Shared helpers
# ------------------------------------------------------------------


def _relationship_wording(ticker: str, label: str) -> str:
    return {
        "DIRECT": f"Relevance: directly about the {ticker} issuer.",
        "AFFILIATE": f"Relevance: a controlled affiliate of the {ticker} issuer.",
        "MANAGED_CAPITAL": (
            "Relevance: Brookfield’s asset-management platform; "
            "this does not establish that BN entered the transaction."
        ),
        "UNDERLYING_ASSET": (
            "Relevance: ETHB holds ether, so this affects the fund’s "
            "underlying asset; the trust did not cause the event."
        ),
        "FAMILY_UNRESOLVED": (
            "Relevance: the source names the corporate family but does not "
            "establish which entity acted."
        ),
    }.get(label, f"Relevance: {label}")
