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
