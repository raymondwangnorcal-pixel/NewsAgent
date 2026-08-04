from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from news_agent.watchlist.models import Classification, EntityMap, EntityName, FilingCoverage, RelationshipLabel, TickerEntity


DEFAULT_ENTITY_MAP_PATH = Path(__file__).resolve().parents[3] / "config" / "entity_map.json"
DEFAULT_AMBIGUITIES_PATH = Path(__file__).resolve().parents[3] / "config" / "entity_map_ambiguities.json"
RELATIONSHIP_LABELS = {
    "self": RelationshipLabel.DIRECT,
    "controlled_affiliate": RelationshipLabel.AFFILIATE,
    "managed_capital": RelationshipLabel.MANAGED_CAPITAL,
    "underlying_asset": RelationshipLabel.UNDERLYING_ASSET,
    "family_ambiguous": RelationshipLabel.FAMILY_UNRESOLVED,
    "unrelated": RelationshipLabel.MENTION_ONLY,
}
EVENT_ROLES = {"party", "subject", "quoted_speaker", "mentioned", "absent"}


def load_entity_map(path: Path = DEFAULT_ENTITY_MAP_PATH) -> EntityMap:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Entity map is missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Entity map is not valid JSON: {path}") from exc
    return parse_entity_map(raw)


def load_relationship_ambiguities(path: Path = DEFAULT_AMBIGUITIES_PATH) -> list[dict[str, str]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise ValueError(f"Relationship ambiguity queue is missing or invalid: {path}") from exc
    items = raw.get("items") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        raise ValueError("Relationship ambiguity queue must contain an items array.")
    required = {"id", "ticker", "candidate", "proposed_relationship", "status", "evidence_url", "reason"}
    parsed: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict) or not required.issubset(item):
            raise ValueError("Relationship ambiguity item is malformed.")
        parsed.append({key: str(item[key]) for key in required})
    return parsed


def parse_entity_map(raw: Any) -> EntityMap:
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise ValueError("Entity map schema_version must be 1.")
    generated_at = _datetime(raw.get("generated_at"), "generated_at")
    raw_tickers = raw.get("tickers")
    if not isinstance(raw_tickers, dict) or not raw_tickers:
        raise ValueError("Entity map must contain a non-empty tickers object.")
    tickers: dict[str, TickerEntity] = {}
    for ticker, value in raw_tickers.items():
        tickers[str(ticker)] = _parse_ticker(str(ticker), value)
    return EntityMap(1, generated_at, tickers)


def classify_text(
    entity: TickerEntity,
    text: str,
    event_role: str,
    *,
    today: date | None = None,
    current_governing_accession: str | None = None,
) -> Classification:
    if event_role not in EVENT_ROLES:
        raise ValueError(f"Unsupported event role: {event_role}")
    lowered = text.casefold()
    for negative in entity.negative_names:
        if re.search(rf"\b{re.escape(negative.casefold())}\b", lowered):
            return Classification(entity.ticker, RelationshipLabel.MENTION_ONLY, reason="negative_name")
    for candidate in sorted(entity.names, key=lambda item: len(item.name), reverse=True):
        match = re.search(rf"\b{re.escape(candidate.name.casefold())}\b", lowered)
        if match is None:
            continue
        if candidate.requires_context and not any(
            re.search(rf"\b{re.escape(term.casefold())}\b", lowered) for term in candidate.requires_context
        ):
            continue
        fresh = _is_fresh(candidate, today or datetime.now(timezone.utc).date(), current_governing_accession)
        label = _label_for(candidate.relationship, event_role, fresh)
        return Classification(
            ticker=entity.ticker,
            label=label,
            matched_name=candidate.name,
            matched_span=match.span(),
            relationship_source=candidate.source,
            relationship_fresh=fresh,
            event_role=event_role,
            reason="matched" if fresh else "stale_relationship",
        )
    return Classification(entity.ticker, RelationshipLabel.MENTION_ONLY, event_role=event_role, reason="no_entity_match")


def _label_for(relationship: str, event_role: str, fresh: bool) -> RelationshipLabel:
    if event_role in {"quoted_speaker", "mentioned", "absent"}:
        return RelationshipLabel.MENTION_ONLY
    if not fresh and relationship in {"controlled_affiliate", "managed_capital", "underlying_asset"}:
        return RelationshipLabel.MENTION_ONLY
    return RELATIONSHIP_LABELS[relationship]


