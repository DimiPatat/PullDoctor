"""
explore_damage_targets.py

Interactive menu for labeling enemy targets in a fight (bosses, adds,
priority adds) with custom display names/categories. On start it ONLY
shows a menu -- it does NOT touch the Warcraft Logs API until you
choose "add". "remove" and "list" work entirely from the local
damage_targets.generated.json file.

IMPORTANT: labeling is entirely OPTIONAL. damage_to_target_analyzer.py
already works with zero configuration -- it shows every raw enemy NPC
name it finds, with damage per player, immediately. This tool exists
only to let you rename raw names into cleaner labels (e.g. "Boss A",
"Priority Add A") and/or group several raw names into one rollup
category.

Menu:
  [a] Add labels for targets seen in a fight -- fetches the report/
                                                 fight THEN shows every
                                                 enemy NPC that took
                                                 damage
  [r] Remove labels from the tracked list       -- local config only
  [l] List currently tracked labels              -- local config only
  [d] Done -- exit

Usage:
    python explore_damage_targets.py
    python explore_damage_targets.py <report_code>
    python explore_damage_targets.py <report_code> <fight_id>
    python explore_damage_targets.py <report_code> <fight_id> --export my_labels.json
    python explore_damage_targets.py <report_code> <fight_id> --replace
"""
from __future__ import annotations

import argparse
import os

from wcl_api import WCLClient, WCLAPIError
import cli_helpers
import config
import damage_to_target_analyzer
import log_parser
import manage_damage_targets
from target_label_config_io import (
    DEFAULT_CONFIG_PATH,
    TargetLabelConfigError,
    load_generated_target_labels,
    save_target_labels,
)
from target_label_schema import TargetLabel
from selection_utils import parse_selection


def clear_screen() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def _pause() -> None:
    input("\nPress Enter to return to the menu...")


def get_labeled_target_names(encounter_id: int, path) -> set[str]:
    try:
        existing = load_generated_target_labels(path)
    except TargetLabelConfigError as exc:
        print(
            f"\nWARNING: {path} has a problem and couldn't be read -- "
            f"skipping the 'already labeled' highlighting for this run.\n{exc}\n"
        )
        return set()
    encounter_config = existing.get(encounter_id)
    if encounter_config is None:
        return set()
    return {label.target_name for label in encounter_config.labels}


def print_target_table(target_summaries, labeled_names: set[str] = frozenset()) -> None:
    if not target_summaries:
        print("No enemy targets took damage in this fight.")
        return
    if labeled_names:
        print("(* in the L column = already labeled)")
    header = f"{'#':>3} L {'Target Name':<28} {'Instances':>9} {'TotalDmg':>14} {'Players':>8}"
    print(header)
    print("-" * len(header))
    for index, summary in enumerate(target_summaries, start=1):
        marker = "*" if summary.target_name in labeled_names else " "
        print(
            f"{index:>3} {marker} {summary.target_name:<28.28} {summary.instance_count:>9} "
            f"{summary.total_damage:>14,} {len(summary.damage_by_player_id):>8}"
        )


def fetch_and_analyze(report_code: str | None, fight_id: int | None) -> dict | None:
    """The ONLY function in this module that talks to the Warcraft Logs API."""
    if not report_code:
        report_code = input(
            "Warcraft Logs report code (the part of the URL after /reports/): "
        ).strip()
        if not report_code:
            print("No report code given -- cancelled.")
            return None

    try:
        client_id, client_secret = config.get_credentials()
    except RuntimeError as exc:
        print(exc)
        return None

    client = WCLClient(client_id, client_secret)
    try:
        raw_report = client.get_report_fights(report_code)
    except WCLAPIError as exc:
        print(f"Error fetching report: {exc}")
        return None

    print(f"Report: {raw_report['title']}  ({raw_report['zone']['name']})")

    try:
        resolved_fight_id = cli_helpers.resolve_fight_id(raw_report["fights"], fight_id)
    except SystemExit:
        print("No valid fight selected -- cancelled.")
        return None

    raw_fight = next(f for f in raw_report["fights"] if f["id"] == resolved_fight_id)

    try:
        raw_master_data = client.get_report_master_data(report_code)
        raw_events_by_type = client.get_report_events_multi(
            report_code, resolved_fight_id, event_types=["DamageDone"],
        )
    except WCLAPIError as exc:
        print(f"Error fetching fight data: {exc}")
        return None

    parsed = log_parser.parse_fight_bundle(raw_fight, raw_master_data, raw_events_by_type)
    print(f"\nEncounter ID: {parsed.fight.encounter_id}")

    target_summaries = damage_to_target_analyzer.analyze_damage_by_target(parsed)

    return {"parsed": parsed, "target_summaries": target_summaries}


