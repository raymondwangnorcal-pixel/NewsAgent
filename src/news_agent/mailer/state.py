from __future__ import annotations

import fcntl
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from news_agent.mailer.models import DeliveryState, EmailEdition, RecipientOutcome


DEFAULT_STATE_PATH = Path("data/email_state.db")
DEFAULT_LOCK_PATH = Path("data/email_state.lock")
SCHEMA_VERSION = 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class EmailStateStore:
    def __init__(self, path: Path = DEFAULT_STATE_PATH) -> None:
        self.path = path

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        self._migrate(connection)
        return connection

    def _migrate(self, connection: sqlite3.Connection) -> None:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise RuntimeError("Email state database was created by a newer NewsAgent version.")
        if version == 0:
            connection.executescript(
                """
                CREATE TABLE editions (
                    id INTEGER PRIMARY KEY,
                    local_date TEXT NOT NULL UNIQUE,
                    subject TEXT NOT NULL,
                    plain_text TEXT NOT NULL,
                    html TEXT NOT NULL,
                    state TEXT NOT NULL,
                    article_window_end TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE edition_stories (
                    edition_id INTEGER NOT NULL REFERENCES editions(id) ON DELETE CASCADE,
                    story_id TEXT NOT NULL,
                    category TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    PRIMARY KEY (edition_id, story_id)
                );
                CREATE TABLE deliveries (
                    edition_id INTEGER NOT NULL REFERENCES editions(id) ON DELETE CASCADE,
                    recipient TEXT NOT NULL,
                    state TEXT NOT NULL,
                    error_code TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (edition_id, recipient)
                );
                CREATE TABLE quote_cache (
                    ticker TEXT PRIMARY KEY,
                    close_date TEXT NOT NULL,
                    close_price REAL NOT NULL,
                    previous_close REAL NOT NULL,
                    provider TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            connection.commit()

    @contextmanager
    def lock(self, path: Path = DEFAULT_LOCK_PATH) -> Iterator[None]:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+", encoding="utf-8") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                handle.seek(0)
                pid_text = handle.read().strip()
                if pid_text.isdigit() and _pid_is_alive(int(pid_text)):
                    raise RuntimeError(f"Email delivery is already running (pid {pid_text}).") from exc
                raise RuntimeError("Email delivery lock is held and could not be recovered.") from exc
            handle.seek(0)
            handle.truncate()
            handle.write(str(os.getpid()))
            handle.flush()
            try:
                yield
            finally:
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
            row = connection.execute("SELECT * FROM editions WHERE local_date = ?", (local_date,)).fetchone()
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

    def delivery_outcomes(self, edition_id: int) -> list[RecipientOutcome]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT recipient, state, error_code FROM deliveries WHERE edition_id = ? ORDER BY recipient",
                (edition_id,),
            ).fetchall()
        return [RecipientOutcome(row["recipient"], row["state"], row["error_code"]) for row in rows]

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

    def cached_quote(self, ticker: str) -> tuple[str, float, float, str] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT close_date, close_price, previous_close, provider FROM quote_cache WHERE ticker = ?",
                (ticker,),
            ).fetchone()
        if row is None:
            return None
        return str(row["close_date"]), float(row["close_price"]), float(row["previous_close"]), str(row["provider"])


def _edition_from_row(row: sqlite3.Row) -> EmailEdition:
    return EmailEdition(
        edition_id=int(row["id"]),
        local_date=str(row["local_date"]),
        subject=str(row["subject"]),
        plain_text=str(row["plain_text"]),
        html=str(row["html"]),
        state=str(row["state"]),
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
