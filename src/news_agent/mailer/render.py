from __future__ import annotations

import html
from dataclasses import dataclass

from news_agent.formatting import FormattedMessage
from news_agent.time import briefing_today
from news_agent.mailer.quotes import EndOfDayQuote
from news_agent.mailer.watchlist_news import WatchlistStory


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
    sections = "".join(f"<section><pre>{html.escape(message.text)}</pre></section>" for message in messages)
    extra = f"<section>{watchlist_html}</section>" if watchlist_html else ""
    rendered_html = (
        '<html><body style="font-family: Helvetica, Arial, sans-serif;">'
        f'<h1 style="font-family: Helvetica, Arial, sans-serif;">{html.escape(header)}</h1>{sections}{extra}'
        "<footer><small>For informational purposes only; not investment advice.</small></footer>"
        "</body></html>"
    )
    subject = f"Morning Briefing — {briefing_today().isoformat()}"
    return RenderedEmail(subject=subject, plain_text=plain_text, html=rendered_html)


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
