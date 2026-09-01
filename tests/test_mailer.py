from __future__ import annotations

import re
import socket
import sqlite3
import ssl
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from news_agent import cli
from news_agent.formatting import FormattedMessage
from news_agent.mailer import quotes, service as mailer_service, watchlist_news
from news_agent.mailer.models import EmailSettings, EmailWatchlistEntry, RecipientOutcome
from news_agent.mailer.quotes import EndOfDayQuote, EodhdQuoteProvider, TiingoQuoteProvider, expected_quote_close_date, fetch_quote_with_fallback, fetch_quotes_with_shared_deadline, is_regular_nyse_market_hours
from news_agent.mailer.render import RenderedEmail, render_minimal_newsletter, render_parity_email, render_watchlist_section
from news_agent.mailer.settings import email_settings_from_env
from news_agent.mailer.smtp import send_email
from news_agent.mailer.state import EmailStateStore
from news_agent.mailer.watchlist import load_email_watchlist, validate_shared_watchlist_consistency
from news_agent.mailer.schedule import scheduled_email_is_due
from news_agent.models import Article, EnrichmentConfig, ExtractionPolicyConfig, OpenAICostConfig, StockQuote
from news_agent.openai_budget import OpenAIBudget
from news_agent.watchlist.models import Filing


class FakeSMTP:
    def __init__(self) -> None:
        self.started_tls = False
        self.logged_in = False

    def ehlo(self) -> None:
        return None

    def starttls(self, context: object) -> None:
        self.started_tls = True

    def login(self, username: str, password: str) -> None:
        self.logged_in = True

    def mail(self, sender: str) -> tuple[int, bytes]:
        return 250, b"ok"

    def rcpt(self, recipient: str) -> tuple[int, bytes]:
        return 250, b"ok"

    def data(self, message: bytes) -> tuple[int, bytes]:
        return 250, b"accepted"

    def quit(self) -> None:
        return None


class StartTlsFailsSMTP(FakeSMTP):
    def starttls(self, context: object) -> None:
        raise ssl.SSLError("handshake failed")


class DataResetSMTP(FakeSMTP):
    def data(self, message: bytes) -> tuple[int, bytes]:
        raise ConnectionResetError("connection reset")


class MailResetSMTP(FakeSMTP):
    def mail(self, sender: str) -> tuple[int, bytes]:
        raise ConnectionResetError("connection reset")


def smtp_settings(recipients: tuple[str, ...] = ("to@example.com",)) -> EmailSettings:
    return EmailSettings("smtp.gmail.com", 587, "sender@example.com", "secret", "sender@example.com", recipients)


def extracted_article(url: str, source: str = "Reuters") -> Article:
    return Article(
        title="Company reports earnings",
        url=url,
        source=source,
        published_at=datetime.now(timezone.utc),
        extracted_text="Company reported earnings and raised its full-year guidance.",
        enrichment_status="extracted",
    )


def test_parity_email_plain_text_is_header_and_exact_messages() -> None:
    messages = [FormattedMessage("Finance", "FINANCE\nA story"), FormattedMessage("World", "WORLD\nAnother story")]

    rendered = render_parity_email(messages, "Morning Briefing - 2026-07-25")

    assert rendered.plain_text == "Morning Briefing - 2026-07-25\n\nFINANCE\nA story\n\nWORLD\nAnother story\n"
    assert "<pre style=" in rendered.html
    assert "font-family: Lato, Helvetica, Arial, sans-serif" in rendered.html


def test_rendered_email_font_stack_is_valid_inside_html_style_attributes() -> None:
    rendered = render_parity_email([], "Morning Briefing - 2026-09-01")

    assert "font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif" in rendered.html
    assert 'BlinkMacSystemFont, "Segoe UI"' not in rendered.html


def test_native_newsletter_declares_utf8_for_standalone_preview() -> None:
    messages = [
        FormattedMessage(
            "Global News",
            "GLOBAL NEWS\n\nVolker Türk issued a sufficiently long warning. It’s important context.",
            category="global",
        )
    ]

    rendered = render_minimal_newsletter(messages, "Morning Briefing - 2026-09-01")

    assert rendered.html.startswith('<html><head><meta charset="utf-8">')
    assert "</head><body" in rendered.html
    assert "Volker Türk" in rendered.html
    assert "It’s important context." in rendered.html


def test_native_newsletter_links_source_label_and_keeps_plain_text_url() -> None:
    messages = [
        FormattedMessage(
            "Business + Tech",
            "BUSINESS + TECH\n\nA substantive story.\n(via BBC)\nhttps://www.bbc.co.uk/news/example",
        )
    ]

    rendered = render_minimal_newsletter(messages, "Morning Briefing - 2026-07-28")

    assert "(via BBC)\nhttps://www.bbc.co.uk/news/example" in rendered.plain_text
    assert '<a href="https://www.bbc.co.uk/news/example">(via BBC)</a>' in rendered.html
    assert ">https://www.bbc.co.uk/news/example<" not in rendered.html
    assert "<pre" not in rendered.html
    assert "font-family: Lato, Helvetica, Arial, sans-serif" in rendered.html
    assert 'padding: 8.5px 0' in rendered.html


def test_native_newsletter_links_each_source_in_merged_story() -> None:
    messages = [
        FormattedMessage(
            "Global",
            "GLOBAL\n\nA merged story.\n(via BBC, Reuters)\n"
            "https://example.com/bbc, https://example.com/reuters",
        )
    ]

    rendered = render_minimal_newsletter(messages, "Morning Briefing - 2026-07-28")

    assert '<a href="https://example.com/bbc">BBC</a>' in rendered.html
    assert '<a href="https://example.com/reuters">Reuters</a>' in rendered.html
    assert ">https://example.com/bbc<" not in rendered.html
    assert ">https://example.com/reuters<" not in rendered.html


