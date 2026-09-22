"""
docs_index.py

Builds a simple index.html listing every generated report under
docs/reports/, most recent first -- for GitHub Pages. Regenerated
fully from scratch on every run (stateless: it just scans whatever
.html files currently exist in the reports folder), so there's no
separate "database" of past reports to keep in sync.

Sorting is done by parsing the "-<DDMMYYYY><HHMM>.html" timestamp
suffix that report_filename.py's build_report_filename() always
appends (see that module) -- NOT by filename string order (which would
sort incorrectly whenever two reports have different raid-name
prefixes of different lengths) and NOT by filesystem mtime (which
would be wrong if files are ever re-copied/checked out by git in a
different order than they were originally created, e.g. after a fresh
clone -- git does not preserve original file creation/modification
timestamps).
"""
from __future__ import annotations

import datetime
import html as html_module
import os
import re
from dataclasses import dataclass

_TIMESTAMP_SUFFIX_RE = re.compile(r"-(\d{2})(\d{2})(\d{4})(\d{2})(\d{2})\.html$", re.IGNORECASE)


@dataclass
class IndexedReport:
    filename: str
    raid_name: str  # the sanitized name portion before the timestamp -- NOT necessarily human-readable (see report_filename.sanitize_for_filename)
    generated_at: datetime.datetime


def parse_report_filename(filename: str) -> IndexedReport | None:
    """
    Parse one report_filename.py-style filename into an IndexedReport.
    Returns None (rather than raising) for any .html file that doesn't
    match the expected "<Name>-<DDMMYYYY><HHMM>.html" pattern -- e.g. a
    hand-placed file that isn't one of this project's own generated
    reports -- so a stray file in the folder can't crash index
    generation; it's just silently excluded from the index instead.
    """
    match = _TIMESTAMP_SUFFIX_RE.search(filename)
    if not match:
        return None
    day, month, year, hour, minute = match.groups()
    try:
        generated_at = datetime.datetime(int(year), int(month), int(day), int(hour), int(minute))
    except ValueError:
        return None  # e.g. a filename that LOOKS like the pattern but has an impossible date (day=99, month=13, etc.)
    raid_name = filename[: match.start()]
    return IndexedReport(filename=filename, raid_name=raid_name, generated_at=generated_at)


def scan_reports_directory(reports_dir: str) -> list[IndexedReport]:
    """
    List every valid report in `reports_dir`, sorted NEWEST FIRST by
    their parsed generation timestamp. Returns an empty list (not an
    error) if the directory doesn't exist yet -- e.g. the very first
    time this ever runs, before any report has been generated.
    """
    if not os.path.isdir(reports_dir):
        return []
    indexed = []
    for filename in os.listdir(reports_dir):
        if not filename.lower().endswith(".html"):
            continue
        parsed = parse_report_filename(filename)
        if parsed is not None:
            indexed.append(parsed)
    indexed.sort(key=lambda r: r.generated_at, reverse=True)
    return indexed


def _esc(value) -> str:
    return html_module.escape(str(value))


def render_index_html(
    reports: list[IndexedReport],
    reports_subfolder_name: str = "reports",
    title: str = "Raid Reports",
) -> str:
    """
    Render a minimal, dependency-free index page listing every report,
    newest first, each linking to its file under `reports_subfolder_name/`.
    """
    if not reports:
        body = '<p class="empty">No reports have been generated yet.</p>'
    else:
        items = []
        for r in reports:
            timestamp_str = r.generated_at.strftime("%A %d %B %Y, %H:%M")
            href = f"{reports_subfolder_name}/{r.filename}"
            items.append(
                f'<li><a href="{_esc(href)}">{_esc(r.raid_name)}</a>'
                f'<span class="ts"> &mdash; generated {_esc(timestamp_str)}</span></li>'
            )
        body = "<ul>" + "".join(items) + "</ul>"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{_esc(title)}</title>
<style>
body {{ background:#14151a; color:#e8e8ec; font-family:-apple-system,"Segoe UI",Roboto,Arial,sans-serif; margin:0; padding:24px; }}
h1 {{ font-size:20px; margin:0 0 16px 0; }}
ul {{ list-style:none; padding:0; margin:0; max-width:700px; }}
li {{ padding:10px 14px; border-bottom:1px solid #2f3140; }}
a {{ color:#6c8cff; text-decoration:none; font-weight:600; }}
a:hover {{ text-decoration:underline; }}
.ts {{ color:#9a9db0; font-size:12.5px; }}
.empty {{ color:#9a9db0; font-style:italic; }}
</style>
</head>
<body>
<h1>{_esc(title)}</h1>
{body}
</body>
</html>"""


def build_index(reports_dir: str, index_path: str, title: str = "Raid Reports") -> list[IndexedReport]:
    """
    Scan `reports_dir` and write index.html to `index_path`. Returns
    the list of reports found, so the caller can log/print a summary
    without needing to re-scan the directory itself.
    """
    reports = scan_reports_directory(reports_dir)
    reports_subfolder_name = os.path.basename(os.path.normpath(reports_dir))
    html_content = render_index_html(reports, reports_subfolder_name=reports_subfolder_name, title=title)
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(html_content)
    return reports
