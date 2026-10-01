"""
automation.py

MERGED MODULE -- combines what used to be three separate files into
one, as part of the PullDoctor file-count reduction pass:
    - schedule_guard.py            (DST-safe "is it really the right time yet?" check)
    - latest_guild_report.py       (find a guild's single most-recent report)
    - publish_scheduled_report.py  (the unattended script the GitHub Actions workflow runs)

These three were grouped together because they're all part of the same
"run this completely unattended, on a schedule, with nobody watching"
pipeline -- schedule_guard decides WHEN it's allowed to actually do
work, latest_guild_report decides WHICH report to work on, and
publish_scheduled_report ties both together with the rest of the
already-merged analysis/report-writing pipeline (core.py, reporting.py,
main.py) to produce the actual GitHub Pages output.

Sections are ordered by dependency: schedule_guard (no deps) ->
latest_guild_report (depends on wcl.py's WCLClient/WCLAPIError) ->
publish_scheduled_report (depends on both sections above, plus wcl.py,
core.py, reporting.py, and main.py).

NOTE on cross-file symbol renames: publish_scheduled_report.py
previously imported from a half-dozen now-merged modules under their
OLD standalone names (wcl_api.py, config.py, guild_config.py,
log_parser.py, player_roles.py, html_report.py, docs_index.py,
report_filename.py, fight_filters.py). All of these have since been
merged into wcl.py, core.py, and reporting.py respectively -- every
import below has been updated to pull from the CURRENT merged location
instead. Every renamed call site keeps the exact same function/
argument signature as the original; only the import path (and, where a
module-qualified prefix like `config.get_credentials()` or
`guild_config.GUILD_NAME` no longer applies because the whole module
was absorbed, the call-site prefix) changed. schedule_guard.py and
latest_guild_report.py's own internals needed NO changes beyond this
-- they're reproduced here exactly as sections of this file.
"""
from __future__ import annotations

import datetime
import os
import sys
import time
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from wcl import (
    GUILD_NAME,
    GUILD_SERVER_REGION,
    GUILD_SERVER_SLUG,
    WCLAPIError,
    WCLClient,
    get_credentials,
    is_configured,
)
from core import (
    FightFilterCriteria,
    filter_fights,
    format_filter_summary,
    parse_fight_bundle,
    parse_player_roles,
)
from reporting import (
    build_index,
    build_report_filename,
    sanitize_for_filename,
    write_html_report,
)
from main import EVENT_TYPES_NEEDED, run_all_analyzers


# =======================================================================
# SECTION 1 -- DST-safe scheduled-window check
# (originally schedule_guard.py)
#
# GitHub Actions' cron scheduler ONLY runs in UTC -- there is no way to
# give it a timezone directly. Brussels (like the rest of the EU) shifts
# between CET (UTC+1) and CEST (UTC+2) twice a year, so a single fixed
# UTC cron expression can never track "23:15 Brussels time" perfectly
# year-round: the .github/workflows/scheduled_report.yml file works
# around this with TWO cron lines (one for the CET months, one for the
# CEST months), but that's still only an approximation -- the exact
# switchover date (the last Sunday of March/October) doesn't align
# cleanly with cron's month-based granularity, so the workflow's own
# schedule can be up to ~1 hour off for roughly the last week of March
# and the last week of October each year.
#
# is_within_scheduled_window() closes that gap for real: it checks the
# ACTUAL current Brussels local time (via the standard library's
# zoneinfo, which has its own correct, actively-maintained DST rules --
# no hardcoded month bucketing here) and only allows the script's real
# work to proceed if it's genuinely the right day and close enough to
# the right time. This means the cron schedule only needs to be an
# approximately-right trigger; this guard is what actually guarantees
# the report is never generated on the wrong day or more than
# `tolerance_minutes` off from the intended time, regardless of any DST
# edge case.
#
# GITHUB ACTIONS SCHEDULING DELAY -- the other half of the gap: cron-
# triggered workflow runs on GitHub's *shared* runners are best-effort
# and are documented to sometimes fire late under load, occasionally by
# an hour or more. A run intended for "Wednesday 23:15 Brussels" can
# therefore actually start executing well after midnight, i.e. on
# Thursday local time. The ORIGINAL version of this guard only checked
# "is today's weekday a scheduled weekday", which made it reject these
# late-started runs outright (the workflow then reports success, but
# nothing gets published, since the guard bailed out before doing any
# work) -- that's the bug: a late GitHub Actions start silently turns
# into a skipped report. late_start_grace_minutes below fixes this by
# also accepting a run that starts shortly after midnight as still
# "belonging" to the previous calendar day's scheduled slot, as long as
# it starts within that grace window of the previous day's target time.
#
# Section 3 (publish_scheduled_report) calls this before doing any real
# work, and supports --force to bypass it (used by the workflow's
# manual workflow_dispatch trigger, where "I clicked run" already
# implies "yes, run it now regardless of the schedule").
# =======================================================================