def _prompt_label_for_target(summary) -> TargetLabel:
    print(f"\n--- {summary.target_name} ---")
    print(f"  {summary.total_damage:,} total damage from {len(summary.damage_by_player_id)} player(s), "
          f"{summary.instance_count} instance(s) seen")
    for player_id, player_name, damage in summary.ranked_players()[:5]:
        print(f"    {player_name:<15} {damage:>12,}")

    display_name = input(f"  Display name for {summary.target_name!r} (e.g. 'Boss A', 'Priority Add A'): ").strip()
    if not display_name:
        display_name = summary.target_name
        print(f"  (blank -- keeping raw name {summary.target_name!r})")

    category = input(
        "  Category (optional, e.g. boss/miniboss/priority_add/add -- used for rollups): "
    ).strip() or None
    notes = input(f"  Notes for {summary.target_name!r} (optional): ").strip() or None

    return TargetLabel(
        target_name=summary.target_name, display_name=display_name, category=category, notes=notes,
    )


def prompt_for_labels(target_summaries) -> list[TargetLabel]:
    while True:
        raw = input(
            "\nChoose targets to label by number (examples: 1,3,5-7). "
            "Press Enter to select none: "
        )
        try:
            indexes = parse_selection(raw, len(target_summaries))
            break
        except ValueError as exc:
            print(f"Invalid selection: {exc}")

    return [_prompt_label_for_target(target_summaries[index]) for index in indexes]


def _do_add(parsed, target_summaries, export_path, replace: bool) -> bool:
    labels = prompt_for_labels(target_summaries)
    if not labels:
        print("\nNo labels selected -- nothing saved.")
        return False

    print()
    try:
        path = save_target_labels(
            encounter_id=parsed.fight.encounter_id,
            encounter_name=parsed.fight.name,
            labels=labels,
            path=export_path,
            replace=replace,
            verbose=True,
        )
    except TargetLabelConfigError as exc:
        print(
            f"Could not save -- {export_path} currently has a problem and wasn't "
            f"touched (nothing was overwritten):\n\n{exc}\n\n"
            f"Fix it, then try again:\n"
            f"  python manage_damage_targets.py validate\n"
            f"  python manage_damage_targets.py repair"
        )
        return False

    print(f"\nSaved to: {path}")
    return True


def _resolve_target_encounter_id(path, session: dict, prompt_verb: str) -> int | None:
    try:
        encounters = load_generated_target_labels(path)
    except TargetLabelConfigError as exc:
        print(f"WARNING: could not read {path}:\n{exc}")
        return None

    if not encounters:
        print(f"No encounters configured yet in {path}.")
        print("Choose 'add' first to explore a report and label some targets.")
        return None

    if len(encounters) == 1:
        return next(iter(encounters))

    last_encounter_id = session["parsed"].fight.encounter_id if session.get("parsed") else None

    print(f"{len(encounters)} encounter(s) configured:\n")
    for encounter_id in sorted(encounters):
        config_obj = encounters[encounter_id]
        marker = " (last used this session)" if encounter_id == last_encounter_id else ""
        print(f"  {encounter_id:>6}  {config_obj.encounter_name}  ({len(config_obj.labels)} label(s)){marker}")

    default_hint = f" (Enter for {last_encounter_id})" if last_encounter_id in encounters else ""
    raw = input(f"\nWhich encounter_id do you want to {prompt_verb}?{default_hint}: ").strip()
    if not raw and last_encounter_id in encounters:
        return last_encounter_id
    if not raw:
        print("No encounter_id given -- cancelled.")
        return None
    try:
        chosen_id = int(raw)
    except ValueError:
        print(f"'{raw}' isn't a valid encounter_id.")
        return None
    if chosen_id not in encounters:
        print(f"Encounter {chosen_id} is not configured.")
        return None
    return chosen_id


