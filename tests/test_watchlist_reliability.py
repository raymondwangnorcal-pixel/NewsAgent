from __future__ import annotations

import io
import json
import gzip
import sqlite3
import threading
import urllib.error
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from news_agent.watchlist.benchmark import load_benchmark_candidates
from news_agent.watchlist.edgar import EdgarClient, RateLimiter, parse_recent_filings, validate_sec_contact_email
from news_agent.watchlist.discovery import fetch_distinct_yahoo_feeds, route_discovery_results
from news_agent.watchlist.entity_map import classify_text, load_entity_map
from news_agent.watchlist.gate import GateState, activate_gate, evaluate_gate
from news_agent.watchlist.http import RetryingJsonClient
from news_agent.watchlist.materiality import ethereum_item_is_material, filing_is_material
from news_agent.watchlist.filings import discover_material_filings
from news_agent.watchlist.models import ActivationPreflight, EdgarResult, EntityMap, Filing, GateMetrics, RelationshipLabel, SourceState
from news_agent.mailer.watchlist_news import deserialize_articles, serialize_articles
from news_agent.models import Article
from news_agent.mailer.models import RecipientOutcome
from news_agent.mailer.state import SCHEMA_VERSION, EmailStateStore


def test_entity_map_captures_observed_ethb_and_shop_forms() -> None:
    entity_map = load_entity_map()

    assert len(entity_map.tickers) == 9
    assert entity_map.tickers["ETHB"].filing.required_edgar is True
    assert {"8-K", "10-Q"}.issubset(entity_map.tickers["ETHB"].filing.observed_forms)
    assert entity_map.tickers["SHOP"].filing.legal_regime == "foreign_private_issuer"
    assert entity_map.tickers["SHOP"].filing.observed_forms == ("8-K", "10-Q", "10-K")


def test_entity_classifier_requires_context_for_short_alias() -> None:
    entity = load_entity_map().tickers["NET"]

    rejected = classify_text(entity, "The company reported higher net income.", "subject")
    accepted = classify_text(entity, "Cloudflare (NET) reported an outage.", "subject")

    assert rejected.label is RelationshipLabel.MENTION_ONLY
    assert accepted.label is RelationshipLabel.DIRECT


def test_entity_classifier_does_not_upgrade_officer_quote() -> None:
    entity = load_entity_map().tickers["AAPL"]

    result = classify_text(entity, "Apple CEO commented on the transaction.", "quoted_speaker")

    assert result.label is RelationshipLabel.MENTION_ONLY


def test_bare_brookfield_story_is_eligible_for_bn_with_family_level_wording() -> None:
    entity = load_entity_map().tickers["BN"]

    result = classify_text(entity, "Brookfield announced a material acquisition.", "subject")

    assert result.label is RelationshipLabel.FAMILY_UNRESOLVED
    assert result.matched_name == "Brookfield"


def test_stale_underlying_relationship_does_not_render() -> None:
    entity = load_entity_map().tickers["ETHB"]

    result = classify_text(entity, "Ethereum completed a protocol upgrade.", "subject", today=date(2028, 1, 1))

    assert result.label is RelationshipLabel.MENTION_ONLY
    assert result.reason == "stale_relationship"


def test_parse_recent_filings_and_materiality() -> None:
    payload = {
        "filings": {
            "recent": {
                "accessionNumber": ["0000000001-26-000001", "0000000001-26-000002"],
                "filingDate": ["2026-07-30", "2026-07-29"],
                "acceptanceDateTime": ["2026-07-30T16:01:02Z", "2026-07-29T12:00:00Z"],
                "form": ["8-K", "6-K"],
                "primaryDocument": ["first.htm", "second.htm"],
                "items": ["2.02,9.01", ""],
            }
        }
    }

    filings = parse_recent_filings("0000000001", payload)

    assert filings[0].items == ("2.02", "9.01")
    assert filings[0].url.endswith("/000000000126000001/first.htm")
    assert filing_is_material(filings[0]) is True
    assert filing_is_material(filings[1]) is None


