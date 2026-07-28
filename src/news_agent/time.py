from __future__ import annotations

import os
from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DEFAULT_TIMEZONE = "America/New_York"


def briefing_timezone() -> ZoneInfo:
    name = os.getenv("BRIEFING_TIMEZONE", DEFAULT_TIMEZONE).strip() or DEFAULT_TIMEZONE
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Invalid BRIEFING_TIMEZONE={name!r}") from exc


def briefing_now() -> datetime:
    return datetime.now(briefing_timezone())


def briefing_today() -> date:
    return briefing_now().date()
