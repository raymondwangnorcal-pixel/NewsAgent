from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from news_agent.models import MarketMover, StoryCluster


DEFAULT_ALERT_HISTORY_PATH = Path("data/alert_history.json")
DEFAULT_ALERT_CONFIG_PATH = Path("config/alerts.json")


@dataclass(frozen=True)
class AlertConfig:
    enabled: bool = False
    cooldown_minutes: int = 180
    min_source_count: int = 5
    major_index_threshold: float = 2.0
    mega_cap_threshold: float = 7.0
    crypto_threshold: float = 8.0


@dataclass(frozen=True)
class NewsAlert:
    alert_id: str
    headline: str
    summary: str
    why_it_matters: str
    sources: tuple[str, ...]
    confidence: str

    def to_message(self) -> str:
        sources = ", ".join(self.sources[:5])
        return (
            f"Alert: {self.headline}\n"
            f"Summary: {self.summary}\n"
            f"Why it matters: {self.why_it_matters}\n"
            f"Confidence: {self.confidence}\n"
            f"Sources: {sources}"
        ).strip()


def load_alert_config(path: Path = DEFAULT_ALERT_CONFIG_PATH) -> AlertConfig:
    if not path.exists():
        return AlertConfig()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return AlertConfig()
    alerts = data.get("alerts", data) if isinstance(data, dict) else {}
    thresholds = alerts.get("market_thresholds", {}) if isinstance(alerts, dict) else {}
    return AlertConfig(
        enabled=bool(alerts.get("enabled", False)),
        cooldown_minutes=int(alerts.get("cooldown_minutes", 180)),
        min_source_count=int(alerts.get("min_source_count", 5)),
        major_index_threshold=float(thresholds.get("major_index", 2.0)),
        mega_cap_threshold=float(thresholds.get("mega_cap", 7.0)),
        crypto_threshold=float(thresholds.get("crypto", 8.0)),
    )


def alert_id(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


def market_alerts(movers: tuple[MarketMover, ...], config: AlertConfig) -> list[NewsAlert]:
    alerts: list[NewsAlert] = []
    for mover in movers:
        threshold = None
        if mover.asset_type == "major_index_etf":
            threshold = config.major_index_threshold
        elif mover.asset_type == "mega_cap_stock":
            threshold = config.mega_cap_threshold
        elif mover.asset_type == "crypto":
            threshold = config.crypto_threshold
        if threshold is None or abs(mover.percent_change) < threshold:
            continue
        direction = "up" if mover.percent_change > 0 else "down"
        alerts.append(
            NewsAlert(
                alert_id=alert_id("market", mover.symbol, f"{mover.percent_change:.1f}"),
                headline=f"{mover.symbol} moves {direction} {abs(mover.percent_change):.1f}%",
                summary=mover.move_reason
                or "The asset moved sharply, but the immediate catalyst was not clear from major headlines.",
                why_it_matters="A move this large can quickly affect risk appetite and related assets.",
                sources=mover.reason_sources or ("Stooq",),
                confidence="high" if mover.reason_confidence == "high" else "medium",
            )
        )
    return alerts


def cluster_alerts(clusters: list[StoryCluster], config: AlertConfig) -> list[NewsAlert]:
    alerts: list[NewsAlert] = []
    trigger_terms = (
        "emergency",
        "fed",
        "war",
        "missile",
        "invasion",
        "supreme court",
        "indictment",
        "ai",
        "antitrust",
        "acquisition",
    )
    for cluster in clusters:
        text = f"{cluster.title} {cluster.representative_summary}".lower()
        if cluster.source_count < config.min_source_count and not any(term in text for term in trigger_terms):
            continue
        if cluster.source_count < 2:
            continue
        confidence = "high" if cluster.source_count >= config.min_source_count else "medium"
        alerts.append(
            NewsAlert(
                alert_id=alert_id("story", cluster.key, cluster.title),
                headline=cluster.title,
                summary=cluster.representative_summary,
                why_it_matters=cluster.why_it_matters
                or "The story is appearing across reputable sources and may develop quickly.",
                sources=tuple(cluster.sources[:5]),
                confidence=confidence,
            )
        )
    return alerts


def load_alert_history(path: Path = DEFAULT_ALERT_HISTORY_PATH) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    alerts = data.get("alerts", {})
    return alerts if isinstance(alerts, dict) else {}


def parse_seen_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def apply_alert_cooldown(
    alerts: list[NewsAlert],
    path: Path = DEFAULT_ALERT_HISTORY_PATH,
    cooldown_minutes: int = 180,
) -> list[NewsAlert]:
    history = load_alert_history(path)
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=cooldown_minutes)
    return [
        alert
        for alert in alerts
        if parse_seen_time(history.get(alert.alert_id, "")) < cutoff
    ]


def save_alert_history(alerts: list[NewsAlert], path: Path = DEFAULT_ALERT_HISTORY_PATH) -> None:
    history = load_alert_history(path)
    now = datetime.now(timezone.utc).isoformat()
    for alert in alerts:
        history[alert.alert_id] = now
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"alerts": history}, indent=2, sort_keys=True), encoding="utf-8")


def generate_alerts(
    clusters: list[StoryCluster],
    movers: tuple[MarketMover, ...],
    config: AlertConfig,
) -> list[NewsAlert]:
    if not config.enabled:
        return []
    alerts = market_alerts(movers, config) + cluster_alerts(clusters, config)
    alerts.sort(key=lambda item: (item.confidence == "high", len(item.sources)), reverse=True)
    return alerts
