"""
time_format.py
One shared helper for rendering fight-relative millisecond timestamps as
M:SS (e.g. 183000 -> "3:03") instead of raw seconds (e.g. "183.0s"),
used consistently across report.py, death_analyzer.py,
cooldown_analyzer.py, and html_report.py.

Pulled out into its own module (rather than defined once in report.py
and imported elsewhere) specifically to avoid a circular import: both
death_analyzer.py and cooldown_analyzer.py are imported BY report.py,
so they can't import format_timestamp back from report.py.
"""
from __future__ import annotations


def format_timestamp(ms: float) -> str:
    """
    Format a fight-relative timestamp in milliseconds as M:SS.
    Seconds are zero-padded to 2 digits; minutes are not padded and can
    exceed 59 (e.g. a 65-minute fight shows "65:07", not "1:05:07"),
    since encounters are never long enough to need an hours place, and
    this keeps the format consistent/sortable as plain text. Negative
    or garbage input is clamped to 0:00 rather than raising, so a
    single bad event can't crash an entire report render.
    """
    total_seconds = max(0, ms) / 1000.0
    minutes = int(total_seconds // 60)
    seconds = int(total_seconds % 60)
    return f"{minutes}:{seconds:02d}"


def fight_relative_ms(raw_ms: float, fight_start_time: float, fight_duration_ms: float) -> int:
    """
    Convert a RAW, report-relative timestamp (the convention used by
    Event.timestamp, and therefore by anything copied straight from it --
    confirmed for CooldownUsage.cast_timestamps in cooldown_analyzer.py,
    and ASSUMED (not independently verified this session -- flagging
    for a follow-up check) for DefensiveWindow.cast_timestamp in
    defensive_damage_prevention_analyzer.py, since it's built from the
    same underlying Casts events) into a FIGHT-relative offset in
    milliseconds, clamped to [0, fight_duration_ms] so a slightly-off
    timestamp can't push a marker outside its own timeline strip.
    death_analyzer.py already does this exact subtraction inline
    (time_into_fight_ms = death.timestamp - fight.start_time) --
    this helper just makes the same conversion reusable and clamped
    for html_report.py's timeline-strip markers.
    """
    relative = raw_ms - fight_start_time
    return int(max(0, min(fight_duration_ms, relative)))
