"""When a scheduled run is allowed to send.

The briefing is triggered at 8:20 in BRIEFING_TIMEZONE by an external cron and
runs on GitHub Actions, whose queue can hold a run for minutes or, on a bad day,
hours. A run that starts inside the window sends that day's briefing. One that
starts after the cutoff does not: a "morning" briefing at lunch undermines the
promise more than a missed day does, and a missed day is visible -- the run
prints why, and nothing is published, which the morning check reports.

The window also opens well before the trigger, so a trigger firing at the wrong
hour (a timezone misconfiguration upstream) is caught and logged rather than
sent at four in the morning.
"""
from __future__ import annotations

from datetime import datetime, time

from news_agent.time import briefing_now, briefing_timezone


SEND_WINDOW_OPENS = time(8, 0)
SEND_CUTOFF = time(10, 30)


def scheduled_email_is_due(now: datetime | None = None) -> bool:
    """Whether a scheduled run that starts at *now* should send."""
    local_now = now or briefing_now()
    local_time = local_now.timetz().replace(tzinfo=None)
    return SEND_WINDOW_OPENS <= local_time <= SEND_CUTOFF


def scheduled_skip_message(now: datetime | None = None) -> str:
    """The line a skipped run prints, so the Actions log says what happened."""
    local_now = now or briefing_now()
    started = local_now.strftime("%-I:%M %p")
    window = f"{SEND_WINDOW_OPENS.strftime('%-I:%M')}–{SEND_CUTOFF.strftime('%-I:%M %p')}"
    return (
        f"Warning: scheduled send skipped: the run started at {started} {briefing_timezone().key}, "
        f"outside the {window} send window. No briefing today."
    )
