from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from news_agent.compress import CompressionRunResult
from news_agent.compression_audit import cleanup_compression_audits, write_compression_audit
from news_agent.draft import DraftCandidate
from news_agent.models import Article, BriefingParagraph, CompressionConfig


def test_audit_artifact_contains_original_delivered_evidence_and_cost(tmp_path: Path) -> None:
    article = Article(
        title="Rates stay unchanged",
        url="https://aggregator.example/item",
        canonical_url="https://reuters.com/world/rates",
        source="Reuters",
        published_at=datetime(2026, 7, 23, tzinfo=timezone.utc),
        summary="The central bank held rates steady.",
        enrichment_status="extracted",
    )
    candidate = DraftCandidate(
        story_id="rates",
        category="finance",
        title=article.title,
        articles=(article,),
    )
    paragraph = BriefingParagraph(
        story_id="rates",
        category="finance",
        paragraph="The bank held rates.",
        full_paragraph="The central bank held interest rates unchanged.",
        sources=("Reuters",),
        compression_status="compressed",
        compression_ratio=0.45,
    )
    result = CompressionRunResult(
        [paragraph],
        input_tokens=100,
        output_tokens=25,
        cost_usd=0.00015,
    )

    path = write_compression_audit(
        [paragraph],
        [candidate],
        result,
        CompressionConfig(),
        path=tmp_path / "compression_audit_test.json",
        now=datetime(2026, 7, 23, tzinfo=timezone.utc),
    )
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["model"] == "gpt-5.6-terra"
    assert payload["input_tokens"] == 100
    assert payload["output_tokens"] == 25
    assert payload["compression_cost_usd"] == 0.00015
    assert payload["stories"][0]["full_paragraph"] == paragraph.full_paragraph
    assert payload["stories"][0]["delivered_paragraph"] == paragraph.paragraph
    assert payload["stories"][0]["guard_result"] == "passed"
    assert payload["stories"][0]["evidence"] == [
        {
            "canonical_url": "https://reuters.com/world/rates",
            "evidence_type": "extracted",
            "source": "Reuters",
            "url": "https://aggregator.example/item",
        }
    ]


def test_audit_retention_removes_only_records_older_than_30_days(tmp_path: Path) -> None:
    now = datetime(2026, 7, 23, tzinfo=timezone.utc)
    old_path = tmp_path / "compression_audit_old.json"
    current_path = tmp_path / "compression_audit_current.json"
    unrelated_path = tmp_path / "other.json"
    for path in (old_path, current_path, unrelated_path):
        path.write_text("{}", encoding="utf-8")
    old_timestamp = (now - timedelta(days=31)).timestamp()
    current_timestamp = (now - timedelta(days=29)).timestamp()
    os.utime(old_path, (old_timestamp, old_timestamp))
    os.utime(current_path, (current_timestamp, current_timestamp))
    os.utime(unrelated_path, (old_timestamp, old_timestamp))

    removed = cleanup_compression_audits(tmp_path, now=now)

    assert removed == [old_path]
    assert not old_path.exists()
    assert current_path.exists()
    assert unrelated_path.exists()