BRUSSELS_TZ = ZoneInfo("Europe/Brussels")

# Python's date.weekday(): Monday=0, Tuesday=1, Wednesday=2, ... Sunday=6.
DEFAULT_SCHEDULED_WEEKDAYS = (0, 2)  # Monday and Wednesday
DEFAULT_TARGET_HOUR = 23
DEFAULT_TARGET_MINUTE = 15
DEFAULT_TOLERANCE_MINUTES = 30

# How late (in minutes, past midnight) a run is still allowed to start
# and be treated as belonging to the PREVIOUS day's scheduled slot.
# This absorbs GitHub Actions' own documented best-effort delay on
# cron-triggered workflows, which can push a 23:15 Wednesday run into
# the small hours of Thursday. 6 hours comfortably covers the delays
# seen in practice while still refusing a run that's genuinely on the
# wrong day (e.g. a Thursday afternoon manual mistake would still be
# well outside this window).
DEFAULT_LATE_START_GRACE_MINUTES = 360


@dataclass
class ScheduleCheckResult:
    is_within_window: bool
    reason: str
    brussels_now: datetime.datetime


def _minutes_since_target(brussels_now: datetime.datetime, target_day: datetime.date,
                           target_hour: int, target_minute: int) -> float:
    target_dt = datetime.datetime.combine(
        target_day, datetime.time(hour=target_hour, minute=target_minute), tzinfo=BRUSSELS_TZ,
    )
    return (brussels_now - target_dt).total_seconds() / 60


