"""
report_filename.py

Builds the output path for generate_html_report.py's HTML reports:

    reports/<SanitizedRaidName>-<DDMMYYYY><HHMM>.html

The date/time used is ALWAYS "now" -- the moment the report is being
GENERATED on your machine (i.e. when you run the .py module) -- never
the date the raid actually happened or anything read from the log
itself. This lets you re-run the same report code multiple times (e.g.
after a consumables config fix) and get a distinctly-named file each
time rather than overwriting the previous one.

Example: a raid zone named "The Venomous Abyss", report generated on
20 September 2026 at 11:47, produces:
    reports/TheVenomousAbyss-200920261147.html

Sanitization strips every character that isn't a letter or digit
(spaces, apostrophes, colons, etc.) -- "The Venomous Abyss" becomes
"TheVenomousAbyss". The FULL filename always ends with the literal
".html" extension -- build_report_filename()/build_report_path() both
guarantee this unconditionally, so the output is never accidentally
saved with no extension (which some file explorers display as a
generic "File" type rather than recognizing it as HTML).
"""
from __future__ import annotations

import datetime
import os
import re

REPORTS_SUBFOLDER = "reports"
_FALLBACK_NAME = "Raid"
_SANITIZE_PATTERN = re.compile(r"[^A-Za-z0-9]")
_EXTENSION = ".html"


def sanitize_for_filename(name: str) -> str:
    """Strip every non-alphanumeric character. Falls back to _FALLBACK_NAME if that leaves nothing."""
    cleaned = _SANITIZE_PATTERN.sub("", name or "")
    return cleaned or _FALLBACK_NAME


def build_report_filename(raid_name: str, generated_at: datetime.datetime | None = None) -> str:
    """
    Build just the filename (no directory), e.g.
    "TheVenomousAbyss-200920261147.html". `generated_at` defaults to
    datetime.datetime.now() -- pass an explicit value only for testing.
    """
    generated_at = generated_at or datetime.datetime.now()
    timestamp = generated_at.strftime("%d%m%Y%H%M")
    return f"{sanitize_for_filename(raid_name)}-{timestamp}{_EXTENSION}"


def build_report_path(
    raid_name: str,
    reports_dir: str = REPORTS_SUBFOLDER,
    generated_at: datetime.datetime | None = None,
) -> str:
    """Build the full path, e.g. "reports/TheVenomousAbyss-200920261147.html", relative to the current working directory."""
    filename = build_report_filename(raid_name, generated_at=generated_at)
    return os.path.join(reports_dir, filename)


def ensure_reports_dir(reports_dir: str = REPORTS_SUBFOLDER) -> str:
    """Create the reports subfolder if it doesn't exist yet (no-op if it already does). Returns the directory path."""
    os.makedirs(reports_dir, exist_ok=True)
    return reports_dir


def ensure_html_extension(path: str) -> str:
    """
    Defensive helper: guarantees `path` ends with ".html", regardless
    of what was passed in. Used as a final safety net for the
    user-supplied --output-path override in generate_html_report.py,
    so an explicit override can never accidentally produce an
    extensionless (or wrongly-extensioned) file either.
    """
    if path.lower().endswith(_EXTENSION):
        return path
    return path + _EXTENSION
