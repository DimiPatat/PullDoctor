"""
publish_scheduled_report.py

The script the GitHub Actions workflow (.github/workflows/
scheduled_report.yml) actually runs. Fully unattended -- no report
code, no fight number, nothing typed in:

  1. Look up guild_config.py for your guild's name/server/region.
  2. Find your guild's single most recent report automatically, via
     latest_guild_report.py (deep_scan=True + a startTime window --
     see below for why both are used together here specifically).
  3. Generate the SAME HTML report generate_html_report.py already
     produces (every qualifying fight in that report), reusing its
     analysis pipeline.
  4. Write it into docs/reports/<RaidName>-<DDMMYYYY><HHMM>.html and
     rebuild docs/index.html (see docs_index.py) so GitHub Pages
     (serving the docs/ folder) always has an up-to-date list linking
     to every report generated so far.

WHY deep_scan=True AND a startTime window, TOGETHER, HERE SPECIFICALLY:
latest_guild_report.py's fast 2-page default is a reasonable general-
purpose efficiency tradeoff, but THIS script runs unattended on a
schedule with no one watching -- correctness matters far more than
saving 1-2 extra API calls, especially since this only runs twice a
week (well within the 3,600 points/hour budget regardless). Passing a
startTime filter for "only the last RECENT_REPORT_WINDOW_DAYS days"
ALSO keeps the guild's full report history from ever needing multiple
pages walked in practice (a raid guild uploading a few logs a week
will have every recent report fit on one page within that window), so
deep_scan mostly just adds a safety margin rather than routinely
walking a large number of pages.

Usage (this is what the GitHub Actions workflow calls):
    python publish_scheduled_report.py
    python publish_scheduled_report.py --recent-days 21   # widen the window if nothing is found
"""
from __future__ import annotations

import argparse
import sys

from wcl_api import WCLClient, WCLAPIError
import config
import guild_config
import latest_guild_report
import log_parser
import player_roles as player_roles_module
import html_report
import docs_index
import schedule_guard
from main import EVENT_TYPES_NEEDED, run_all_analyzers
from fight_filters import FightFilterCriteria, filter_fights, format_filter_summary
from report_filename import build_report_filename, sanitize_for_filename

DEFAULT_RECENT_DAYS = 10
DOCS_DIR = "docs"
REPORTS_SUBDIR = "reports"


def build_argument_parser() -> argparse.ArgumentParser:
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
        help="Skip the day/time schedule check (schedule_guard.py) and run regardless "
             "of the current Brussels local time. Used by the workflow's manual "
             "'Run workflow' button (workflow_dispatch) -- a manual trigger already "
             "implies you want it to run right now.",
    )
    return parser


def _recent_start_time_ms(recent_days: int) -> float:
    import time
    return (time.time() - recent_days * 86400) * 1000


def main() -> None:
    args = build_argument_parser().parse_args()

    if not args.force:
        check = schedule_guard.is_within_scheduled_window()
        if not check.is_within_window:
            print(
                f"Not running: {check.reason} "
                f"(current Brussels time: {check.brussels_now.strftime('%A %Y-%m-%d %H:%M %Z')}). "
                f"This is expected -- the workflow's cron trigger fires more often than "
                f"the actual schedule, and this guard (schedule_guard.py) only lets real "
                f"work happen on the correct Monday/Wednesday evening, correctly accounting "
                f"for CET/CEST regardless of how the cron itself is approximated. "
                f"Use --force to bypass this (e.g. for a manual run)."
            )
            return
        print(f"Schedule check passed: {check.reason}")

    if not guild_config.is_configured():
        print(
            "ERROR: guild_config.py still has its placeholder values. Edit "
            "GUILD_NAME / GUILD_SERVER_SLUG / GUILD_SERVER_REGION in that file "
            "(see the instructions inside it) before running this script."
        )
        sys.exit(1)

    try:
        client_id, client_secret = config.get_credentials()
    except RuntimeError as exc:
        print(exc)
        sys.exit(1)

    client = WCLClient(client_id, client_secret)

    print(
        f"Looking up the most recent report for guild "
        f"'{guild_config.GUILD_NAME}' ({guild_config.GUILD_SERVER_SLUG}-{guild_config.GUILD_SERVER_REGION}), "
        f"within the last {args.recent_days} day(s)..."
    )
    try:
        latest = latest_guild_report.find_latest_guild_report(
            client,
            guild_config.GUILD_NAME,
            guild_config.GUILD_SERVER_SLUG,
            guild_config.GUILD_SERVER_REGION,
            deep_scan=True,
            start_time=_recent_start_time_ms(args.recent_days),
        )
    except latest_guild_report.NoGuildReportsFoundError as exc:
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
        parsed = log_parser.parse_fight_bundle(raw_fight, raw_master_data, raw_events_by_type)
        report_data = run_all_analyzers(parsed)
        report_data.player_roles = player_roles_module.parse_player_roles(raw_player_details)
        all_fight_data.append(report_data)

    import os
    reports_dir = os.path.join(DOCS_DIR, REPORTS_SUBDIR)
    os.makedirs(reports_dir, exist_ok=True)
    filename = build_report_filename(raw_report["zone"]["name"])
    output_path = os.path.join(reports_dir, filename)

    html_report.write_html_report(all_fight_data, output_path, title=raw_report["title"], report_code=report_code)
    print(f"Report written to {output_path}")

    index_path = os.path.join(DOCS_DIR, "index.html")
    indexed_reports = docs_index.build_index(reports_dir, index_path, title=f"{guild_config.GUILD_NAME} -- Raid Reports")
    print(f"Rebuilt {index_path} ({len(indexed_reports)} report(s) listed).")


if __name__ == "__main__":
    main()
