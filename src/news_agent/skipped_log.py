from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from news_agent.models import QualityGateConfig, StoryCluster


DEFAULT_SKIPPED_DIR = Path("data")
DEFAULT_LOW_CONTENT_QUALITY_SKIP_THRESHOLD = QualityGateConfig().low_content_quality_skip_threshold


@dataclass(frozen=True)
class SkippedStory:
    headline: str
    category_candidate: str
    score: float
    reason_skipped: str
    source_names: tuple[str, ...]
    url: str
    duplicate_cluster_id: str = ""
    watchlist_match: tuple[str, ...] = ()


def default_skipped_path(today: date | None = None) -> Path:
    selected_day = today or date.today()
    return DEFAULT_SKIPPED_DIR / f"skipped_stories_{selected_day.isoformat()}.json"


def skip_reason(
    cluster: StoryCluster,
    selected_ids: set[int],
    category_full: bool = False,
    low_content_quality_threshold: float = DEFAULT_LOW_CONTENT_QUALITY_SKIP_THRESHOLD,
) -> str:
    if cluster.skip_reason:
        return cluster.skip_reason
    if id(cluster) in selected_ids:
        return ""
    if cluster.content_quality_penalty >= low_content_quality_threshold:
        return "low content quality"
    if cluster.quality_score < 0.55 and cluster.source_count <= 1:
        return "low source quality"
    if cluster.source_count <= 1 and cluster.impact_score < 3.0:
        return "no reliable source confirmation"
    if category_full:
        return "category already full"
    return "score below threshold"


def build_skipped_stories(
    clusters: list[StoryCluster],
    selected_clusters: list[StoryCluster],
    low_content_quality_threshold: float = DEFAULT_LOW_CONTENT_QUALITY_SKIP_THRESHOLD,
) -> list[SkippedStory]:
    selected_ids = {id(cluster) for cluster in selected_clusters}
    skipped: list[SkippedStory] = []
    for cluster in clusters:
        reason = skip_reason(
            cluster,
            selected_ids,
            category_full=id(cluster) not in selected_ids,
            low_content_quality_threshold=low_content_quality_threshold,
        )
        if not reason:
            continue
        category = cluster.category or "uncategorized"
        skipped.append(
            SkippedStory(
                headline=cluster.title,
                category_candidate=category,
                score=round(cluster.total_score, 2),
                reason_skipped=reason,
                source_names=tuple(cluster.sources),
                url=cluster.urls[0] if cluster.urls else "",
                duplicate_cluster_id=cluster.key,
                watchlist_match=cluster.watchlist_matches,
            )
        )
    skipped.sort(key=lambda item: item.score, reverse=True)
    return skipped


def write_skipped_log(skipped: list[SkippedStory], path: Path | None = None) -> Path:
    resolved = path or default_skipped_path()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    payload = [
        {
            "headline": item.headline,
            "category_candidate": item.category_candidate,
            "score": item.score,
            "reason_skipped": item.reason_skipped,
            "source_names": list(item.source_names),
            "url": item.url,
            "duplicate_cluster_id": item.duplicate_cluster_id,
            "watchlist_match": list(item.watchlist_match),
        }
        for item in skipped
    ]
    resolved.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return resolved


def format_skipped_table(skipped: list[SkippedStory], limit: int = 20) -> str:
    if not skipped:
        return "No skipped stories logged."
    lines = ["Skipped stories", "score | category | reason | headline | watchlist"]
    for item in skipped[:limit]:
        watchlist = ", ".join(item.watchlist_match) if item.watchlist_match else "-"
        lines.append(
            f"{item.score:.2f} | {item.category_candidate} | {item.reason_skipped} | {item.headline} | {watchlist}"
        )
    return "\n".join(lines)