def _do_remove_menu(path, session: dict) -> None:
    encounter_id = _resolve_target_encounter_id(path, session, "remove from")
    if encounter_id is None:
        return
    manage_damage_targets.interactive_remove_labels(encounter_id, path)


def _do_list_menu(path, session: dict) -> None:
    try:
        encounters = load_generated_target_labels(path)
    except TargetLabelConfigError as exc:
        print(f"WARNING: could not read {path}:\n{exc}")
        return
    if not encounters:
        print(f"No encounters configured yet in {path}.")
        print("Choose 'add' first to explore a report and label some targets.")
        return
    if len(encounters) == 1:
        manage_damage_targets.print_encounter_detail(next(iter(encounters.values())))
        return

    print(f"{len(encounters)} encounter(s) configured in {path}:\n")
    for encounter_id in sorted(encounters):
        config_obj = encounters[encounter_id]
        print(f"  {encounter_id:>6}  {config_obj.encounter_name}  ({len(config_obj.labels)} label(s))")

    raw = input("\nShow detail for which encounter_id? (Enter to skip): ").strip()
    if not raw:
        return
    try:
        chosen_id = int(raw)
    except ValueError:
        print(f"'{raw}' isn't a valid encounter_id.")
        return
    if chosen_id not in encounters:
        print(f"Encounter {chosen_id} is not configured.")
        return
    manage_damage_targets.print_encounter_detail(encounters[chosen_id])


def run_interactive_session(args: argparse.Namespace) -> None:
    session: dict = {"parsed": None, "target_summaries": None}
    replace_pending = args.replace

    while True:
        clear_screen()
        print("PullDoctor -- Damage-by-Target Labeling Explorer\n")
        print(
            "What would you like to do?\n"
            "  [a] Add labels for targets seen in a fight\n"
            "  [r] Remove labels from the tracked list\n"
            "  [l] List currently tracked labels\n"
            "  [d] Done"
        )
        choice = input("> ").strip().lower()

        if choice in {"a", "add"}:
            if session["parsed"] is None:
                result = fetch_and_analyze(args.report_code, args.fight_id)
                if result is None:
                    _pause()
                    continue
                session.update(result)

            print()
            labeled_names = get_labeled_target_names(session["parsed"].fight.encounter_id, args.export)
            print_target_table(session["target_summaries"], labeled_names)
            saved = _do_add(session["parsed"], session["target_summaries"], args.export, replace_pending)
            if saved:
                replace_pending = False
            _pause()

        elif choice in {"r", "remove"}:
            print()
            _do_remove_menu(args.export, session)
            _pause()

        elif choice in {"l", "list"}:
            print()
            _do_list_menu(args.export, session)
            _pause()

        elif choice in {"d", "done", "q", "quit"}:
            print("\nDone. target_label_config.py will load any saved changes automatically.")
            return

        else:
            print("Enter a, r, l, or d.")
            _pause()


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Label enemy targets (bosses/adds/priority adds) for cleaner damage-by-target reports (menu-driven)."
    )
    parser.add_argument(
        "report_code", nargs="?", default=None,
        help="Warcraft Logs report code -- only needed for 'add'; prompted for if omitted.",
    )
    parser.add_argument(
        "fight_id", nargs="?", type=int, default=None,
        help="Optional fight ID -- only needed for 'add'; prompted for if omitted.",
    )
    parser.add_argument(
        "--export", default=str(DEFAULT_CONFIG_PATH),
        help="Generated JSON config path (default: damage_targets.generated.json)",
    )
    parser.add_argument(
        "--replace", action="store_true",
        help="Wipe the encounter's previously saved labels on the FIRST successful 'add' this session.",
    )
    return parser


def main() -> None:
    args = build_argument_parser().parse_args()
    run_interactive_session(args)


if __name__ == "__main__":
    main()
