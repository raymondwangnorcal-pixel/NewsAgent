from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from news_agent.cluster import extract_entities, normalize_title, tokenize
from news_agent.models import StoryCluster


DEFAULT_HISTORY_PATH = Path("data/story_history.json")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_datetime(value: str | None) -> datetime:
    if not value:
        return utc_now()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return utc_now()
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def cluster_category(cluster: StoryCluster) -> str:
    if cluster.category_candidates:
        return cluster.category_candidates[0]
    return "uncategorized"


def cluster_keywords(cluster: StoryCluster) -> set[str]:
    text = f"{cluster.title} {cluster.representative_summary}"
    return {token for token in tokenize(normalize_title(text)) if len(token) > 3}


def stable_cluster_hash(cluster: StoryCluster, category: str | None = None) -> str:
    entities = sorted(extract_entities(f"{cluster.title} {cluster.representative_summary}"))[:8]
    keywords = sorted(cluster_keywords(cluster))[:12]
    base = "|".join((category or cluster_category(cluster), " ".join(entities), " ".join(keywords)))
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]


def load_history(path: Path = DEFAULT_HISTORY_PATH) -> dict[str, Any]:
    if not path.exists():
        return {"stories": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"stories": []}
    if not isinstance(data, dict):
        return {"stories": []}
    stories = data.get("stories", [])
    return {"stories": stories if isinstance(stories, list) else []}


def keyword_overlap(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def find_history_record(cluster: StoryCluster, records: list[dict[str, Any]]) -> dict[str, Any] | None:
    cluster_id = stable_cluster_hash(cluster)
    category = cluster_category(cluster)
    keywords = cluster_keywords(cluster)
    for record in records:
        if record.get("cluster_id") == cluster_id:
            return record
    best_record = None
    best_overlap = 0.0
    for record in records:
        if record.get("category") != category:
            continue
        record_keywords = set(record.get("keywords", []))
        overlap = keyword_overlap(keywords, record_keywords)
        if overlap > best_overlap:
            best_record = record
            best_overlap = overlap
    return best_record if best_overlap >= 0.62 else None


def has_meaningful_update(cluster: StoryCluster, record: dict[str, Any]) -> bool:
    old_sources = set(record.get("sources", []))
    new_sources = set(cluster.sources) - old_sources
    old_keywords = set(record.get("keywords", []))
    added_keywords = cluster_keywords(cluster) - old_keywords
    old_summary = str(record.get("summary", ""))
    summary_changed = keyword_overlap(tokenize(old_summary), tokenize(cluster.representative_summary)) < 0.75
    return len(new_sources) >= 1 or len(added_keywords) >= 4 or summary_changed


def apply_history(
    clusters: list[StoryCluster],
    path: Path = DEFAULT_HISTORY_PATH,
    ignore_history: bool = False,
) -> None:
    if ignore_history:
        return
    records = load_history(path)["stories"]
    for cluster in clusters:
        record = find_history_record(cluster, records)
        if record is None:
            continue
        if has_meaningful_update(cluster, record):
            previous_seen = record.get("last_seen_time") or record.get("first_seen_time")
            cluster.is_update = True
            cluster.update_note = f"New sources or details since {previous_seen[:10]}."
            cluster.total_score += 0.6
        else:
            cluster.skip_reason = "stale/repeated from yesterday"
            cluster.total_score -= 4.0


def story_record(cluster: StoryCluster, now: datetime) -> dict[str, Any]:
    record = {
        "cluster_id": stable_cluster_hash(cluster),
        "canonical_headline": cluster.title,
        "keywords": sorted(cluster_keywords(cluster)),
        "first_seen_time": now.isoformat(),
        "last_seen_time": now.isoformat(),
        "category": cluster_category(cluster),
        "sources": cluster.sources,
        "summary": cluster.representative_summary,
        "last_update_note": cluster.update_note,
    }
    return record


def save_story_history(
    clusters: list[StoryCluster],
    path: Path = DEFAULT_HISTORY_PATH,
    retention_days: int = 7,
) -> None:
    now = utc_now()
    cutoff = now - timedelta(days=retention_days)
    existing = load_history(path)["stories"]
    by_id: dict[str, dict[str, Any]] = {}
    for record in existing:
        last_seen = parse_datetime(record.get("last_seen_time"))
        if last_seen >= cutoff:
            by_id[str(record.get("cluster_id"))] = dict(record)

    for cluster in clusters:
        record = story_record(cluster, now)
        existing_record = by_id.get(record["cluster_id"])
        if existing_record:
            record["first_seen_time"] = existing_record.get("first_seen_time", record["first_seen_time"])
        by_id[record["cluster_id"]] = record

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"stories": list(by_id.values())}, indent=2, sort_keys=True), encoding="utf-8")