def test_headline_index_uses_email_safe_aligned_label_cells() -> None:
    messages = [
        FormattedMessage(
            "Business + Tech",
            "BUSINESS + TECH\n\nA sufficiently long business headline. Supporting context.",
            category="business_tech",
        ),
        FormattedMessage(
            "U.S. News",
            "U.S. NEWS\n\nA sufficiently long domestic headline. Supporting context.",
            category="domestic",
        ),
    ]

    rendered = render_minimal_newsletter(messages, "Morning Briefing - 2026-09-01")
    index_html = rendered.html.split("In This Briefing", maxsplit=1)[1].split("</table></div>", maxsplit=1)[0]

    assert index_html.count("<table") == 2
    assert '<table class="briefing-desktop-index"' in index_html
    assert '<table class="briefing-mobile-index"' in index_html
    assert index_html.count('width="140"') == 2
    assert "table-layout:fixed" in index_html
    assert "margin:0 0 12px" in rendered.html
    assert "padding:20px 28px" in rendered.html
    assert "padding:6px 12px 6px 8px" in index_html
    assert "vertical-align:top" in index_html
    assert "<b>Business + Tech</b>" in index_html
    assert "<b>U.S. News</b>" in index_html


def test_newsletter_uses_approved_distinct_category_accents() -> None:
    categories = (
        ("business_tech", "Business + Tech", "#1565C0"),
        ("domestic", "U.S. News", "#C62828"),
        ("global", "Global News", "#2E7D32"),
        ("culture", "Culture + Media", "#EF6C00"),
        ("finance", "Finance", "#7B1FA2"),
    )
    messages = [
        FormattedMessage(
            label,
            f"{label.upper()}\n\nA sufficiently long category headline. Supporting context.",
            category=category,
        )
        for category, label, _accent in categories
    ]

    rendered = render_minimal_newsletter(messages, "Morning Briefing - 2026-09-01")

    for _category, label, accent in categories:
        assert f"border-left:3px solid {accent}" in rendered.html
        assert f"border-bottom:1px solid {accent}" in rendered.html
        assert rendered.html.count(f"<b>{label}</b>") == 3


def test_headline_index_links_labels_and_headlines_to_matching_sections() -> None:
    messages = [
        FormattedMessage(
            "Business + Tech",
            "BUSINESS + TECH\n\nA sufficiently long business headline. Supporting context.",
            category="business_tech",
        ),
        FormattedMessage(
            "Global News",
            "GLOBAL NEWS\n\nA sufficiently long global headline. Supporting context.",
            category="global",
        ),
    ]

    rendered = render_minimal_newsletter(messages, "Morning Briefing - 2026-09-01")

    for anchor, label, headline in (
        ("section-business-tech", "Business + Tech", "A sufficiently long business headline."),
        ("section-global", "Global News", "A sufficiently long global headline."),
    ):
        assert rendered.html.count(f'href="#{anchor}"') == 2
        assert re.search(
            rf'<a href="#{anchor}"[^>]*><b>{re.escape(label)}</b></a>',
            rendered.html,
        )
        assert re.search(
            rf'<a href="#{anchor}"[^>]*><b>{re.escape(headline)}</b></a>',
            rendered.html,
        )
        destination = f'<a id="{anchor}" name="{anchor}"></a>'
        assert destination in rendered.html
        assert rendered.html.index(f'href="#{anchor}"') < rendered.html.index(destination)


def test_headline_index_shows_same_size_underlined_jump_hint() -> None:
    messages = [
        FormattedMessage(
            "Business + Tech",
            "BUSINESS + TECH\n\nA sufficiently long business headline. Supporting context.",
            category="business_tech",
        )
    ]

    rendered = render_minimal_newsletter(messages, "Morning Briefing - 2026-09-01")

    heading = re.search(
        r'<p class="briefing-index-heading" style="([^"]*font-size:10px;[^"]*)"><b>In This Briefing</b> '
        r'<span style="([^"]*)" class="briefing-desktop-only">'
        r'\(click to jump to category\)</span></p>',
        rendered.html,
    )
    assert heading is not None
    hint_style = heading.group(2)
    assert "text-transform:none" in hint_style
    assert "text-decoration:underline" in hint_style
    assert "font-size" not in hint_style
    assert "font-weight" not in hint_style


def test_mobile_newsletter_replaces_briefing_links_with_plain_text() -> None:
    messages = [
        FormattedMessage(
            "Business + Tech",
            "BUSINESS + TECH\n\nA sufficiently long business headline. Supporting context.",
            category="business_tech",
        )
    ]

    rendered = render_minimal_newsletter(messages, "Morning Briefing - 2026-09-01")

    assert (
        '<a href="#section-business-tech" class="briefing-desktop-only"'
        in rendered.html
    )
    mobile_index = rendered.html.split(
        '<table class="briefing-mobile-index"', maxsplit=1
    )[1].split("</table>", maxsplit=1)[0]
    assert "<b>Business + Tech</b>" in mobile_index
    assert "<b>A sufficiently long business headline.</b>" in mobile_index
    assert 'href="#section-business-tech"' not in mobile_index
    assert (
        '<span style="text-transform:none; text-decoration:underline;" '
        'class="briefing-desktop-only">(click to jump to category)</span>'
        in rendered.html
    )
    assert "@media screen and (max-width:600px)" in rendered.html
    assert ".briefing-desktop-only{display:none!important;}" in rendered.html
    assert ".briefing-desktop-index{display:none!important;}" in rendered.html
    assert ".briefing-mobile-index{display:table!important;width:100%!important;}" in rendered.html


