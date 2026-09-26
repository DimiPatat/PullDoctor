"""
generate_html_report.py

Fetches every QUALIFYING fight in a report (see fight_filters.py -- by
default: real boss pulls only, encounterID != 0, at least 15 seconds
long), runs role classification + every analyzer against each one, and
writes a single collapsible/filterable HTML report covering the whole
raid night.

OUTPUT LOCATION AND NAMING (see report_filename.py): by default, the
report is written to a "reports/" subfolder (created automatically if
it doesn't exist yet), named:

    <SanitizedRaidName>-<DDMMYYYY><HHMM>.html

e.g. a raid zone called "The Venomous Abyss", report generated on
20 September 2026 at 11:47 -> reports/TheVenomousAbyss-200920261147.html

IMPORTANT: the date/time in the filename is when the report is
GENERATED on your machine (i.e. the moment you run this .py module) --
NOT the date the raid/log actually happened. This means re-running
this script twice against the same report code (e.g. after fixing a
consumables config) produces two distinctly-timestamped files rather
than silently overwriting the first one.

The output ALWAYS ends in the literal ".html" extension -- both the
default auto-named path AND any custom path you supply as the second
argument are passed through report_filename.ensure_html_extension(),
so the file is never accidentally saved with no extension (which some
file managers/Windows Explorer will display as a generic "File" type
rather than recognizing it as an HTML document you can double-click
to open in a browser).

Usage:
    python generate_html_report.py <report_code>
    python generate_html_report.py <report_code> <custom_output_path>
    python generate_html_report.py <report_code> --include-trash
    python generate_html_report.py <report_code> --min-duration 30
"""
import argparse

from wcl_api import WCLClient, WCLAPIError
import config
import log_parser
import player_roles as player_roles_module
import html_report
import report_filename
from main import EVENT_TYPES_NEEDED, run_all_analyzers
from fight_filters import FightFilterCriteria, filter_fights, format_filter_summary


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate one HTML report covering every qualifying fight in a report.")
    parser.add_argument("report_code")
    parser.add_argument(
        "output_path", nargs="?", default=None,
        help="Optional: write to this path instead of the auto-named "
             "reports/<RaidName>-<DDMMYYYY><HHMM>.html file. A '.html' "
             "extension is guaranteed either way.",
    )
    parser.add_argument("--include-trash", action="store_true")
    parser.add_argument("--min-duration", type=float, default=15.0)
    return parser


def main():
    args = build_argument_parser().parse_args()

    try:
        client_id, client_secret = config.get_credentials()
    except RuntimeError as exc:
        print(exc)
        raise SystemExit(1)

    client = WCLClient(client_id, client_secret)

    try:
        raw_report = client.get_report_fights(args.report_code)
    except WCLAPIError as exc:
        print(f"Error fetching report: {exc}")
        raise SystemExit(1)

    raid_name = raw_report["zone"]["name"]
    print(f"Report: {raw_report['title']}  ({raid_name})")

    if args.output_path is not None:
        # Even a user-supplied path is guaranteed to end in .html --
        # never silently saved with no/wrong extension.
        output_path = report_filename.ensure_html_extension(args.output_path)
    else:
        report_filename.ensure_reports_dir()
        output_path = report_filename.build_report_path(raid_name)
    print(f"Output will be written to: {output_path}")

    all_raw_fights = raw_report["fights"]
    criteria = FightFilterCriteria(only_boss_fights=not args.include_trash, min_duration_seconds=args.min_duration)
    filter_result = filter_fights(all_raw_fights, criteria)
    print(format_filter_summary(filter_result, criteria))

    raw_fights = filter_result.kept
    if not raw_fights:
        print("\nNo fights qualify with the current filters -- nothing to report on.")
        print("Try --include-trash and/or a lower --min-duration if this report is mostly short/trash pulls.")
        raise SystemExit(1)

    print(f"\nFetching and analyzing {len(raw_fights)} fight(s)... this may take a while.")

    raw_master_data = client.get_report_master_data(args.report_code)

    all_fight_data = []
    for i, raw_fight in enumerate(raw_fights, start=1):
        fight_id = raw_fight["id"]
        print(f"  [{i}/{len(raw_fights)}] {raw_fight['name']} (#{fight_id})...")
        try:
            raw_events_by_type = client.get_report_events_multi(args.report_code, fight_id, event_types=EVENT_TYPES_NEEDED)
            raw_player_details = client.get_player_details(args.report_code, fight_id)
        except WCLAPIError as exc:
            print(f"      skipped -- {exc}")
            continue

        parsed = log_parser.parse_fight_bundle(raw_fight, raw_master_data, raw_events_by_type)
        report_data = run_all_analyzers(parsed)
        report_data.player_roles = player_roles_module.parse_player_roles(raw_player_details)
        all_fight_data.append(report_data)

    html_report.write_html_report(all_fight_data, output_path, title=raw_report["title"], report_code=args.report_code)
    print(f"\nHTML report written to {output_path}")


if __name__ == "__main__":
    main()
