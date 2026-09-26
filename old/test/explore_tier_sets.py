"""
explore_tier_sets.py
Fetches ONE real fight from a Warcraft Logs report and runs
tier_set_explorer.py's heuristic candidate-discovery scan against it,
printing anything found so you can decide what to track via
`manage_tier_sets.py add`.

Only needs the CombatantInfo event stream (the same snapshot
gear_analyzer.py already relies on) -- no Casts/Healing/Damage events
are fetched here, keeping this fast and light on API usage, in keeping
with the project's existing "don't waste API calls" principle.

Usage:
    python explore_tier_sets.py <report_code> [fight_id]
    python explore_tier_sets.py <report_code> [fight_id] --min-players 1
"""
import argparse
import sys

from wcl_api import WCLClient, WCLAPIError
import config
import log_parser
import cli_helpers
import tier_set_explorer
from fight_filters import FightFilterCriteria, filter_fights, format_filter_summary

EVENT_TYPES_NEEDED = ["CombatantInfo"]


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Scan one fight's CombatantInfo for likely tier-set item candidates."
    )
    parser.add_argument("report_code")
    parser.add_argument("fight_id", nargs="?", type=int, default=None)
    parser.add_argument(
        "--min-players", type=int, default=2, metavar="N",
        help="Minimum number of same-class players who must share an identical "
             "item_id in a tier slot before it's flagged as a candidate (default 2). "
             "Lower to 1 only for a single-player log, at the cost of more false positives.",
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
        raw_master_data = client.get_report_master_data(args.report_code)
        raw_events_by_type = client.get_report_events_multi(
            args.report_code, fight_id, event_types=EVENT_TYPES_NEEDED
        )
        parsed = log_parser.parse_fight_bundle(raw_fight, raw_master_data, raw_events_by_type)
    except WCLAPIError as exc:
        print(f"Error fetching fight data: {exc}")
        sys.exit(1)

    print(f"\nScanning {parsed.fight.name} (fight {fight_id}) for tier-set candidates...\n")
    candidates = tier_set_explorer.discover_candidate_tier_pieces(parsed, min_players_sharing=args.min_players)
    print(tier_set_explorer.format_candidates(candidates))


if __name__ == "__main__":
    main()
