from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import html
import re
from typing import Protocol

from news_agent.watchlist.edgar import EdgarClient
from news_agent.watchlist.materiality import filing_is_material, six_k_content_is_material, six_k_metadata_is_material
from news_agent.watchlist.models import EntityMap, Filing, SourceState


@dataclass(frozen=True)
class FilingOutcome:
    ticker: str
    state: SourceState
    filings: tuple[Filing, ...] = ()
    observed_forms: tuple[str, ...] = ()
    error_code: str = ""
    eligible_count: int = 0
    processed_count: int = 0
    catchup_expected: int = 0
    catchup_processed: int = 0
    dispositions: tuple[tuple[str, str], ...] = ()
    filing_bodies: tuple[tuple[str, str], ...] = ()
    current_annual_accession: str | None = None


class FilingState(Protocol):
    def latest_successful_watchlist_source(self, source_id: str, discovery_key: str) -> dict[str, object] | None: ...
    def cache_watchlist_source(
        self, source_id: str, discovery_key: str, briefing_date: str, *, state: str,
        payload: bytes | None, etag: str = "", last_modified: str = "", error_code: str = "",
    ) -> None: ...
    def source_watermark(self, source_id: str, discovery_key: str) -> str | None: ...
    def advance_source_watermark(self, source_id: str, discovery_key: str, successful_through: str) -> None: ...


def discover_material_filings(
    entity_map: EntityMap,
    client: EdgarClient,
    *,
    briefing_date: date,
    lookback_days: int = 2,
    state_store: FilingState | None = None,
    cutoff: datetime | None = None,
    persist_state: bool = True,
) -> dict[str, FilingOutcome]:
    earliest = briefing_date - timedelta(days=lookback_days)
    edition_cutoff = cutoff or datetime.combine(briefing_date, time.max, tzinfo=timezone.utc)
    outcomes: dict[str, FilingOutcome] = {}
    for ticker, entity in entity_map.tickers.items():
        if not entity.filing.required_edgar:
            outcomes[ticker] = FilingOutcome(ticker, SourceState.UNSUPPORTED)
            continue
        cached = state_store.latest_successful_watchlist_source("edgar", entity.cik) if state_store else None
        result = client.fetch_submissions(
            entity.cik,
            entity.filing.observed_forms,
            etag=str(cached.get("etag", "")) if cached else "",
            last_modified=str(cached.get("last_modified", "")) if cached else "",
            cached_payload=cached.get("payload") if cached else None,  # type: ignore[arg-type]
        )
        if state_store and persist_state:
            state_store.cache_watchlist_source(
                "edgar",
                entity.cik,
                briefing_date.isoformat(),
                state=result.state.value,
                payload=result.payload,
                etag=result.etag,
                last_modified=result.last_modified,
                error_code=result.error_code,
            )
        if result.state is not SourceState.OK:
            if result.state is not SourceState.NOT_MODIFIED:
                outcomes[ticker] = FilingOutcome(ticker, result.state, error_code=result.error_code)
                continue
        watermark_text = state_store.source_watermark("edgar", entity.cik) if state_store else None
        try:
            watermark = datetime.fromisoformat(watermark_text) if watermark_text else None
        except ValueError:
            watermark = None
        candidates: list[Filing] = []
        for filing in result.filings:
            accepted_at = filing.accepted_at
            if accepted_at is not None and accepted_at.tzinfo is None:
                accepted_at = accepted_at.replace(tzinfo=timezone.utc)
            after_start = accepted_at > watermark if watermark and accepted_at else filing.filing_date >= earliest
            before_cutoff = accepted_at <= edition_cutoff if accepted_at else filing.filing_date <= briefing_date
            if after_start and before_cutoff:
                candidates.append(filing)
        material: list[Filing] = []
        dispositions: list[tuple[str, str]] = []
        filing_bodies: list[tuple[str, str]] = []
        incomplete_required_document = False
        for filing in candidates:
            determination = filing_is_material(filing)
            if determination is None:
                fetch_document = getattr(client, "fetch_filing_document", None)
                document_result = fetch_document(filing) if fetch_document else None
                body = _document_text(document_result.data) if document_result and document_result.data else ""
                if body:
                    filing_bodies.append((filing.accession, body))
                    determination = six_k_content_is_material(body)
                    reason = "rendered_content" if determination else "excluded_content_not_material"
                else:
                    determination = six_k_metadata_is_material(filing.description)
                    reason = "rendered_metadata_fallback" if determination else "excluded_indeterminate_6k"
                    incomplete_required_document = incomplete_required_document or not determination
            else:
                reason = "rendered" if determination else "excluded_by_form_policy"
            dispositions.append((filing.accession, reason))
            if determination:
                material.append(filing)
        catchup = sum(filing.filing_date < briefing_date for filing in candidates) if watermark else 0
        annual_filings = [
            filing for filing in result.filings
            if entity.filing.annual_form
            and filing.form.removesuffix("/A") == entity.filing.annual_form
        ]
        current_annual = max(
            annual_filings,
            key=lambda filing: (filing.accepted_at or datetime.min.replace(tzinfo=timezone.utc), filing.accession),
            default=None,
        )
        if state_store and persist_state and not incomplete_required_document:
            state_store.advance_source_watermark("edgar", entity.cik, edition_cutoff.isoformat())
        outcomes[ticker] = FilingOutcome(
            ticker,
            SourceState.FAILED if incomplete_required_document else result.state,
            filings=tuple(material),
            observed_forms=result.observed_forms,
            eligible_count=len(candidates),
            processed_count=len(dispositions),
            catchup_expected=catchup,
            catchup_processed=catchup,
            dispositions=tuple(dispositions),
            filing_bodies=tuple(filing_bodies),
            error_code="filing_document_unavailable" if incomplete_required_document else "",
            current_annual_accession=current_annual.accession if current_annual else None,
        )
    return outcomes


def _document_text(payload: bytes) -> str:
    decoded = payload.decode("utf-8", errors="replace")
    without_scripts = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", decoded)
    without_tags = re.sub(r"(?s)<[^>]+>", " ", without_scripts)
    return re.sub(r"\s+", " ", html.unescape(without_tags)).strip()