def test_edgar_parses_compact_acceptance_time_and_description() -> None:
    payload = {
        "filings": {"recent": {
            "accessionNumber": ["0000000001-26-000003"],
            "filingDate": ["2026-07-30"],
            "acceptanceDateTime": ["20260730160102"],
            "form": ["6-K"],
            "primaryDocument": ["results.htm"],
            "primaryDocDescription": ["Interim results"],
        }}
    }

    filing = parse_recent_filings("0000000001", payload)[0]

    assert filing.accepted_at == datetime(2026, 7, 30, 16, 1, 2, tzinfo=timezone.utc)
    assert filing.description == "Interim results"


def test_exhibit_only_8k_is_not_material() -> None:
    filing = Filing("0000000001", "0000000001-26-000001", "8-K", date(2026, 7, 30), None, "x.htm", ("9.01",))

    assert filing_is_material(filing) is False


def test_regulation_fd_8k_requires_content_review() -> None:
    filing = Filing(
        "0001776909",
        "0001628280-26-047438",
        "8-K",
        date(2026, 7, 7),
        datetime(2026, 7, 7, 13, 2, tzinfo=timezone.utc),
        "curi-20260707.htm",
        ("7.01", "9.01"),
    )

    assert filing_is_material(filing) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Ethereum completed a major protocol upgrade.", True),
        ("A commentator predicted a higher ether price.", False),
    ],
)
def test_ethereum_materiality_boundary(text: str, expected: bool) -> None:
    assert ethereum_item_is_material(text) is expected


def test_retry_client_honors_retry_after_and_stops_after_three_attempts() -> None:
    sleeps: list[float] = []
    calls = 0

    def failing_opener(_request: object, _timeout: float) -> object:
        nonlocal calls
        calls += 1
        raise urllib.error.HTTPError("https://example.com", 503, "busy", {"Retry-After": "2"}, io.BytesIO())

    result = RetryingJsonClient(opener=failing_opener, sleep=sleeps.append, jitter=lambda: 0).get(
        "https://example.com", {}
    )

    assert calls == 3
    assert sleeps == [2.0, 2.0]
    assert result.error_code == "http_503"
    assert result.attempts == 3


def test_retry_client_returns_valid_json_without_retry() -> None:
    response = SimpleNamespace(status=200, headers={}, read=lambda: b'{"ok": true}')
    result = RetryingJsonClient(opener=lambda *_args: response, sleep=lambda _seconds: None).get(
        "https://example.com", {}
    )

    assert result.data == {"ok": True}
    assert result.attempts == 1


def test_retry_client_decodes_gzip_response() -> None:
    response = SimpleNamespace(
        status=200,
        headers={"Content-Encoding": "gzip"},
        read=lambda: gzip.compress(b'{"ok": true}'),
    )

    result = RetryingJsonClient(opener=lambda *_args: response, sleep=lambda _seconds: None).get(
        "https://example.com", {}
    )

    assert result.data == {"ok": True}


def test_gate_activation_ignores_optional_failures_and_safe_ambiguity() -> None:
    preflight = ActivationPreflight(
        implementation_version="abc",
        entity_map_valid=True,
        sec_contact_valid=True,
        tests_passed=True,
        dry_run_version="abc",
        optional_source_failures=("yahoo:AAPL",),
        unresolved_relationships=("BN:brookfield-family",),
    )

    assert activate_gate(preflight, confirmed=True) is GateState.MEASURING


def test_gate_activation_rejects_required_source_failure() -> None:
    preflight = ActivationPreflight(
        implementation_version="abc",
        entity_map_valid=True,
        sec_contact_valid=True,
        tests_passed=True,
        dry_run_version="abc",
        required_edgar_failures=("ETHB",),
    )

    with pytest.raises(ValueError, match="required_edgar_failed:ETHB"):
        activate_gate(preflight, confirmed=True)


def test_gate_passes_at_exact_thresholds() -> None:
    metrics = GateMetrics(
        evaluated_ticker_days=100,
        required_source_failures=2,
        eligible_filings=10,
        processed_filings=10,
        expected_catchup_filings=2,
        processed_catchup_filings=2,
        relationship_claims=20,
        false_relationship_claims=1,
        rendered_stories_reviewed=20,
        irrelevant_stories=1,
        independent_non_filing_events=20,
        found_non_filing_events=16,
    )

    assert evaluate_gate(metrics) == (GateState.PASS, ())


