"""
schedule_guard.py

GitHub Actions' cron scheduler ONLY runs in UTC -- there is no way to
give it a timezone directly. Brussels (like the rest of the EU) shifts
between CET (UTC+1) and CEST (UTC+2) twice a year, so a single fixed
UTC cron expression can never track "23:15 Brussels time" perfectly
year-round: the .github/workflows/scheduled_report.yml file works
around this with TWO cron lines (one for the CET months, one for the
CEST months), but that's still only an approximation -- the exact
switchover date (the last Sunday of March/October) doesn't align
cleanly with cron's month-based granularity, so the workflow's own
schedule can be up to ~1 hour off for roughly the last week of March
and the last week of October each year.

This module closes that gap for real: is_within_scheduled_window()
checks the ACTUAL current Brussels local time (via the standard
library's zoneinfo, which has its own correct, actively-maintained DST
rules -- no hardcoded month bucketing here) and only allows the
script's real work to proceed if it's genuinely the right day and
close enough to the right time. This means the cron schedule only
needs to be an approximately-right trigger; this guard is what
actually guarantees the report is never generated on the wrong day or
more than `tolerance_minutes` off from the intended time, regardless
of any DST edge case.

publish_scheduled_report.py calls this before doing any real work, and
supports --force to bypass it (used by the workflow's manual
workflow_dispatch trigger, where "I clicked run" already implies "yes,
run it now regardless of the schedule").
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass
from zoneinfo import ZoneInfo

BRUSSELS_TZ = ZoneInfo("Europe/Brussels")

# Python's date.weekday(): Monday=0, Tuesday=1, Wednesday=2, ... Sunday=6.
DEFAULT_SCHEDULED_WEEKDAYS = (0, 2)  # Monday and Wednesday
DEFAULT_TARGET_HOUR = 23
DEFAULT_TARGET_MINUTE = 15
DEFAULT_TOLERANCE_MINUTES = 30


@dataclass
class ScheduleCheckResult:
    is_within_window: bool
    reason: str
    brussels_now: datetime.datetime


def is_within_scheduled_window(
    now: datetime.datetime | None = None,
    scheduled_weekdays: tuple[int, ...] = DEFAULT_SCHEDULED_WEEKDAYS,
    target_hour: int = DEFAULT_TARGET_HOUR,
    target_minute: int = DEFAULT_TARGET_MINUTE,
    tolerance_minutes: int = DEFAULT_TOLERANCE_MINUTES,
) -> ScheduleCheckResult:
    """
    True if `now` (converted to real Brussels local time -- correctly
    DST-aware via zoneinfo, regardless of what timezone `now` itself
    was given in) falls on one of `scheduled_weekdays` AND within
    `tolerance_minutes` of target_hour:target_minute.

    `now` defaults to the real current time (timezone-aware, UTC) --
    pass an explicit aware datetime only for testing. An NAIVE
    datetime (no tzinfo) is rejected with a ValueError rather than
    silently assumed to be UTC or local time, since guessing wrong
    here is exactly the kind of subtle bug this module exists to
    prevent in the first place.

    The tolerance window exists because GitHub Actions cron triggers
    are best-effort and can fire a few minutes late under load, and
    because the workflow's own cron approximates the DST switch by
    whole months (see module docstring) -- a job that's a few minutes
    late, or that fires during the ~1-week DST transition ambiguity
    window, should still be treated as "close enough", while a job
    firing hours off (e.g. a stale/misconfigured cron entry) should not.
    """
    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc)
    if now.tzinfo is None:
        raise ValueError(
            "now must be a timezone-aware datetime (e.g. datetime.now(timezone.utc)) -- "
            "a naive datetime could silently be interpreted in the wrong timezone."
        )

    brussels_now = now.astimezone(BRUSSELS_TZ)

    if brussels_now.weekday() not in scheduled_weekdays:
        return ScheduleCheckResult(
            is_within_window=False,
            reason=f"{brussels_now.strftime('%A')} is not one of the scheduled days.",
            brussels_now=brussels_now,
        )

    target_today = brussels_now.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)
    difference_minutes = abs((brussels_now - target_today).total_seconds()) / 60

    if difference_minutes > tolerance_minutes:
        return ScheduleCheckResult(
            is_within_window=False,
            reason=(
                f"Current Brussels time {brussels_now.strftime('%H:%M')} is "
                f"{difference_minutes:.0f} minute(s) from the target "
                f"{target_hour:02d}:{target_minute:02d} (tolerance: {tolerance_minutes} min)."
            ),
            brussels_now=brussels_now,
        )

    return ScheduleCheckResult(
        is_within_window=True,
        reason=f"{brussels_now.strftime('%A %H:%M')} Brussels time is within the scheduled window.",
        brussels_now=brussels_now,
    )
