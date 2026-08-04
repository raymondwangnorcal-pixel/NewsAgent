from __future__ import annotations

import re
import os
import json
import threading
import time
from datetime import date, datetime, timezone
from typing import Any, Callable

from news_agent.watchlist.http import BytesResponse, RetryingBytesClient, RetryingJsonClient
from news_agent.watchlist.models import EdgarResult, Filing, SourceState


SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
CONTACT_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def validate_sec_contact_email(value: str | None) -> bool:
    return bool(value and CONTACT_RE.fullmatch(value.strip()))


def sec_contact_email_from_env() -> str:
    value = os.environ.get("SEC_CONTACT_EMAIL", "").strip()
    if not validate_sec_contact_email(value):
        raise ValueError("SEC_CONTACT_EMAIL is required and must be a valid email address for EDGAR.")
    return value


class RateLimiter:
    """Thread-safe fixed-spacing limiter; network calls occur outside database transactions."""

    def __init__(
        self,
        requests_per_second: float = 5.0,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not 0 < requests_per_second <= 10:
            raise ValueError("EDGAR requests_per_second must be greater than zero and at most 10.")
        self._interval = 1.0 / requests_per_second
        self._monotonic = monotonic
        self._sleep = sleep
        self._next_at = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = self._monotonic()
            delay = self._next_at - now
            if delay > 0:
                self._sleep(delay)
                now = self._monotonic()
            self._next_at = max(now, self._next_at) + self._interval


class EdgarClient:
    def __init__(
        self,
        contact_email: str,
        *,
        http: RetryingJsonClient | None = None,
        text_http: RetryingBytesClient | None = None,
        limiter: RateLimiter | None = None,
    ) -> None:
        if not validate_sec_contact_email(contact_email):
            raise ValueError("SEC_CONTACT_EMAIL is missing or invalid.")
        self._user_agent = f"NewsAgent/1.0 ({contact_email.strip()})"
        self._http = http or RetryingJsonClient()
        self._text_http = text_http or RetryingBytesClient()
        self._limiter = limiter or RateLimiter()

    def fetch_filing_document(self, filing: Filing) -> BytesResponse:
        headers = {
            "User-Agent": self._user_agent,
            "Accept-Encoding": "gzip, deflate",
            "Host": "www.sec.gov",
        }
        self._limiter.wait()
        return self._text_http.get(filing.url, headers)

    def fetch_submissions(
        self,
        cik: str,
        supported_forms: tuple[str, ...],
        *,
        etag: str = "",
        last_modified: str = "",
        cached_payload: bytes | None = None,
    ) -> EdgarResult:
        if not re.fullmatch(r"\d{10}", cik):
            raise ValueError("EDGAR CIK must be zero-padded to ten digits.")
        headers = {"User-Agent": self._user_agent, "Accept-Encoding": "gzip, deflate", "Host": "data.sec.gov"}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        self._limiter.wait()
        response = self._http.get(SUBMISSIONS_URL.format(cik=cik), headers)
        if response.status == 304:
            if cached_payload is None:
                return EdgarResult(
                    SourceState.FAILED,
                    error_code="not_modified_without_cache",
                    attempts=response.attempts,
                )
            try:
                cached_data = json.loads(cached_payload.decode("utf-8"))
                cached_filings = parse_recent_filings(cik, cached_data)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
                return EdgarResult(SourceState.FAILED, error_code="invalid_cached_payload", attempts=response.attempts)
            observed_forms = tuple(sorted({filing.form.removesuffix("/A") for filing in cached_filings}))
            return EdgarResult(
                SourceState.NOT_MODIFIED,
                filings=tuple(
                    filing for filing in cached_filings
                    if filing.form.removesuffix("/A") in supported_forms
                ),
                etag=response.headers.get("etag", etag),
                last_modified=response.headers.get("last-modified", last_modified),
                observed_forms=observed_forms,
                attempts=response.attempts,
                payload=cached_payload,
            )
        if response.error_code or response.data is None or response.status != 200:
            return EdgarResult(SourceState.FAILED, error_code=response.error_code or "invalid_response", attempts=response.attempts)
        filings = parse_recent_filings(cik, response.data)
        observed_forms = tuple(sorted({filing.form.removesuffix("/A") for filing in filings}))
        accepted = tuple(filing for filing in filings if filing.form.removesuffix("/A") in supported_forms)
        return EdgarResult(
            SourceState.OK,
            filings=accepted,
            etag=response.headers.get("etag", ""),
            last_modified=response.headers.get("last-modified", ""),
            observed_forms=observed_forms,
            attempts=response.attempts,
            payload=json.dumps(response.data, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        )


def parse_recent_filings(cik: str, payload: dict[str, Any]) -> tuple[Filing, ...]:
    filings = payload.get("filings")
    recent = filings.get("recent") if isinstance(filings, dict) else None
    if not isinstance(recent, dict):
        raise ValueError("SEC submissions response is missing filings.recent.")
    required = ("accessionNumber", "filingDate", "acceptanceDateTime", "form", "primaryDocument")
    columns = {key: recent.get(key) for key in required}
    if any(not isinstance(value, list) for value in columns.values()):
        raise ValueError("SEC submissions recent filing columns are malformed.")
    lengths = {len(value) for value in columns.values() if isinstance(value, list)}
    if len(lengths) != 1:
        raise ValueError("SEC submissions recent filing columns have different lengths.")
    items_column = recent.get("items", [""] * next(iter(lengths), 0))
    if not isinstance(items_column, list) or len(items_column) != next(iter(lengths), 0):
        items_column = [""] * next(iter(lengths), 0)
    descriptions = recent.get("primaryDocDescription", [""] * next(iter(lengths), 0))
    if not isinstance(descriptions, list) or len(descriptions) != next(iter(lengths), 0):
        descriptions = [""] * next(iter(lengths), 0)
    result: list[Filing] = []
    for index in range(next(iter(lengths), 0)):
        form = str(columns["form"][index])
        accepted = _accepted_datetime(str(columns["acceptanceDateTime"][index]))
        item_text = str(items_column[index]).strip()
        result.append(
            Filing(
                cik=cik,
                accession=str(columns["accessionNumber"][index]),
                form=form,
                filing_date=date.fromisoformat(str(columns["filingDate"][index])),
                accepted_at=accepted,
                primary_document=str(columns["primaryDocument"][index]),
                description=str(descriptions[index]).strip(),
                items=tuple(value.strip() for value in item_text.split(",") if value.strip()),
                is_amendment=form.endswith("/A"),
            )
        )
    return tuple(result)


def _accepted_datetime(value: str) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        try:
            return datetime.strptime(value, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
