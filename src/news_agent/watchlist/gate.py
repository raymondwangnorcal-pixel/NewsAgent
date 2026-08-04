from __future__ import annotations

from enum import StrEnum

from news_agent.watchlist.models import ActivationPreflight, GateMetrics


class GateState(StrEnum):
    DISABLED = "DISABLED"
    MEASURING = "MEASURING"
    PASS = "PASS"
    FAIL = "FAIL"


def activate_gate(preflight: ActivationPreflight, *, confirmed: bool) -> GateState:
    if not confirmed:
        raise ValueError("Gate A activation requires --confirm.")
    if not preflight.passed:
        raise ValueError("Gate A activation preflight failed: " + ", ".join(preflight.blocking_reasons))
    return GateState.MEASURING


def evaluate_gate(metrics: GateMetrics) -> tuple[GateState, tuple[str, ...]]:
    missing = []
    if metrics.independent_non_filing_events < 20:
        missing.append("non_filing_events")
    if metrics.relationship_claims < 20:
        missing.append("relationship_claims")
    if metrics.rendered_stories_reviewed < 20:
        missing.append("rendered_stories")
    if missing:
        return GateState.MEASURING, tuple(f"insufficient_{item}" for item in missing)
    failures: list[str] = []
    retrieval_rate = (
        metrics.required_source_failures / metrics.evaluated_ticker_days
        if metrics.evaluated_ticker_days else 1.0
    )
    if retrieval_rate > 0.02:
        failures.append("required_source_retrieval")
    if metrics.processed_filings != metrics.eligible_filings:
        failures.append("eligible_filing_processing")
    if metrics.processed_catchup_filings != metrics.expected_catchup_filings:
        failures.append("filing_catchup")
    if metrics.false_relationship_claims / metrics.relationship_claims > 0.05:
        failures.append("relationship_accuracy")
    if metrics.confirmed_duplicate_events:
        failures.append("same_event_duplicates")
    if metrics.irrelevant_stories / metrics.rendered_stories_reviewed > 0.05:
        failures.append("story_relevance")
    if metrics.found_non_filing_events / metrics.independent_non_filing_events < 0.80:
        failures.append("non_filing_recall")
    return (GateState.FAIL, tuple(failures)) if failures else (GateState.PASS, ())