def _is_fresh(name: EntityName, today: date, current_accession: str | None) -> bool:
    if name.relationship == "self":
        return True
    if name.expires_at is not None and today > name.expires_at:
        return False
    if current_accession and name.verified_against_accession and current_accession != name.verified_against_accession:
        return False
    return True


def _parse_ticker(ticker: str, raw: Any) -> TickerEntity:
    if not isinstance(raw, dict):
        raise ValueError(f"Entity map ticker {ticker} must be an object.")
    cik = str(raw.get("cik", ""))
    if not re.fullmatch(r"\d{10}", cik):
        raise ValueError(f"Entity map ticker {ticker} needs a zero-padded ten-digit CIK.")
    filing_raw = raw.get("filing")
    if not isinstance(filing_raw, dict):
        raise ValueError(f"Entity map ticker {ticker} needs filing metadata.")
    forms = tuple(str(value).strip() for value in filing_raw.get("observed_forms", ()) if str(value).strip())
    required = bool(filing_raw.get("required_edgar"))
    if required and not forms:
        raise ValueError(f"Entity map ticker {ticker} requires EDGAR but has no observed forms.")
    filing = FilingCoverage(
        legal_regime=_required_text(filing_raw, "legal_regime", ticker),
        required_edgar=required,
        observed_forms=forms,
        annual_form=str(filing_raw["annual_form"]) if filing_raw.get("annual_form") else None,
        subsidiary_exhibit=_required_text(filing_raw, "subsidiary_exhibit", ticker),
        omission_allowance=_required_text(filing_raw, "omission_allowance", ticker),
        evidence_url=_http_url(filing_raw.get("evidence_url"), ticker),
        verified_at=_date(filing_raw.get("verified_at"), f"{ticker}.filing.verified_at"),
    )
    raw_names = raw.get("names")
    if not isinstance(raw_names, list) or not raw_names:
        raise ValueError(f"Entity map ticker {ticker} needs at least one name.")
    names = tuple(_parse_name(item, ticker) for item in raw_names)
    if not any(item.relationship == "self" for item in names):
        raise ValueError(f"Entity map ticker {ticker} needs a self name.")
    return TickerEntity(
        ticker=ticker,
        legal_issuer=_required_text(raw, "legal_issuer", ticker),
        cik=cik,
        filing=filing,
        names=names,
        negative_names=tuple(str(value) for value in raw.get("negative_names", ())),
        discovery_keys=tuple(str(value) for value in raw.get("discovery_keys", (ticker,))),
        notes=tuple(str(value) for value in raw.get("notes", ())),
    )


def _parse_name(raw: Any, ticker: str) -> EntityName:
    if not isinstance(raw, dict):
        raise ValueError(f"Entity map ticker {ticker} has an invalid name entry.")
    relationship = str(raw.get("relationship", ""))
    if relationship not in RELATIONSHIP_LABELS:
        raise ValueError(f"Entity map ticker {ticker} has invalid relationship {relationship!r}.")
    return EntityName(
        name=_required_text(raw, "name", ticker),
        relationship=relationship,
        source=_required_text(raw, "source", ticker),
        verified_at=_date(raw.get("verified_at"), f"{ticker}.name.verified_at"),
        expires_at=_date(raw["expires_at"], f"{ticker}.name.expires_at") if raw.get("expires_at") else None,
        min_tokens=int(raw.get("min_tokens", 1)),
        requires_context=tuple(str(value) for value in raw.get("requires_context", ())),
        verified_against_accession=str(raw["verified_against_accession"]) if raw.get("verified_against_accession") else None,
    )


def _required_text(raw: dict[str, Any], key: str, ticker: str) -> str:
    value = str(raw.get(key, "")).strip()
    if not value:
        raise ValueError(f"Entity map ticker {ticker} needs {key}.")
    return value


def _http_url(value: Any, ticker: str) -> str:
    text = str(value or "")
    if not text.startswith("https://"):
        raise ValueError(f"Entity map ticker {ticker} needs an HTTPS filing evidence URL.")
    return text


def _date(value: Any, field: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError(f"Entity map {field} must be an ISO date.") from exc


def _datetime(value: Any, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError(f"Entity map {field} must be an ISO datetime.") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"Entity map {field} must include a timezone.")
    return parsed
