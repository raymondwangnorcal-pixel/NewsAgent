from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from news_agent.mailer.state import SCHEMA_VERSION, EmailStateStore
from news_agent.models import Article, BriefingParagraph, StoryCluster
from news_agent.newsletter_review import DecisionEvent, SelectionOutcome, build_candidate_records, format_newsletter_metrics, newsletter_metrics


def _review_candidate(store: EmailStateStore, *, candidate_id: str = "candidate") -> None:
    with store.connect() as connection:
        now = datetime.now(timezone.utc).isoformat()
        edition = connection.execute("""INSERT INTO editions(local_date, revision, subject, plain_text, html, state, article_window_end, created_at, updated_at, edition_kind)
            VALUES ('2026-08-05', 1, 'subject', '', '', 'smtp_accepted', ?, ?, ?, 'production')""", (now, now, now)).lastrowid
        connection.execute("""INSERT INTO newsletter_runs(run_id, briefing_date, edition_id, pipeline_version, config_hash, deck_target, candidates_total, openai_mode, created_at)
            VALUES ('run', '2026-08-05', ?, 'v1', 'hash', 25, 1, 'full', ?)""", (edition, now))
        connection.execute("""INSERT INTO newsletter_candidates(candidate_id, story_key, candidate_kind, run_id, briefing_date, disposition, filter_stage, filter_reason_code,
            legacy_skip_reason, review_stratum, headline, category, culture_lane, canonical_url, all_urls_json, url_hashes_json, sources_json, source_count,
            summary_excerpt, delivered_paragraph, total_score, importance, evidence_score, quality_score, content_quality_penalty, is_update, merged_from_json, created_at)
            VALUES (?, 'story', 'cluster', 'run', '2026-08-05', 'filtered', 'selection', 'selection_deck_capacity', '', 'near_miss', 'Headline', 'finance', '',
            'https://example.com/a', '[\"https://example.com/a\"]', '[\"urlhash\"]', '[]', 1, 'excerpt', '', 1, 1, 1, 1, 0, 0, '[]', ?)""", (candidate_id, now))


def test_fresh_state_creates_newsletter_review_schema(tmp_path: Path) -> None:
    path = tmp_path / "state.db"

    EmailStateStore(path).connect().close()

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        candidate_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(newsletter_candidates)")
        }
        run_columns = {row[1] for row in connection.execute("PRAGMA table_info(newsletter_runs)")}
        manual_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(newsletter_manual_examples)")
        }

    assert {"candidate_id", "filter_reason_code", "review_stratum", "excerpt_purged_at"}.issubset(candidate_columns)
    assert {"history_update_json", "history_applied_at", "history_abandoned_at"}.issubset(run_columns)
    assert {"source_url_hash", "pipeline_version", "label_schema_version"}.issubset(manual_columns)


def test_candidate_records_preserve_hard_rejected_article_and_terminal_reason() -> None:
    article = Article(
        title="Empty item", url="https://example.com/item?utm=tracking", source="Example",
        published_at=datetime(2026, 8, 5, tzinfo=timezone.utc), summary="", reputation=1.0,
    )
    cluster = StoryCluster(key="selected", title="Selected", articles=[article], category="finance")
    records = build_candidate_records(
        [cluster], [], run_id="run-1", deck_target=25,
        decision_events=(DecisionEvent(article, "hard_rejected_article", "quality_gate", "quality_gate_hard_reject", "empty_summary"),),
    )

    hard_reject = next(record for record in records if record.candidate_kind == "hard_rejected_article")
    assert hard_reject.filter_stage == "quality_gate"
    assert hard_reject.filter_reason_code == "quality_gate_hard_reject"
    assert hard_reject.legacy_skip_reason == "empty_summary"
    assert hard_reject.review_stratum == "hard_reject"


