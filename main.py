"""
main.py

The orchestrator: fetches a report, lets you pick a fight, parses it,
runs every analyzer, and writes a combined report.

By default writes plain .txt and .md files for the one fight you
picked. Pass --html to ALSO write that same single fight out as a
self-contained HTML file (the same collapsible/filterable, class-
colored-meter-bar format generate_html_report.py produces for a whole
raid night) -- reusing reporting.py's Section 5 HTML rendering as-is,
just given a list containing this ONE fight's FightReportData instead
of every qualifying fight in the report. --html-only skips the
.txt/.md files entirely if you only want the HTML output.

The HTML output (when requested) follows the exact same naming/folder
convention as generate_html_report.py (see reporting.py's Section 1):
    reports/<SanitizedFightName>-<DDMMYYYY><HHMM>.html
using the FIGHT's own name (e.g. "Ula'tek") rather than the raid ZONE
name, since this is a single-fight report, not a whole raid night.

By default, only BOSS fights (encounterID != 0) longer than 15 seconds
are shown when choosing a fight interactively, or accepted when you
pass an explicit fight_id -- see core.py's Section 6 (fight_filters).

UPDATED FOR THE PROJECT-WIDE MERGE PASS: this file previously imported
from a long list of small, single-purpose modules (wcl_api, config,
log_parser, cli_helpers, death_analyzer, healing_analyzer,
damage_analyzer, damage_done_analyzer, cooldown_analyzer,
consumables_analyzer, gear_analyzer, avoidable_damage_analyzer,
defensive_damage_prevention_analyzer, player_roles, report, html_report,
report_filename, raid_cooldowns_config, defensive_cooldown_data,
consumable_data, boss_mechanics_config, tier_set_analyzer,
tier_set_data, fight_filters) -- ALL of these have since been folded
into a small set of merged modules, per the project's file-count
reduction pass:
    - wcl.py         -- WCLClient, WCLAPIError, get_credentials (was wcl_api.py + config.py)
    - core.py        -- parse_fight_bundle, FightFilterCriteria/filter_fights/
                        format_filter_summary, parse_player_roles, resolve_fight_id
                        (was log_parser.py + fight_filters.py + cli_helpers.py + player_roles.py, etc.)
    - fight_analyzers.py -- analyze_deaths, analyze_healing, analyze_damage_taken,
                        get_biggest_hits, analyze_damage_done
                        (was death_analyzer.py + healing_analyzer.py + damage_analyzer.py + damage_done_analyzer.py)
    - raid_cooldowns.py -- analyze_cooldown_usage + TRACKED_COOLDOWNS
                        (was cooldown_analyzer.py + raid_cooldowns_config.py, etc.)
    - defensive_cooldowns.py -- analyze_cooldown_usage + as_cooldown_definitions() +
                        TRACKED_DEFENSIVE_COOLDOWNS + analyze_damage_prevention
                        (was cooldown_analyzer.py + defensive_cooldown_data.py +
                        defensive_damage_prevention_analyzer.py, etc.)
    - consumables.py -- analyze_consumables, check_vantus_rune, ALL_TRACKED_CONSUMABLES,
                        MANDATORY_CATEGORIES, VANTUS_RUNE_NAME
                        (was consumables_analyzer.py + consumable_data.py, etc.)
    - gear.py        -- analyze_gear (was gear_analyzer.py)
    - tier_sets.py   -- analyze_tier_sets (was tier_set_analyzer.py + tier_set_data.py)
    - boss_mechanics.py -- ENCOUNTERS, get_encounter_config, analyze_avoidable_damage
                        (was boss_mechanics_config.py + avoidable_damage_analyzer.py, etc.)
    - reporting.py   -- FightReportData, render_text, write_report, write_html_report,
                        ensure_reports_dir, build_report_path, ensure_html_extension
                        (was report.py + html_report.py + report_filename.py + docs_index.py + raid_scorecard.py)

Every renamed call site below keeps the exact same function/argument
signature as the original; only the import path (and, where a module-
qualified prefix like `config.get_credentials()` no longer applies
because the whole module was absorbed, the call-site prefix) changed.

CHANGED (this update): run_all_analyzers() still also calls
tier_sets.analyze_tier_sets(), populating FightReportData.tier_set_reports
-- used by reporting.py's markdown Gear Check table and HTML Gear Check
table to show "Tier Pieces" (X/5) and a colored "Tier Set" track string
per player. tier_sets.TRACKED_TIER_SET_PIECES starts EMPTY until you
populate it via `tier_sets_cli.py explore` + `tier_sets_cli.py add` --
see that module's docstring.

IMPORTANT for anyone else importing from this file: generate_html_report.py
and automation.py both still do `from main import EVENT_TYPES_NEEDED,
run_all_analyzers` -- that public surface (both names, same signatures)
is preserved exactly, so neither of those files needed any changes on
their end of this import.

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

from wcl import WCLClient, WCLAPIError, get_credentials
from core import (
    FightFilterCriteria,
    filter_fights,
    format_filter_summary,
    parse_fight_bundle,
    parse_player_roles,
    resolve_fight_id,
)
import fight_analyzers
import raid_cooldowns
import defensive_cooldowns
import consumables
import gear
import tier_sets
import boss_mechanics
import reporting

EVENT_TYPES_NEEDED = [
    "Casts", "Healing", "DamageTaken", "DamageDone", "Deaths",
    "CombatantInfo", "Debuffs",
]


def fetch_and_parse(client: WCLClient, report_code: str, fight_id: int, raw_fight: dict):
    raw_master_data = client.get_report_master_data(report_code)
    raw_events_by_type = client.get_report_events_multi(report_code, fight_id, event_types=EVENT_TYPES_NEEDED)
    return parse_fight_bundle(raw_fight, raw_master_data, raw_events_by_type)


def run_all_analyzers(parsed, player_roles: dict | None = None) -> reporting.FightReportData:
    player_roles = player_roles or {}
    tank_player_ids = {pid for pid, role in player_roles.items() if role.role == "tank"}

    death_reports = fight_analyzers.analyze_deaths(parsed)
    healer_summaries = fight_analyzers.analyze_healing(parsed)
    damage_taken_summaries = fight_analyzers.analyze_damage_taken(parsed)
    biggest_hits = fight_analyzers.get_biggest_hits(parsed, top_n=5, exclude_player_ids=tank_player_ids)
    damage_done_summaries = fight_analyzers.analyze_damage_done(parsed)

    cooldown_usages = raid_cooldowns.analyze_cooldown_usage(parsed, raid_cooldowns.TRACKED_COOLDOWNS)
    defensive_cooldown_usages = defensive_cooldowns.analyze_cooldown_usage(
        parsed, defensive_cooldowns.as_cooldown_definitions()
    )
    defensive_damage_prevention = defensive_cooldowns.analyze_damage_prevention(
        parsed, defensive_cooldowns.TRACKED_DEFENSIVE_COOLDOWNS
    )

    consumable_results = consumables.analyze_consumables(parsed, consumables.ALL_TRACKED_CONSUMABLES)
    vantus_check = consumables.check_vantus_rune(parsed, consumables.VANTUS_RUNE_NAME)

    gear_reports = gear.analyze_gear(parsed)
    tier_set_reports = tier_sets.analyze_tier_sets(parsed)

    avoidable_config = boss_mechanics.get_encounter_config(parsed.fight.encounter_id, boss_mechanics.ENCOUNTERS)
    avoidable_reports = boss_mechanics.analyze_avoidable_damage(parsed, boss_mechanics.ENCOUNTERS)

    return reporting.FightReportData(
        parsed_fight=parsed, death_reports=death_reports, healer_summaries=healer_summaries,
        damage_taken_summaries=damage_taken_summaries, biggest_hits=biggest_hits,
        damage_done_summaries=damage_done_summaries, cooldown_usages=cooldown_usages,
        defensive_cooldown_usages=defensive_cooldown_usages, defensive_damage_prevention=defensive_damage_prevention,
        consumable_results=consumable_results, consumable_categories=consumables.MANDATORY_CATEGORIES,
        vantus_check=vantus_check, gear_reports=gear_reports, tier_set_reports=tier_set_reports,
        avoidable_reports=avoidable_reports, avoidable_config=avoidable_config, player_roles=player_roles,
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
        client_id, client_secret = get_credentials()
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

    fight_id = resolve_fight_id(filter_result.kept, args.fight_id, all_raw_fights=all_raw_fights)
    raw_fight = next(f for f in all_raw_fights if f["id"] == fight_id)

    try:
        parsed = fetch_and_parse(client, args.report_code, fight_id, raw_fight)
        raw_player_details = client.get_player_details(args.report_code, fight_id)
    except WCLAPIError as exc:
        print(f"Error fetching fight data: {exc}")
        sys.exit(1)

    player_roles = parse_player_roles(raw_player_details)
    report_data = run_all_analyzers(parsed, player_roles=player_roles)

    print("\n" + reporting.render_text(report_data))

    if not args.html_only:
        text_path, md_path = reporting.write_report(report_data, ".", f"fight_{fight_id}_report")
        print(f"\nReport written to {text_path} and {md_path}")

    if want_html:
        if args.html_output is not None:
            html_path = reporting.ensure_html_extension(args.html_output)
        else:
            reporting.ensure_reports_dir()
            html_path = reporting.build_report_path(parsed.fight.name)
        reporting.write_html_report([report_data], html_path, title=parsed.fight.name, report_code=args.report_code)
        print(f"HTML report written to {html_path}")


if __name__ == "__main__":
    main()
