from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum


class SourceState(StrEnum):
    OK = "OK"
    NOT_MODIFIED = "NOT_MODIFIED"
    UNSUPPORTED = "UNSUPPORTED"
    FAILED = "FAILED"


class RelationshipLabel(StrEnum):
    DIRECT = "DIRECT"
    AFFILIATE = "AFFILIATE"
    MANAGED_CAPITAL = "MANAGED_CAPITAL"
    UNDERLYING_ASSET = "UNDERLYING_ASSET"
    FAMILY_UNRESOLVED = "FAMILY_UNRESOLVED"
    MENTION_ONLY = "MENTION_ONLY"


@dataclass(frozen=True)
class FilingCoverage:
    legal_regime: str
    required_edgar: bool
    observed_forms: tuple[str, ...]
    annual_form: str | None
    subsidiary_exhibit: str
    omission_allowance: str
    evidence_url: str
    verified_at: date


@dataclass(frozen=True)
class EntityName:
    name: str
    relationship: str
    source: str
    verified_at: date
    expires_at: date | None
    min_tokens: int = 1
    requires_context: tuple[str, ...] = ()
    verified_against_accession: str | None = None


@dataclass(frozen=True)
class TickerEntity:
    ticker: str
    legal_issuer: str
    cik: str
    filing: FilingCoverage
    names: tuple[EntityName, ...]
    negative_names: tuple[str, ...] = ()
    discovery_keys: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class EntityMap:
    schema_version: int
    generated_at: datetime
    tickers: dict[str, TickerEntity]


@dataclass(frozen=True)
class Classification:
    ticker: str
    label: RelationshipLabel
    matched_name: str = ""
    matched_span: tuple[int, int] | None = None
    relationship_source: str = ""
    relationship_fresh: bool = False
    event_role: str = "absent"
    reason: str = ""


@dataclass(frozen=True)
class Filing:
    cik: str
    accession: str
    form: str
    filing_date: date
    accepted_at: datetime | None
    primary_document: str
    items: tuple[str, ...] = ()
    is_amendment: bool = False
    description: str = ""

    @property
    def url(self) -> str:
        accession_compact = self.accession.replace("-", "")
        cik_compact = str(int(self.cik))
        return f"https://www.sec.gov/Archives/edgar/data/{cik_compact}/{accession_compact}/{self.primary_document}"


@dataclass(frozen=True)
class EdgarResult:
    state: SourceState
    filings: tuple[Filing, ...] = ()
    etag: str = ""
    last_modified: str = ""
    observed_forms: tuple[str, ...] = ()
    error_code: str = ""
    attempts: int = 1
    payload: bytes | None = None


@dataclass(frozen=True)
class BenchmarkCandidate:
    ticker: str
    event_date: date
    source_url: str
    headline: str
    materiality_rationale: str
    provenance: str


@dataclass(frozen=True)
class ActivationPreflight:
    implementation_version: str
    entity_map_valid: bool
    sec_contact_valid: bool
    tests_passed: bool
    dry_run_version: str
    required_edgar_failures: tuple[str, ...] = ()
    migration_errors: tuple[str, ...] = ()
    processing_errors: tuple[str, ...] = ()
    optional_source_failures: tuple[str, ...] = ()
    unresolved_relationships: tuple[str, ...] = ()

    @property
    def blocking_reasons(self) -> tuple[str, ...]:
        reasons: list[str] = []
        if not self.entity_map_valid:
            reasons.append("entity_map_invalid")
        if not self.sec_contact_valid:
            reasons.append("sec_contact_invalid")
        if not self.tests_passed:
            reasons.append("tests_not_recorded_as_passing")
        if self.dry_run_version != self.implementation_version:
            reasons.append("dry_run_version_mismatch")
        reasons.extend(f"required_edgar_failed:{value}" for value in self.required_edgar_failures)
        reasons.extend(f"migration_error:{value}" for value in self.migration_errors)
        reasons.extend(f"processing_error:{value}" for value in self.processing_errors)
        return tuple(reasons)

    @property
    def passed(self) -> bool:
        return not self.blocking_reasons


@dataclass(frozen=True)
class GateMetrics:
    evaluated_ticker_days: int = 0
    required_source_failures: int = 0
    eligible_filings: int = 0
    processed_filings: int = 0
    expected_catchup_filings: int = 0
    processed_catchup_filings: int = 0
    relationship_claims: int = 0
    false_relationship_claims: int = 0
    rendered_stories_reviewed: int = 0
    irrelevant_stories: int = 0
    independent_non_filing_events: int = 0
    found_non_filing_events: int = 0
    confirmed_duplicate_events: int = 0
    reasons: list[str] = field(default_factory=list)