def test_candidate_records_deduplicate_hard_rejections_with_one_canonical_story() -> None:
    first = Article(
        title="Publisher story", url="https://feed.example.com/first", canonical_url="https://publisher.example.com/story",
        source="First feed", published_at=datetime(2026, 8, 5, tzinfo=timezone.utc), summary="",
    )
    second = Article(
        title="Publisher story", url="https://feed.example.com/second", canonical_url="https://publisher.example.com/story",
        source="Second feed", published_at=datetime(2026, 8, 5, tzinfo=timezone.utc), summary="",
    )

    records = build_candidate_records(
        [], [], run_id="run-1", deck_target=25,
        decision_events=(
            DecisionEvent(first, "hard_rejected_article", "quality_gate", "quality_gate_hard_reject", "empty_summary"),
            DecisionEvent(second, "hard_rejected_article", "quality_gate", "quality_gate_hard_reject", "empty_summary"),
        ),
    )

    assert len(records) == 1
    assert records[0].candidate_kind == "hard_rejected_article"
    assert records[0].story_key


def test_candidate_records_preserve_selection_phase_and_exact_reason() -> None:
    selected = StoryCluster(key="selected", title="Selected", category="finance")
    filtered = StoryCluster(key="filtered", title="Filtered", category="finance")

    records = build_candidate_records(
        [selected, filtered],
        [BriefingParagraph(story_id="selected", category="finance", paragraph="Delivered.", sources=())],
        run_id="run-1", deck_target=25,
        selection_outcomes=(
            SelectionOutcome(selected, "floor"),
            SelectionOutcome(filtered, "", "selection_source_cap"),
        ),
    )

    selected_record = next(item for item in records if item.headline == "Selected")
    filtered_record = next(item for item in records if item.headline == "Filtered")
    assert selected_record.selection_phase == "floor"
    assert filtered_record.filter_reason_code == "selection_source_cap"


def test_filtered_labels_require_frozen_batch_and_valid_reason(tmp_path: Path) -> None:
    store = EmailStateStore(tmp_path / "state.db")
    _review_candidate(store)
    with pytest.raises(ValueError, match="frozen review batch"):
        store.record_newsletter_label("candidate", "filtered_candidate", "relevant", "gate_too_strict", "", "newsletter-rubric-v1")
    with store.connect() as connection:
        connection.execute("""INSERT INTO newsletter_review_batches VALUES ('batch', 'v1', 'newsletter-rubric-v1', 'seed', '2026-08-05', '2026-08-05',
            '{\"near_miss\": 1}', '{\"near_miss\": 10}', '[\"candidate\"]', ?)""", (datetime.now(timezone.utc).isoformat(),))
    with pytest.raises(ValueError, match="valid reason"):
        store.record_newsletter_label("candidate", "filtered_candidate", "relevant", "not-a-code", "", "newsletter-rubric-v1")
    store.record_newsletter_label("candidate", "filtered_candidate", "relevant", "gate_too_strict", "", "newsletter-rubric-v1")


def test_manual_examples_are_deduplicated_and_url_matched(tmp_path: Path) -> None:
    store = EmailStateStore(tmp_path / "state.db")
    _review_candidate(store)
    item = {"example_date": "2026-08-05", "headline": "Example", "source_url": "https://example.com/a?tracking=1", "publisher": "Example", "why_it_matters": "Important", "provenance": "external_outlet"}
    assert store.import_newsletter_examples([item]) == 1
    assert store.import_newsletter_examples([item]) == 0
    example = store.pending_newsletter_examples()[0]
    # Candidate hashes are deliberately fixture values; matching is by the retained hash contract.
    with store.connect() as connection:
        connection.execute("UPDATE newsletter_candidates SET url_hashes_json=?", (f'["{store._newsletter_url_hash(item["source_url"])}"]',))
    assert store.candidates_for_example(example)[0]["candidate_id"] == "candidate"


def test_metrics_gate_small_denominators() -> None:
    metrics = newsletter_metrics([{"subject_type": "sent_story", "verdict": "irrelevant"}])
    assert "not yet reportable — 1 of 40" in format_newsletter_metrics(metrics)