def is_within_scheduled_window(
    now: datetime.datetime | None = None,
    scheduled_weekdays: tuple[int, ...] = DEFAULT_SCHEDULED_WEEKDAYS,
    target_hour: int = DEFAULT_TARGET_HOUR,
    target_minute: int = DEFAULT_TARGET_MINUTE,
    tolerance_minutes: int = DEFAULT_TOLERANCE_MINUTES,
    late_start_grace_minutes: int = DEFAULT_LATE_START_GRACE_MINUTES,
) -> ScheduleCheckResult:
    """
    True if `now` (converted to real Brussels local time -- correctly
    DST-aware via zoneinfo, regardless of what timezone `now` itself
    was given in) falls on one of `scheduled_weekdays` AND within
    `tolerance_minutes` of target_hour:target_minute -- OR if `now`
    falls shortly after midnight on the day immediately AFTER a
    scheduled weekday, within `late_start_grace_minutes` of that
    PREVIOUS day's target time. The second case exists because GitHub
    Actions cron triggers are best-effort and documented to sometimes
    fire late; a run meant for e.g. Wednesday 23:15 can actually start
    on Thursday 02:30, and should still be treated as that Wednesday's
    run rather than being rejected as "wrong day".

    `now` defaults to the real current time (timezone-aware, UTC) --
    pass an explicit aware datetime only for testing. A NAIVE
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
    firing hours off on a genuinely wrong day should not.
    """
    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc)
    if now.tzinfo is None:
        raise ValueError(
            "now must be a timezone-aware datetime (e.g. datetime.now(timezone.utc)) -- "
            "a naive datetime could silently be interpreted in the wrong timezone."
        )
    brussels_now = now.astimezone(BRUSSELS_TZ)

    # Case 1: today is a scheduled weekday and we're close enough to today's target time.
    if brussels_now.weekday() in scheduled_weekdays:
        difference_minutes = abs(_minutes_since_target(brussels_now, brussels_now.date(), target_hour, target_minute))
        if difference_minutes <= tolerance_minutes:
            return ScheduleCheckResult(
                is_within_window=True,
                reason=f"{brussels_now.strftime('%A %H:%M')} Brussels time is within the scheduled window.",
                brussels_now=brussels_now,
            )

    # Case 2: yesterday was a scheduled weekday, and we're within the late-start
    # grace window of YESTERDAY's target time (i.e. a run that was supposed to
    # start yesterday evening but was delayed by GitHub Actions into the early
    # hours of today).
    yesterday = brussels_now.date() - datetime.timedelta(days=1)
    if yesterday.weekday() in scheduled_weekdays:
        minutes_late = _minutes_since_target(brussels_now, yesterday, target_hour, target_minute)
        if tolerance_minutes < minutes_late <= late_start_grace_minutes:
            return ScheduleCheckResult(
                is_within_window=True,
                reason=(
                    f"{brussels_now.strftime('%A %H:%M')} Brussels time is a late-started run "
                    f"({minutes_late:.0f} minute(s) after {yesterday.strftime('%A')}'s "
                    f"{target_hour:02d}:{target_minute:02d} target, within the "
                    f"{late_start_grace_minutes}-minute late-start grace window) -- "
                    f"treating it as that day's scheduled run."
                ),
                brussels_now=brussels_now,
            )

    if brussels_now.weekday() not in scheduled_weekdays:
        return ScheduleCheckResult(
            is_within_window=False,
            reason=f"{brussels_now.strftime('%A')} is not one of the scheduled days.",
            brussels_now=brussels_now,
        )

    target_today = brussels_now.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)
    difference_minutes = abs((brussels_now - target_today).total_seconds()) / 60
    return ScheduleCheckResult(
        is_within_window=False,
        reason=(
            f"Current Brussels time {brussels_now.strftime('%H:%M')} is "
            f"{difference_minutes:.0f} minute(s) from the target "
            f"{target_hour:02d}:{target_minute:02d} (tolerance: {tolerance_minutes} min)."
        ),
        brussels_now=brussels_now,
    )


# =======================================================================
# SECTION 2 -- find a guild's single most-recent report
# (originally latest_guild_report.py)
#
# Finds the single most recently-uploaded report for a guild, using
# WCLClient.get_guild_reports_page() (ReportData.reports(guildName,
# guildServerSlug, guildServerRegion, ...)).
#
# WHY THIS DOESN'T JUST TRUST "PAGE 1": Warcraft Logs' public API docs
# do not document what ORDER reports() returns results in (ascending or
# descending by startTime) -- and guessing wrong would silently make
# this whole feature return an OLD report instead of the newest one,
# which is exactly the kind of bug that could go unnoticed for weeks in
# an unattended scheduled job. So instead of assuming an order:
#   1. Fetch page 1 (up to `limit` reports).
#   2. If there's more than one page (per the pagination's own
#      "last_page" field), ALSO fetch the last page.
#   3. Compare every report's startTime seen across BOTH fetched pages,
#      and return whichever one has the highest startTime.
# This correctly finds the true most-recent report regardless of
# whether the API sorts ascending (newest would be on the last page) or
# descending (newest would be on page 1) -- it only costs 2 API calls
# total regardless of how many reports the guild has, rather than
# walking every page.
#
# For a fully-guaranteed-correct answer regardless of how the data is
# paginated/sorted (e.g. if you suspect something unusual, like results
# not being sorted by time at all), pass deep_scan=True to walk every
# page and compare all of them -- more API calls, but zero assumptions.
# =======================================================================

