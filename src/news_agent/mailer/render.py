from __future__ import annotations

import html
from urllib.parse import urlparse
from dataclasses import dataclass

from news_agent.formatting import FormattedMessage
from news_agent.time import briefing_today
from news_agent.mailer.quotes import EndOfDayQuote
from news_agent.mailer.watchlist_news import WatchlistStory

SYSTEM_FONT_STACK = '-apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif'


@dataclass(frozen=True)
class RenderedEmail:
    subject: str
    plain_text: str
    html: str


def render_parity_email(messages: list[FormattedMessage], header: str) -> RenderedEmail:
    plain_text = "\n\n".join((header, *(message.text for message in messages))).strip() + "\n"
    subject = f"Morning Briefing — {briefing_today().isoformat()}"
    rendered_html = (
        '<html><body style="font-family: Helvetica, Arial, sans-serif;">'
        '<pre style="font-family: Helvetica, Arial, sans-serif; white-space: pre-wrap;">'
        + html.escape(plain_text)
        + "</pre></body></html>"
    )
    return RenderedEmail(subject=subject, plain_text=plain_text, html=rendered_html)


def render_minimal_newsletter(
    messages: list[FormattedMessage],
    header: str,
    watchlist_html: str = "",
    watchlist_text: str = "",
) -> RenderedEmail:
    plain_parts = [header, *(message.text for message in messages)]
    if watchlist_text:
        plain_parts.append(watchlist_text)
    plain_parts.append("For informational purposes only; not investment advice.")
    plain_text = "\n\n".join(plain_parts).strip() + "\n"
    sections = "".join(f"<section>{_render_message_with_source_links(message.text)}</section>" for message in messages)
    extra = f"<section>{watchlist_html}</section>" if watchlist_html else ""
    rendered_html = (
        f'<html><body style="font-family: {SYSTEM_FONT_STACK};">'
        f'<h1 style="font-family: {SYSTEM_FONT_STACK};">{html.escape(header)}</h1>{sections}{extra}'
        "<footer><small>For informational purposes only; not investment advice.</small></footer>"
        "</body></html>"
    )
    subject = f"Morning Briefing — {briefing_today().isoformat()}"
    return RenderedEmail(subject=subject, plain_text=plain_text, html=rendered_html)


def _render_message_with_source_links(message: str) -> str:
    """Render a category heading and its stories without exposing raw source URLs."""
    blocks = [block for block in message.split("\n\n") if block]
    if not blocks:
        return ""

    heading = f'<p style="margin: 0;">{html.escape(blocks[0])}</p>'
    stories = "".join(_render_story_block(block) for block in blocks[1:])
    return heading + stories


def _render_story_block(block: str) -> str:
    lines = block.splitlines()
    rendered: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith("(via ") and line.endswith(")") and index + 1 < len(lines):
            urls = [value.strip() for value in lines[index + 1].split(",")]
            labels = [value.strip() for value in line[5:-1].split(",")]
            if (
                len(urls) > 1
                and len(urls) == len(labels)
                and all(_is_http_url(url) for url in urls)
            ):
                linked_labels = ", ".join(
                    f'<a href="{html.escape(url, quote=True)}">{html.escape(label)}</a>'
                    for label, url in zip(labels, urls, strict=True)
                )
                rendered.append(f"(via {linked_labels})")
                index += 2
                continue
        if (
            line.startswith("(via ")
            and line.endswith(")")
            and index + 1 < len(lines)
            and _is_http_url(lines[index + 1])
        ):
            url = html.escape(lines[index + 1], quote=True)
            rendered.append(f'<a href="{url}">{html.escape(line)}</a>')
            index += 2
            continue
        rendered.append(html.escape(line))
        index += 1
    return (
        f'<div style="padding: 22px 0; font-family: {SYSTEM_FONT_STACK}; '
        'font-size: 16px; line-height: 1.5;">'
        + "<br>".join(rendered)
        + "</div>"
    )


def _is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def render_watchlist_section(
    quotes: dict[str, EndOfDayQuote | None],
    stories: list[WatchlistStory],
) -> tuple[str, str]:
    lines = ["WATCHLIST"]
    html_rows = ["<h2>Watchlist</h2><ul>"]
    by_ticker = {story.ticker: story for story in stories}
    for ticker, quote in quotes.items():
        if quote is None:
            quote_line = f"{ticker}: quote unavailable"
        else:
            quote_line = f"{ticker}: {quote.close_price:.2f} ({quote.percent_change:+.2f}%) · close {quote.close_date}"
        lines.append(quote_line)
        story = by_ticker.get(ticker)
        article_links = ""
        if story is not None and story.articles:
            article_links = " ".join(
                f'<a href="{html.escape(article.canonical_url or article.url, quote=True)}">{html.escape(article.source)}</a>'
                for article in story.articles[:2]
            )
        body = ""
        if story is not None and story.search_error:
            body = "News search unavailable."
            lines.append("News search unavailable.")
        elif story is not None and story.summary:
            body = html.escape(story.summary)
            if story.why_it_matters:
                body += " " + html.escape(story.why_it_matters)
            lines.extend((story.summary, f"Why it matters: {story.why_it_matters}"))
        elif story is not None and story.summary_unavailable and story.articles:
            headline = story.articles[0].title
            body = "Summary unavailable: " + html.escape(headline)
            lines.append(f"Summary unavailable: {headline}")
        html_rows.append(f"<li><strong>{html.escape(quote_line)}</strong><br>{body} {article_links}</li>")
    html_rows.append("</ul>")
    return "\n".join(lines), "".join(html_rows)