def test_mobile_briefing_stacks_each_headline_below_its_category() -> None:
    messages = [
        FormattedMessage(
            "Business + Tech",
            "BUSINESS + TECH\n\nDisney will add more live sports to Disney+ beginning this fall. "
            "Supporting context.",
            category="business_tech",
        )
    ]

    rendered = render_minimal_newsletter(messages, "Morning Briefing - 2026-09-01")
    assert '<table class="briefing-mobile-index"' in rendered.html
    mobile_index = rendered.html.split(
        '<table class="briefing-mobile-index"', maxsplit=1
    )[1].split("</table>", maxsplit=1)[0]

    assert mobile_index.count("<td") == 1
    assert mobile_index.index("<b>Business + Tech</b>") < mobile_index.index(
        "<b>Disney will add more live sports to Disney+ beginning this fall.</b>"
    )
    assert 'href="#section-business-tech"' not in mobile_index
    assert ".briefing-mobile-index{display:none;}" in rendered.html
    assert ".briefing-mobile-index{display:table!important;width:100%!important;}" in rendered.html
    assert ".briefing-desktop-index{display:none!important;}" in rendered.html


def test_mobile_briefing_uses_right_edge_instead_of_wrapping_early() -> None:
    messages = [
        FormattedMessage(
            "Business + Tech",
            "BUSINESS + TECH\n\nDisney will add more live sports to Disney+ beginning this fall. "
            "Supporting context.",
            category="business_tech",
        )
    ]

    rendered = render_minimal_newsletter(messages, "Morning Briefing - 2026-09-01")

    assert (
        '<div class="briefing-index" style="padding:20px 28px;'
        in rendered.html
    )
    assert (
        ".briefing-index{padding-top:0!important;padding-left:0!important;"
        "padding-right:12px!important;padding-bottom:0!important;}"
        in rendered.html
    )
    assert '<table class="briefing-mobile-index" width="100%"' in rendered.html
    assert 'style="padding:8px 0 20px 8px; border-left:3px solid #1565C0;' in rendered.html


def test_mobile_briefing_moves_rows_to_header_edge_without_changing_text_inset() -> None:
    messages = [
        FormattedMessage(
            "Business + Tech",
            "BUSINESS + TECH\n\nA sufficiently long business headline. Supporting context.",
            category="business_tech",
        )
    ]

    rendered = render_minimal_newsletter(messages, "Morning Briefing - 2026-09-01")

    assert (
        ".briefing-index{padding-top:0!important;padding-left:0!important;"
        "padding-right:12px!important;padding-bottom:0!important;}"
        in rendered.html
    )
    assert '<p class="briefing-index-heading" style="margin:0 0 12px;' in rendered.html
    assert 'style="padding:8px 0 20px 8px; border-left:3px solid #1565C0;' in rendered.html


def test_mobile_briefing_heading_matches_category_label_alignment_and_weight() -> None:
    messages = [
        FormattedMessage(
            "Business + Tech",
            "BUSINESS + TECH\n\nA sufficiently long business headline. Supporting context.",
            category="business_tech",
        )
    ]

    rendered = render_minimal_newsletter(messages, "Morning Briefing - 2026-09-01")

    assert (
        ".briefing-index-heading{margin:0!important;padding:14px 0 8px 8px!important;"
        "font-size:11.5px!important;font-weight:700!important;color:#0F1419!important;"
        "border-left:3px solid #0F1419!important;}"
        in rendered.html
    )
    assert ">In This Briefing " not in rendered.html
    assert "><b>In This Briefing</b> " in rendered.html


def test_mobile_briefing_heading_has_matching_border_and_reaches_watchlist() -> None:
    messages = [
        FormattedMessage(
            "Business + Tech",
            "BUSINESS + TECH\n\nA sufficiently long business headline. Supporting context.",
            category="business_tech",
        )
    ]

    rendered = render_minimal_newsletter(
        messages,
        "Morning Briefing - 2026-09-01",
        watchlist_html='<div class="watchlist-marker">Watchlist</div>',
    )

    assert (
        ".briefing-index{padding-top:0!important;padding-left:0!important;"
        "padding-right:12px!important;padding-bottom:0!important;}"
        in rendered.html
    )
    assert (
        ".briefing-index-heading{margin:0!important;padding:14px 0 8px 8px!important;"
        "font-size:11.5px!important;font-weight:700!important;color:#0F1419!important;"
        "border-left:3px solid #0F1419!important;}"
        in rendered.html
    )
    assert rendered.html.index("</table></div>") < rendered.html.index("watchlist-marker")


def test_mobile_briefing_borders_continue_through_reduced_heading_and_bottom_spacing() -> None:
    messages = [
        FormattedMessage(
            "Business + Tech",
            "BUSINESS + TECH\n\nA sufficiently long business headline. Supporting context.",
            category="business_tech",
        ),
        FormattedMessage(
            "Finance",
            "FINANCE\n\nA sufficiently long finance headline. Supporting context.",
            category="finance",
        ),
    ]

    rendered = render_minimal_newsletter(messages, "Morning Briefing - 2026-09-01")

    assert (
        ".briefing-index{padding-top:0!important;padding-left:0!important;"
        "padding-right:12px!important;padding-bottom:0!important;}"
        in rendered.html
    )
    assert (
        ".briefing-index-heading{margin:0!important;padding:14px 0 8px 8px!important;"
        "font-size:11.5px!important;font-weight:700!important;color:#0F1419!important;"
        "border-left:3px solid #0F1419!important;}"
        in rendered.html
    )
    assert 'style="padding:8px 0 10px 8px; border-left:3px solid #1565C0;' in rendered.html
    assert 'style="padding:8px 0 20px 8px; border-left:3px solid #7B1FA2;' in rendered.html