def test_gate_fails_above_thresholds() -> None:
    metrics = GateMetrics(
        evaluated_ticker_days=99,
        required_source_failures=2,
        eligible_filings=1,
        processed_filings=1,
        relationship_claims=20,
        false_relationship_claims=2,
        rendered_stories_reviewed=20,
        irrelevant_stories=2,
        independent_non_filing_events=20,
        found_non_filing_events=15,
        confirmed_duplicate_events=1,
    )

    state, failures = evaluate_gate(metrics)

    assert state is GateState.FAIL
    assert set(failures) == {
        "required_source_retrieval",
        "relationship_accuracy",
        "same_event_duplicates",
        "story_relevance",
        "non_filing_recall",
    }


def test_benchmark_import_rejects_duplicates(tmp_path: Path) -> None:
    item = {
        "ticker": "AAPL",
        "event_date": "2026-07-30",
        "source_url": "https://example.com/event",
        "headline": "Event",
        "materiality_rationale": "Material product change",
        "provenance": "weekly independent search",
    }
    path = tmp_path / "events.json"
    path.write_text(json.dumps([item, item]), encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate"):
        load_benchmark_candidates(path, {"AAPL"})


@pytest.mark.parametrize(("value", "expected"), [("agent@example.com", True), ("", False), ("secret", False)])
def test_sec_contact_validation(value: str, expected: bool) -> None:
    assert validate_sec_contact_email(value) is expected


def test_discovery_fetches_each_key_once_and_routes_eth_usd_only_to_ethb() -> None:
    entity_map = load_entity_map()
    calls: list[str] = []

    def fetcher(feed: object) -> tuple[tuple[object, ...], str]:
        url = str(getattr(feed, "url"))
        key = url.split("s=", 1)[1].split("&", 1)[0]
        calls.append(key)
        article = SimpleNamespace(url=f"https://example.com/{key}", canonical_url="")
        return (article,), ""  # type: ignore[return-value]

    results = fetch_distinct_yahoo_feeds(entity_map, fetcher)  # type: ignore[arg-type]
    routed = route_discovery_results(entity_map, results)

    assert len(calls) == len(set(calls)) == 10
    assert calls.count("ETH-USD") == 1
    assert {article.url for article in routed["ETHB"]} == {
        "https://example.com/ETHB",
        "https://example.com/ETH-USD",
    }
    assert all(article.url != "https://example.com/ETH-USD" for article in routed["AAPL"])


def test_v2_migration_creates_backup_and_watchlist_schema(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE editions (
            id INTEGER PRIMARY KEY, local_date TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
            subject TEXT NOT NULL, plain_text TEXT NOT NULL, html TEXT NOT NULL,
            state TEXT NOT NULL, article_window_end TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            UNIQUE(local_date, revision)
        );
        CREATE TABLE edition_stories (
            edition_id INTEGER NOT NULL, story_id TEXT NOT NULL, category TEXT NOT NULL,
            position INTEGER NOT NULL, PRIMARY KEY (edition_id, story_id)
        );
        CREATE TABLE deliveries (
            edition_id INTEGER NOT NULL, recipient TEXT NOT NULL, state TEXT NOT NULL,
            error_code TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL,
            PRIMARY KEY (edition_id, recipient)
        );
        CREATE TABLE quote_cache (
            ticker TEXT PRIMARY KEY, close_date TEXT NOT NULL, close_price REAL NOT NULL,
            previous_close REAL NOT NULL, provider TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        PRAGMA user_version = 2;
        """
    )
    connection.close()

    store = EmailStateStore(path)
    assert store.gate_state() == "DISABLED"

    backups = list(tmp_path.glob("state.db.v2-backup-*"))
    assert len(backups) == 1
    with sqlite3.connect(path) as migrated:
        assert migrated.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        tables = {row[0] for row in migrated.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {
        "watchlist_source_cache",
        "watchlist_gate_windows",
        "watchlist_benchmark_events",
        "newsletter_runs",
        "newsletter_candidates",
        "newsletter_adjudications",
        "newsletter_manual_examples",
        "newsletter_review_batches",
    }.issubset(tables)


def test_failed_fetch_does_not_become_successful_daily_cache_entry(tmp_path: Path) -> None:
    store = EmailStateStore(tmp_path / "state.db")

    store.cache_watchlist_source("yahoo", "AAPL", "2026-07-31", state="FAILED", payload=b"bad", error_code="timeout")

    assert store.successful_watchlist_source("yahoo", "AAPL", "2026-07-31") is None


def test_not_modified_requires_and_reuses_existing_body(tmp_path: Path) -> None:
    store = EmailStateStore(tmp_path / "state.db")
    store.cache_watchlist_source("edgar", "AAPL", "2026-07-31", state="NOT_MODIFIED", payload=None)
    assert store.successful_watchlist_source("edgar", "AAPL", "2026-07-31") is None

    store.cache_watchlist_source("edgar", "AAPL", "2026-07-31", state="OK", payload=b"body")
    store.cache_watchlist_source("edgar", "AAPL", "2026-07-31", state="NOT_MODIFIED", payload=None)

    assert store.successful_watchlist_source("edgar", "AAPL", "2026-07-31") == b"body"


def test_edgar_not_modified_rehydrates_cached_filings() -> None:
    payload = json.dumps({
        "filings": {"recent": {
            "accessionNumber": ["0000000001-26-000001"],
            "filingDate": ["2026-07-30"],
            "acceptanceDateTime": ["2026-07-30T16:00:00Z"],
            "form": ["8-K"],
            "primaryDocument": ["event.htm"],
            "items": ["2.02"],
        }}
    }).encode()
    response = SimpleNamespace(status=304, headers={}, read=lambda: b"")
    client = EdgarClient(
        "agent@example.com",
        http=RetryingJsonClient(opener=lambda *_args: response, sleep=lambda _seconds: None),
        limiter=RateLimiter(monotonic=lambda: 0.0, sleep=lambda _seconds: None),
    )

    result = client.fetch_submissions("0000000001", ("8-K",), cached_payload=payload)

    assert result.state is SourceState.NOT_MODIFIED
    assert result.filings[0].accession == "0000000001-26-000001"


def test_article_cache_round_trip() -> None:
    article = Article(
        "Headline", "https://example.com/a", "Example", datetime(2026, 7, 31, tzinfo=timezone.utc),
        extracted_text="Material report", enrichment_status="extracted", feed_categories=("finance",),
    )

    restored = deserialize_articles(serialize_articles((article,)))

    assert restored == (article,)


def test_edgar_watermark_catches_up_after_outage(tmp_path: Path) -> None:
    full_map = load_entity_map()
    entity_map = EntityMap(full_map.schema_version, full_map.generated_at, {"BN": full_map.tickers["BN"]})
    store = EmailStateStore(tmp_path / "state.db")
    store.advance_source_watermark("edgar", full_map.tickers["BN"].cik, "2026-07-28T12:00:00+00:00")
    filing = Filing(
        full_map.tickers["BN"].cik,
        "0001001085-26-000099",
        "6-K",
        date(2026, 7, 29),
        datetime(2026, 7, 29, 15, 0, tzinfo=timezone.utc),
        "results.htm",
        description="Interim results",
    )

    class Client:
        def fetch_submissions(self, *_args: object, **_kwargs: object) -> EdgarResult:
            return EdgarResult(SourceState.OK, (filing,), payload=b'{"filings":{"recent":{}}}')

    outcome = discover_material_filings(
        entity_map,
        Client(),  # type: ignore[arg-type]
        briefing_date=date(2026, 7, 31),
        cutoff=datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc),
        state_store=store,
    )["BN"]

    assert outcome.filings == (filing,)
    assert outcome.catchup_expected == outcome.catchup_processed == 1
    assert outcome.dispositions == ((filing.accession, "rendered_metadata_fallback"),)


def test_six_k_content_is_evaluated_before_metadata_fallback(tmp_path: Path) -> None:
    full_map = load_entity_map()
    entity_map = EntityMap(full_map.schema_version, full_map.generated_at, {"BN": full_map.tickers["BN"]})
    filing = Filing(
        full_map.tickers["BN"].cik,
        "0001001085-26-000100",
        "6-K",
        date(2026, 7, 31),
        datetime(2026, 7, 31, 10, 0, tzinfo=timezone.utc),
        "event.htm",
    )

    class Client:
        def fetch_submissions(self, *_args: object, **_kwargs: object) -> EdgarResult:
            return EdgarResult(SourceState.OK, (filing,), payload=b'{"filings":{"recent":{}}}')

        def fetch_filing_document(self, _filing: Filing) -> object:
            return SimpleNamespace(data=b"<html><body>Quarterly results and updated guidance</body></html>")

    outcome = discover_material_filings(
        entity_map,
        Client(),  # type: ignore[arg-type]
        briefing_date=date(2026, 7, 31),
        cutoff=datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc),
        state_store=EmailStateStore(tmp_path / "state.db"),
    )["BN"]

    assert outcome.state is SourceState.OK
    assert outcome.filings == (filing,)
    assert outcome.dispositions == ((filing.accession, "rendered_content"),)
    assert outcome.filing_bodies[0][1] == "Quarterly results and updated guidance"


def test_regulation_fd_acquisition_is_material_after_content_review(tmp_path: Path) -> None:
    full_map = load_entity_map()
    entity_map = EntityMap(full_map.schema_version, full_map.generated_at, {"CURI": full_map.tickers["CURI"]})
    filing = Filing(
        full_map.tickers["CURI"].cik,
        "0001628280-26-047438",
        "8-K",
        date(2026, 7, 7),
        datetime(2026, 7, 7, 13, 2, tzinfo=timezone.utc),
        "curi-20260707.htm",
        ("7.01", "9.01"),
    )

    class Client:
        def fetch_submissions(self, *_args: object, **_kwargs: object) -> EdgarResult:
            return EdgarResult(SourceState.OK, (filing,), payload=b'{"filings":{"recent":{}}}')

        def fetch_filing_document(self, _filing: Filing) -> object:
            return SimpleNamespace(data=(
                b"<html><body>CuriosityStream announced the completion of its acquisition "
                b"of the remaining ownership interests in its German operations.</body></html>"
            ))

    outcome = discover_material_filings(
        entity_map,
        Client(),  # type: ignore[arg-type]
        briefing_date=date(2026, 7, 7),
        cutoff=datetime(2026, 7, 7, 14, 0, tzinfo=timezone.utc),
        state_store=EmailStateStore(tmp_path / "state.db"),
    )["CURI"]

    assert outcome.state is SourceState.OK
    assert outcome.filings == (filing,)
    assert outcome.dispositions == ((filing.accession, "rendered_content"),)


def test_routine_regulation_fd_notice_is_excluded_after_content_review(tmp_path: Path) -> None:
    full_map = load_entity_map()
    entity_map = EntityMap(full_map.schema_version, full_map.generated_at, {"CURI": full_map.tickers["CURI"]})
    filing = Filing(
        full_map.tickers["CURI"].cik,
        "0001628280-26-047439",
        "8-K",
        date(2026, 7, 7),
        datetime(2026, 7, 7, 13, 3, tzinfo=timezone.utc),
        "curi-conference.htm",
        ("7.01", "9.01"),
    )

    class Client:
        def fetch_submissions(self, *_args: object, **_kwargs: object) -> EdgarResult:
            return EdgarResult(SourceState.OK, (filing,), payload=b'{"filings":{"recent":{}}}')

        def fetch_filing_document(self, _filing: Filing) -> object:
            return SimpleNamespace(data=b"<html><body>The company will participate in an investor conference.</body></html>")

    outcome = discover_material_filings(
        entity_map,
        Client(),  # type: ignore[arg-type]
        briefing_date=date(2026, 7, 7),
        cutoff=datetime(2026, 7, 7, 14, 0, tzinfo=timezone.utc),
        state_store=EmailStateStore(tmp_path / "state.db"),
    )["CURI"]

    assert outcome.state is SourceState.OK
    assert outcome.filings == ()
    assert outcome.dispositions == ((filing.accession, "excluded_content_not_material"),)


def test_unavailable_regulation_fd_document_does_not_fail_or_block_watermark(tmp_path: Path) -> None:
    full_map = load_entity_map()
    entity = full_map.tickers["CURI"]
    entity_map = EntityMap(full_map.schema_version, full_map.generated_at, {"CURI": entity})
    filing = Filing(
        entity.cik,
        "0001628280-26-047440",
        "8-K",
        date(2026, 7, 7),
        datetime(2026, 7, 7, 13, 4, tzinfo=timezone.utc),
        "curi-unavailable.htm",
        ("7.01", "9.01"),
    )

    class Client:
        def fetch_submissions(self, *_args: object, **_kwargs: object) -> EdgarResult:
            return EdgarResult(SourceState.OK, (filing,), payload=b'{"filings":{"recent":{}}}')

        def fetch_filing_document(self, _filing: Filing) -> object:
            return SimpleNamespace(data=None)

    store = EmailStateStore(tmp_path / "state.db")
    cutoff = datetime(2026, 7, 7, 14, 0, tzinfo=timezone.utc)
    outcome = discover_material_filings(
        entity_map,
        Client(),  # type: ignore[arg-type]
        briefing_date=date(2026, 7, 7),
        cutoff=cutoff,
        state_store=store,
    )["CURI"]

    assert outcome.state is SourceState.OK
    assert outcome.filings == ()
    assert outcome.dispositions == ((filing.accession, "excluded_document_unavailable"),)
    assert store.source_watermark("edgar", entity.cik) == cutoff.isoformat()


def test_test_delivery_does_not_write_watchlist_sent_history(tmp_path: Path) -> None:
    store = EmailStateStore(tmp_path / "state.db")
    test_edition = store.prepare_test_revision("2026-07-31", "Subject", "Plain", "<p>x</p>", [("event-1", "watchlist:AAPL")])

    store.record_delivery(test_edition.edition_id, RecipientOutcome("reader@example.com", "smtp_accepted"))

    with store.connect() as connection:
        count = connection.execute("SELECT COUNT(*) FROM watchlist_sent_history").fetchone()[0]
    assert count == 0


def test_production_delivery_starts_watchlist_suppression(tmp_path: Path) -> None:
    store = EmailStateStore(tmp_path / "state.db")
    edition = store.prepare_edition("2026-07-31", "Subject", "Plain", "<p>x</p>", [("event-1", "watchlist:AAPL")])

    store.record_delivery(edition.edition_id, RecipientOutcome("reader@example.com", "smtp_accepted"))

    with store.connect() as connection:
        row = connection.execute("SELECT event_id, ticker, edition_id FROM watchlist_sent_history").fetchone()
    assert tuple(row) == ("event-1", "AAPL", edition.edition_id)


def test_disabled_gate_records_diagnostics_without_gate_metrics(tmp_path: Path) -> None:
    store = EmailStateStore(tmp_path / "state.db")
    state = store.record_watchlist_run("run-1", "2026-07-31", [_run_record()])

    assert state == "DISABLED"
    with store.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM watchlist_diagnostics").fetchone()[0] == 2
        metrics = connection.execute(
            "SELECT metrics_json FROM watchlist_gate_windows WHERE ended_at IS NULL"
        ).fetchone()[0]
    assert json.loads(metrics) == {}


def test_unsupported_source_is_not_marked_required(tmp_path: Path) -> None:
    store = EmailStateStore(tmp_path / "state.db")
    record = _run_record()
    record["official_state"] = "UNSUPPORTED"

    store.record_watchlist_run("run-1", "2026-07-31", [record])

    with store.connect() as connection:
        required = connection.execute(
            "SELECT required_source FROM watchlist_diagnostics WHERE source_id = 'edgar'"
        ).fetchone()[0]
    assert required == 0


def test_build_lock_rejects_contending_thread(tmp_path: Path) -> None:
    store = EmailStateStore(tmp_path / "state.db")
    lock_path = tmp_path / "build.lock"
    acquired = threading.Event()
    release = threading.Event()

    def holder() -> None:
        with store.lock(lock_path):
            acquired.set()
            release.wait(timeout=2)

    thread = threading.Thread(target=holder)
    thread.start()
    assert acquired.wait(timeout=1)
    try:
        with pytest.raises(RuntimeError, match="another build is already running"):
            with store.lock(lock_path):
                pass
    finally:
        release.set()
        thread.join(timeout=2)


def test_weekly_gate_notice_starts_on_day_seven_and_stops_after_pass(tmp_path: Path) -> None:
    store = EmailStateStore(tmp_path / "state.db")
    store.record_activation_preflight("v1", {"passed": True}, True)
    store.activate_gate("v1", confirmed=True)
    with store.connect() as connection:
        connection.execute(
            "UPDATE watchlist_gate_windows SET started_at = ? WHERE ended_at IS NULL",
            ((datetime.now(timezone.utc) - timedelta(days=5)).isoformat(),),
        )
    assert store.gate_progress_notice() == ""
    with store.connect() as connection:
        connection.execute(
            "UPDATE watchlist_gate_windows SET started_at = ? WHERE ended_at IS NULL",
            ((datetime.now(timezone.utc) - timedelta(days=6)).isoformat(),),
        )
    assert "Watchlist evaluation day 7" in store.gate_progress_notice()
    with store.connect() as connection:
        connection.execute("UPDATE watchlist_gate_windows SET state = 'PASS' WHERE ended_at IS NULL")
    assert store.gate_progress_notice() == ""


def test_measurable_gate_failure_prepares_one_alert_and_recovery_resets_window(tmp_path: Path) -> None:
    store = EmailStateStore(tmp_path / "state.db")
    store.record_activation_preflight("v1", {"passed": True}, True)
    store.activate_gate("v1", confirmed=True)
    imported_at = datetime.now(timezone.utc).isoformat()
    store.import_benchmark_events([
        {
            "ticker": "AAPL", "event_date": f"2026-07-{index + 1:02d}",
            "source_url": f"https://example.com/{index}", "headline": f"Event {index}",
            "materiality_rationale": "Material", "provenance": "independent",
            "imported_at": imported_at,
        }
        for index in range(20)
    ])
    for item in store.pending_benchmark_events():
        store.review_benchmark_event(int(item["id"]), "material", found_by_newsagent=True)
    with store.connect() as connection:
        now = datetime.now(timezone.utc).isoformat()
        connection.executemany(
            "INSERT INTO watchlist_adjudications(subject_type, subject_id, verdict, created_at) VALUES ('relationship_claim', ?, 'correct', ?)",
            [(f"r-{index}", now) for index in range(20)],
        )
        connection.executemany(
            "INSERT INTO watchlist_adjudications(subject_type, subject_id, verdict, created_at) VALUES ('rendered_story', ?, 'relevant', ?)",
            [(f"s-{index}", now) for index in range(20)],
        )
    failed = _run_record()
    failed["official_state"] = "FAILED"

    assert store.record_watchlist_run("run-fail", "2026-07-31", [failed]) == "FAIL"
    first = store.prepare_gate_failure_alert("2026-07-31")
    second = store.prepare_gate_failure_alert("2026-07-31")
    assert first.edition_id == second.edition_id
    assert first.edition_kind == "gate_alert"
    assert "--restart-after-gate-failure --confirm" in first.plain_text
    store.record_failure_alert_terminal("failed")
    assert store.scheduled_work_allowed() is False
    assert store.gate_recovery_allowed() is True

    store.complete_gate_recovery("v2", succeeded=True)

    assert store.gate_state() == "MEASURING"
    assert store.scheduled_work_allowed() is True


def test_retention_protects_document_for_active_edition_then_purges(tmp_path: Path) -> None:
    store = EmailStateStore(tmp_path / "state.db")
    record = _run_record()
    store.record_watchlist_run("run-1", "2025-01-01", [record])
    edition = store.prepare_edition("2025-01-01", "Subject", "Plain", "<p>x</p>", [("event-1", "watchlist:AAPL")])
    old = datetime(2025, 1, 1, tzinfo=timezone.utc).isoformat()
    now = datetime(2026, 7, 31, tzinfo=timezone.utc)
    with store.connect() as connection:
        connection.execute("UPDATE watchlist_documents SET created_at = ?", (old,))

    assert store.cleanup_watchlist_retention(now=now)["document_bodies"] == 0
    store.record_delivery(edition.edition_id, RecipientOutcome("reader@example.com", "failed", "timeout"))
    assert store.cleanup_watchlist_retention(now=now)["document_bodies"] == 1


def _run_record() -> dict[str, object]:
    return {
        "ticker": "AAPL",
        "official_state": "OK",
        "optional_state": "OK",
        "content_state": "CONTENT",
        "retrieval_state": "COMPLETE",
        "event_ids": ["event-1"],
        "relationship_label": "DIRECT",
        "relationship_source": "https://example.com/relationship",
        "eligible_filings": 1,
        "processed_filings": 1,
        "expected_catchup_filings": 0,
        "processed_catchup_filings": 0,
        "filing_dispositions": [["accession-1", "rendered"]],
        "price_move_percent": 4.0,
        "documents": [{
            "document_id": "doc-1", "source_id": "editorial",
            "canonical_url": "https://example.com/story", "body": "body", "metadata": {},
        }],
        "event_documents": [("event-1", "doc-1")],
    }
