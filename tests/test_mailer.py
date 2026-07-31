from __future__ import annotations

import socket
import sqlite3
import ssl
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from news_agent import cli
from news_agent.formatting import FormattedMessage
from news_agent.mailer import quotes, service as mailer_service, watchlist_news
from news_agent.mailer.models import EmailSettings, EmailWatchlistEntry, RecipientOutcome
from news_agent.mailer.quotes import EndOfDayQuote, EodhdQuoteProvider, TiingoQuoteProvider, fetch_quote_with_fallback, fetch_quotes_with_shared_deadline
from news_agent.mailer.render import render_minimal_newsletter, render_parity_email, render_watchlist_section
from news_agent.mailer.settings import email_settings_from_env
from news_agent.mailer.smtp import send_email
from news_agent.mailer.state import EmailStateStore
from news_agent.mailer.watchlist import load_email_watchlist, validate_shared_watchlist_consistency
from news_agent.mailer.schedule import scheduled_email_is_due
from news_agent.models import Article, EnrichmentConfig, ExtractionPolicyConfig, OpenAICostConfig
from news_agent.openai_budget import OpenAIBudget


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
    assert "font-family: Helvetica, Arial, sans-serif" in rendered.html


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
    assert 'font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif' in rendered.html
    assert 'padding: 22px 0' in rendered.html


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
    assert revision.subject == "Subject [Test resend #2]"
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

    quote = fetch_quote_with_fallback("AAPL", (EmptyProvider(), BackupProvider()), retry_seconds=0)

    assert quote is not None
    assert quote.provider == "EODHD"
    assert quote.percent_change == pytest.approx(10.0)


def test_shared_quote_deadline_is_passed_to_each_concurrent_ticker() -> None:
    seen_deadlines: list[float] = []

    def fake_fetcher(ticker: str, *, deadline: float) -> EndOfDayQuote:
        seen_deadlines.append(deadline)
        return EndOfDayQuote(ticker, "2026-07-24", 100.0, 99.0, "Fake")

    quotes_by_ticker = fetch_quotes_with_shared_deadline(("AAPL", "NVO", "META"), retry_seconds=5, fetcher=fake_fetcher)

    assert set(quotes_by_ticker) == {"AAPL", "NVO", "META"}
    assert len(seen_deadlines) == 3
    assert len(set(seen_deadlines)) == 1


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

    assert "News search unavailable." in plain
    assert "News search unavailable." in html


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
