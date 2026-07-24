from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from news_agent.compress import CompressionRunResult
from news_agent.draft import DraftCandidate
from news_agent.models import BriefingParagraph, CompressionConfig


DEFAULT_COMPRESSION_AUDIT_DIR = Path("data") / "compression_audits"
COMPRESSION_AUDIT_RETENTION_DAYS = 30


def cleanup_compression_audits(
    audit_dir: Path = DEFAULT_COMPRESSION_AUDIT_DIR,
    retention_days: int = COMPRESSION_AUDIT_RETENTION_DAYS,
    now: datetime | None = None,
) -> list[Path]:
    selected_now = now or datetime.now(timezone.utc)
    cutoff = selected_now - timedelta(days=retention_days)
    removed: list[Path] = []
    if not audit_dir.exists():
        return removed
    for path in audit_dir.glob("compression_audit_*.json"):
        modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        if modified < cutoff:
            path.unlink()
            removed.append(path)
    return removed


def default_compression_audit_path(
    audit_dir: Path = DEFAULT_COMPRESSION_AUDIT_DIR,
    now: datetime | None = None,
) -> Path:
    selected_now = now or datetime.now(timezone.utc)
    stamp = selected_now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return audit_dir / f"compression_audit_{stamp}.json"


def write_compression_audit(
    paragraphs: list[BriefingParagraph],
    candidates: list[DraftCandidate],
    result: CompressionRunResult,
    config: CompressionConfig,
    path: Path | None = None,
    now: datetime | None = None,
) -> Path:
    selected_now = now or datetime.now(timezone.utc)
    resolved = path or default_compression_audit_path(now=selected_now)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    candidate_by_id = {candidate.story_id: candidate for candidate in candidates}
    payload = {
        "created_at": selected_now.astimezone(timezone.utc).isoformat(),
        "model": config.model,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "compression_cost_usd": result.cost_usd,
        "compression_budget_exhausted": result.budget_exhausted,
        "guard_deltas": result.guard_deltas or {},
        "stories": [],
    }
    for paragraph in paragraphs:
        candidate = candidate_by_id.get(paragraph.story_id)
        articles = candidate.articles if candidate is not None else ()
        payload["stories"].append(
            {
                "story_id": paragraph.story_id,
                "category": paragraph.category,
                "evidence": [
                    {
                        "source": article.source,
                        "url": article.url,
                        "canonical_url": article.canonical_url or article.url,
                        "evidence_type": article.enrichment_status,
                    }
                    for article in articles
                ],
                "full_paragraph": paragraph.full_paragraph or paragraph.paragraph,
                "delivered_paragraph": paragraph.paragraph,
                "compression_status": paragraph.compression_status,
                "compression_ratio": paragraph.compression_ratio,
                "guard_result": (
                    "failed"
                    if paragraph.compression_status == "kept_original_guard_failed"
                    else "warning"
                    if paragraph.story_id in (result.guard_deltas or {})
                    else "passed"
                    if paragraph.compression_status == "compressed"
                    else "not_run"
                ),
            }
        )
    resolved.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return resolved
