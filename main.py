"""
main.py

The orchestrator: fetches a report, lets you pick a fight, parses it,
runs every analyzer, and writes a combined report.

By default writes plain .txt and .md files for the one fight you
picked. Pass --html to ALSO write that same single fight out as a
self-contained HTML file (the same collapsible/filterable, class-
colored-meter-bar format generate_html_report.py produces for a whole
raid night) -- reusing html_report.py's rendering as-is, just given a
list containing this ONE fight's FightReportData instead of every
qualifying fight in the report. --html-only skips the .txt/.md files
entirely if you only want the HTML output.

The HTML output (when requested) follows the exact same naming/folder
convention as generate_html_report.py (see report_filename.py):
    reports/<SanitizedFightName>-<DDMMYYYY><HHMM>.html
using the FIGHT's own name (e.g. "Ula'tek") rather than the raid ZONE
name, since this is a single-fight report, not a whole raid night.

By default, only BOSS fights (encounterID != 0) longer than 15 seconds
are shown when choosing a fight interactively, or accepted when you
pass an explicit fight_id -- see fight_filters.py.

Usage:
    python main.py <report_code> [fight_id]
    python main.py <report_code> [fight_id] --html
    python main.py <report_code> [fight_id] --html-only
    python main.py <report_code> [fight_id] --html --html-output <custom_path.html>
    python main.py <report_code> [fight_id] --include-trash
    python main.py <report_code> [fight_id] --min-duration 30
"""
import argparse
import sys

from wcl_api import WCLClient, WCLAPIError
import config
import log_parser
import cli_helpers
import death_analyzer
import healing_analyzer
import damage_analyzer
import damage_done_analyzer
import cooldown_analyzer
import consumables_analyzer
import gear_analyzer
import avoidable_damage_analyzer
import defensive_damage_prevention_analyzer
import player_roles as player_roles_module
import report
import html_report
import report_filename
import raid_cooldowns_config
import defensive_cooldown_data
import consumable_data
import boss_mechanics_config
from fight_filters import FightFilterCriteria, filter_fights, format_filter_summary

EVENT_TYPES_NEEDED = [
    "Casts", "Healing", "DamageTaken", "DamageDone", "Deaths",
    "CombatantInfo", "Debuffs",
]


def fetch_and_parse(client: WCLClient, report_code: str, fight_id: int, raw_fight: dict):
    raw_master_data = client.get_report_master_data(report_code)
    raw_events_by_type = client.get_report_events_multi(report_code, fight_id, event_types=EVENT_TYPES_NEEDED)
    return log_parser.parse_fight_bundle(raw_fight, raw_master_data, raw_events_by_type)


