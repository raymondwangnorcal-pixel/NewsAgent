from __future__ import annotations

import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

QUALITY_GATE_REJECTIONS_FILENAME_RE = re.compile(r"^quality_gate_rejections_(\d{4}-\d{2}-\d{2})\.json$")


def _rejection_files_in_window(log_dir: Path, days: int, today: date | None = None) -> list[Path]:
    """Return rejection-log paths in `log_dir` whose embedded filename date falls
    within the most recent `days` calendar days (inclusive of today)."""

    if days <= 0 or not log_dir.is_dir():
        return []

    selected_today = today or date.today()
    cutoff = selected_today - timedelta(days=days - 1)

    matches: list[Path] = []
    for path in log_dir.glob("quality_gate_rejections_*.json"):
        match = QUALITY_GATE_REJECTIONS_FILENAME_RE.match(path.name)
        if not match:
            continue
        try:
            file_date = date.fromisoformat(match.group(1))
        except ValueError:
            continue
        if cutoff <= file_date <= selected_today:
            matches.append(path)
    return matches


def aggregate_source_rejections(log_dir: Path, days: int) -> dict[str, int]:
    """Aggregate raw hard-rejection counts by source over the last `days` calendar days.

    Reads `quality_gate_rejections_{date}.json` files in `log_dir` (the naming
    pattern from `quality_gate.default_quality_gate_rejections_path`) whose
    embedded filename date falls within the most recent `days` calendar days
    (inclusive of today), grouping by the `source` field of each rejection
    entry and counting raw occurrences.

    This is a v1 raw-count report, not a true rejection *rate*: total-fetched
    counts per source aren't logged anywhere, so a denominator isn't available
    (see Decision 7 in the content-quality-gate plan; that's explicitly out of
    scope here). Unreadable, corrupt, or malformed files are skipped with a
    warning printed to stderr — a bad file never crashes the aggregation.
    """

    counts: dict[str, int] = {}
    for path in _rejection_files_in_window(log_dir, days):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"quality_report: skipping unreadable file {path}: {exc}", file=sys.stderr)
            continue

        if not isinstance(raw, list):
            print(f"quality_report: skipping malformed file {path}: expected a JSON array", file=sys.stderr)
            continue

        for entry in raw:
            if not isinstance(entry, dict):
                continue
            source = entry.get("source")
            if not isinstance(source, str) or not source:
                continue
            counts[source] = counts.get(source, 0) + 1

    return counts


def format_source_rejection_report(counts: dict[str, int]) -> str:
    if not counts:
        return "No quality-gate rejection data found."
    lines = ["Quality gate rejections by source", "count | source"]
    for source, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"{count} | {source}")
    return "\n".join(lines)
