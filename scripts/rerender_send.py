#!/usr/bin/env python3
"""Re-render a stored newsletter with current formatting and send as a test email.

Reads the content from a stored edition, parses the plain text back into
FormattedMessage objects, re-renders through render_minimal_newsletter()
with the current code, and sends the result. This lets you iterate on
render.py changes and see the result in your inbox without re-running
the full news pipeline.

Usage:
    python scripts/rerender_send.py                # re-render latest + send
    python scripts/rerender_send.py --preview       # save to preview.html, no send
    python scripts/rerender_send.py --edition 42    # re-render a specific edition
    python scripts/rerender_send.py --count 2       # send 2 copies (after successive edits)
"""

from __future__ import annotations

import argparse
import html as html_lib
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# Ensure src/ is importable when run from the project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from news_agent.env import load_dotenv
from news_agent.formatting import CATEGORY_HEADERS, FormattedMessage
from news_agent.mailer.quotes import EndOfDayQuote
from news_agent.mailer.render import render_minimal_newsletter, render_watchlist_section
from news_agent.mailer.settings import email_settings_from_env
from news_agent.mailer.smtp import send_email
from news_agent.mailer.state import EmailStateStore
from news_agent.mailer.watchlist_news import WatchlistStory
from news_agent.models import Article

# Map display prefixes back to category keys: "🧠 BUSINESS + TECH" → "business_tech"
_REVERSE_HEADERS: dict[str, str] = {v: k for k, v in CATEGORY_HEADERS.items()}

# Regex matching any category header line in plain text.
_HEADER_RE = re.compile(
    r"^(" + "|".join(re.escape(h) for h in _REVERSE_HEADERS) + r") · .+$",
    re.MULTILINE,
)

_WATCHLIST_MARKER = "\nWATCHLIST\n"
_FOOTER_MARKER = "For informational purposes only"

