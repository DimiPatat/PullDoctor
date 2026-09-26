"""
explore_consumables.py

Interactive menu for managing tracked consumables. On start it ONLY
shows a menu -- it does NOT touch the Warcraft Logs API until you
choose "add". "remove" and "list" work entirely from the local
consumables.generated.json file.

Menu:
  [a] Add consumables to the tracked list  -- fetches the report/fight,
                                               detects known items, and
                                               lets you pick which to
                                               track (plus manually add
                                               anything not auto-detected)
  [r] Remove consumables from the tracked list -- reads the local config only
  [l] List currently tracked consumables       -- reads the local config only
  [d] Done -- exit

Usage:
    python explore_consumables.py
    python explore_consumables.py <report_code>
    python explore_consumables.py <report_code> <fight_id>
    python explore_consumables.py <report_code> <fight_id> --export my_consumables.json
    python explore_consumables.py <report_code> <fight_id> --replace

Note on scope: gear enchants and gems are intentionally NOT part of
this tool -- see explore_gear.py / gear_compliance_analyzer.py for that.
"""
from __future__ import annotations

import argparse
import os

from wcl_api import WCLClient, WCLAPIError
import cli_helpers
import config
import consumable_data
import consumable_explorer
import log_parser
import manage_consumables
import roster
from consumable_config_io import (
    DEFAULT_CONFIG_PATH,
    ConsumablesConfigError,
    load_generated_consumables,
    save_consumable_selection,
)
from consumable_schema import ConsumableDefinition
from selection_utils import parse_selection


def clear_screen() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def _pause() -> None:
    input("\nPress Enter to return to the menu...")


def get_tracked_consumable_names(path) -> set[str]:
    try:
        consumables, _ = load_generated_consumables(path)
    except ConsumablesConfigError as exc:
        print(
            f"\nWARNING: {path} has a problem and couldn't be read -- "
            f"skipping the 'already tracked' highlighting for this run.\n{exc}\n"
        )
        return set()
    return {consumable.name for consumable in consumables}


def print_detected_table(detected, roster_size: int, tracked_names: set[str] = frozenset()) -> None:
    if not detected:
        print("No known consumables detected in this pull.")
        return
    if tracked_names:
        print("(* in the T column = already tracked)")
    header = f"{'#':>3} T {'Item':<32} {'Category':<15} {'Players':>9}  {'Detected Via':<28}"
    print(header)
    print("-" * len(header))
    for index, entry in enumerate(detected, start=1):
        marker = "*" if entry.definition.name in tracked_names else " "
        via_text = "/".join(sorted(entry.detected_via))
        coverage = f"{entry.player_count}/{roster_size}"
        print(
            f"{index:>3} {marker} {entry.definition.name:<32.32} "
            f"{entry.definition.category:<15.15} {coverage:>9}  {via_text:<28}"
        )


def fetch_and_detect(report_code: str | None, fight_id: int | None) -> dict | None:
    """The ONLY function in this module that talks to the Warcraft Logs API."""
    if not report_code:
        report_code = input("Warcraft Logs report code (the part of the URL after /reports/): ").strip()
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
            report_code, resolved_fight_id, event_types=["Casts", "CombatantInfo"],
        )
    except WCLAPIError as exc:
        print(f"Error fetching fight data: {exc}")
        return None

    parsed = log_parser.parse_fight_bundle(raw_fight, raw_master_data, raw_events_by_type)
    print(f"\nParsed: {parsed.fight.name}  ({'kill' if parsed.fight.kill else 'wipe'})")

    detected = consumable_explorer.analyze_known_consumable_matches(
        parsed, consumable_data.SEED_REFERENCE_CONSUMABLES
    )
    roster_size = len(roster.get_fight_roster(parsed))

    return {"parsed": parsed, "detected": detected, "roster_size": roster_size}


def _prompt_category_mandatory_for_new_categories(new_categories: list[str]) -> dict[str, bool]:
    updates: dict[str, bool] = {}
    for category in new_categories:
        suggested = consumable_data.DEFAULT_CATEGORY_MANDATORY.get(category, True)
        default_label = "Y" if suggested else "n"
        while True:
            answer = input(
                f"Category '{category}' is new. Flag it as MANDATORY (missing = "
                f"fails the check for that player)? [y/n] (default {default_label}): "
            ).strip().lower()
            if not answer:
                updates[category] = suggested
                break
            if answer in {"y", "yes"}:
                updates[category] = True
                break
            if answer in {"n", "no"}:
                updates[category] = False
                break
            print("Enter y or n, or press Enter for the default.")
    return updates


