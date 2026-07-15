from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from news_agent.quality_report import aggregate_source_rejections, format_source_rejection_report


def _write_log(log_dir: Path, day: date, entries: list[dict[str, str]]) -> Path:
    path = log_dir / f"quality_gate_rejections_{day.isoformat()}.json"
    path.write_text(json.dumps(entries), encoding="utf-8")
    return path


def _entry(source: str, title: str = "Some headline", reason: str = "empty_summary") -> dict[str, str]:
    return {
        "title": title,
        "source": source,
        "url": f"https://example.com/{title}",
        "reason": reason,
    }


def test_aggregate_source_rejections_counts_across_multiple_days(tmp_path: Path) -> None:
    today = date.today()
    _write_log(
        tmp_path,
        today,
        [_entry("Wire Service"), _entry("Wire Service"), _entry("Sports Desk")],
    )
    _write_log(
        tmp_path,
        today - timedelta(days=1),
        [_entry("Wire Service"), _entry("Tech Blog")],
    )

    counts = aggregate_source_rejections(tmp_path, days=7)

    assert counts == {"Wire Service": 3, "Sports Desk": 1, "Tech Blog": 1}


def test_aggregate_source_rejections_skips_corrupt_file(tmp_path: Path) -> None:
    today = date.today()
    _write_log(tmp_path, today, [_entry("Wire Service")])

    corrupt_path = tmp_path / f"quality_gate_rejections_{(today - timedelta(days=1)).isoformat()}.json"
    corrupt_path.write_text("{not valid json", encoding="utf-8")

    counts = aggregate_source_rejections(tmp_path, days=7)

    assert counts == {"Wire Service": 1}


def test_aggregate_source_rejections_skips_malformed_json_shape(tmp_path: Path) -> None:
    today = date.today()
    _write_log(tmp_path, today, [_entry("Wire Service")])

    # Malformed: top-level object instead of the expected array of entries.
    malformed_path = tmp_path / f"quality_gate_rejections_{(today - timedelta(days=2)).isoformat()}.json"
    malformed_path.write_text(json.dumps({"unexpected": "shape"}), encoding="utf-8")

    counts = aggregate_source_rejections(tmp_path, days=7)

    assert counts == {"Wire Service": 1}


def test_aggregate_source_rejections_skips_entries_missing_source(tmp_path: Path) -> None:
    today = date.today()
    entries = [_entry("Wire Service"), {"title": "No source field", "url": "https://example.com/x"}]
    _write_log(tmp_path, today, entries)

    counts = aggregate_source_rejections(tmp_path, days=7)

    assert counts == {"Wire Service": 1}


def test_aggregate_source_rejections_excludes_files_outside_window(tmp_path: Path) -> None:
    today = date.today()
    _write_log(tmp_path, today, [_entry("In Window")])
    _write_log(tmp_path, today - timedelta(days=10), [_entry("Out Of Window")])

    counts = aggregate_source_rejections(tmp_path, days=7)

    assert counts == {"In Window": 1}
    assert "Out Of Window" not in counts


def test_aggregate_source_rejections_empty_dir_returns_empty_dict(tmp_path: Path) -> None:
    assert aggregate_source_rejections(tmp_path, days=7) == {}


def test_aggregate_source_rejections_missing_dir_returns_empty_dict(tmp_path: Path) -> None:
    assert aggregate_source_rejections(tmp_path / "does-not-exist", days=7) == {}


def test_aggregate_source_rejections_ignores_unrelated_filenames(tmp_path: Path) -> None:
    (tmp_path / "skipped_stories_2026-07-14.json").write_text(
        json.dumps([_entry("Should Not Count")]), encoding="utf-8"
    )

    counts = aggregate_source_rejections(tmp_path, days=7)

    assert counts == {}


def test_format_source_rejection_report_empty() -> None:
    assert format_source_rejection_report({}) == "No quality-gate rejection data found."


def test_format_source_rejection_report_sorted_by_count_desc() -> None:
    report = format_source_rejection_report({"Sports Desk": 1, "Wire Service": 3, "Tech Blog": 2})

    lines = report.splitlines()
    assert lines[0] == "Quality gate rejections by source"
    assert lines[1] == "count | source"
    assert lines[2] == "3 | Wire Service"
    assert lines[3] == "2 | Tech Blog"
    assert lines[4] == "1 | Sports Desk"
