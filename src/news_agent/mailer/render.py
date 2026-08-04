from __future__ import annotations

import html
from urllib.parse import urlparse
from dataclasses import dataclass
from zoneinfo import ZoneInfo

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
    *,
    gate_state: str = "DISABLED",
    pending_relationships: int = 0,
    gate_progress_notice: str = "",
) -> tuple[str, str]:
    lines = ["WATCHLIST"]
    html_rows = ["<h2>Watchlist</h2>"]
    by_ticker = {story.ticker: story for story in stories}
    for ticker, quote in quotes.items():
        if quote is None:
            quote_line = f"{ticker}: quote unavailable"
            quote_html = html.escape(quote_line)
        else:
            timing = "live" if quote.quote_kind == "live" else f"close {quote.close_date}"
            color = "#188038" if quote.percent_change > 0 else "#d93025" if quote.percent_change < 0 else "#5f6368"
            quote_line = f"{ticker}: {quote.close_price:.2f} ({quote.percent_change:+.2f}%) · {timing}"
            quote_html = (
                f"{html.escape(ticker)}: "
                f'<span style="color: {color};">{quote.close_price:.2f} '
                f"({quote.percent_change:+.2f}%)</span> · {html.escape(timing)}"
            )
        lines.append(quote_line)
        row_parts = [f"<div><strong>{quote_html}</strong>"]
        story = by_ticker.get(ticker)
        has_content = False
        if story is not None and story.disclosures:
            has_content = True
            lines.append("  Disclosed")
            row_parts.append("<h3>Disclosed</h3><ul>")
            for filing in story.disclosures[:2]:
                accepted = getattr(filing, "accepted_at", None)
                timestamp = (
                    accepted.astimezone(ZoneInfo("America/New_York")).strftime("%H:%M ET")
                    if accepted is not None and accepted.tzinfo is not None
                    else str(getattr(filing, "filing_date", ""))
                )
                item_values = tuple(getattr(filing, "items", ()))
                detail = f"Items {', '.join(item_values)}" if item_values else "material filing"
                headline = f"{getattr(filing, 'form', 'Filing')} accepted {timestamp} — {detail}"
                url = str(getattr(filing, "url", ""))
                lines.extend((f"    {headline}", f"    {url}"))
                row_parts.append(
                    f'<li><a href="{html.escape(url, quote=True)}">{html.escape(headline)}</a></li>'
                )
            for filing in story.disclosures[2:4]:
                headline = f"Also: {getattr(filing, 'form', 'Filing')} — {getattr(filing, 'filing_date', '')}"
                url = str(getattr(filing, "url", ""))
                lines.extend((f"    {headline}", f"    {url}"))
                row_parts.append(
                    f'<li><a href="{html.escape(url, quote=True)}">{html.escape(headline)}</a></li>'
                )
            row_parts.append("</ul>")
        if story is not None and (story.summary or (story.summary_unavailable and story.articles)):
            has_content = True
            lines.append("  Reported")
            row_parts.append("<h3>Reported</h3>")
            if story.summary:
                lines.append(f"    {story.summary}")
                body = html.escape(story.summary)
                if story.why_it_matters:
                    lines.append(f"    Why it matters: {story.why_it_matters}")
                    body += " " + html.escape(story.why_it_matters)
            else:
                headline = story.articles[0].title
                lines.append(f"    Summary unavailable: {headline}")
                body = "Summary unavailable: " + html.escape(headline)
            links = " ".join(
                f'<a href="{html.escape(article.canonical_url or article.url, quote=True)}">{html.escape(article.source)}</a>'
                for article in story.articles[:2]
            )
            row_parts.append(f"<p>{body} {links}</p>")
            if story.relationship_label:
                relation = _relationship_wording(ticker, str(story.relationship_label))
                lines.append(f"    {relation}")
                citation = html.escape(story.relationship_source, quote=True)
                row_parts.append(f'<p>{html.escape(relation)} <a href="{citation}">relationship evidence</a></p>')
        if story is not None and story.official_retrieval_failed and has_content:
            lines.append("Official filing retrieval failed.")
            row_parts.append("<p><strong>Official filing retrieval failed.</strong></p>")
        if story is not None and story.official_retrieval_failed and not has_content:
            warning = "Official filing retrieval failed; no complete news determination was possible."
            lines.append(warning)
            row_parts.append(f"<p>{html.escape(warning)}</p>")
        elif story is not None and story.classification_incomplete:
            warning = "Watchlist classification incomplete; some candidates were not evaluated."
            lines.append(warning)
            row_parts.append(f"<p>{html.escape(warning)}</p>")
        elif story is not None and story.search_error:
            warning = "Some optional news sources failed." if has_content else "No verified news today (partial sources)."
            lines.append(warning)
            row_parts.append(f"<p>{html.escape(warning)}</p>")
        elif not has_content and not (story is not None and story.official_retrieval_failed):
            lines.append("No verified news today.")
            row_parts.append("<p>No verified news today.</p>")
        row_parts.append("</div>")
        html_rows.extend(row_parts)
    if gate_state == "DISABLED":
        lines.append("Watchlist evaluation disabled.")
        html_rows.append("<p><em>Watchlist evaluation disabled.</em></p>")
    if pending_relationships:
        notice = f"Watchlist review needed: {pending_relationships} relationship(s)."
        lines.append(notice)
        html_rows.append(f"<p><em>{html.escape(notice)}</em></p>")
    if gate_progress_notice:
        lines.append(gate_progress_notice)
        html_rows.append(f"<p><em>{html.escape(gate_progress_notice)}</em></p>")
    return "\n".join(lines), "".join(html_rows)


def _relationship_wording(ticker: str, label: str) -> str:
    return {
        "DIRECT": f"Relevance: directly about the {ticker} issuer.",
        "AFFILIATE": f"Relevance: a controlled affiliate of the {ticker} issuer.",
        "MANAGED_CAPITAL": "Relevance: Brookfield's asset-management platform; this does not establish that BN entered the transaction.",
        "UNDERLYING_ASSET": "Relevance: ETHB holds ether, so this affects the fund's underlying asset; the trust did not cause the event.",
        "FAMILY_UNRESOLVED": "Relevance: the source names the corporate family but does not establish which entity acted.",
    }.get(label, f"Relevance: {label}")