def test_mobile_newsletter_matches_story_headline_size_to_body_text() -> None:
    messages = [
        FormattedMessage(
            "Business + Tech",
            "BUSINESS + TECH\n\nA sufficiently long business headline. Supporting context.",
            category="business_tech",
        )
    ]

    rendered = render_minimal_newsletter(messages, "Morning Briefing - 2026-09-01")

    assert '<p class="story-headline" style="margin:0 0 14px; font-size:19px;' in rendered.html
    assert ".story-headline{font-size:14px!important;}" in rendered.html
    assert '<p style="margin:0; font-size:14px; line-height:1.6;' in rendered.html


def test_watchlist_appears_immediately_after_headline_index() -> None:
    messages = [
        FormattedMessage(
            "Business + Tech",
            "BUSINESS + TECH\n\nA sufficiently long business headline. Supporting context.",
            category="business_tech",
        )
    ]
    watchlist_html = '<div id="watchlist-marker">Watchlist content</div>'

    rendered = render_minimal_newsletter(
        messages,
        "Morning Briefing - 2026-09-01",
        watchlist_html=watchlist_html,
    )
    index_start = rendered.html.index("In This Briefing")
    index_end = rendered.html.index("</table></div>", index_start) + len("</table></div>")

    assert rendered.html[index_end:].startswith(watchlist_html)
    assert rendered.html.index('border-bottom:1px solid #1565C0', index_end) > index_end


def test_watchlist_uses_compact_grid_news_rows_and_subdued_status() -> None:
    quotes = {
        "AAPL": EndOfDayQuote("AAPL", "2026-08-31", 205.00, 202.47, "Tiingo"),
        "COST": EndOfDayQuote("COST", "2026-08-31", 935.89, 947.83, "Tiingo"),
        "NVO": EndOfDayQuote("NVO", "2026-08-31", 45.16, 44.27, "Tiingo"),
    }
    stories = [
        watchlist_news.WatchlistStory(
            "NVO",
            summary="Novo Nordisk reported a material company update.",
        )
    ]

    _plain, html = render_watchlist_section(
        quotes,
        stories,
        gate_state="DISABLED",
    )

    assert "<b>Watchlist</b>" in html
    assert html.count('width="50%"') >= 2
    assert "No verified news today." not in html
    assert "Novo Nordisk reported a material company update." in html
    assert "Watchlist evaluation disabled." in html
    assert "font-style:italic" in html


def test_watchlist_renders_filing_meaning_before_secondary_sec_metadata() -> None:
    filing = Filing(
        "0000353278",
        "0000353278-26-000023",
        "6-K",
        date(2026, 8, 4),
        datetime(2026, 8, 4, 21, 23, tzinfo=timezone.utc),
        "caq22026.htm",
        headline="Novo Nordisk raised its 2026 sales and profit outlook.",
        event_key="financial_results_outlook",
    )
    story = watchlist_news.WatchlistStory("NVO", disclosures=(filing,))

    plain, html = render_watchlist_section(
        {"NVO": EndOfDayQuote("NVO", "2026-08-31", 45.16, 44.27, "Tiingo")},
        [story],
    )

    for rendered in (plain, html):
        assert "Novo Nordisk raised its 2026 sales and profit outlook." in rendered
        assert "6-K · 17:23 ET" in rendered
        assert "material filing" not in rendered
        assert "6-K accepted" not in rendered


