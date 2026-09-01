from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
import html
import re
from typing import Protocol

from news_agent.watchlist.edgar import EdgarClient
from news_agent.watchlist.materiality import filing_is_material, official_content_is_material, six_k_metadata_is_material
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
        seen_events: set[tuple[date, str]] = set()
        dispositions: list[tuple[str, str]] = []
        filing_bodies: list[tuple[str, str]] = []
        incomplete_required_document = False
        for filing in candidates:
            body = ""
            determination = filing_is_material(filing)
            if determination is None:
                fetch_document = getattr(client, "fetch_filing_document", None)
                document_result = fetch_document(filing) if fetch_document else None
                body = _document_text(document_result.data) if document_result and document_result.data else ""
                if body:
                    filing_bodies.append((filing.accession, body))
                    determination = official_content_is_material(body)
                    reason = "rendered_content" if determination else "excluded_content_not_material"
                else:
                    determination = six_k_metadata_is_material(filing.description)
                    if determination:
                        reason = "rendered_metadata_fallback"
                    elif filing.form.removesuffix("/A") == "8-K":
                        # Item 7.01 is optional content review: a transient document
                        # fetch failure must not make the entire EDGAR source fail.
                        reason = "excluded_document_unavailable"
                    else:
                        reason = "excluded_indeterminate_6k"
                        incomplete_required_document = True
            else:
                reason = "rendered" if determination else "excluded_by_form_policy"
            if determination:
                headline, event_key = _describe_filing_event(
                    filing,
                    body or filing.description,
                    entity.legal_issuer,
                )
                described_filing = replace(
                    filing,
                    headline=headline,
                    event_key=event_key,
                )
                event_identity = (filing.filing_date, event_key)
                if event_key and event_identity in seen_events:
                    reason = "excluded_duplicate_event"
                else:
                    if event_key:
                        seen_events.add(event_identity)
                    material.append(described_filing)
            dispositions.append((filing.accession, reason))
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


def _describe_filing_event(filing: Filing, text: str, legal_issuer: str) -> tuple[str, str]:
    """Return a reader-facing headline and a conservative deduplication key."""
    issuer = _reader_issuer_name(legal_issuer)
    lowered = text.casefold()
    form = filing.form.removesuffix("/A")

    if "2.02" in filing.items:
        return f"{issuer} reported financial results.", "financial_results"
    if form in {"10-Q", "10-K", "20-F", "40-F"} and not text.strip():
        period = "quarterly" if form == "10-Q" else "annual"
        return f"{issuer} filed its {period} financial report.", f"{period}_financial_report"

    has_results = bool(
        re.search(r"\bq[1-4]\s+20\d{2}\b", text, flags=re.IGNORECASE)
        or any(term in lowered for term in ("financial results", "quarterly results", "financial report", "earnings"))
    )
    has_outlook = "outlook" in lowered or "guidance" in lowered
    if has_results and has_outlook:
        direction = "updated"
        if re.search(r"\b(?:raise[sd]?|improved)\b", text, flags=re.IGNORECASE):
            direction = "raised"
        elif re.search(r"\b(?:lower(?:ed|s)?|cut|reduced)\b", text, flags=re.IGNORECASE):
            direction = "lowered"

        quarter_match = re.search(r"\b(Q[1-4])\s+(20\d{2})\b", text, flags=re.IGNORECASE)
        year_match = re.search(r"\b(20\d{2})\b", text)
        year = quarter_match.group(2) if quarter_match else year_match.group(1) if year_match else ""
        year_phrase = f" {year}" if year else ""
        headline = f"{issuer} {direction} its{year_phrase} sales and profit outlook"

        sales_match = re.search(
            r"adjusted sales(?: growth)?\s+(?:increased|rose)\s+by\s+(\d+(?:\.\d+)?)\s*%",
            text,
            flags=re.IGNORECASE,
        )
        profit_match = re.search(
            r"adjusted operating profit(?: growth)?\s+(?:increased|rose)\s+by\s+(\d+(?:\.\d+)?)\s*%",
            text,
            flags=re.IGNORECASE,
        )
        if quarter_match and sales_match and profit_match:
            headline += (
                f" after adjusted {quarter_match.group(1).upper()} sales rose {sales_match.group(1)}%"
                f" and adjusted profit rose {profit_match.group(1)}%"
            )
        else:
            headline += " after reporting quarterly results"
        return headline + ".", "financial_results_outlook"

    if has_results:
        quarter_match = re.search(r"\b(Q[1-4])\s+(20\d{2})\b", text, flags=re.IGNORECASE)
        period = f" {quarter_match.group(1).upper()} {quarter_match.group(2)}" if quarter_match else ""
        return f"{issuer} reported{period} financial results.", "financial_results"
    if has_outlook:
        return f"{issuer} updated its sales and profit outlook.", "financial_outlook"
    if any(term in lowered for term in ("share repurchase", "stock repurchase", "share buyback")):
        return f"{issuer} updated its share buyback program.", "share_buyback"
    if any(term in lowered for term in ("acquisition", "merger")):
        return f"{issuer} announced a major acquisition or merger update.", "acquisition_or_merger"
    if "regulatory approval" in lowered:
        return f"{issuer} announced a regulatory approval.", "regulatory_approval"
    if any(term in lowered for term in ("chief executive", " ceo ")):
        return f"{issuer} announced a leadership update.", "leadership"
    if "dividend" in lowered:
        return f"{issuer} announced a dividend update.", "dividend"
    if any(term in lowered for term in ("capital raise", "offering")):
        return f"{issuer} announced a financing update.", "financing"
    if "restructuring" in lowered:
        return f"{issuer} announced a restructuring update.", "restructuring"

    description = filing.description.strip().rstrip(".")
    generic_descriptions = {"", "6-k", "form 6-k", "8-k", "form 8-k"}
    if description.casefold() not in generic_descriptions:
        return f"{issuer}: {description}.", _normalized_event_key(description)
    return f"{issuer} filed an important company update.", "company_update"


def _reader_issuer_name(legal_issuer: str) -> str:
    return re.sub(
        r"\s+(?:A/S|Inc\.?|Corporation|Corp\.?|Ltd\.?|Limited|plc)$",
        "",
        legal_issuer.strip(),
        flags=re.IGNORECASE,
    ).strip()


def _normalized_event_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_") or "company_update"
