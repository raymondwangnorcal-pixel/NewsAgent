from __future__ import annotations

import fcntl
import hashlib
import html
import json
import logging
import os
import random
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from news_agent.mailer.models import DeliveryState, EmailEdition, RecipientOutcome


DEFAULT_STATE_PATH = Path("data/email_state.db")
DEFAULT_LOCK_PATH = Path("data/email_state.lock")
SCHEMA_VERSION = 4
_HELD_LOCKS: set[tuple[Path, int]] = set()
logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class EmailStateStore:
    def __init__(self, path: Path = DEFAULT_STATE_PATH) -> None:
        self.path = path

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        self._migrate(connection)
        return connection

    def _migrate(self, connection: sqlite3.Connection) -> None:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise RuntimeError("Email state database was created by a newer NewsAgent version.")
        if version == SCHEMA_VERSION:
            # Keep additive schemas self-healing while the implementation is
            # developed against local fixtures and databases created by earlier
            # iterations.
            self._create_watchlist_schema(connection)
            self._create_newsletter_schema(connection)
            connection.commit()
            return
        if version == 0:
            self._create_schema(connection)
            return
        if version == 1:
            connection.executescript(
                """
                ALTER TABLE edition_stories RENAME TO edition_stories_v1;
                ALTER TABLE deliveries RENAME TO deliveries_v1;
                ALTER TABLE editions RENAME TO editions_v1;
                ALTER TABLE quote_cache RENAME TO quote_cache_v1;
                """
            )
            self._create_schema(connection, commit=False)
            connection.executescript(
                """
                INSERT INTO editions(id, local_date, revision, subject, plain_text, html, state, article_window_end, created_at, updated_at)
                SELECT id, local_date, 1, subject, plain_text, html, state, article_window_end, created_at, updated_at FROM editions_v1;
                INSERT INTO edition_stories(edition_id, story_id, category, position)
                SELECT edition_id, story_id, category, position FROM edition_stories_v1;
                INSERT INTO deliveries(edition_id, recipient, state, error_code, updated_at)
                SELECT edition_id, recipient, state, error_code, updated_at FROM deliveries_v1;
                INSERT INTO quote_cache(ticker, close_date, close_price, previous_close, provider, updated_at)
                SELECT ticker, close_date, close_price, previous_close, provider, updated_at FROM quote_cache_v1;
                DROP TABLE edition_stories_v1;
                DROP TABLE deliveries_v1;
                DROP TABLE editions_v1;
                DROP TABLE quote_cache_v1;
                """
            )
            connection.execute(
                "INSERT OR REPLACE INTO schema_migrations(version, applied_at, backup_path) VALUES (3, ?, '')",
                (_now(),),
            )
            connection.execute(
                "INSERT OR REPLACE INTO schema_migrations(version, applied_at, backup_path) VALUES (4, ?, '')",
                (_now(),),
            )
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            connection.commit()
            return
        if version == 2:
            backup_path = self._backup(connection, version=2)
            connection.execute("ALTER TABLE editions ADD COLUMN edition_kind TEXT NOT NULL DEFAULT 'production'")
            self._create_watchlist_schema(connection)
            self._create_newsletter_schema(connection)
            connection.execute(
                "INSERT INTO schema_migrations(version, applied_at, backup_path) VALUES (3, ?, ?)",
                (_now(), str(backup_path) if backup_path else ""),
            )
            connection.execute(
                "INSERT INTO schema_migrations(version, applied_at, backup_path) VALUES (4, ?, ?)",
                (_now(), str(backup_path) if backup_path else ""),
            )
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            connection.commit()
            return
        if version == 3:
            backup_path = self._backup(connection, version=3)
            self._create_watchlist_schema(connection)
            self._create_newsletter_schema(connection)
            connection.execute(
                "INSERT INTO schema_migrations(version, applied_at, backup_path) VALUES (4, ?, ?)",
                (_now(), str(backup_path) if backup_path else ""),
            )
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            connection.commit()

    def _create_schema(self, connection: sqlite3.Connection, commit: bool = True) -> None:
        connection.executescript(
            """
            CREATE TABLE editions (
                id INTEGER PRIMARY KEY,
                local_date TEXT NOT NULL,
                revision INTEGER NOT NULL DEFAULT 1,
                subject TEXT NOT NULL, plain_text TEXT NOT NULL, html TEXT NOT NULL,
                state TEXT NOT NULL, article_window_end TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                edition_kind TEXT NOT NULL DEFAULT 'production',
                UNIQUE(local_date, revision)
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
            """
        )
        self._create_watchlist_schema(connection)
        self._create_newsletter_schema(connection)
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        if commit:
            connection.commit()

    def _backup(self, connection: sqlite3.Connection, *, version: int) -> Path | None:
        if not self.path.exists() or self.path == Path(":memory:"):
            return None
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_path = self.path.with_name(f"{self.path.name}.v{version}-backup-{timestamp}")
        suffix = 1
        while backup_path.exists():
            backup_path = self.path.with_name(f"{self.path.name}.v{version}-backup-{timestamp}-{suffix}")
            suffix += 1
        backup = sqlite3.connect(backup_path)
        try:
            connection.backup(backup)
        finally:
            backup.close()
        return backup_path

    def _create_watchlist_schema(self, connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL,
                backup_path TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS watchlist_source_cache (
                source_id TEXT NOT NULL,
                discovery_key TEXT NOT NULL,
                briefing_date TEXT NOT NULL,
                state TEXT NOT NULL,
                payload BLOB,
                etag TEXT NOT NULL DEFAULT '',
                last_modified TEXT NOT NULL DEFAULT '',
                retrieved_at TEXT NOT NULL,
                success_at TEXT,
                error_code TEXT NOT NULL DEFAULT '',
                payload_purged_at TEXT,
                PRIMARY KEY (source_id, discovery_key, briefing_date)
            );
            CREATE TABLE IF NOT EXISTS watchlist_daily_cache (
                ticker TEXT NOT NULL,
                briefing_date TEXT NOT NULL,
                content_state TEXT NOT NULL,
                retrieval_state TEXT NOT NULL,
                source_keys_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (ticker, briefing_date)
            );
            CREATE TABLE IF NOT EXISTS watchlist_source_watermarks (
                source_id TEXT NOT NULL,
                discovery_key TEXT NOT NULL,
                successful_through TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (source_id, discovery_key)
            );
            CREATE TABLE IF NOT EXISTS watchlist_documents (
                document_id TEXT PRIMARY KEY,
                ticker TEXT NOT NULL,
                source_id TEXT NOT NULL,
                accession TEXT NOT NULL DEFAULT '',
                form_type TEXT NOT NULL DEFAULT '',
                accepted_at TEXT,
                first_observed_at TEXT NOT NULL,
                canonical_url TEXT NOT NULL,
                content_hash TEXT NOT NULL DEFAULT '',
                body TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                payload_purged_at TEXT
            );
            CREATE TABLE IF NOT EXISTS watchlist_events (
                event_id TEXT PRIMARY KEY,
                ticker TEXT NOT NULL,
                linkage_basis TEXT NOT NULL,
                classifier_version TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS watchlist_event_documents (
                event_id TEXT NOT NULL REFERENCES watchlist_events(event_id) ON DELETE CASCADE,
                document_id TEXT NOT NULL REFERENCES watchlist_documents(document_id) ON DELETE CASCADE,
                PRIMARY KEY (event_id, document_id)
            );
            CREATE TABLE IF NOT EXISTS watchlist_sent_history (
                event_id TEXT NOT NULL,
                ticker TEXT NOT NULL,
                first_delivered_at TEXT NOT NULL,
                edition_id INTEGER NOT NULL REFERENCES editions(id),
                PRIMARY KEY (event_id, ticker)
            );
            CREATE TABLE IF NOT EXISTS watchlist_diagnostics (
                id INTEGER PRIMARY KEY,
                run_id TEXT NOT NULL,
                ticker TEXT NOT NULL,
                briefing_date TEXT NOT NULL,
                source_id TEXT NOT NULL DEFAULT '',
                source_state TEXT NOT NULL DEFAULT '',
                required_source INTEGER NOT NULL DEFAULT 0,
                content_state TEXT NOT NULL DEFAULT '',
                retrieval_state TEXT NOT NULL DEFAULT '',
                diagnostic_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_watchlist_diagnostics_date_ticker
                ON watchlist_diagnostics(briefing_date, ticker);
            DELETE FROM watchlist_diagnostics
            WHERE id NOT IN (
                SELECT MAX(id) FROM watchlist_diagnostics
                GROUP BY ticker, briefing_date, source_id
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_watchlist_one_daily_source_diagnostic
                ON watchlist_diagnostics(ticker, briefing_date, source_id);
            CREATE TABLE IF NOT EXISTS quote_history (
                ticker TEXT NOT NULL,
                trading_date TEXT NOT NULL,
                close_price REAL NOT NULL,
                previous_close REAL NOT NULL,
                provider TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (ticker, trading_date)
            );
            CREATE TABLE IF NOT EXISTS watchlist_gate_windows (
                id INTEGER PRIMARY KEY,
                state TEXT NOT NULL,
                implementation_version TEXT NOT NULL DEFAULT '',
                started_at TEXT,
                ended_at TEXT,
                preflight_json TEXT NOT NULL DEFAULT '{}',
                metrics_json TEXT NOT NULL DEFAULT '{}',
                reasons_json TEXT NOT NULL DEFAULT '[]',
                halt_latched INTEGER NOT NULL DEFAULT 0,
                failure_alert_state TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_watchlist_one_open_gate
                ON watchlist_gate_windows((ended_at IS NULL)) WHERE ended_at IS NULL;
            CREATE TABLE IF NOT EXISTS watchlist_benchmark_events (
                id INTEGER PRIMARY KEY,
                ticker TEXT NOT NULL,
                event_date TEXT NOT NULL,
                source_url TEXT NOT NULL,
                headline TEXT NOT NULL,
                materiality_rationale TEXT NOT NULL,
                provenance TEXT NOT NULL,
                verdict TEXT NOT NULL DEFAULT 'pending',
                found_by_newsagent INTEGER,
                imported_at TEXT NOT NULL,
                reviewed_at TEXT,
                UNIQUE(ticker, event_date, source_url)
            );
            CREATE TABLE IF NOT EXISTS watchlist_adjudications (
                id INTEGER PRIMARY KEY,
                subject_type TEXT NOT NULL,
                subject_id TEXT NOT NULL,
                verdict TEXT NOT NULL,
                reviewer_note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                UNIQUE(subject_type, subject_id)
            );
            CREATE TABLE IF NOT EXISTS watchlist_relationship_reviews (
                review_id TEXT PRIMARY KEY,
                ticker TEXT NOT NULL,
                candidate TEXT NOT NULL,
                proposed_relationship TEXT NOT NULL,
                evidence_url TEXT NOT NULL,
                status TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL,
                reviewed_at TEXT
            );
            """
        )
        row = connection.execute("SELECT id FROM watchlist_gate_windows WHERE ended_at IS NULL").fetchone()
        if row is None:
            now = _now()
            connection.execute(
                "INSERT INTO watchlist_gate_windows(state, created_at, updated_at) VALUES ('DISABLED', ?, ?)",
                (now, now),
            )

    def _create_newsletter_schema(self, connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS newsletter_runs (
                run_id TEXT PRIMARY KEY,
                briefing_date TEXT NOT NULL,
                edition_id INTEGER REFERENCES editions(id) ON DELETE SET NULL,
                pipeline_version TEXT NOT NULL,
                config_hash TEXT NOT NULL,
                deck_target INTEGER NOT NULL,
                candidates_total INTEGER NOT NULL,
                openai_mode TEXT NOT NULL,
                history_update_json TEXT,
                history_applied_at TEXT,
                history_abandoned_at TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS newsletter_candidates (
                candidate_id TEXT PRIMARY KEY,
                story_key TEXT NOT NULL,
                candidate_kind TEXT NOT NULL,
                run_id TEXT NOT NULL REFERENCES newsletter_runs(run_id) ON DELETE CASCADE,
                briefing_date TEXT NOT NULL,
                disposition TEXT NOT NULL CHECK(disposition IN ('selected', 'filtered')),
                filter_stage TEXT NOT NULL DEFAULT '',
                filter_reason_code TEXT NOT NULL DEFAULT '',
                legacy_skip_reason TEXT NOT NULL DEFAULT '',
                review_stratum TEXT NOT NULL,
                headline TEXT,
                category TEXT NOT NULL,
                culture_lane TEXT NOT NULL DEFAULT '',
                canonical_url TEXT,
                all_urls_json TEXT,
                url_hashes_json TEXT NOT NULL DEFAULT '[]',
                sources_json TEXT NOT NULL DEFAULT '[]',
                source_count INTEGER NOT NULL DEFAULT 0,
                summary_excerpt TEXT,
                delivered_paragraph TEXT,
                total_score REAL NOT NULL DEFAULT 0,
                importance REAL NOT NULL DEFAULT 0,
                evidence_score REAL NOT NULL DEFAULT 0,
                quality_score REAL NOT NULL DEFAULT 0,
                content_quality_penalty REAL NOT NULL DEFAULT 0,
                llm_importance INTEGER,
                deck_rank INTEGER,
                selection_phase TEXT NOT NULL DEFAULT '',
                is_update INTEGER NOT NULL DEFAULT 0,
                merged_from_json TEXT NOT NULL DEFAULT '[]',
                excerpt_purged_at TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(run_id, candidate_kind, story_key)
            );
            CREATE INDEX IF NOT EXISTS idx_newsletter_candidates_date_disposition
                ON newsletter_candidates(briefing_date, disposition);
            CREATE INDEX IF NOT EXISTS idx_newsletter_candidates_run_disposition
                ON newsletter_candidates(run_id, disposition);
            CREATE INDEX IF NOT EXISTS idx_newsletter_candidates_story_key
                ON newsletter_candidates(story_key);
            CREATE INDEX IF NOT EXISTS idx_newsletter_candidates_stratum_disposition
                ON newsletter_candidates(review_stratum, disposition);
            CREATE TABLE IF NOT EXISTS newsletter_adjudications (
                id INTEGER PRIMARY KEY,
                subject_type TEXT NOT NULL CHECK(subject_type IN ('sent_story', 'filtered_candidate')),
                subject_id TEXT NOT NULL REFERENCES newsletter_candidates(candidate_id) ON DELETE CASCADE,
                verdict TEXT NOT NULL CHECK(verdict IN ('relevant', 'irrelevant', 'unclear')),
                reason_code TEXT NOT NULL DEFAULT '',
                reviewer_note TEXT,
                label_schema_version TEXT NOT NULL,
                pipeline_version TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(subject_type, subject_id)
            );
            CREATE TABLE IF NOT EXISTS newsletter_manual_examples (
                id INTEGER PRIMARY KEY,
                example_date TEXT NOT NULL,
                headline TEXT,
                source_url TEXT,
                source_url_hash TEXT NOT NULL,
                publisher TEXT NOT NULL,
                expected_category TEXT NOT NULL DEFAULT '',
                why_it_matters TEXT,
                provenance TEXT NOT NULL,
                verdict TEXT NOT NULL DEFAULT 'pending' CHECK(verdict IN ('pending', 'relevant', 'irrelevant', 'unclear')),
                matched_candidate_id TEXT REFERENCES newsletter_candidates(candidate_id) ON DELETE SET NULL,
                match_state TEXT NOT NULL DEFAULT 'unmatched' CHECK(match_state IN ('unmatched', 'matched_sent', 'matched_filtered', 'not_retrieved')),
                matched_by TEXT NOT NULL DEFAULT '' CHECK(matched_by IN ('', 'url', 'manual')),
                pipeline_version TEXT,
                label_schema_version TEXT,
                imported_at TEXT NOT NULL,
                reviewed_at TEXT,
                UNIQUE(example_date, source_url_hash)
            );
            CREATE TABLE IF NOT EXISTS newsletter_review_batches (
                batch_id TEXT PRIMARY KEY,
                pipeline_version TEXT NOT NULL,
                label_schema_version TEXT NOT NULL,
                seed TEXT NOT NULL,
                window_start TEXT NOT NULL,
                window_end TEXT NOT NULL,
                stratum_population_json TEXT NOT NULL,
                stratum_target_json TEXT NOT NULL,
                candidate_ids_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )

    @contextmanager
    def lock(self, path: Path = DEFAULT_LOCK_PATH) -> Iterator[None]:
        lock_key = (path.resolve(), threading.get_ident())
        if lock_key in _HELD_LOCKS:
            yield
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+", encoding="utf-8") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                handle.seek(0)
                pid_text = handle.read().strip()
                if pid_text.isdigit() and _pid_is_alive(int(pid_text)):
                    raise RuntimeError("another build is already running") from exc
                raise RuntimeError("another build is already running") from exc
            handle.seek(0)
            handle.truncate()
            handle.write(str(os.getpid()))
            handle.flush()
            _HELD_LOCKS.add(lock_key)
            try:
                yield
            finally:
                _HELD_LOCKS.discard(lock_key)
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def prepare_edition(
        self,
        local_date: str,
        subject: str,
        plain_text: str,
        html: str,
        story_ids: list[tuple[str, str]],
    ) -> EmailEdition:
        now = _now()
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM editions WHERE local_date = ? AND revision = 1", (local_date,)).fetchone()
            if row is None:
                cursor = connection.execute(
                    """
                    INSERT INTO editions(local_date, subject, plain_text, html, state, article_window_end, created_at, updated_at)
                    VALUES (?, ?, ?, ?, 'prepared', ?, ?, ?)
                    """,
                    (local_date, subject, plain_text, html, now, now, now),
                )
                edition_id = int(cursor.lastrowid)
                connection.executemany(
                    "INSERT INTO edition_stories(edition_id, story_id, category, position) VALUES (?, ?, ?, ?)",
                    [(edition_id, story_id, category, index) for index, (story_id, category) in enumerate(story_ids)],
                )
                row = connection.execute("SELECT * FROM editions WHERE id = ?", (edition_id,)).fetchone()
            return _edition_from_row(row)

    def prepare_newsletter_run(
        self,
        *,
        run_id: str,
        briefing_date: str,
        pipeline_version: str,
        config_hash: str,
        deck_target: int,
        openai_mode: str,
        history_update_json: str | None,
        subject: str,
        plain_text: str,
        html: str,
        story_ids: list[tuple[str, str]],
        candidates: list[dict[str, object]],
    ) -> EmailEdition:
        """Durably pair the production edition, review frame, and history outbox."""
        now = _now()
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM editions WHERE local_date = ? AND revision = 1", (briefing_date,)
            ).fetchone()
            if row is None:
                cursor = connection.execute(
                    """INSERT INTO editions(local_date, subject, plain_text, html, state, article_window_end, created_at, updated_at)
                       VALUES (?, ?, ?, ?, 'prepared', ?, ?, ?)""",
                    (briefing_date, subject, plain_text, html, now, now, now),
                )
                edition_id = int(cursor.lastrowid)
                connection.executemany(
                    "INSERT INTO edition_stories(edition_id, story_id, category, position) VALUES (?, ?, ?, ?)",
                    [(edition_id, story_id, category, index) for index, (story_id, category) in enumerate(story_ids)],
                )
                row = connection.execute("SELECT * FROM editions WHERE id = ?", (edition_id,)).fetchone()
            edition = _edition_from_row(row)
            connection.execute(
                """INSERT INTO newsletter_runs(run_id, briefing_date, edition_id, pipeline_version, config_hash, deck_target,
                   candidates_total, openai_mode, history_update_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (run_id, briefing_date, edition.edition_id, pipeline_version, config_hash, deck_target,
                 len(candidates), openai_mode, history_update_json, now),
            )
            columns = (
                "candidate_id, story_key, candidate_kind, run_id, briefing_date, disposition, filter_stage, filter_reason_code, "
                "legacy_skip_reason, review_stratum, headline, category, culture_lane, canonical_url, all_urls_json, url_hashes_json, "
                "sources_json, source_count, summary_excerpt, delivered_paragraph, total_score, importance, evidence_score, quality_score, "
                "content_quality_penalty, llm_importance, deck_rank, selection_phase, is_update, merged_from_json, created_at"
            )
            placeholders = ", ".join("?" for _ in columns.split(", "))
            for candidate in candidates:
                values = dict(candidate)
                values.update(run_id=run_id, briefing_date=briefing_date, created_at=now)
                connection.execute(
                    f"INSERT INTO newsletter_candidates({columns}) VALUES ({placeholders})",
                    tuple(values.get(column) for column in columns.split(", ")),
                )
            return edition

    def newsletter_history_update(self, edition_id: int) -> tuple[str, str | None, str | None] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT run_id, history_update_json, history_abandoned_at FROM newsletter_runs WHERE edition_id = ? ORDER BY created_at DESC LIMIT 1",
                (edition_id,),
            ).fetchone()
        return (str(row["run_id"]), row["history_update_json"], row["history_abandoned_at"]) if row else None

    def mark_newsletter_history_applied(self, run_id: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE newsletter_runs SET history_applied_at = ?, history_update_json = NULL WHERE run_id = ? AND history_abandoned_at IS NULL",
                (_now(), run_id),
            )

    def newsletter_send_allowed(self, edition_id: int) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT history_update_json, history_applied_at, history_abandoned_at FROM newsletter_runs WHERE edition_id = ? ORDER BY created_at DESC LIMIT 1",
                (edition_id,),
            ).fetchone()
        return row is None or (row["history_abandoned_at"] is None and (row["history_update_json"] is None or row["history_applied_at"] is not None))

    def record_newsletter_label(
        self, candidate_id: str, subject_type: str, verdict: str, reason_code: str, reviewer_note: str, label_schema_version: str
    ) -> None:
        allowed_reasons = {
            "trivial", "duplicate_of_other_story", "wrong_category", "thin_evidence", "stale", "promotional",
            "misleading_framing", "compression_lost_meaning", "gate_too_strict", "evidence_underrated",
            "single_source_but_credible", "classifier_missed_category", "wrongly_merged", "stale_rule_too_aggressive",
        }
        if subject_type not in {"sent_story", "filtered_candidate"} or verdict not in {"relevant", "irrelevant", "unclear"}:
            raise ValueError("Invalid newsletter review label.")
        if verdict != "unclear" and reason_code not in allowed_reasons:
            raise ValueError("A valid reason code is required for conclusive newsletter labels.")
        with self.connect() as connection:
            row = connection.execute(
                "SELECT r.pipeline_version FROM newsletter_candidates c JOIN newsletter_runs r ON r.run_id = c.run_id WHERE c.candidate_id = ?",
                (candidate_id,),
            ).fetchone()
            if row is None:
                raise ValueError("Unknown newsletter candidate.")
            if subject_type == "filtered_candidate":
                batches = connection.execute("SELECT candidate_ids_json FROM newsletter_review_batches WHERE label_schema_version = ?", (label_schema_version,)).fetchall()
                if not any(candidate_id in json.loads(str(batch["candidate_ids_json"])) for batch in batches):
                    raise ValueError("Filtered candidates may be labelled only from a frozen review batch.")
            connection.execute(
                """INSERT INTO newsletter_adjudications(subject_type, subject_id, verdict, reason_code, reviewer_note,
                   label_schema_version, pipeline_version, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (subject_type, candidate_id, verdict, reason_code, reviewer_note, label_schema_version, row["pipeline_version"], _now()),
            )

    def newsletter_label_rows(self, pipeline_version: str | None = None) -> list[dict[str, object]]:
        query = "SELECT a.subject_type, a.verdict, a.pipeline_version, a.label_schema_version, c.category, c.review_stratum FROM newsletter_adjudications a JOIN newsletter_candidates c ON c.candidate_id=a.subject_id"
        args: tuple[object, ...] = ()
        if pipeline_version:
            query += " WHERE a.pipeline_version = ?"
            args = (pipeline_version,)
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(query, args).fetchall()]

    def pending_newsletter_evaluations(self, *, disposition: str | None = None) -> list[dict[str, object]]:
        query = """
            SELECT c.*, r.pipeline_version FROM newsletter_candidates c
            JOIN newsletter_runs r ON r.run_id = c.run_id
            LEFT JOIN newsletter_adjudications a ON a.subject_id = c.candidate_id
            JOIN editions e ON e.id = r.edition_id
            WHERE a.id IS NULL AND e.edition_kind = 'production'
              AND (c.disposition = 'filtered' OR e.state = 'smtp_accepted')
        """
        params: list[object] = []
        if disposition:
            query += " AND c.disposition = ?"
            params.append(disposition)
        # Diagnostics are non-metric work; keep their review order predictable and
        # oldest-first. Frozen randomized batches supply population estimates.
        query += " ORDER BY c.briefing_date ASC, c.created_at ASC, c.candidate_id ASC"
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(query, params).fetchall()]

    def newsletter_pilot_status(self, label_schema_version: str) -> tuple[str | None, int]:
        """Return the newest comparable production version and its eligible days."""
        with self.connect() as connection:
            row = connection.execute("""
                SELECT r.pipeline_version, COUNT(DISTINCT r.briefing_date) AS days
                FROM newsletter_runs r JOIN editions e ON e.id = r.edition_id
                WHERE e.edition_kind = 'production' AND e.state = 'smtp_accepted'
                  AND r.openai_mode != 'off'
                GROUP BY r.pipeline_version ORDER BY MAX(r.created_at) DESC LIMIT 1
            """).fetchone()
        return (str(row["pipeline_version"]), int(row["days"])) if row else (None, 0)

    def create_newsletter_review_batch(self, label_schema_version: str) -> dict[str, object] | None:
        version, days = self.newsletter_pilot_status(label_schema_version)
        if version is None or days < 7:
            return None
        with self.connect() as connection:
            existing = connection.execute("""
                SELECT * FROM newsletter_review_batches WHERE pipeline_version = ?
                  AND label_schema_version = ? ORDER BY created_at DESC LIMIT 1
            """, (version, label_schema_version)).fetchone()
            if existing is not None:
                return dict(existing)
            rows = connection.execute("""
                SELECT c.candidate_id, c.review_stratum, c.briefing_date
                FROM newsletter_candidates c JOIN newsletter_runs r ON r.run_id = c.run_id
                JOIN editions e ON e.id = r.edition_id
                LEFT JOIN newsletter_adjudications a ON a.subject_id = c.candidate_id
                WHERE c.disposition = 'filtered' AND a.id IS NULL AND r.pipeline_version = ?
                  AND r.openai_mode != 'off' AND e.edition_kind = 'production' AND e.state = 'smtp_accepted'
                ORDER BY c.candidate_id
            """, (version,)).fetchall()
            if not rows:
                return None
            grouped: dict[str, list[str]] = {}
            for row in rows:
                grouped.setdefault(str(row["review_stratum"]), []).append(str(row["candidate_id"]))
            population = {key: len(value) for key, value in grouped.items()}
            total = sum(population.values())
            targets = {key: (25 if count / total >= .15 or key in {"near_miss", "mid"} else 10) for key, count in population.items()}
            seed = hashlib.sha256(uuid.uuid4().bytes).hexdigest()
            rng = random.Random(seed)
            chosen: list[str] = []
            for key in sorted(grouped):
                pool = grouped[key]
                chosen.extend(rng.sample(pool, min(len(pool), (targets[key] + 4) // 5)))
            batch = {
                "batch_id": uuid.uuid4().hex, "pipeline_version": version, "label_schema_version": label_schema_version,
                "seed": seed, "window_start": min(str(row["briefing_date"]) for row in rows),
                "window_end": max(str(row["briefing_date"]) for row in rows),
                "stratum_population_json": json.dumps(population, sort_keys=True),
                "stratum_target_json": json.dumps(targets, sort_keys=True),
                "candidate_ids_json": json.dumps(sorted(chosen)), "created_at": _now(),
            }
            connection.execute("""INSERT INTO newsletter_review_batches VALUES
                (:batch_id, :pipeline_version, :label_schema_version, :seed, :window_start, :window_end,
                 :stratum_population_json, :stratum_target_json, :candidate_ids_json, :created_at)""", batch)
            return batch

    def pending_newsletter_batch(self, label_schema_version: str) -> list[dict[str, object]]:
        with self.connect() as connection:
            batches = connection.execute("""SELECT * FROM newsletter_review_batches WHERE label_schema_version = ?
                ORDER BY created_at ASC""", (label_schema_version,)).fetchall()
            for batch in batches:
                ids = json.loads(str(batch["candidate_ids_json"]))
                if not ids:
                    continue
                marks = ",".join("?" for _ in ids)
                rows = connection.execute(f"""SELECT c.*, r.pipeline_version FROM newsletter_candidates c
                    JOIN newsletter_runs r ON r.run_id=c.run_id LEFT JOIN newsletter_adjudications a ON a.subject_id=c.candidate_id
                    WHERE c.candidate_id IN ({marks}) AND a.id IS NULL ORDER BY c.briefing_date, c.candidate_id""", ids).fetchall()
                if rows:
                    return [dict(row) for row in rows]
        return []

    @staticmethod
    def _newsletter_url_hash(url: str) -> str:
        from news_agent.newsletter_review import _normal_url
        return hashlib.sha256(_normal_url(url).encode("utf-8")).hexdigest()

    def import_newsletter_examples(self, items: list[dict[str, str]]) -> int:
        allowed = {"manual_recall", "external_outlet", "reader_report", "historical_case"}
        inserted = 0
        with self.connect() as connection:
            for item in items:
                url = item.get("source_url", "").strip()
                if not url.startswith("https://") or not item.get("headline", "").strip() or not item.get("why_it_matters", "").strip() or item.get("provenance") not in allowed:
                    raise ValueError("Newsletter examples require headline, HTTPS URL, rationale, and independent provenance.")
                cursor = connection.execute("""INSERT OR IGNORE INTO newsletter_manual_examples
                    (example_date, headline, source_url, source_url_hash, publisher, expected_category, why_it_matters, provenance, imported_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""", (item["example_date"], item["headline"], url,
                    self._newsletter_url_hash(url), item.get("publisher", ""), item.get("expected_category", ""),
                    item["why_it_matters"], item["provenance"], _now()))
                inserted += cursor.rowcount
        return inserted

    def pending_newsletter_examples(self) -> list[dict[str, object]]:
        with self.connect() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM newsletter_manual_examples WHERE verdict='pending' ORDER BY example_date, id").fetchall()]

    def candidates_for_example(self, example: dict[str, object]) -> list[dict[str, object]]:
        with self.connect() as connection:
            rows = connection.execute("""SELECT c.*, r.pipeline_version FROM newsletter_candidates c JOIN newsletter_runs r ON r.run_id=c.run_id
                JOIN editions e ON e.id=r.edition_id WHERE r.briefing_date=? AND e.edition_kind='production' AND e.state='smtp_accepted'
                ORDER BY c.candidate_id""", (example["example_date"],)).fetchall()
        hash_value = str(example["source_url_hash"])
        return [dict(row) for row in rows if hash_value in json.loads(str(row["url_hashes_json"]))]

    def review_newsletter_example(self, example_id: int, verdict: str, candidate_id: str | None, label_schema_version: str) -> None:
        if verdict not in {"relevant", "irrelevant", "unclear"}:
            raise ValueError("Invalid newsletter example verdict.")
        with self.connect() as connection:
            version = None
            state = "not_retrieved"
            if candidate_id:
                row = connection.execute("SELECT c.disposition, r.pipeline_version FROM newsletter_candidates c JOIN newsletter_runs r ON r.run_id=c.run_id WHERE c.candidate_id=?", (candidate_id,)).fetchone()
                if row is None: raise ValueError("Unknown newsletter candidate.")
                version, state = str(row["pipeline_version"]), "matched_sent" if row["disposition"] == "selected" else "matched_filtered"
            else:
                example = connection.execute("SELECT example_date FROM newsletter_manual_examples WHERE id=?", (example_id,)).fetchone()
                if example is not None:
                    run = connection.execute("""SELECT r.pipeline_version FROM newsletter_runs r JOIN editions e ON e.id=r.edition_id
                        WHERE r.briefing_date=? AND e.edition_kind='production' AND e.state='smtp_accepted' ORDER BY r.created_at DESC LIMIT 1""", (example["example_date"],)).fetchone()
                    version = str(run["pipeline_version"]) if run else None
            connection.execute("""UPDATE newsletter_manual_examples SET verdict=?, matched_candidate_id=?, match_state=?, matched_by=?,
                pipeline_version=?, label_schema_version=?, reviewed_at=? WHERE id=? AND verdict='pending'""",
                (verdict, candidate_id, state, "url" if candidate_id else "manual", version, label_schema_version, _now(), example_id))

    def export_newsletter_labels(self) -> list[dict[str, object]]:
        with self.connect() as connection:
            return [dict(row) for row in connection.execute("""SELECT a.*, c.briefing_date, c.category, c.review_stratum
                FROM newsletter_adjudications a JOIN newsletter_candidates c ON c.candidate_id=a.subject_id
                ORDER BY c.briefing_date, a.subject_type, a.subject_id""").fetchall()]

    def cleanup_newsletter_retention(self, *, now: datetime | None = None) -> dict[str, int]:
        now = now or datetime.now(timezone.utc)
        def cutoff(days: int) -> str: return (now - timedelta(days=days)).isoformat()
        with self.connect() as connection:
            values: dict[str, int] = {}
            for table, fields, days in (("newsletter_candidates", "headline=NULL, canonical_url=NULL, all_urls_json=NULL, summary_excerpt=NULL, delivered_paragraph=NULL, excerpt_purged_at=?", 30),
                                        ("newsletter_adjudications", "reviewer_note=NULL", 30),
                                        ("newsletter_manual_examples", "headline=NULL, source_url=NULL, why_it_matters=NULL", 30)):
                params = (_now(), cutoff(days)) if table == "newsletter_candidates" else (cutoff(days),)
                condition = "created_at < ? AND excerpt_purged_at IS NULL" if table == "newsletter_candidates" else ("created_at < ? AND reviewer_note IS NOT NULL" if table == "newsletter_adjudications" else "imported_at < ? AND (headline IS NOT NULL OR source_url IS NOT NULL OR why_it_matters IS NOT NULL)")
                cursor = connection.execute(f"UPDATE {table} SET {fields} WHERE {condition}", params)
                values[table] = cursor.rowcount
            connection.execute("DELETE FROM newsletter_adjudications WHERE created_at < ?", (cutoff(365),))
            cursor = connection.execute("""DELETE FROM newsletter_candidates WHERE created_at < ? AND candidate_id NOT IN
                (SELECT subject_id FROM newsletter_adjudications)""", (cutoff(180),))
            values["expired_candidates"] = cursor.rowcount
            values["expired_examples"] = connection.execute(
                "DELETE FROM newsletter_manual_examples WHERE imported_at < ?", (cutoff(365),)
            ).rowcount
            values["expired_batches"] = connection.execute(
                "DELETE FROM newsletter_review_batches WHERE created_at < ?", (cutoff(400),)
            ).rowcount
            values["expired_runs"] = connection.execute("""DELETE FROM newsletter_runs WHERE created_at < ?
                AND run_id NOT IN (SELECT run_id FROM newsletter_candidates)""", (cutoff(400),)).rowcount
            stale = connection.execute("""SELECT run_id, edition_id FROM newsletter_runs
                WHERE briefing_date < ? AND history_update_json IS NOT NULL AND history_applied_at IS NULL AND history_abandoned_at IS NULL""",
                (now.date().isoformat(),)).fetchall()
            for row in stale:
                connection.execute("UPDATE newsletter_runs SET history_update_json=NULL, history_abandoned_at=? WHERE run_id=?", (_now(), row["run_id"]))
                if row["edition_id"] is not None:
                    connection.execute("UPDATE editions SET state='failed', updated_at=? WHERE id=? AND state='prepared'", (_now(), row["edition_id"]))
            values["abandoned_history"] = len(stale)
            return values

    def prepare_test_revision(self, local_date: str, subject: str, plain_text: str, html: str, story_ids: list[tuple[str, str]]) -> EmailEdition:
        now = _now()
        with self.connect() as connection:
            revision = int(connection.execute("SELECT COALESCE(MAX(revision), 0) + 1 FROM editions WHERE local_date = ?", (local_date,)).fetchone()[0])
            subject = f"[TEST] {subject} (revision {revision})"
            cursor = connection.execute(
                "INSERT INTO editions(local_date, revision, subject, plain_text, html, state, article_window_end, created_at, updated_at, edition_kind) VALUES (?, ?, ?, ?, ?, 'prepared', ?, ?, ?, 'test')",
                (local_date, revision, subject, plain_text, html, now, now, now),
            )
            edition_id = int(cursor.lastrowid)
            connection.executemany("INSERT INTO edition_stories(edition_id, story_id, category, position) VALUES (?, ?, ?, ?)", [(edition_id, story_id, category, index) for index, (story_id, category) in enumerate(story_ids)])
            row = connection.execute("SELECT * FROM editions WHERE id = ?", (edition_id,)).fetchone()
            return _edition_from_row(row)

    def record_delivery(self, edition_id: int, outcome: RecipientOutcome) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO deliveries(edition_id, recipient, state, error_code, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(edition_id, recipient) DO UPDATE SET
                  state=excluded.state, error_code=excluded.error_code, updated_at=excluded.updated_at
                """,
                (edition_id, outcome.recipient, outcome.state, outcome.error_code, _now()),
            )
            states = [
                str(row["state"])
                for row in connection.execute(
                    "SELECT state FROM deliveries WHERE edition_id = ?", (edition_id,)
                ).fetchall()
            ]
            state = _edition_delivery_state(states)
            connection.execute(
                "UPDATE editions SET state = ?, updated_at = ? WHERE id = ?",
                (state, _now(), edition_id),
            )
            if outcome.state == "smtp_accepted":
                edition = connection.execute("SELECT edition_kind FROM editions WHERE id = ?", (edition_id,)).fetchone()
                if edition is not None and edition["edition_kind"] == "production":
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO watchlist_sent_history(event_id, ticker, first_delivered_at, edition_id)
                        SELECT story_id, substr(category, 11), ?, edition_id
                        FROM edition_stories
                        WHERE edition_id = ? AND category LIKE 'watchlist:%'
                        """,
                        (_now(), edition_id),
                    )

    def delivery_outcomes(self, edition_id: int) -> list[RecipientOutcome]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT recipient, state, error_code FROM deliveries WHERE edition_id = ? ORDER BY recipient",
                (edition_id,),
            ).fetchall()
        return [RecipientOutcome(row["recipient"], row["state"], row["error_code"]) for row in rows]

    def production_delivery_complete(self, local_date: str, recipients: tuple[str, ...]) -> bool:
        required_recipients = set(recipients)
        if not required_recipients:
            return False
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT e.id AS edition_id, d.recipient
                FROM editions e
                JOIN deliveries d ON d.edition_id = e.id
                WHERE e.local_date = ? AND e.edition_kind = 'production'
                  AND d.state = 'smtp_accepted'
                """,
                (local_date,),
            ).fetchall()
        accepted_by_edition: dict[int, set[str]] = {}
        for row in rows:
            accepted_by_edition.setdefault(int(row["edition_id"]), set()).add(str(row["recipient"]))
        return any(required_recipients.issubset(accepted) for accepted in accepted_by_edition.values())

    def latest_editions(self, limit: int = 10) -> list[EmailEdition]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM editions ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [_edition_from_row(row) for row in rows]

    def edition(self, edition_id: int) -> EmailEdition | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM editions WHERE id = ?", (edition_id,)).fetchone()
        return _edition_from_row(row) if row else None

    def cache_quote(self, ticker: str, close_date: str, close_price: float, previous_close: float, provider: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO quote_cache(ticker, close_date, close_price, previous_close, provider, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(ticker) DO UPDATE SET
                  close_date=excluded.close_date, close_price=excluded.close_price,
                  previous_close=excluded.previous_close, provider=excluded.provider, updated_at=excluded.updated_at
                """,
                (ticker, close_date, close_price, previous_close, provider, _now()),
            )

    def cached_quote(
        self, ticker: str, *, expected_close_date: str | None = None
    ) -> tuple[str, float, float, str] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT close_date, close_price, previous_close, provider FROM quote_cache WHERE ticker = ?",
                (ticker,),
            ).fetchone()
        if row is None:
            return None
        close_date = str(row["close_date"])
        if expected_close_date is not None and close_date != expected_close_date:
            logger.warning(
                "watchlist cached quote rejected: ticker=%s close_date=%s expected_close_date=%s",
                ticker, close_date, expected_close_date,
            )
            return None
        return close_date, float(row["close_price"]), float(row["previous_close"]), str(row["provider"])

    def record_quote_history(
        self,
        ticker: str,
        trading_date: str,
        close_price: float,
        previous_close: float,
        provider: str,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO quote_history(ticker, trading_date, close_price, previous_close, provider, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(ticker, trading_date) DO UPDATE SET
                  close_price=excluded.close_price, previous_close=excluded.previous_close,
                  provider=excluded.provider
                """,
                (ticker, trading_date, close_price, previous_close, provider, _now()),
            )

    def watchlist_event_was_sent(self, event_id: str, ticker: str, *, within_hours: int = 48) -> bool:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=within_hours)).isoformat()
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM watchlist_sent_history
                WHERE event_id = ? AND ticker = ? AND first_delivered_at >= ?
                """,
                (event_id, ticker, cutoff),
            ).fetchone()
        return row is not None

    def gate_state(self) -> str:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT state FROM watchlist_gate_windows WHERE ended_at IS NULL ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return str(row["state"]) if row else "DISABLED"

    def record_activation_preflight(self, implementation_version: str, payload: dict[str, object], passed: bool) -> None:
        now = _now()
        value = dict(payload)
        value["passed"] = passed
        value["implementation_version"] = implementation_version
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE watchlist_gate_windows
                SET implementation_version = ?, preflight_json = ?, updated_at = ?
                WHERE ended_at IS NULL
                """,
                (implementation_version, json.dumps(value, sort_keys=True), now),
            )

    def activate_gate(self, implementation_version: str, confirmed: bool) -> None:
        if not confirmed:
            raise ValueError("Gate A activation requires --confirm.")
        now = _now()
        with self.connect() as connection:
            row = connection.execute(
                "SELECT id, state, implementation_version, preflight_json FROM watchlist_gate_windows WHERE ended_at IS NULL"
            ).fetchone()
            if row is None or row["state"] != "DISABLED":
                raise ValueError("Gate A can be activated only from DISABLED.")
            preflight = json.loads(str(row["preflight_json"]))
            if not preflight.get("passed") or row["implementation_version"] != implementation_version:
                raise ValueError("Gate A activation requires a passing preflight for this implementation version.")
            connection.execute(
                "UPDATE watchlist_gate_windows SET state = 'MEASURING', started_at = ?, updated_at = ? WHERE id = ?",
                (now, now, row["id"]),
            )

    def record_gate_evaluation(self, state: str, metrics: dict[str, object], reasons: list[str]) -> None:
        if state not in {"MEASURING", "PASS", "FAIL"}:
            raise ValueError(f"Invalid evaluated Gate A state: {state}")
        with self.connect() as connection:
            row = connection.execute(
                "SELECT id, state FROM watchlist_gate_windows WHERE ended_at IS NULL"
            ).fetchone()
            if row is None or row["state"] == "DISABLED":
                raise ValueError("Gate A metrics cannot be recorded while evaluation is disabled.")
            connection.execute(
                """
                UPDATE watchlist_gate_windows
                SET state = ?, metrics_json = ?, reasons_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (state, json.dumps(metrics, sort_keys=True), json.dumps(reasons), _now(), row["id"]),
            )

    def record_watchlist_run(
        self,
        run_id: str,
        briefing_date: str,
        records: list[dict[str, object]],
    ) -> str:
        """Persist one production Watchlist evaluation and update Gate A atomically."""
        created_at = _now()
        with self.connect() as connection:
            for record in records:
                ticker = str(record["ticker"])
                source_keys = ["edgar", f"yahoo:{ticker}"]
                connection.execute(
                    """
                    INSERT INTO watchlist_daily_cache(
                        ticker, briefing_date, content_state, retrieval_state,
                        source_keys_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(ticker, briefing_date) DO UPDATE SET
                        content_state=excluded.content_state,
                        retrieval_state=excluded.retrieval_state,
                        source_keys_json=excluded.source_keys_json,
                        updated_at=excluded.updated_at
                    """,
                    (
                        ticker, briefing_date, str(record["content_state"]),
                        str(record["retrieval_state"]), json.dumps(source_keys), created_at, created_at,
                    ),
                )
                diagnostic = {
                    key: value
                    for key, value in record.items()
                    if key not in {"documents", "event_documents"}
                }
                for source_id, source_state, required in (
                    (
                        "edgar",
                        str(record["official_state"]),
                        int(str(record["official_state"]) != "UNSUPPORTED"),
                    ),
                    ("yahoo", str(record["optional_state"]), 0),
                ):
                    connection.execute(
                        """
                        INSERT INTO watchlist_diagnostics(
                            run_id, ticker, briefing_date, source_id, source_state,
                            required_source, content_state, retrieval_state,
                            diagnostic_json, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(ticker, briefing_date, source_id) DO UPDATE SET
                            run_id=excluded.run_id,
                            source_state=excluded.source_state,
                            required_source=excluded.required_source,
                            content_state=excluded.content_state,
                            retrieval_state=excluded.retrieval_state,
                            diagnostic_json=excluded.diagnostic_json,
                            created_at=excluded.created_at
                        """,
                        (
                            run_id, ticker, briefing_date, source_id, source_state, required,
                            str(record["content_state"]), str(record["retrieval_state"]),
                            json.dumps(diagnostic, sort_keys=True), created_at,
                        ),
                    )
                for document in record.get("documents", []):  # type: ignore[union-attr]
                    body = document.get("body")
                    content_hash = hashlib.sha256(str(body).encode("utf-8")).hexdigest() if body else ""
                    connection.execute(
                        """
                        INSERT INTO watchlist_documents(
                            document_id, ticker, source_id, accession, form_type, accepted_at,
                            first_observed_at, canonical_url, content_hash, body, metadata_json, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(document_id) DO UPDATE SET
                            metadata_json=excluded.metadata_json,
                            body=COALESCE(watchlist_documents.body, excluded.body),
                            content_hash=CASE WHEN watchlist_documents.content_hash = ''
                                THEN excluded.content_hash ELSE watchlist_documents.content_hash END
                        """,
                        (
                            str(document["document_id"]), ticker, str(document["source_id"]),
                            str(document.get("accession", "")), str(document.get("form_type", "")),
                            document.get("accepted_at"), created_at, str(document["canonical_url"]),
                            content_hash, body, json.dumps(document.get("metadata", {}), sort_keys=True),
                            created_at,
                        ),
                    )
                label = str(record.get("relationship_label", ""))
                for event_id in record.get("event_ids", []):  # type: ignore[union-attr]
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO watchlist_events(
                            event_id, ticker, linkage_basis, classifier_version, created_at
                        ) VALUES (?, ?, ?, 'entity-map-v1', ?)
                        """,
                        (str(event_id), ticker, label or "official_filing", created_at),
                    )
                for event_id, document_id in record.get("event_documents", []):  # type: ignore[union-attr]
                    connection.execute(
                        "INSERT OR IGNORE INTO watchlist_event_documents(event_id, document_id) VALUES (?, ?)",
                        (str(event_id), str(document_id)),
                    )
            gate = connection.execute(
                "SELECT id, state, started_at FROM watchlist_gate_windows WHERE ended_at IS NULL"
            ).fetchone()
            if gate is None or gate["state"] != "MEASURING":
                return str(gate["state"]) if gate else "DISABLED"
            metrics = self._gate_metrics(connection, str(gate["started_at"]))
            from news_agent.watchlist.gate import evaluate_gate
            from news_agent.watchlist.models import GateMetrics

            state, reasons = evaluate_gate(GateMetrics(**metrics))
            connection.execute(
                """
                UPDATE watchlist_gate_windows
                SET state = ?, metrics_json = ?, reasons_json = ?, updated_at = ? WHERE id = ?
                """,
                (state.value, json.dumps(metrics, sort_keys=True), json.dumps(reasons), created_at, gate["id"]),
            )
            return state.value

    def _gate_metrics(self, connection: sqlite3.Connection, started_at: str) -> dict[str, int]:
        rows = connection.execute(
            """
            SELECT source_state, diagnostic_json FROM watchlist_diagnostics
            WHERE required_source = 1 AND created_at >= ?
            """,
            (started_at,),
        ).fetchall()
        diagnostics = [json.loads(str(row["diagnostic_json"])) for row in rows]
        relationship_rows = connection.execute(
            """
            SELECT verdict FROM watchlist_adjudications
            WHERE subject_type = 'relationship_claim' AND created_at >= ?
              AND verdict IN ('correct', 'incorrect')
            """,
            (started_at,),
        ).fetchall()
        story_rows = connection.execute(
            """
            SELECT verdict FROM watchlist_adjudications
            WHERE subject_type = 'rendered_story' AND created_at >= ?
              AND verdict IN ('relevant', 'irrelevant')
            """,
            (started_at,),
        ).fetchall()
        duplicate_count = int(connection.execute(
            "SELECT COUNT(*) FROM watchlist_adjudications WHERE subject_type = 'duplicate_event' AND verdict = 'duplicate' AND created_at >= ?",
            (started_at,),
        ).fetchone()[0])
        benchmark = connection.execute(
            """
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN found_by_newsagent = 1 THEN 1 ELSE 0 END) AS found
            FROM watchlist_benchmark_events WHERE verdict = 'material'
            """
        ).fetchone()
        return {
            "evaluated_ticker_days": len(rows),
            "required_source_failures": sum(row["source_state"] == "FAILED" for row in rows),
            "eligible_filings": sum(int(item.get("eligible_filings", 0)) for item in diagnostics),
            "processed_filings": sum(int(item.get("processed_filings", 0)) for item in diagnostics),
            "expected_catchup_filings": sum(int(item.get("expected_catchup_filings", 0)) for item in diagnostics),
            "processed_catchup_filings": sum(int(item.get("processed_catchup_filings", 0)) for item in diagnostics),
            "relationship_claims": len(relationship_rows),
            "false_relationship_claims": sum(row["verdict"] == "incorrect" for row in relationship_rows),
            "rendered_stories_reviewed": len(story_rows),
            "irrelevant_stories": sum(row["verdict"] == "irrelevant" for row in story_rows),
            "independent_non_filing_events": int(benchmark["total"] or 0),
            "found_non_filing_events": int(benchmark["found"] or 0),
            "confirmed_duplicate_events": duplicate_count,
        }

    def prepare_gate_failure_alert(self, local_date: str) -> EmailEdition:
        with self.connect() as connection:
            gate = connection.execute(
                "SELECT id, state, metrics_json, reasons_json FROM watchlist_gate_windows WHERE ended_at IS NULL"
            ).fetchone()
            if gate is None or gate["state"] != "FAIL":
                raise ValueError("Gate A failure alert requires a failed gate window.")
            existing = connection.execute(
                "SELECT * FROM editions WHERE edition_kind = 'gate_alert' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if existing is not None and str(existing["subject"]).endswith(f"window {gate['id']}"):
                return _edition_from_row(existing)
            metrics = json.loads(str(gate["metrics_json"]))
            reasons = json.loads(str(gate["reasons_json"]))
            lines = ["Watchlist Gate A failed.", ""]
            for reason in reasons:
                lines.append(f"- {_failure_metric_line(str(reason), metrics)}")
            lines.extend(("", "Scheduled NewsAgent work is halted after this alert.", "Restart: --restart-after-gate-failure --confirm"))
            plain_text = "\n".join(lines) + "\n"
            rendered_html = "<html><body><pre>" + html.escape(plain_text) + "</pre></body></html>"
            revision = int(connection.execute(
                "SELECT COALESCE(MAX(revision), 0) + 1 FROM editions WHERE local_date = ?", (local_date,)
            ).fetchone()[0])
            subject = f"Watchlist Gate A failed — window {gate['id']}"
            now = _now()
            cursor = connection.execute(
                """
                INSERT INTO editions(
                    local_date, revision, subject, plain_text, html, state,
                    article_window_end, created_at, updated_at, edition_kind
                ) VALUES (?, ?, ?, ?, ?, 'prepared', ?, ?, ?, 'gate_alert')
                """,
                (local_date, revision, subject, plain_text, rendered_html, now, now, now),
            )
            row = connection.execute("SELECT * FROM editions WHERE id = ?", (cursor.lastrowid,)).fetchone()
            return _edition_from_row(row)

    def record_failure_alert_terminal(self, outcome: str) -> None:
        if outcome not in {"smtp_accepted", "failed", "indeterminate"}:
            raise ValueError(f"Invalid Gate A failure alert outcome: {outcome}")
        with self.connect() as connection:
            row = connection.execute(
                "SELECT id, state FROM watchlist_gate_windows WHERE ended_at IS NULL"
            ).fetchone()
            if row is None or row["state"] != "FAIL":
                raise ValueError("Gate A failure alert can be finalized only for a failed window.")
            connection.execute(
                """
                UPDATE watchlist_gate_windows
                SET failure_alert_state = ?, halt_latched = 1, updated_at = ? WHERE id = ?
                """,
                (outcome, _now(), row["id"]),
            )

    def scheduled_work_allowed(self) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT halt_latched FROM watchlist_gate_windows WHERE ended_at IS NULL"
            ).fetchone()
        return row is None or not bool(row["halt_latched"])

    def gate_failure_alert_pending(self) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT state, halt_latched, failure_alert_state FROM watchlist_gate_windows WHERE ended_at IS NULL"
            ).fetchone()
        return bool(
            row is not None
            and row["state"] == "FAIL"
            and not row["halt_latched"]
            and not row["failure_alert_state"]
        )

    def gate_recovery_allowed(self) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT state, halt_latched FROM watchlist_gate_windows WHERE ended_at IS NULL"
            ).fetchone()
        return bool(row is not None and row["state"] == "FAIL" and row["halt_latched"])

    def complete_gate_recovery(self, implementation_version: str, *, succeeded: bool) -> None:
        if not succeeded:
            return
        now = _now()
        with self.connect() as connection:
            row = connection.execute(
                "SELECT id, state, halt_latched FROM watchlist_gate_windows WHERE ended_at IS NULL"
            ).fetchone()
            if row is None or row["state"] != "FAIL" or not row["halt_latched"]:
                raise ValueError("Gate A recovery requires a halted failed window.")
            connection.execute(
                "UPDATE watchlist_gate_windows SET ended_at = ?, updated_at = ? WHERE id = ?",
                (now, now, row["id"]),
            )
            connection.execute(
                """
                INSERT INTO watchlist_gate_windows(
                    state, implementation_version, started_at, preflight_json,
                    created_at, updated_at
                ) VALUES ('MEASURING', ?, ?, '{"recovery_health_check": true}', ?, ?)
                """,
                (implementation_version, now, now, now),
            )

    def cache_watchlist_source(
        self,
        source_id: str,
        discovery_key: str,
        briefing_date: str,
        *,
        state: str,
        payload: bytes | None,
        etag: str = "",
        last_modified: str = "",
        error_code: str = "",
    ) -> None:
        with self.connect() as connection:
            existing = connection.execute(
                """
                SELECT payload, success_at FROM watchlist_source_cache
                WHERE source_id = ? AND discovery_key = ? AND briefing_date = ?
                """,
                (source_id, discovery_key, briefing_date),
            ).fetchone()
            usable_payload = payload
            if state == "NOT_MODIFIED" and existing is not None and existing["payload"] is not None:
                usable_payload = bytes(existing["payload"])
            success_at = _now() if state in {"OK", "NOT_MODIFIED"} and usable_payload is not None else None
            connection.execute(
                """
                INSERT INTO watchlist_source_cache(
                    source_id, discovery_key, briefing_date, state, payload, etag,
                    last_modified, retrieved_at, success_at, error_code
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id, discovery_key, briefing_date) DO UPDATE SET
                    state=excluded.state,
                    payload=CASE WHEN excluded.success_at IS NOT NULL THEN excluded.payload ELSE watchlist_source_cache.payload END,
                    etag=CASE WHEN excluded.success_at IS NOT NULL THEN excluded.etag ELSE watchlist_source_cache.etag END,
                    last_modified=CASE WHEN excluded.success_at IS NOT NULL THEN excluded.last_modified ELSE watchlist_source_cache.last_modified END,
                    retrieved_at=excluded.retrieved_at,
                    success_at=CASE WHEN excluded.success_at IS NOT NULL THEN excluded.success_at ELSE watchlist_source_cache.success_at END,
                    error_code=excluded.error_code
                """,
                (
                    source_id, discovery_key, briefing_date, state, usable_payload, etag,
                    last_modified, _now(), success_at, error_code,
                ),
            )

    def successful_watchlist_source(self, source_id: str, discovery_key: str, briefing_date: str) -> bytes | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT payload FROM watchlist_source_cache
                WHERE source_id = ? AND discovery_key = ? AND briefing_date = ?
                  AND success_at IS NOT NULL AND state IN ('OK', 'NOT_MODIFIED')
                """,
                (source_id, discovery_key, briefing_date),
            ).fetchone()
        return bytes(row["payload"]) if row is not None and row["payload"] is not None else None

    def latest_successful_watchlist_source(self, source_id: str, discovery_key: str) -> dict[str, object] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT payload, etag, last_modified, briefing_date, success_at
                FROM watchlist_source_cache
                WHERE source_id = ? AND discovery_key = ? AND success_at IS NOT NULL AND payload IS NOT NULL
                ORDER BY success_at DESC LIMIT 1
                """,
                (source_id, discovery_key),
            ).fetchone()
        if row is None:
            return None
        return {
            "payload": bytes(row["payload"]),
            "etag": str(row["etag"]),
            "last_modified": str(row["last_modified"]),
            "briefing_date": str(row["briefing_date"]),
            "success_at": str(row["success_at"]),
        }

    def source_watermark(self, source_id: str, discovery_key: str) -> str | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT successful_through FROM watchlist_source_watermarks WHERE source_id = ? AND discovery_key = ?",
                (source_id, discovery_key),
            ).fetchone()
        return str(row["successful_through"]) if row else None

    def advance_source_watermark(self, source_id: str, discovery_key: str, successful_through: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO watchlist_source_watermarks(source_id, discovery_key, successful_through, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(source_id, discovery_key) DO UPDATE SET
                    successful_through=excluded.successful_through, updated_at=excluded.updated_at
                """,
                (source_id, discovery_key, successful_through, _now()),
            )

    def import_benchmark_events(self, items: list[dict[str, str]]) -> int:
        with self.connect() as connection:
            before = connection.total_changes
            connection.executemany(
                """
                INSERT OR IGNORE INTO watchlist_benchmark_events(
                    ticker, event_date, source_url, headline, materiality_rationale, provenance, imported_at
                ) VALUES (:ticker, :event_date, :source_url, :headline, :materiality_rationale, :provenance, :imported_at)
                """,
                items,
            )
            return connection.total_changes - before

    def pending_benchmark_events(self) -> list[dict[str, object]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, ticker, event_date, source_url, headline, materiality_rationale, provenance
                FROM watchlist_benchmark_events WHERE verdict = 'pending' ORDER BY event_date, id
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def review_benchmark_event(self, event_id: int, verdict: str, *, found_by_newsagent: bool | None = None) -> None:
        if verdict not in {"material", "not_material", "unclear"}:
            raise ValueError("Benchmark verdict must be material, not_material, or unclear.")
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE watchlist_benchmark_events
                SET verdict = ?, found_by_newsagent = ?, reviewed_at = ?
                WHERE id = ? AND verdict = 'pending'
                """,
                (verdict, None if found_by_newsagent is None else int(found_by_newsagent), _now(), event_id),
            )
            if cursor.rowcount != 1:
                raise ValueError(f"Unknown or already reviewed benchmark event: {event_id}")

    def sync_relationship_reviews(self, items: list[dict[str, str]]) -> int:
        with self.connect() as connection:
            before = connection.total_changes
            connection.executemany(
                """
                INSERT OR IGNORE INTO watchlist_relationship_reviews(
                    review_id, ticker, candidate, proposed_relationship, evidence_url,
                    status, reason, created_at
                ) VALUES (:id, :ticker, :candidate, :proposed_relationship, :evidence_url,
                          :status, :reason, :created_at)
                """,
                items,
            )
            return connection.total_changes - before

    def pending_relationship_reviews(self) -> list[dict[str, object]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT review_id, ticker, candidate, proposed_relationship, evidence_url, reason
                FROM watchlist_relationship_reviews WHERE status = 'pending' ORDER BY review_id
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def pending_relationship_count(self) -> int:
        with self.connect() as connection:
            return int(connection.execute(
                "SELECT COUNT(*) FROM watchlist_relationship_reviews WHERE status = 'pending'"
            ).fetchone()[0])

    def gate_progress_notice(self) -> str:
        with self.connect() as connection:
            gate = connection.execute(
                "SELECT state, started_at FROM watchlist_gate_windows WHERE ended_at IS NULL"
            ).fetchone()
            if gate is None or gate["state"] != "MEASURING" or not gate["started_at"]:
                return ""
            started = datetime.fromisoformat(str(gate["started_at"]))
            days = max(0, (datetime.now(timezone.utc).date() - started.date()).days + 1)
            if days == 0 or days % 7:
                return ""
            non_filing = int(connection.execute(
                "SELECT COUNT(*) FROM watchlist_benchmark_events WHERE verdict = 'material'"
            ).fetchone()[0])
            relationships = int(connection.execute(
                "SELECT COUNT(*) FROM watchlist_adjudications WHERE subject_type = 'relationship_claim' AND created_at >= ?",
                (str(gate["started_at"]),),
            ).fetchone()[0])
            stories = int(connection.execute(
                "SELECT COUNT(*) FROM watchlist_adjudications WHERE subject_type = 'rendered_story' AND created_at >= ?",
                (str(gate["started_at"]),),
            ).fetchone()[0])
        return (
            f"Watchlist evaluation day {days}: remaining reviews — "
            f"non-filing events {max(0, 20 - non_filing)}, "
            f"relationship claims {max(0, 20 - relationships)}, "
            f"rendered stories {max(0, 20 - stories)}."
        )

    def review_relationship(self, review_id: str, verdict: str) -> None:
        if verdict not in {"accepted", "rejected", "unclear"}:
            raise ValueError("Relationship verdict must be accepted, rejected, or unclear.")
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE watchlist_relationship_reviews SET status = ?, reviewed_at = ?
                WHERE review_id = ? AND status = 'pending'
                """,
                (verdict, _now(), review_id),
            )
            if cursor.rowcount != 1:
                raise ValueError(f"Unknown or already reviewed relationship: {review_id}")

    def pending_watchlist_evaluations(self, limit: int = 40) -> list[dict[str, object]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT e.event_id, e.ticker, e.linkage_basis,
                       d.canonical_url, d.metadata_json, e.created_at,
                       EXISTS(
                           SELECT 1 FROM watchlist_adjudications a
                           WHERE a.subject_type = 'rendered_story' AND a.subject_id = e.event_id
                       ) AS story_reviewed,
                       EXISTS(
                           SELECT 1 FROM watchlist_adjudications a
                           WHERE a.subject_type = 'relationship_claim' AND a.subject_id = e.event_id
                       ) AS relationship_reviewed
                FROM watchlist_events e
                LEFT JOIN watchlist_event_documents ed ON ed.event_id = e.event_id
                LEFT JOIN watchlist_documents d ON d.document_id = ed.document_id
                WHERE NOT EXISTS (
                          SELECT 1 FROM watchlist_adjudications a
                          WHERE a.subject_type = 'rendered_story' AND a.subject_id = e.event_id
                      )
                   OR (
                       e.linkage_basis IN ('DIRECT', 'AFFILIATE', 'MANAGED_CAPITAL', 'UNDERLYING_ASSET')
                       AND NOT EXISTS (
                           SELECT 1 FROM watchlist_adjudications a
                           WHERE a.subject_type = 'relationship_claim' AND a.subject_id = e.event_id
                       )
                   )
                GROUP BY e.event_id
                ORDER BY e.created_at, e.event_id
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        result: list[dict[str, object]] = []
        for row in rows:
            metadata = json.loads(str(row["metadata_json"] or "{}"))
            result.append({
                "event_id": str(row["event_id"]),
                "ticker": str(row["ticker"]),
                "relationship_label": str(row["linkage_basis"]),
                "canonical_url": str(row["canonical_url"] or ""),
                "headline": str(metadata.get("title") or metadata.get("description") or row["event_id"]),
                "story_reviewed": bool(row["story_reviewed"]),
                "relationship_reviewed": bool(row["relationship_reviewed"]),
            })
        return result

    def pending_large_move_reviews(self, limit: int = 20) -> list[dict[str, object]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT ticker, briefing_date, diagnostic_json
                FROM watchlist_diagnostics d
                WHERE source_id = 'edgar' AND content_state = 'QUIET'
                  AND NOT EXISTS (
                    SELECT 1 FROM watchlist_adjudications a
                    WHERE a.subject_type = 'large_move'
                      AND a.subject_id = d.ticker || ':' || d.briefing_date
                  )
                ORDER BY briefing_date, ticker
                """
            ).fetchall()
        result = []
        for row in rows:
            diagnostic = json.loads(str(row["diagnostic_json"]))
            move = diagnostic.get("price_move_percent")
            if move is not None and abs(float(move)) >= 3.0:
                result.append({
                    "subject_id": f"{row['ticker']}:{row['briefing_date']}",
                    "ticker": str(row["ticker"]),
                    "briefing_date": str(row["briefing_date"]),
                    "price_move_percent": float(move),
                })
            if len(result) >= limit:
                break
        return result

    def record_adjudication(self, subject_type: str, subject_id: str, verdict: str, note: str = "") -> None:
        allowed = {
            "rendered_story": {"relevant", "irrelevant", "unclear"},
            "relationship_claim": {"correct", "incorrect", "unclear"},
            "duplicate_event": {"duplicate", "not_duplicate", "unclear"},
            "large_move": {"missed", "no_miss", "unclear"},
        }
        if subject_type not in allowed or verdict not in allowed[subject_type]:
            raise ValueError("Invalid Watchlist adjudication type or verdict.")
        with self.connect() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO watchlist_adjudications(
                        subject_type, subject_id, verdict, reviewer_note, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (subject_type, subject_id, verdict, note, _now()),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError(f"Watchlist item already reviewed: {subject_type}:{subject_id}") from exc

    def cleanup_watchlist_retention(self, *, now: datetime | None = None) -> dict[str, int]:
        current = now or datetime.now(timezone.utc)
        body_cutoff = (current - timedelta(days=7)).isoformat()
        metadata_cutoff_date = (current - timedelta(days=365)).date().isoformat()
        metadata_cutoff_time = (current - timedelta(days=365)).isoformat()
        counts: dict[str, int] = {}
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE watchlist_documents
                SET body = NULL, payload_purged_at = ?
                WHERE body IS NOT NULL AND created_at < ?
                  AND document_id NOT IN (
                    SELECT wed.document_id FROM edition_stories es
                    JOIN watchlist_event_documents wed ON wed.event_id = es.story_id
                    JOIN editions e ON e.id = es.edition_id
                    WHERE es.category LIKE 'watchlist:%'
                      AND e.state IN ('prepared', 'sending', 'indeterminate')
                  )
                """,
                (current.isoformat(), body_cutoff),
            )
            counts["document_bodies"] = cursor.rowcount
            cursor = connection.execute(
                """
                UPDATE watchlist_source_cache SET payload = NULL, payload_purged_at = ?
                WHERE payload IS NOT NULL AND retrieved_at < ?
                  AND NOT EXISTS (
                    SELECT 1 FROM editions
                    WHERE state IN ('prepared', 'sending', 'indeterminate')
                  )
                """,
                (current.isoformat(), body_cutoff),
            )
            counts["source_payloads"] = cursor.rowcount
            counts["diagnostics"] = connection.execute(
                "DELETE FROM watchlist_diagnostics WHERE briefing_date < ?", (metadata_cutoff_date,)
            ).rowcount
            counts["quote_history"] = connection.execute(
                "DELETE FROM quote_history WHERE trading_date < ?", (metadata_cutoff_date,)
            ).rowcount
            counts["benchmark_events"] = connection.execute(
                "DELETE FROM watchlist_benchmark_events WHERE imported_at < ?", (metadata_cutoff_time,)
            ).rowcount
            counts["daily_cache"] = connection.execute(
                "DELETE FROM watchlist_daily_cache WHERE briefing_date < ?", (metadata_cutoff_date,)
            ).rowcount
            counts["source_cache_metadata"] = connection.execute(
                """
                DELETE FROM watchlist_source_cache WHERE retrieved_at < ?
                  AND NOT EXISTS (
                    SELECT 1 FROM editions
                    WHERE state IN ('prepared', 'sending', 'indeterminate')
                  )
                """,
                (metadata_cutoff_time,),
            ).rowcount
            counts["relationship_reviews"] = connection.execute(
                "DELETE FROM watchlist_relationship_reviews WHERE created_at < ?", (metadata_cutoff_time,)
            ).rowcount
            counts["adjudications"] = connection.execute(
                "DELETE FROM watchlist_adjudications WHERE created_at < ?", (metadata_cutoff_time,)
            ).rowcount
            old_documents = [
                str(row["document_id"])
                for row in connection.execute(
                    """
                    SELECT d.document_id FROM watchlist_documents d
                    WHERE d.created_at < ? AND d.document_id NOT IN (
                        SELECT wed.document_id FROM edition_stories es
                        JOIN watchlist_event_documents wed ON wed.event_id = es.story_id
                        JOIN editions e ON e.id = es.edition_id
                        WHERE es.category LIKE 'watchlist:%'
                          AND e.state IN ('prepared', 'sending', 'indeterminate')
                    )
                    """,
                    (metadata_cutoff_time,),
                ).fetchall()
            ]
            if old_documents:
                placeholders = ",".join("?" for _ in old_documents)
                connection.execute(
                    f"DELETE FROM watchlist_event_documents WHERE document_id IN ({placeholders})",
                    old_documents,
                )
                counts["documents"] = connection.execute(
                    f"DELETE FROM watchlist_documents WHERE document_id IN ({placeholders})",
                    old_documents,
                ).rowcount
            else:
                counts["documents"] = 0
            counts["event_documents"] = connection.execute(
                """
                DELETE FROM watchlist_event_documents
                WHERE event_id IN (
                    SELECT event_id FROM watchlist_events WHERE created_at < ?
                ) AND event_id NOT IN (
                    SELECT es.story_id FROM edition_stories es
                    JOIN editions e ON e.id = es.edition_id
                    WHERE es.category LIKE 'watchlist:%'
                      AND e.state IN ('prepared', 'sending', 'indeterminate')
                )
                """,
                (metadata_cutoff_time,),
            ).rowcount
            counts["events"] = connection.execute(
                """
                DELETE FROM watchlist_events WHERE created_at < ?
                  AND event_id NOT IN (
                    SELECT es.story_id FROM edition_stories es
                    JOIN editions e ON e.id = es.edition_id
                    WHERE es.category LIKE 'watchlist:%'
                      AND e.state IN ('prepared', 'sending', 'indeterminate')
                  )
                """,
                (metadata_cutoff_time,),
            ).rowcount
            counts["sent_history"] = connection.execute(
                "DELETE FROM watchlist_sent_history WHERE first_delivered_at < ?", (metadata_cutoff_time,)
            ).rowcount
        return counts


def _edition_from_row(row: sqlite3.Row) -> EmailEdition:
    return EmailEdition(
        edition_id=int(row["id"]),
        local_date=str(row["local_date"]),
        revision=int(row["revision"]),
        subject=str(row["subject"]),
        plain_text=str(row["plain_text"]),
        html=str(row["html"]),
        state=str(row["state"]),
        edition_kind=str(row["edition_kind"]) if "edition_kind" in row.keys() else "production",
    )


def _pid_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _edition_delivery_state(states: list[str]) -> DeliveryState:
    """Keep the first SMTP acceptance as the edition watermark."""
    if "smtp_accepted" in states:
        return "smtp_accepted"
    if "indeterminate" in states:
        return "indeterminate"
    if "sending" in states:
        return "sending"
    if "failed" in states:
        return "failed"
    return "prepared"


def _failure_metric_line(reason: str, metrics: dict[str, object]) -> str:
    numerator_denominator = {
        "required_source_retrieval": ("required_source_failures", "evaluated_ticker_days", "maximum 2%"),
        "eligible_filing_processing": ("processed_filings", "eligible_filings", "must equal 100%"),
        "filing_catchup": ("processed_catchup_filings", "expected_catchup_filings", "must equal 100%"),
        "relationship_accuracy": ("false_relationship_claims", "relationship_claims", "maximum 5% false"),
        "story_relevance": ("irrelevant_stories", "rendered_stories_reviewed", "maximum 5% irrelevant"),
        "non_filing_recall": ("found_non_filing_events", "independent_non_filing_events", "minimum 80% recall"),
    }
    if reason == "same_event_duplicates":
        return f"{reason}: {metrics.get('confirmed_duplicate_events', 0)} (threshold 0)"
    keys = numerator_denominator.get(reason)
    if keys is None:
        return f"{reason}: see local diagnostics"
    numerator, denominator, threshold = keys
    return f"{reason}: {metrics.get(numerator, 0)}/{metrics.get(denominator, 0)} ({threshold})"