def test_email_settings_deduplicate_recipients(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GMAIL_SMTP_HOST", "smtp.gmail.com")
    monkeypatch.setenv("GMAIL_SMTP_PORT", "587")
    monkeypatch.setenv("GMAIL_SMTP_USERNAME", "sender@example.com")
    monkeypatch.setenv("GMAIL_SMTP_APP_PASSWORD", "secret")
    monkeypatch.setenv("EMAIL_FROM", "sender@example.com")
    monkeypatch.setenv("EMAIL_TO", "one@example.com, two@example.com, one@example.com")

    settings = email_settings_from_env()

    assert settings.recipients == ("one@example.com", "two@example.com")


def test_smtp_sender_reports_acceptance() -> None:
    settings = smtp_settings()

    outcome = send_email(settings, "to@example.com", "Subject", "Plain", "<p>HTML</p>", smtp_factory=lambda *_args: FakeSMTP())

    assert outcome.state == "smtp_accepted"


def test_smtp_dns_failure_is_recorded_without_raising() -> None:
    outcome = send_email(
        smtp_settings(), "to@example.com", "Subject", "Plain", "<p>HTML</p>",
        smtp_factory=lambda *_args: (_ for _ in ()).throw(socket.gaierror("dns")),
    )

    assert outcome == RecipientOutcome("to@example.com", "failed", "dns_failure")


def test_smtp_connection_refusal_has_stable_error_code() -> None:
    outcome = send_email(
        smtp_settings(), "to@example.com", "Subject", "Plain", "<p>HTML</p>",
        smtp_factory=lambda *_args: (_ for _ in ()).throw(ConnectionRefusedError("refused")),
    )

    assert outcome == RecipientOutcome("to@example.com", "failed", "connection_refused")


def test_smtp_tls_handshake_failure_is_recorded() -> None:
    outcome = send_email(
        smtp_settings(), "to@example.com", "Subject", "Plain", "<p>HTML</p>",
        smtp_factory=lambda *_args: StartTlsFailsSMTP(),
    )

    assert outcome == RecipientOutcome("to@example.com", "failed", "tls_handshake_failed")


def test_connection_reset_after_data_is_indeterminate() -> None:
    outcome = send_email(
        smtp_settings(), "to@example.com", "Subject", "Plain", "<p>HTML</p>",
        smtp_factory=lambda *_args: DataResetSMTP(),
    )

    assert outcome == RecipientOutcome("to@example.com", "indeterminate", "network_error")


def test_connection_reset_before_data_is_retry_safe_failure() -> None:
    outcome = send_email(
        smtp_settings(), "to@example.com", "Subject", "Plain", "<p>HTML</p>",
        smtp_factory=lambda *_args: MailResetSMTP(),
    )

    assert outcome == RecipientOutcome("to@example.com", "failed", "network_error")


def test_state_store_reuses_local_date_edition(tmp_path: Path) -> None:
    store = EmailStateStore(tmp_path / "state.db")

    first = store.prepare_edition("2026-07-25", "Subject", "Plain", "<p>HTML</p>", [("story", "finance")])
    second = store.prepare_edition("2026-07-25", "Changed", "Changed", "<p>Changed</p>", [])

    assert second.edition_id == first.edition_id
    assert second.revision == 1
    assert second.plain_text == "Plain"


def test_state_store_creates_isolated_test_revisions(tmp_path: Path) -> None:
    store = EmailStateStore(tmp_path / "state.db")
    original = store.prepare_edition("2026-07-25", "Subject", "Original", "<p>Original</p>", [("original", "finance")])
    revision = store.prepare_test_revision("2026-07-25", "Subject", "Rebuilt", "<p>Rebuilt</p>", [("rebuilt", "finance")])
    another_revision = store.prepare_test_revision("2026-07-25", "Subject", "Rebuilt again", "<p>Again</p>", [])

    assert (original.revision, revision.revision, another_revision.revision) == (1, 2, 3)
    assert store.edition(original.edition_id).plain_text == "Original"  # type: ignore[union-attr]
    assert revision.subject == "[TEST] Subject (revision 2)"
    assert revision.edition_kind == "test"
    assert original.edition_kind == "production"
    store.record_delivery(revision.edition_id, RecipientOutcome("to@example.com", "smtp_accepted"))
    assert store.delivery_outcomes(original.edition_id) == []
    assert store.delivery_outcomes(revision.edition_id)[0].state == "smtp_accepted"


def test_state_store_migrates_v1_database_and_preserves_quote_cache(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE editions (
            id INTEGER PRIMARY KEY, local_date TEXT NOT NULL UNIQUE,
            subject TEXT NOT NULL, plain_text TEXT NOT NULL, html TEXT NOT NULL,
            state TEXT NOT NULL, article_window_end TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE edition_stories (
            edition_id INTEGER NOT NULL REFERENCES editions(id) ON DELETE CASCADE,
            story_id TEXT NOT NULL, category TEXT NOT NULL, position INTEGER NOT NULL,
            PRIMARY KEY (edition_id, story_id)
        );
        CREATE TABLE deliveries (
            edition_id INTEGER NOT NULL REFERENCES editions(id) ON DELETE CASCADE,
            recipient TEXT NOT NULL, state TEXT NOT NULL, error_code TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL,
            PRIMARY KEY (edition_id, recipient)
        );
        CREATE TABLE quote_cache (
            ticker TEXT PRIMARY KEY, close_date TEXT NOT NULL, close_price REAL NOT NULL,
            previous_close REAL NOT NULL, provider TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        INSERT INTO editions VALUES (1, '2026-07-25', 'Subject', 'Plain', '<p>HTML</p>', 'smtp_accepted', 'x', 'x', 'x');
        INSERT INTO deliveries VALUES (1, 'to@example.com', 'smtp_accepted', '', 'x');
        INSERT INTO quote_cache VALUES ('AAPL', '2026-07-24', 100, 99, 'Tiingo', 'x');
        PRAGMA user_version = 1;
        """
    )
    connection.commit()
    connection.close()

    store = EmailStateStore(path)
    migrated = store.edition(1)

    assert migrated is not None and migrated.revision == 1
    assert store.delivery_outcomes(1)[0].recipient == "to@example.com"
    assert store.cached_quote("AAPL") == ("2026-07-24", 100.0, 99.0, "Tiingo")


def test_first_recipient_acceptance_remains_the_edition_watermark(tmp_path: Path) -> None:
    store = EmailStateStore(tmp_path / "state.db")
    edition = store.prepare_edition("2026-07-25", "Subject", "Plain", "<p>HTML</p>", [])

    store.record_delivery(edition.edition_id, RecipientOutcome("one@example.com", "smtp_accepted"))
    store.record_delivery(edition.edition_id, RecipientOutcome("two@example.com", "failed", "550"))

    assert store.edition(edition.edition_id).state == "smtp_accepted"  # type: ignore[union-attr]


def test_service_records_each_recipient_when_first_send_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = EmailStateStore(tmp_path / "state.db")
    service = mailer_service.EmailService(store)
    edition = store.prepare_edition("2026-07-25", "Subject", "Plain", "<p>HTML</p>", [])
    monkeypatch.setattr(mailer_service, "email_settings_from_env", lambda: smtp_settings(("one@example.com", "two@example.com")))
    calls: list[str] = []

    def fake_send(_settings: EmailSettings, recipient: str, *_args: object, **_kwargs: object) -> RecipientOutcome:
        calls.append(recipient)
        if recipient == "one@example.com":
            raise socket.gaierror("dns")
        return RecipientOutcome(recipient, "smtp_accepted")

    monkeypatch.setattr(mailer_service, "send_email", fake_send)
    outcomes = service.send_edition(edition)

    assert calls == ["one@example.com", "two@example.com"]
    assert [(item.recipient, item.state) for item in outcomes] == [
        ("one@example.com", "failed"),
        ("two@example.com", "smtp_accepted"),
    ]
    assert store.delivery_outcomes(edition.edition_id)[0].state == "failed"


def test_automatic_send_does_not_retry_indeterminate_recipient(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = EmailStateStore(tmp_path / "state.db")
    service = mailer_service.EmailService(store)
    edition = store.prepare_edition("2026-07-25", "Subject", "Plain", "<p>HTML</p>", [])
    store.record_delivery(edition.edition_id, RecipientOutcome("to@example.com", "indeterminate", "network_error"))
    monkeypatch.setattr(mailer_service, "email_settings_from_env", lambda: smtp_settings())
    monkeypatch.setattr(mailer_service, "send_email", lambda *_args, **_kwargs: pytest.fail("must not retry"))

    assert service.send_edition(edition) == [RecipientOutcome("to@example.com", "indeterminate", "network_error")]


def test_confirmed_resend_retries_an_accepted_recipient(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = EmailStateStore(tmp_path / "state.db")
    service = mailer_service.EmailService(store)
    edition = store.prepare_edition("2026-07-25", "Subject", "Plain", "<p>HTML</p>", [])
    store.record_delivery(edition.edition_id, RecipientOutcome("to@example.com", "smtp_accepted"))
    monkeypatch.setattr(mailer_service, "email_settings_from_env", lambda: smtp_settings())
    monkeypatch.setattr(
        mailer_service,
        "send_email",
        lambda _settings, recipient, *_args, **_kwargs: RecipientOutcome(recipient, "smtp_accepted"),
    )

    assert service.resend(edition.edition_id, confirmed=True) == [RecipientOutcome("to@example.com", "smtp_accepted")]


def test_status_reports_failed_not_stranded_sending(tmp_path: Path) -> None:
    store = EmailStateStore(tmp_path / "state.db")
    edition = store.prepare_edition("2026-07-25", "Subject", "Plain", "<p>HTML</p>", [])
    store.record_delivery(edition.edition_id, RecipientOutcome("to@example.com", "failed", "dns_failure"))

    status = mailer_service.EmailService(store).status_lines()[0]
    assert "r1" in status
    assert "to@example.com: failed" in status


def test_email_watchlist_rejects_more_than_ten_entries(tmp_path: Path) -> None:
    path = tmp_path / "watchlist.json"
    entries = ", ".join(
        f'{{"ticker": "T{index}", "display_name": "Ticker {index}", "instrument_type": "stock"}}'
        for index in range(11)
    )
    path.write_text('{"items": [' + entries + ']}', encoding="utf-8")

    with pytest.raises(ValueError, match="between one and ten"):
        load_email_watchlist(path)


def test_shared_watchlist_aliases_are_consistent() -> None:
    validate_shared_watchlist_consistency()


def test_scheduled_email_starts_at_815_am() -> None:
    from zoneinfo import ZoneInfo

    zone = ZoneInfo("America/New_York")
    assert not scheduled_email_is_due(datetime(2026, 7, 25, 8, 19, tzinfo=zone))
    assert scheduled_email_is_due(datetime(2026, 7, 25, 8, 20, tzinfo=zone))
    assert scheduled_email_is_due(datetime(2026, 7, 25, 8, 35, tzinfo=zone))
    assert not scheduled_email_is_due(datetime(2026, 7, 25, 8, 36, tzinfo=zone))


def test_quote_provider_fallback_uses_eodhd_after_tiingo_failure() -> None:
    class EmptyProvider:
        name = "Tiingo"
        token = "token"

        def fetch(self, ticker: str) -> EndOfDayQuote | None:
            return None

    class BackupProvider:
        name = "EODHD"
        token = "token"

        def fetch(self, ticker: str) -> EndOfDayQuote | None:
            return EndOfDayQuote(ticker, "2026-07-24", 110.0, 100.0, self.name)

    quote = fetch_quote_with_fallback(
        "AAPL", (EmptyProvider(), BackupProvider()), retry_seconds=0, expected_close_date=date(2026, 7, 24)
    )

    assert quote is not None
    assert quote.provider == "EODHD"
    assert quote.percent_change == pytest.approx(10.0)


def test_shared_quote_deadline_and_expected_date_are_passed_to_each_concurrent_ticker() -> None:
    seen_deadlines: list[float] = []
    seen_dates: list[date] = []

    def fake_fetcher(ticker: str, *, deadline: float, expected_close_date: date) -> EndOfDayQuote:
        seen_deadlines.append(deadline)
        seen_dates.append(expected_close_date)
        return EndOfDayQuote(ticker, "2026-07-24", 100.0, 99.0, "Fake")

    expected = date(2026, 7, 24)
    quotes_by_ticker = fetch_quotes_with_shared_deadline(
        ("AAPL", "NVO", "META"), retry_seconds=5, fetcher=fake_fetcher, expected_close_date=expected
    )

    assert set(quotes_by_ticker) == {"AAPL", "NVO", "META"}
    assert len(seen_deadlines) == 3
    assert len(set(seen_deadlines)) == 1
    assert seen_dates == [expected, expected, expected]


def test_stale_primary_quote_triggers_fresh_fallback(caplog: pytest.LogCaptureFixture) -> None:
    class StaleProvider:
        name = "Tiingo"
        token = "token"

        def fetch(self, ticker: str) -> EndOfDayQuote:
            return EndOfDayQuote(ticker, "2026-07-30", 100.0, 99.0, self.name)

    class FreshProvider:
        name = "EODHD"
        token = "token"

        def fetch(self, ticker: str) -> EndOfDayQuote:
            return EndOfDayQuote(ticker, "2026-07-31", 101.0, 100.0, self.name)

    quote = fetch_quote_with_fallback(
        "CURI", (StaleProvider(), FreshProvider()), retry_seconds=0, expected_close_date=date(2026, 7, 31)
    )

    assert quote == EndOfDayQuote("CURI", "2026-07-31", 101.0, 100.0, "EODHD")
    assert "watchlist quote rejected" in caplog.text


def test_all_stale_provider_quotes_are_unavailable() -> None:
    class StaleProvider:
        name = "Stale"
        token = "token"

        def fetch(self, ticker: str) -> EndOfDayQuote:
            return EndOfDayQuote(ticker, "2026-07-30", 100.0, 99.0, self.name)

    assert fetch_quote_with_fallback(
        "CURI", (StaleProvider(), StaleProvider()), retry_seconds=0, expected_close_date=date(2026, 7, 31)
    ) is None


def test_expected_quote_close_date_accepts_friday_for_monday_morning() -> None:
    from zoneinfo import ZoneInfo

    assert expected_quote_close_date(datetime(2026, 8, 3, 8, 20, tzinfo=ZoneInfo("America/New_York"))) == date(2026, 7, 31)


def test_expected_quote_close_date_uses_same_day_after_regular_close() -> None:
    from zoneinfo import ZoneInfo

    assert expected_quote_close_date(datetime(2026, 7, 31, 17, 0, tzinfo=ZoneInfo("America/New_York"))) == date(2026, 7, 31)


def test_regular_nyse_market_hours_cover_open_session_only() -> None:
    from zoneinfo import ZoneInfo

    ny = ZoneInfo("America/New_York")
    assert is_regular_nyse_market_hours(datetime(2026, 8, 3, 12, 0, tzinfo=ny))
    assert not is_regular_nyse_market_hours(datetime(2026, 8, 3, 9, 29, tzinfo=ny))
    assert not is_regular_nyse_market_hours(datetime(2026, 8, 3, 16, 0, tzinfo=ny))
    assert not is_regular_nyse_market_hours(datetime(2026, 8, 1, 12, 0, tzinfo=ny))


def test_live_watchlist_quote_uses_finance_snapshot_price() -> None:
    quote = mailer_service._live_watchlist_quote(
        "AAPL", StockQuote("AAPL", price=306.01, previous_close=308.91, provider="Yahoo Finance")
    )

    assert quote is not None
    assert quote.close_price == 306.01
    assert quote.previous_close == 308.91
    assert quote.quote_kind == "live"


def test_live_watchlist_quote_requires_a_previous_close() -> None:
    assert mailer_service._live_watchlist_quote("AAPL", StockQuote("AAPL", price=306.01)) is None


def test_watchlist_renderer_marks_intraday_prices_as_live() -> None:
    plain, html = render_watchlist_section(
        {"AAPL": EndOfDayQuote("AAPL", "2026-08-03", 306.01, 308.91, "Yahoo Finance", "live")}, []
    )

    assert "AAPL: 306.01 (-0.94%) · live" in plain
    assert 'style="color: #d93025;"' in html


def test_quote_cache_rejects_a_stale_close_date(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    store = EmailStateStore(tmp_path / "state.db")
    store.cache_quote("CURI", "2026-07-30", 2.42, 2.49, "Tiingo")

    assert store.cached_quote("CURI", expected_close_date="2026-07-31") is None
    assert "watchlist cached quote rejected" in caplog.text


def test_quote_cache_uses_the_expected_close_date(tmp_path: Path) -> None:
    store = EmailStateStore(tmp_path / "state.db")
    store.cache_quote("CURI", "2026-07-31", 2.42, 2.49, "Tiingo")

    assert store.cached_quote("CURI", expected_close_date="2026-07-31") == ("2026-07-31", 2.42, 2.49, "Tiingo")


def test_expected_quote_close_date_skips_a_market_holiday() -> None:
    from zoneinfo import ZoneInfo

    assert expected_quote_close_date(datetime(2026, 9, 7, 8, 20, tzinfo=ZoneInfo("America/New_York"))) == date(2026, 9, 4)


def test_test_revision_does_not_allow_production_quote_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = EmailStateStore(tmp_path / "state.db")
    service = mailer_service.EmailService(store)
    captured: dict[str, object] = {}

    def fake_render_newsletter(*_args: object, **kwargs: object) -> tuple[RenderedEmail, list[object]]:
        captured.update(kwargs)
        return RenderedEmail("Subject", "Plain", "<p>Plain</p>"), []

    monkeypatch.setattr(service, "render_newsletter", fake_render_newsletter)

    service.prepare_newsletter_edition(
        [], "Header", EnrichmentConfig(), OpenAIBudget(OpenAICostConfig()), test_revision=True
    )

    assert captured["persist_quotes"] is False
    assert captured["allow_cached_quotes"] is False


def test_tiingo_provider_parses_last_available_trading_day(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(quotes, "_get_json", lambda *_args: [
        {"date": "2026-07-23T00:00:00.000Z", "close": 100.0},
        {"date": "2026-07-24T00:00:00.000Z", "close": 105.0},
    ])

    quote = TiingoQuoteProvider("token").fetch("AAPL")

    assert quote == EndOfDayQuote("AAPL", "2026-07-24", 105.0, 100.0, "Tiingo")


def test_eodhd_provider_rejects_malformed_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(quotes, "_get_json", lambda *_args: [{"date": "2026-07-24", "close": 105.0}])

    assert EodhdQuoteProvider("token").fetch("AAPL") is None


def test_missing_quote_keys_name_the_required_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TIINGO_API_KEY", raising=False)
    monkeypatch.delenv("EODHD_API_KEY", raising=False)

    with pytest.raises(ValueError, match="TIINGO_API_KEY, EODHD_API_KEY"):
        quotes.validate_quote_provider_configuration()


def test_render_watchlist_shows_summary_unavailable_headline() -> None:
    story = watchlist_news.WatchlistStory("AAPL", articles=(extracted_article("https://reuters.com/a"),), summary_unavailable=True)

    plain, html = render_watchlist_section({"AAPL": EndOfDayQuote("AAPL", "2026-07-24", 105.0, 100.0, "Tiingo")}, [story])

    assert "Summary unavailable: Company reports earnings" in plain
    assert "(via Reuters)" in plain
    assert "(via <a href=" in html
    assert "https://reuters.com/a" in html


def test_summarize_watchlist_renders_material_event(monkeypatch: pytest.MonkeyPatch) -> None:
    article = extracted_article("https://reuters.com/a")
    response = SimpleNamespace(output_text='{"material": true, "summary": "Earnings beat expectations.", "why_it_matters": "It raises the growth outlook.", "source_urls": ["https://reuters.com/a"]}')
    monkeypatch.setattr(watchlist_news, "request_structured_response", lambda **_kwargs: SimpleNamespace(response=response))

    story = watchlist_news.summarize_watchlist(
        EmailWatchlistEntry("AAPL", "Apple", "stock"), (article,), OpenAIBudget(OpenAICostConfig()),
    )

    assert story.summary == "Earnings beat expectations."
    assert story.why_it_matters == "It raises the growth outlook."
    assert story.articles == (article,)


def test_watchlist_budget_exhaustion_is_visible_and_does_not_render_unevaluated_story(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    article = extracted_article("https://reuters.com/a")
    monkeypatch.setattr(
        watchlist_news,
        "request_structured_response",
        lambda **_kwargs: SimpleNamespace(response=None, budget_exhausted=True),
    )

    story = watchlist_news.summarize_watchlist(
        EmailWatchlistEntry("AAPL", "Apple", "stock"),
        (article,),
        OpenAIBudget(OpenAICostConfig()),
    )
    plain, _html = render_watchlist_section({"AAPL": None}, [story])

    assert story.articles == ()
    assert story.classification_incomplete is True
    assert "Watchlist classification incomplete" in plain
    assert "No verified news today." not in plain


def test_watchlist_discovery_filters_unallowlisted_and_prefers_primary(monkeypatch: pytest.MonkeyPatch) -> None:
    first = extracted_article("https://example.com/first", "Example")
    primary = extracted_article("https://reuters.com/primary", "Reuters")
    monkeypatch.setattr(watchlist_news, "fetch_feed_with_status", lambda _feed: ((first, primary), ""))
    monkeypatch.setattr(watchlist_news, "enrich_article", lambda article, _config: article)
    primary_policy = ExtractionPolicyConfig("reuters", ("reuters.com",), "article_text", "primary")
    monkeypatch.setattr(
        watchlist_news,
        "policy_for_url",
        lambda url, _config: primary_policy if "reuters" in url else None,
    )

    articles, error = watchlist_news.discover_watchlist_articles(
        EmailWatchlistEntry("AAPL", "Apple", "stock"), EnrichmentConfig(),
    )

    assert error == ""
    assert articles == (primary,)


def test_watchlist_discovery_reports_feed_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(watchlist_news, "fetch_feed_with_status", lambda _feed: ((), "timeout"))

    articles, error = watchlist_news.discover_watchlist_articles(
        EmailWatchlistEntry("AAPL", "Apple", "stock"), EnrichmentConfig(),
    )

    assert articles == ()
    assert error == "search_unavailable"


def test_render_watchlist_labels_search_unavailable() -> None:
    story = watchlist_news.WatchlistStory("AAPL", search_error="search_unavailable")

    plain, html = render_watchlist_section({"AAPL": None}, [story])

    assert "No verified news today (partial sources)." in plain
    assert "No verified news today (partial sources)." in html


def test_render_watchlist_required_source_failure_is_not_a_quiet_row() -> None:
    story = watchlist_news.WatchlistStory(
        "AAPL", search_error="search_unavailable", official_retrieval_failed=True
    )

    plain, html = render_watchlist_section({"AAPL": None}, [story])

    warning = "Official filing retrieval failed; no complete news determination was possible."
    assert plain.count(warning) == 1
    assert warning in html
    assert "No verified news today." not in plain


def test_shared_watchlist_discovery_marks_unfinished_ticker_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    entries = (
        EmailWatchlistEntry("AAPL", "Apple", "stock"),
        EmailWatchlistEntry("NVO", "Novo Nordisk", "adr"),
    )

    def fake_discovery(entry: EmailWatchlistEntry, _config: EnrichmentConfig) -> tuple[tuple[Article, ...], str]:
        if entry.ticker == "AAPL":
            return ((extracted_article("https://reuters.com/a"),), "")
        import time
        time.sleep(0.02)
        return (), ""

    monkeypatch.setattr(watchlist_news, "discover_watchlist_articles", fake_discovery)

    discovered = watchlist_news.discover_watchlists_with_shared_deadline(entries, EnrichmentConfig(), timeout_seconds=0.001)

    assert discovered["AAPL"][1] == ""
    assert discovered["NVO"] == ((), "search_unavailable")


def test_cli_raises_on_total_email_delivery_failure() -> None:
    with pytest.raises(Exception, match="No Gmail recipient reached SMTP acceptance"):
        cli.accepted_email_count_or_raise([RecipientOutcome("to@example.com", "failed", "dns_failure")])
