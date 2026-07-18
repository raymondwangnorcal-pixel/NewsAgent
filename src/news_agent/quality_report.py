from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

QUALITY_GATE_REJECTIONS_FILENAME_RE = re.compile(r"^quality_gate_rejections_(\d{4}-\d{2}-\d{2})\.json$")
QUALITY_GATE_AUDIT_FILENAME_RE = re.compile(r"^quality_gate_audit_(\d{4}-\d{2}-\d{2})\.json$")


@dataclass(frozen=True)
class SourceQualityStats:
    fetched: int = 0
    hard_rejected: int = 0
    quarantined: int = 0

    @property
    def removed(self) -> int:
        return self.hard_rejected + self.quarantined

    @property
    def removal_rate(self) -> float | None:
        if not self.fetched:
            return None
        return self.removed / self.fetched


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


def _audit_files_in_window(log_dir: Path, days: int, today: date | None = None) -> list[Path]:
    if days <= 0 or not log_dir.is_dir():
        return []

    selected_today = today or date.today()
    cutoff = selected_today - timedelta(days=days - 1)
    matches: list[Path] = []
    for path in log_dir.glob("quality_gate_audit_*.json"):
        match = QUALITY_GATE_AUDIT_FILENAME_RE.match(path.name)
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


def aggregate_source_quality(log_dir: Path, days: int) -> dict[str, SourceQualityStats]:
    """Aggregate fetched, hard-rejected, and quarantined article counts by source.

    Audit journals include every fetched article, so this report can describe a
    quality-gate removal *rate*. Older rejection-only logs intentionally remain
    supported by `aggregate_source_rejections()` but do not have a denominator.
    """

    fetched: dict[str, int] = {}
    hard_rejected: dict[str, int] = {}
    quarantined: dict[str, int] = {}
    for path in _audit_files_in_window(log_dir, days):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"quality_report: skipping unreadable audit file {path}: {exc}", file=sys.stderr)
            continue

        if not isinstance(raw, dict):
            print(f"quality_report: skipping malformed audit file {path}: expected a JSON object", file=sys.stderr)
            continue

        source_fetch_counts = raw.get("source_fetch_counts")
        article_decisions = raw.get("articles")
        if not isinstance(source_fetch_counts, dict) or not isinstance(article_decisions, list):
            print(f"quality_report: skipping malformed audit file {path}: missing run fields", file=sys.stderr)
            continue

        for source, count in source_fetch_counts.items():
            if isinstance(source, str) and source and isinstance(count, int) and count >= 0:
                fetched[source] = fetched.get(source, 0) + count

        for decision in article_decisions:
            if not isinstance(decision, dict):
                continue
            source = decision.get("source")
            action = decision.get("final_action")
            if not isinstance(source, str) or not source:
                continue
            if action == "hard_rejected":
                hard_rejected[source] = hard_rejected.get(source, 0) + 1
            elif action == "quarantined":
                quarantined[source] = quarantined.get(source, 0) + 1

    sources = fetched.keys() | hard_rejected.keys() | quarantined.keys()
    return {
        source: SourceQualityStats(
            fetched=fetched.get(source, 0),
            hard_rejected=hard_rejected.get(source, 0),
            quarantined=quarantined.get(source, 0),
        )
        for source in sorted(sources)
    }


def format_source_quality_report(stats: dict[str, SourceQualityStats]) -> str:
    if not stats:
        return "No quality-gate audit data found."

    lines = ["Quality gate removals by source", "hard | quarantined | fetched | removal rate | source"]
    for source, item in sorted(
        stats.items(),
        key=lambda entry: (-entry[1].removed, -entry[1].fetched, entry[0]),
    ):
        rate = f"{item.removal_rate:.1%}" if item.removal_rate is not None else "n/a"
        lines.append(f"{item.hard_rejected} | {item.quarantined} | {item.fetched} | {rate} | {source}")
    return "\n".join(lines)
