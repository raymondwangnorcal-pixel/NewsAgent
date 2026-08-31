"""Durable, storage-neutral records for newsletter relevance review."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from math import ceil

from news_agent.models import Article, BriefingParagraph, StoryCluster


LABEL_SCHEMA_VERSION = "newsletter-rubric-v1"


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _normal_url(value: str) -> str:
    return value.split("?", 1)[0].rstrip("/")


@dataclass(frozen=True)
class CandidateRecord:
    candidate_id: str
    story_key: str
    candidate_kind: str
    disposition: str
    filter_stage: str
    filter_reason_code: str
    legacy_skip_reason: str
    review_stratum: str
    headline: str
    category: str
    culture_lane: str
    canonical_url: str
    all_urls_json: str
    url_hashes_json: str
    sources_json: str
    source_count: int
    summary_excerpt: str
    delivered_paragraph: str
    total_score: float
    importance: float
    evidence_score: float
    quality_score: float
    content_quality_penalty: float
    llm_importance: int | None
    deck_rank: int | None
    selection_phase: str
    is_update: bool
    merged_from_json: str

    def as_db_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class DecisionEvent:
    """An irreversible terminal pipeline decision, captured before data is lost."""
    subject: StoryCluster | Article
    candidate_kind: str
    filter_stage: str
    filter_reason_code: str
    legacy_skip_reason: str = ""


@dataclass(frozen=True)
class SelectionOutcome:
    """The final result of selection for one otherwise eligible cluster."""
    subject: StoryCluster
    selection_phase: str
    filter_reason_code: str = ""


def _filter(cluster: StoryCluster) -> tuple[str, str]:
    reason = cluster.skip_reason or ""
    if reason == "stale/repeated from yesterday":
        return "history", "history_stale"
    if "evidence" in reason or "reliable source" in reason:
        return "evidence", "evidence_gate"
    if "duplicate" in reason:
        return "duplicate", "duplicate_gate_merged"
    return "selection", "selection_below_threshold"


def _stratum(disposition: str, total_score: float, deck_target: int, hard_rejected: bool = False) -> str:
    if hard_rejected:
        return "hard_reject"
    if disposition == "selected":
        return "selected"
    if total_score >= max(1.0, deck_target * 0.2):
        return "near_miss"
    if total_score >= 0:
        return "mid"
    return "deep_filtered"


def build_candidate_records(
    clusters: list[StoryCluster],
    paragraphs: list[BriefingParagraph],
    *,
    run_id: str,
    deck_target: int,
    decision_events: tuple[DecisionEvent, ...] = (),
    selection_outcomes: tuple[SelectionOutcome, ...] = (),
) -> tuple[CandidateRecord, ...]:
    """Create immutable records from the final cluster state without writing storage."""
    paragraph_by_story = {paragraph.story_id: paragraph.paragraph for paragraph in paragraphs}
    records: list[CandidateRecord] = []
    event_by_subject = {id(event.subject): event for event in decision_events}
    selection_by_subject = {id(outcome.subject): outcome for outcome in selection_outcomes}
    subjects: list[StoryCluster | Article] = list(clusters)
    known_subjects = {id(subject) for subject in subjects}
    for event in decision_events:
        if id(event.subject) not in known_subjects:
            subjects.append(event.subject)
            known_subjects.add(id(event.subject))
    for index, subject in enumerate(subjects):
        if isinstance(subject, Article):
            article = subject
            event = event_by_subject[id(article)]
            urls = sorted({_normal_url(article.canonical_url or article.url)})[:5]
            story_key = _digest(urls or [article.source, article.title.casefold(), article.published_at.date().isoformat()])
            records.append(CandidateRecord(
                candidate_id=_digest([run_id, "hard_rejected_article", story_key]), story_key=story_key,
                candidate_kind="hard_rejected_article", disposition="filtered", filter_stage=event.filter_stage,
                filter_reason_code=event.filter_reason_code, legacy_skip_reason=event.legacy_skip_reason,
                review_stratum="hard_reject", headline=article.title, category="uncategorized", culture_lane="",
                canonical_url=urls[0] if urls else "", all_urls_json=json.dumps(urls),
                url_hashes_json=json.dumps([_digest(url) for url in urls]), sources_json=json.dumps([article.source]), source_count=1,
                summary_excerpt=article.best_available_text[:600], delivered_paragraph="", total_score=0.0, importance=0.0,
                evidence_score=0.0, quality_score=0.0, content_quality_penalty=article.content_quality_penalty,
                llm_importance=None, deck_rank=None, selection_phase="", is_update=False, merged_from_json="[]",
            ))
            continue
        cluster = subject
        urls = sorted({_normal_url(url) for url in cluster.urls if url})[:5]
        story_key = _digest(urls or [cluster.title.casefold(), cluster.latest_published_at.date().isoformat()])
        selected = cluster.key in paragraph_by_story
        disposition = "selected" if selected else "filtered"
        event = event_by_subject.get(id(cluster))
        selection = selection_by_subject.get(id(cluster))
        stage, code = ("", "") if selected else (
            (event.filter_stage, event.filter_reason_code) if event else (
                ("selection", selection.filter_reason_code) if selection else _filter(cluster)
            )
        )
        candidate_id = _digest([run_id, "cluster", story_key])
        records.append(CandidateRecord(
            candidate_id=candidate_id, story_key=story_key, candidate_kind="cluster",
            disposition=disposition, filter_stage=stage, filter_reason_code=code,
            legacy_skip_reason=(event.legacy_skip_reason if event else cluster.skip_reason or ""), review_stratum=_stratum(disposition, cluster.total_score, deck_target),
            headline=cluster.title, category=cluster.category or "uncategorized", culture_lane=cluster.culture_lane or "",
            canonical_url=urls[0] if urls else "", all_urls_json=json.dumps(urls),
            url_hashes_json=json.dumps([_digest(url) for url in urls]), sources_json=json.dumps(list(cluster.sources)),
            source_count=len(cluster.sources), summary_excerpt=cluster.representative_summary[:600],
            delivered_paragraph=paragraph_by_story.get(cluster.key, ""), total_score=cluster.total_score,
            importance=cluster.importance, evidence_score=cluster.evidence_score, quality_score=cluster.quality_score,
            content_quality_penalty=cluster.content_quality_penalty, llm_importance=None, deck_rank=index if selected else None,
            selection_phase=selection.selection_phase if selected and selection else "", is_update=cluster.is_update,
            merged_from_json=json.dumps(list(cluster.merged_from)),
        ))
    # Persistence records one logical candidate per kind and normalized story
    # identity. Feed aliases can otherwise produce duplicate hard rejections
    # before cluster-level URL merging runs.
    unique_records: dict[tuple[str, str], CandidateRecord] = {}
    for record in records:
        unique_records.setdefault((record.candidate_kind, record.story_key), record)
    return tuple(unique_records.values())


def bind_run_id(records: tuple[CandidateRecord, ...], run_id: str) -> tuple[CandidateRecord, ...]:
    """Bind pipeline records to the durable run generated by the email transaction."""
    from dataclasses import replace
    return tuple(
        replace(record, candidate_id=_digest([run_id, record.candidate_kind, record.story_key]))
        for record in records
    )


@dataclass(frozen=True)
class NewsletterMetrics:
    sent_reviewed: int
    sent_relevant_rate: float | None
    filtered_reviewed: int
    filtered_relevant_rate: float | None
    unclear_count: int

    @property
    def sent_reportable(self) -> bool:
        return self.sent_reviewed >= 40

    @property
    def filtered_reportable(self) -> bool:
        return self.filtered_reviewed >= 10


def newsletter_metrics(labels: list[dict[str, object]]) -> NewsletterMetrics:
    def rate(subject_type: str) -> tuple[int, float | None]:
        rows = [item for item in labels if item["subject_type"] == subject_type and item["verdict"] != "unclear"]
        return len(rows), (sum(item["verdict"] == "relevant" for item in rows) / len(rows) if rows else None)
    sent_count, sent_rate = rate("sent_story")
    filtered_count, filtered_rate = rate("filtered_candidate")
    return NewsletterMetrics(sent_count, sent_rate, filtered_count, filtered_rate, sum(item["verdict"] == "unclear" for item in labels))


def format_newsletter_metrics(metrics: NewsletterMetrics) -> str:
    def value(rate: float | None, count: int, minimum: int) -> str:
        return f"{rate:.1%} ({count})" if count >= minimum and rate is not None else f"not yet reportable — {count} of {minimum}"
    return "\n".join((
        "Newsletter evaluation report",
        f"Sent false-positive rate: {value(None if metrics.sent_relevant_rate is None else 1 - metrics.sent_relevant_rate, metrics.sent_reviewed, 40)}",
        f"Filtered relevant rate: {value(metrics.filtered_relevant_rate, metrics.filtered_reviewed, 10)}",
        f"Unclear labels: {metrics.unclear_count}",
    ))


def sample_size_for_target(conclusive_target: int, population: int) -> int:
    return min(population, ceil(conclusive_target / 0.80))
