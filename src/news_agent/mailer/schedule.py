from __future__ import annotations

from datetime import datetime, time

from news_agent.time import briefing_now


DAILY_SEND_TIME = time(8, 20)
LAST_RETRY_TIME = time(8, 35)


def scheduled_email_is_due(now: datetime | None = None) -> bool:
    """Return whether an automated Gmail attempt is inside its retry window."""
    local_now = now or briefing_now()
    local_time = local_now.timetz().replace(tzinfo=None)
    return DAILY_SEND_TIME <= local_time <= LAST_RETRY_TIME