class NoGuildReportsFoundError(Exception):
    """Raised when a guild has zero reports at all (not a fetch error -- the guild is just empty/new)."""


@dataclass
class GuildReportSummary:
    code: str
    title: str
    start_time: float
    end_time: float
    zone_name: str | None


def _to_summary(raw_report: dict) -> GuildReportSummary:
    zone = raw_report.get("zone")
    return GuildReportSummary(
        code=raw_report["code"],
        title=raw_report.get("title", "Untitled Report"),
        start_time=raw_report["startTime"],
        end_time=raw_report["endTime"],
        zone_name=zone.get("name") if zone else None,
    )


def find_latest_guild_report(
    client: WCLClient,
    guild_name: str,
    guild_server_slug: str,
    guild_server_region: str,
    limit: int = 25,
    deep_scan: bool = False,
    start_time: float | None = None,
) -> GuildReportSummary:
    """
    Return the single most recent report for this guild. Raises
    NoGuildReportsFoundError if the guild genuinely has zero reports
    in the given window (distinguished from a real API/auth error,
    which raises WCLAPIError instead -- these are NOT the same
    situation and calling code should be able to tell them apart).

    start_time: optional UNIX ms timestamp -- if given, only reports
    starting at or after this time are considered at all (passed
    straight through to the API's own startTime filter, so an old
    guild's full report history never needs to be paginated through
    just to find something recent). See Section 3's
    publish_scheduled_report logic for why the actual scheduled job
    always sets this to "the last N days" rather than leaving it
    unbounded.
    """
    first_page = client.get_guild_reports_page(
        guild_name, guild_server_slug, guild_server_region, page=1, limit=limit, start_time=start_time,
    )
    all_raw_reports: list[dict] = list(first_page.get("data", []))
    total = first_page.get("total", len(all_raw_reports))
    if total == 0 or not all_raw_reports:
        window_note = " in the requested time window" if start_time is not None else ""
        raise NoGuildReportsFoundError(
            f"Guild '{guild_name}' ({guild_server_slug}-{guild_server_region}) "
            f"has no reports{window_note} on Warcraft Logs."
        )
    last_page_number = first_page.get("last_page", 1)
    current_page_number = first_page.get("current_page", 1)
    if deep_scan:
        page = current_page_number + 1
        while page <= last_page_number:
            next_page = client.get_guild_reports_page(
                guild_name, guild_server_slug, guild_server_region, page=page, limit=limit, start_time=start_time,
            )
            all_raw_reports.extend(next_page.get("data", []))
            page += 1
    elif last_page_number > current_page_number:
        # Fast path: also fetch just the LAST page -- see module
        # docstring for why comparing page 1 + the last page (rather
        # than trusting either one alone) correctly handles the report
        # ordering regardless of which direction it's sorted in.
        last_page = client.get_guild_reports_page(
            guild_name, guild_server_slug, guild_server_region, page=last_page_number, limit=limit, start_time=start_time,
        )
        all_raw_reports.extend(last_page.get("data", []))
    summaries = [_to_summary(r) for r in all_raw_reports]
    return max(summaries, key=lambda s: s.start_time)


def find_latest_guild_report_code(
    client: WCLClient,
    guild_name: str,
    guild_server_slug: str,
    guild_server_region: str,
    limit: int = 25,
    deep_scan: bool = False,
    start_time: float | None = None,
) -> str:
    """Convenience wrapper: same as find_latest_guild_report(), but returns just the report code string."""
    return find_latest_guild_report(
        client, guild_name, guild_server_slug, guild_server_region,
        limit=limit, deep_scan=deep_scan, start_time=start_time,
    ).code