def _prompt_manual_additions() -> list[ConsumableDefinition]:
    manual: list[ConsumableDefinition] = []
    while True:
        answer = input("\nManually add an item not listed above? [y/N]: ").strip().lower()
        if answer not in {"y", "yes"}:
            break
        name = input("  Item name (must match the exact log name): ").strip()
        if not name:
            print("  Name cannot be blank -- skipping.")
            continue
        category = input(
            "  Category (e.g. health_potion/combat_potion/oil/food/healthstone/flask/augment_rune): "
        ).strip()
        if not category:
            print("  Category cannot be blank -- skipping.")
            continue
        notes = input("  Notes (optional): ").strip() or None
        manual.append(ConsumableDefinition(name=name, category=category, notes=notes))
    return manual


def _do_add(session: dict, export_path, replace: bool) -> bool:
    detected = session["detected"]
    roster_size = session["roster_size"]
    tracked_names = get_tracked_consumable_names(export_path)

    print()
    print_detected_table(detected, roster_size, tracked_names)

    to_add: list[ConsumableDefinition] = []
    if detected:
        raw = input(
            "\nSelect detected items to add by number (e.g. 1,3,5-7). "
            "Press Enter to select none: "
        )
        try:
            indexes = parse_selection(raw, len(detected))
        except ValueError as exc:
            print(f"Invalid selection: {exc}")
            indexes = []
        for index in indexes:
            entry = detected[index]
            category = input(
                f"  Category for {entry.definition.name!r} "
                f"(detected as {entry.definition.category!r}, press Enter to keep): "
            ).strip() or entry.definition.category
            notes = input(f"  Notes for {entry.definition.name!r} (optional): ").strip() or None
            to_add.append(ConsumableDefinition(
                name=entry.definition.name, category=category,
                ability_ids=tuple(entry.ability_ids_seen), notes=notes,
            ))

    to_add.extend(_prompt_manual_additions())

    if not to_add:
        print("\nNo consumables selected -- nothing saved.")
        return False

    try:
        _existing_consumables, existing_category_mandatory = load_generated_consumables(export_path)
    except ConsumablesConfigError as exc:
        print(f"WARNING: could not read existing categories from {export_path}:\n{exc}")
        existing_category_mandatory = {}

    new_categories = sorted({c.category for c in to_add} - set(existing_category_mandatory))
    category_updates = _prompt_category_mandatory_for_new_categories(new_categories)

    print()
    try:
        path = save_consumable_selection(
            to_add, category_updates, path=export_path, replace=replace, verbose=True,
        )
    except ConsumablesConfigError as exc:
        print(
            f"Could not save -- {export_path} currently has a problem and wasn't "
            f"touched (nothing was overwritten):\n\n{exc}\n\n"
            f"Fix it, then try again:\n"
            f"  python manage_consumables.py validate\n"
            f"  python manage_consumables.py repair"
        )
        return False

    print(f"\nSaved to: {path}")
    return True


def _do_remove_menu(path) -> None:
    manage_consumables.interactive_remove_consumables(path)


def _do_list_menu(path) -> None:
    try:
        consumables, category_mandatory = load_generated_consumables(path)
    except ConsumablesConfigError as exc:
        print(f"WARNING: could not read {path}:\n{exc}")
        return
    if not consumables:
        print(f"No consumables configured yet in {path}.")
        print("Choose 'add' first to explore a report and select some.")
        return
    print(f"{len(consumables)} consumable(s) tracked in {path}:\n")
    manage_consumables.print_consumables_detail(consumables, category_mandatory)


def run_interactive_session(args: argparse.Namespace) -> None:
    session: dict = {"parsed": None, "detected": None, "roster_size": None}
    replace_pending = args.replace

    while True:
        clear_screen()
        print("PullDoctor -- Consumables Explorer\n")
        print(
            "What would you like to do?\n"
            "  [a] Add consumables to the tracked list\n"
            "  [r] Remove consumables from the tracked list\n"
            "  [l] List currently tracked consumables\n"
            "  [d] Done"
        )
        choice = input("> ").strip().lower()

        if choice in {"a", "add"}:
            if session["parsed"] is None:
                result = fetch_and_detect(args.report_code, args.fight_id)
                if result is None:
                    _pause()
                    continue
                session.update(result)

            saved = _do_add(session, args.export, replace_pending)
            if saved:
                replace_pending = False
            _pause()

        elif choice in {"r", "remove"}:
            print()
            _do_remove_menu(args.export)
            _pause()

        elif choice in {"l", "list"}:
            print()
            _do_list_menu(args.export)
            _pause()

        elif choice in {"d", "done", "q", "quit"}:
            print("\nDone. consumable_data.py will load any saved changes automatically.")
            return

        else:
            print("Enter a, r, l, or d.")
            _pause()


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Explore and manage tracked consumables (menu-driven)."
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
        help="Generated JSON config path (default: consumables.generated.json)",
    )
    parser.add_argument(
        "--replace", action="store_true",
        help="Wipe all previously tracked consumables on the FIRST successful 'add' this session.",
    )
    return parser


def main() -> None:
    args = build_argument_parser().parse_args()
    run_interactive_session(args)


if __name__ == "__main__":
    main()