_LEGACY_ROW_RE = re.compile(
    r"<div(?:\s[^>]*)?><strong(?:\s[^>]*)?>(.*?)</strong>(.*?)</div>",
    re.IGNORECASE | re.DOTALL,
)
_LEGACY_QUOTE_RE = re.compile(
    r"^(?P<ticker>[A-Z0-9.-]+):\s*"
    r"(?:(?P<unavailable>quote unavailable)|"
    r"(?P<price>[0-9,]+(?:\.[0-9]+)?)\s*"
    r"\((?P<change>[+-]?[0-9]+(?:\.[0-9]+)?)%\)\s*·\s*"
    r"(?P<timing>live|close(?:\s+(?P<close_date>\d{4}-\d{2}-\d{2}))?))$"
)
_ANCHOR_RE = re.compile(
    r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class _LegacyFiling:
    form: str
    filing_date: str
    items: tuple[str, ...]
    url: str
    headline: str = ""
    accepted_at: None = None


# ------------------------------------------------------------------
# Parse stored plain text → FormattedMessage list
# ------------------------------------------------------------------

def _parse_plain_text(plain_text: str) -> tuple[str, list[FormattedMessage]]:
    """Reconstruct (header, messages) from a stored edition's plain_text."""
    matches = list(_HEADER_RE.finditer(plain_text))
    if not matches:
        return plain_text.strip(), []

    header = plain_text[: matches[0].start()].strip()
    messages: list[FormattedMessage] = []

    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(plain_text)
        section_text = plain_text[start:end]

        # Trim watchlist / footer that may trail the last section.
        for marker in (_WATCHLIST_MARKER, "\nWATCHLIST", f"\n{_FOOTER_MARKER}"):
            idx = section_text.find(marker)
            if idx >= 0:
                section_text = section_text[:idx]

        section_text = section_text.strip()
        if not section_text:
            continue

        prefix = m.group(1)
        messages.append(
            FormattedMessage(
                title=m.group(0),
                text=section_text,
                category=_REVERSE_HEADERS.get(prefix, ""),
            )
        )
    return header, messages


# ------------------------------------------------------------------
# Extract watchlist HTML from the stored rendered HTML
# ------------------------------------------------------------------

def _extract_watchlist_html(stored_html: str) -> str:
    """Pull the watchlist <div> block out of a stored newsletter HTML.

    The watchlist section is the block containing '>Watchlist<' that sits
    between the last news section and the footer.  Returns empty string
    when no watchlist is found.
    """
    # The watchlist header is rendered as uppercase "Watchlist" inside
    # the accent-bar table.  Find the outermost <div> that contains it.
    tag = ">Watchlist<"
    idx = stored_html.find(tag)
    if idx < 0:
        return ""

    # Walk backwards to find the opening <div style="padding:0 28px;">
    search_start = stored_html.rfind('<div style="padding:0 28px;">', 0, idx)
    if search_start < 0:
        legacy_start = stored_html.rfind("<section", 0, idx)
        legacy_end = stored_html.find("</section>", idx)
        if legacy_start < 0 or legacy_end < 0:
            return ""
        legacy_html = stored_html[legacy_start : legacy_end + len("</section>")]
        return _rerender_legacy_watchlist_html(legacy_html)

    # Walk forward to find the matching closing </div>.
    depth = 0
    pos = search_start
    while pos < len(stored_html):
        open_idx = stored_html.find("<div", pos)
        close_idx = stored_html.find("</div>", pos)
        if close_idx < 0:
            break
        if open_idx >= 0 and open_idx < close_idx:
            depth += 1
            pos = open_idx + 4
        else:
            depth -= 1
            if depth == 0:
                return stored_html[search_start : close_idx + len("</div>")]
            pos = close_idx + 6

    return ""


def _rerender_legacy_watchlist_html(legacy_html: str) -> str:
    """Convert the controlled legacy Watchlist fragment through the current renderer."""
    quotes: dict[str, EndOfDayQuote | None] = {}
    stories: list[WatchlistStory] = []

    for strong_html, body_html in _LEGACY_ROW_RE.findall(legacy_html):
        quote_match = _LEGACY_QUOTE_RE.fullmatch(_plain_fragment(strong_html))
        if quote_match is None:
            continue

        ticker = quote_match.group("ticker")
        if quote_match.group("unavailable"):
            quotes[ticker] = None
        else:
            close_price = float(quote_match.group("price").replace(",", ""))
            percent_change = float(quote_match.group("change"))
            change_factor = 1 + percent_change / 100
            previous_close = close_price / change_factor if change_factor else close_price
            timing = quote_match.group("timing")
            quotes[ticker] = EndOfDayQuote(
                ticker=ticker,
                close_date=quote_match.group("close_date") or "",
                close_price=close_price,
                previous_close=previous_close,
                provider="stored edition",
                quote_kind="live" if timing == "live" else "close",
            )

        disclosures = _legacy_disclosures(body_html)
        summary, summary_unavailable, articles = _legacy_reported_story(body_html)
        relationship_label, relationship_source = _legacy_relationship(body_html)
        if disclosures or summary or summary_unavailable or relationship_label:
            stories.append(
                WatchlistStory(
                    ticker=ticker,
                    summary=summary,
                    articles=articles,
                    summary_unavailable=summary_unavailable,
                    disclosures=disclosures,
                    relationship_label=relationship_label,
                    relationship_source=relationship_source,
                )
            )

    if not quotes:
        return legacy_html

    status_text = _plain_fragment(legacy_html)
    gate_state = "DISABLED" if "Watchlist evaluation disabled." in status_text else "MEASURING"
    pending_match = re.search(r"Watchlist review needed:\s*(\d+) relationship", status_text)
    pending_relationships = int(pending_match.group(1)) if pending_match else 0
    return render_watchlist_section(
        quotes,
        stories,
        gate_state=gate_state,
        pending_relationships=pending_relationships,
    )[1]


def _plain_fragment(fragment: str) -> str:
    without_tags = re.sub(r"<[^>]+>", " ", fragment)
    return " ".join(html_lib.unescape(without_tags).split())


def _legacy_disclosures(body_html: str) -> tuple[_LegacyFiling, ...]:
    disclosed = re.search(
        r"<h3>\s*Disclosed\s*</h3>\s*<ul>(.*?)</ul>",
        body_html,
        re.IGNORECASE | re.DOTALL,
    )
    if disclosed is None:
        return ()

    filings: list[_LegacyFiling] = []
    for url, label_html in _ANCHOR_RE.findall(disclosed.group(1)):
        label = _plain_fragment(label_html)
        if label.startswith("Also: "):
            match = re.fullmatch(r"Also:\s*(\S+)\s*—\s*(.+)", label)
            if match is None:
                continue
            form, filing_date = match.groups()
            items: tuple[str, ...] = ()
        else:
            match = re.fullmatch(r"(\S+)\s+accepted\s+(.+?)\s*—\s*(.+)", label)
            if match is None:
                continue
            form, filing_date, detail = match.groups()
            items = (
                tuple(item.strip() for item in detail.removeprefix("Items ").split(","))
                if detail.startswith("Items ")
                else ()
            )
        filings.append(
            _LegacyFiling(
                form,
                filing_date,
                items,
                html_lib.unescape(url),
                headline=label,
            )
        )
    return tuple(filings)


def _legacy_reported_story(body_html: str) -> tuple[str, bool, tuple[Article, ...]]:
    reported = re.search(
        r"<h3>\s*Reported\s*</h3>\s*<p>(.*?)</p>",
        body_html,
        re.IGNORECASE | re.DOTALL,
    )
    if reported is None:
        return "", False, ()

    paragraph_html = reported.group(1)
    first_anchor = _ANCHOR_RE.search(paragraph_html)
    summary = _plain_fragment(paragraph_html[: first_anchor.start()] if first_anchor else paragraph_html)
    articles = tuple(
        Article(
            title=_plain_fragment(source_html),
            url=html_lib.unescape(url),
            source=_plain_fragment(source_html),
            published_at=datetime(1970, 1, 1, tzinfo=timezone.utc),
        )
        for url, source_html in _ANCHOR_RE.findall(paragraph_html)
    )
    unavailable_prefix = "Summary unavailable: "
    if summary.startswith(unavailable_prefix):
        title = summary.removeprefix(unavailable_prefix)
        if articles:
            articles = (Article(title, articles[0].url, articles[0].source, articles[0].published_at), *articles[1:])
        return "", True, articles
    return summary, False, articles


def _legacy_relationship(body_html: str) -> tuple[str, str]:
    for paragraph_html in re.findall(r"<p>(.*?)</p>", body_html, re.IGNORECASE | re.DOTALL):
        paragraph_text = _plain_fragment(paragraph_html)
        if not paragraph_text.startswith("Relevance:"):
            continue
        evidence = next(
            (
                html_lib.unescape(url)
                for url, label_html in _ANCHOR_RE.findall(paragraph_html)
                if "relationship evidence" in _plain_fragment(label_html).casefold()
            ),
            "",
        )
        label_html = paragraph_html[: _ANCHOR_RE.search(paragraph_html).start()] if _ANCHOR_RE.search(paragraph_html) else paragraph_html
        label = _plain_fragment(label_html).removeprefix("Relevance:").strip()
        return label, evidence
    return "", ""


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------

def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Re-render a stored newsletter edition with current formatting.",
    )
    parser.add_argument(
        "--edition",
        type=int,
        default=None,
        help="Edition ID to re-render (default: latest production edition).",
    )
    parser.add_argument(
        "--preview",
        nargs="?",
        const="preview.html",
        default=None,
        metavar="FILE",
        help="Write HTML to FILE instead of sending (default: preview.html).",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=1,
        help="Number of test emails to send (default: 1).",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("data/email_state.db"),
        help="Path to the email state database.",
    )
    args = parser.parse_args()

    # ---- Load the edition ----
    store = EmailStateStore(args.db)
    if args.edition is not None:
        edition = store.edition(args.edition)
        if edition is None:
            raise SystemExit(f"Edition {args.edition} not found.")
    else:
        editions = store.latest_editions(limit=5)
        edition = next(
            (e for e in editions if e.edition_kind == "production"),
            None,
        )
        if edition is None:
            raise SystemExit("No production editions found in the database.")

    print(f"Source: edition {edition.edition_id}  ({edition.local_date} r{edition.revision})")
    print(f"Original subject: {edition.subject}")

    # ---- Parse content ----
    header, messages = _parse_plain_text(edition.plain_text)
    if not messages:
        raise SystemExit("Could not parse any sections from the stored plain text.")
    print(f"Parsed {len(messages)} section(s): {', '.join(m.category or '?' for m in messages)}")

    # ---- Extract watchlist HTML from stored rendering ----
    watchlist_html = _extract_watchlist_html(edition.html)
    if watchlist_html:
        print("Watchlist HTML extracted from stored edition.")
    else:
        print("No watchlist block found; re-rendering without it.")

    # ---- Re-render ----
    rendered = render_minimal_newsletter(
        messages,
        header,
        watchlist_html=watchlist_html,
        watchlist_text="",
    )
    tag = "[Format test]"
    rendered_subject = f"{rendered.subject} {tag}"
    print(f"Re-rendered subject: {rendered_subject}")

    # ---- Output ----
    if args.preview is not None:
        out = Path(args.preview)
        out.write_text(rendered.html, encoding="utf-8")
        print(f"Preview written to {out.resolve()}")
        return

    settings = email_settings_from_env()
    for i in range(args.count):
        for recipient in settings.recipients:
            outcome = send_email(
                settings,
                recipient,
                rendered_subject,
                rendered.plain_text,
                rendered.html,
            )
            label = f"[{i + 1}/{args.count}] " if args.count > 1 else ""
            print(f"  {label}{recipient}: {outcome.state}")
            if outcome.state != "smtp_accepted":
                print(f"    error: {outcome.error_code}", file=sys.stderr)

    print("Done.")


if __name__ == "__main__":
    main()