# =======================================================================
# SECTION 3 -- the unattended script the GitHub Actions workflow runs
# (originally publish_scheduled_report.py)
#
# The script .github/workflows/scheduled_report.yml actually runs.
# Fully unattended -- no report code, no fight number, nothing typed in:
#   1. Look up Section-2-adjacent guild identity (GUILD_NAME/
#      GUILD_SERVER_SLUG/GUILD_SERVER_REGION, from wcl.py Section 3)
#      for your guild's name/server/region.
#   2. Find your guild's single most recent report automatically, via
#      Section 2's find_latest_guild_report() (deep_scan=True + a
#      startTime window -- see below for why both are used together
#      here specifically).
#   3. Generate the SAME HTML report generate_html_report.py already
#      produces (every qualifying fight in that report), reusing its
#      analysis pipeline (core.py + main.py's run_all_analyzers).
#   4. Write it into docs/reports/<RaidName>-<DDMMYYYY><HHMM>.html and
#      rebuild docs/index.html (via reporting.py's build_index(),
#      originally docs_index.py) so GitHub Pages (serving the docs/
#      folder) always has an up-to-date list linking to every report
#      generated so far.
#
# WHY deep_scan=True AND a startTime window, TOGETHER, HERE
# SPECIFICALLY: Section 2's fast 2-page default is a reasonable
# general-purpose efficiency tradeoff, but THIS script runs unattended
# on a schedule with no one watching -- correctness matters far more
# than saving 1-2 extra API calls, especially since this only runs
# twice a week (well within the 3,600 points/hour budget regardless).
# Passing a startTime filter for "only the last RECENT_REPORT_WINDOW
# days" ALSO keeps the guild's full report history from ever needing
# multiple pages walked in practice (a raid guild uploading a few logs
# a week will have every recent report fit on one page within that
# window), so deep_scan mostly just adds a safety margin rather than
# routinely walking a large number of pages.
#
# Usage (this is what the GitHub Actions workflow calls):
#     python automation.py
#     python automation.py --recent-days 21   # widen the window if nothing is found
#     python automation.py --force            # bypass the schedule check (manual run)
# =======================================================================

DEFAULT_RECENT_DAYS = 10
DOCS_DIR = "docs"
REPORTS_SUBDIR = "reports"


def build_argument_parser():
    import argparse
    parser = argparse.ArgumentParser(
        description="Find the guild's latest report and publish it as an HTML file under docs/, for GitHub Pages."
    )
    parser.add_argument(
        "--recent-days", type=int, default=DEFAULT_RECENT_DAYS,
        help=f"Only look at reports uploaded within this many days (default: {DEFAULT_RECENT_DAYS}). "
             f"Widen this if the guild hasn't raided in a while and nothing is found.",
    )
    parser.add_argument("--include-trash", action="store_true")
    parser.add_argument("--min-duration", type=float, default=15.0)
    parser.add_argument(
        "--force", action="store_true",
        help="Skip the day/time schedule check (Section 1's is_within_scheduled_window()) "
             "and run regardless of the current Brussels local time. Used by the workflow's "
             "manual 'Run workflow' button (workflow_dispatch) -- a manual trigger already "
             "implies you want it to run right now.",
    )
    return parser


def _recent_start_time_ms(recent_days: int) -> float:
    return (time.time() - recent_days * 86400) * 1000