def run_all_analyzers(parsed, player_roles: dict | None = None) -> report.FightReportData:
    player_roles = player_roles or {}
    tank_player_ids = {pid for pid, role in player_roles.items() if role.role == "tank"}

    death_reports = death_analyzer.analyze_deaths(parsed)
    healer_summaries = healing_analyzer.analyze_healing(parsed)
    damage_taken_summaries = damage_analyzer.analyze_damage_taken(parsed)
    biggest_hits = damage_analyzer.get_biggest_hits(parsed, top_n=5, exclude_player_ids=tank_player_ids)
    damage_done_summaries = damage_done_analyzer.analyze_damage_done(parsed)

    cooldown_usages = cooldown_analyzer.analyze_cooldown_usage(parsed, raid_cooldowns_config.TRACKED_COOLDOWNS)
    defensive_cooldown_usages = cooldown_analyzer.analyze_cooldown_usage(parsed, defensive_cooldown_data.as_cooldown_definitions())
    defensive_damage_prevention = defensive_damage_prevention_analyzer.analyze_damage_prevention(parsed, defensive_cooldown_data.TRACKED_DEFENSIVE_COOLDOWNS)

    consumable_results = consumables_analyzer.analyze_consumables(parsed, consumable_data.ALL_TRACKED_CONSUMABLES)
    vantus_check = consumables_analyzer.check_vantus_rune(parsed, consumable_data.VANTUS_RUNE_NAME)
    gear_reports = gear_analyzer.analyze_gear(parsed)
    avoidable_config = avoidable_damage_analyzer.get_encounter_config(parsed.fight.encounter_id, boss_mechanics_config.ENCOUNTERS)
    avoidable_reports = avoidable_damage_analyzer.analyze_avoidable_damage(parsed, boss_mechanics_config.ENCOUNTERS)

    return report.FightReportData(
        parsed_fight=parsed, death_reports=death_reports, healer_summaries=healer_summaries,
        damage_taken_summaries=damage_taken_summaries, biggest_hits=biggest_hits,
        damage_done_summaries=damage_done_summaries, cooldown_usages=cooldown_usages,
        defensive_cooldown_usages=defensive_cooldown_usages, defensive_damage_prevention=defensive_damage_prevention,
        consumable_results=consumable_results, consumable_categories=consumable_data.MANDATORY_CATEGORIES,
        vantus_check=vantus_check, gear_reports=gear_reports, avoidable_reports=avoidable_reports,
        avoidable_config=avoidable_config, player_roles=player_roles,
    )


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fetch a Warcraft Logs report, analyze one fight, write a report.")
    parser.add_argument("report_code")
    parser.add_argument("fight_id", nargs="?", type=int, default=None)
    parser.add_argument(
        "--html", action="store_true",
        help="Also write this single fight out as a self-contained HTML file "
             "(same format as generate_html_report.py), in addition to the "
             "usual .txt/.md files.",
    )
    parser.add_argument(
        "--html-only", action="store_true",
        help="Write ONLY the HTML file -- skip the .txt/.md files entirely. "
             "Implies --html.",
    )
    parser.add_argument(
        "--html-output", default=None, metavar="PATH",
        help="Optional: write the HTML file to this exact path instead of the "
             "auto-named reports/<FightName>-<DDMMYYYY><HHMM>.html file. Only "
             "used when --html or --html-only is given. A NAMED flag "
             "(not a second positional argument) deliberately, so it can be "
             "combined with --html/--html-only regardless of whether "
             "fight_id was also given -- e.g. both "
             "'main.py CODE --html --html-output out.html' and "
             "'main.py CODE 40 --html --html-output out.html' work "
             "unambiguously; a plain positional here would otherwise clash "
             "with fight_id's own optional positional slot.",
    )
    parser.add_argument("--include-trash", action="store_true")
    parser.add_argument("--min-duration", type=float, default=15.0)
    return parser


def main():
    args = build_argument_parser().parse_args()
    want_html = args.html or args.html_only

    try:
        client_id, client_secret = config.get_credentials()
    except RuntimeError as exc:
        print(exc)
        sys.exit(1)

    client = WCLClient(client_id, client_secret)

    try:
        raw_report = client.get_report_fights(args.report_code)
    except WCLAPIError as exc:
        print(f"Error fetching report: {exc}")
        sys.exit(1)

    print(f"Report: {raw_report['title']}  ({raw_report['zone']['name']})")

    all_raw_fights = raw_report["fights"]
    criteria = FightFilterCriteria(only_boss_fights=not args.include_trash, min_duration_seconds=args.min_duration)
    filter_result = filter_fights(all_raw_fights, criteria)
    if not criteria.is_a_no_op:
        print(format_filter_summary(filter_result, criteria))

    fight_id = cli_helpers.resolve_fight_id(filter_result.kept, args.fight_id, all_raw_fights=all_raw_fights)
    raw_fight = next(f for f in all_raw_fights if f["id"] == fight_id)

    try:
        parsed = fetch_and_parse(client, args.report_code, fight_id, raw_fight)
        raw_player_details = client.get_player_details(args.report_code, fight_id)
    except WCLAPIError as exc:
        print(f"Error fetching fight data: {exc}")
        sys.exit(1)

    player_roles = player_roles_module.parse_player_roles(raw_player_details)
    report_data = run_all_analyzers(parsed, player_roles=player_roles)

    print("\n" + report.render_text(report_data))

    if not args.html_only:
        text_path, md_path = report.write_report(report_data, ".", f"fight_{fight_id}_report")
        print(f"\nReport written to {text_path} and {md_path}")

    if want_html:
        if args.html_output is not None:
            html_path = report_filename.ensure_html_extension(args.html_output)
        else:
            report_filename.ensure_reports_dir()
            html_path = report_filename.build_report_path(parsed.fight.name)
        html_report.write_html_report([report_data], html_path, title=parsed.fight.name, report_code=args.report_code)
        print(f"HTML report written to {html_path}")


if __name__ == "__main__":
    main()
