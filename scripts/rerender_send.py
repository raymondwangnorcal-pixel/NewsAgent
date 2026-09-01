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
import re
import sys
from pathlib import Path

# Ensure src/ is importable when run from the project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from news_agent.formatting import CATEGORY_HEADERS, FormattedMessage
from news_agent.mailer.render import render_minimal_newsletter
from news_agent.mailer.settings import email_settings_from_env
from news_agent.mailer.smtp import send_email
from news_agent.mailer.state import EmailStateStore

# Map display prefixes back to category keys: "🧠 BUSINESS + TECH" → "business_tech"
_REVERSE_HEADERS: dict[str, str] = {v: k for k, v in CATEGORY_HEADERS.items()}

# Regex matching any category header line in plain text.
_HEADER_RE = re.compile(
    r"^(" + "|".join(re.escape(h) for h in _REVERSE_HEADERS) + r") · .+$",
    re.MULTILINE,
)

_WATCHLIST_MARKER = "\nWATCHLIST\n"
_FOOTER_MARKER = "For informational purposes only"


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
        return ""

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


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------

def main() -> None:
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