def main() -> None:
    args = build_argument_parser().parse_args()

    if not args.force:
        check = is_within_scheduled_window()
        if not check.is_within_window:
            print(
                f"Not running: {check.reason} "
                f"(current Brussels time: {check.brussels_now.strftime('%A %Y-%m-%d %H:%M %Z')}). "
                f"This is expected -- the workflow's cron trigger fires more often than "
                f"the actual schedule, and this guard (Section 1) only lets real "
                f"work happen on the correct Monday/Wednesday evening (plus a late-start grace "
                f"window for delayed GitHub Actions runs), correctly accounting "
                f"for CET/CEST regardless of how the cron itself is approximated. "
                f"Use --force to bypass this (e.g. for a manual run)."
            )
            return
        print(f"Schedule check passed: {check.reason}")

    if not is_configured():
        print(
            "ERROR: wcl.py's guild identity (Section 3) still has its placeholder values. Edit "
            "GUILD_NAME / GUILD_SERVER_SLUG / GUILD_SERVER_REGION in that file "
            "(see the instructions inside it) before running this script."
        )
        sys.exit(1)

    try:
        client_id, client_secret = get_credentials()
    except RuntimeError as exc:
        print(exc)
        sys.exit(1)

    client = WCLClient(client_id, client_secret)

    print(
        f"Looking up the most recent report for guild "
        f"'{GUILD_NAME}' ({GUILD_SERVER_SLUG}-{GUILD_SERVER_REGION}), "
        f"within the last {args.recent_days} day(s)..."
    )
    try:
        latest = find_latest_guild_report(
            client,
            GUILD_NAME,
            GUILD_SERVER_SLUG,
            GUILD_SERVER_REGION,
            deep_scan=True,
            start_time=_recent_start_time_ms(args.recent_days),
        )
    except NoGuildReportsFoundError as exc:
        print(f"No reports found: {exc}")
        print(f"Try re-running with a wider window, e.g. --recent-days {args.recent_days * 3}.")
        sys.exit(1)
    except WCLAPIError as exc:
        print(f"Error looking up guild reports: {exc}")
        sys.exit(1)

    report_code = latest.code
    print(f"Found report: {latest.title!r} (code={report_code}, zone={latest.zone_name})")

    try:
        raw_report = client.get_report_fights(report_code)
    except WCLAPIError as exc:
        print(f"Error fetching report: {exc}")
        sys.exit(1)

    all_raw_fights = raw_report["fights"]
    criteria = FightFilterCriteria(only_boss_fights=not args.include_trash, min_duration_seconds=args.min_duration)
    filter_result = filter_fights(all_raw_fights, criteria)
    print(format_filter_summary(filter_result, criteria))

    raw_fights = filter_result.kept
    if not raw_fights:
        print("No qualifying fights in this report -- nothing to publish.")
        sys.exit(1)

    print(f"Fetching and analyzing {len(raw_fights)} fight(s)...")
    raw_master_data = client.get_report_master_data(report_code)

    all_fight_data = []
    for i, raw_fight in enumerate(raw_fights, start=1):
        fight_id = raw_fight["id"]
        print(f"  [{i}/{len(raw_fights)}] {raw_fight['name']} (#{fight_id})...")
        try:
            raw_events_by_type = client.get_report_events_multi(report_code, fight_id, event_types=EVENT_TYPES_NEEDED)
            raw_player_details = client.get_player_details(report_code, fight_id)
        except WCLAPIError as exc:
            print(f"      skipped -- {exc}")
            continue
        parsed = parse_fight_bundle(raw_fight, raw_master_data, raw_events_by_type)
        report_data = run_all_analyzers(parsed)
        report_data.player_roles = parse_player_roles(raw_player_details)
        all_fight_data.append(report_data)

    reports_dir = os.path.join(DOCS_DIR, REPORTS_SUBDIR)
    os.makedirs(reports_dir, exist_ok=True)
    filename = build_report_filename(raw_report["zone"]["name"])
    output_path = os.path.join(reports_dir, filename)
    write_html_report(all_fight_data, output_path, title=raw_report["title"], report_code=report_code)
    print(f"Report written to {output_path}")

    index_path = os.path.join(DOCS_DIR, "index.html")
    indexed_reports = build_index(reports_dir, index_path, title=f"{GUILD_NAME} -- Raid Reports")
    print(f"Rebuilt {index_path} ({len(indexed_reports)} report(s) listed).")


if __name__ == "__main__":
    main()
