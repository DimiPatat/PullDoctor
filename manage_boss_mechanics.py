"""
manage_boss_mechanics.py

Local-only maintenance tool for boss_mechanics.generated.json: list what's
configured, remove specific mechanics or a whole encounter, and check/repair
the file's JSON syntax -- all WITHOUT needing a Warcraft Logs report.

Every command that changes the file makes a ".bak" backup first.

Usage:
    python manage_boss_mechanics.py list
    python manage_boss_mechanics.py list 3492
    python manage_boss_mechanics.py remove 3492
    python manage_boss_mechanics.py remove-encounter 3492
    python manage_boss_mechanics.py validate
    python manage_boss_mechanics.py repair
"""
from __future__ import annotations

import argparse

from mechanics_config_io import (
    DEFAULT_CONFIG_PATH,
    MechanicsConfigError,
    load_generated_encounters,
    remove_encounter,
    remove_mechanics,
    repair_trailing_commas,
    validate_file,
)
from selection_utils import parse_selection


def print_encounter_detail(config_obj) -> None:
    """Print a numbered list of one encounter's tracked mechanics."""
    print(
        f"{config_obj.encounter_name} (encounter_id={config_obj.encounter_id}) "
        f"-- {len(config_obj.mechanics)} mechanic(s):\n"
    )
    if not config_obj.mechanics:
        print("  (none)")
        return
    for number, mechanic in enumerate(config_obj.mechanics, start=1):
        ids_text = f" ids={list(mechanic.ability_ids)}" if mechanic.ability_ids else ""
        source_flag = " [source!=target]" if mechanic.require_source_differs_from_target else ""
        category_text = f" ({mechanic.category})" if mechanic.category else ""
        threshold_text = (
            f" [flag >= {mechanic.damage_multiplier_threshold}x median]"
            if mechanic.damage_multiplier_threshold is not None
            else ""
        )
        print(
            f"  {number:>2}. {mechanic.ability_name}{category_text} "
            f"[{'+'.join(mechanic.track_via)}]{source_flag}{threshold_text}{ids_text}"
        )
        if mechanic.notes:
            print(f"      notes: {mechanic.notes}")


def _load_or_exit(path):
    try:
        return load_generated_encounters(path)
    except MechanicsConfigError as exc:
        print(f"Could not read {path}:\n\n{exc}")
        raise SystemExit(1)


def interactive_remove_mechanics(
    encounter_id: int,
    path,
    encounters: dict | None = None,
) -> list[str]:
    """Prompt to pick mechanics to remove from one encounter, confirm, then remove (making a .bak backup first)."""
    if encounters is None:
        try:
            encounters = load_generated_encounters(path)
        except MechanicsConfigError as exc:
            print(f"WARNING: could not read {path}:\n{exc}")
            return []

    config_obj = encounters.get(encounter_id)
    if config_obj is None or not config_obj.mechanics:
        print("No mechanics configured yet for this encounter -- nothing to remove.")
        return []

    print_encounter_detail(config_obj)
    raw = input(
        "\nWhich mechanic number(s) to remove (examples: 2 / 1,3 / 2-4)? "
        "Press Enter to cancel: "
    )
    if not raw.strip():
        print("Cancelled -- nothing removed.")
        return []

    try:
        indexes = parse_selection(raw, len(config_obj.mechanics))
    except ValueError as exc:
        print(f"Invalid selection: {exc}")
        return []

    if not indexes:
        print("Cancelled -- nothing removed.")
        return []

    names_to_remove = [config_obj.mechanics[i].ability_name for i in indexes]
    print("\nAbout to remove:")
    for name in names_to_remove:
        print(f"  - {name}")

    confirm = input(
        f"\nA backup of {path} will be saved as {path}.bak first. Proceed? [y/N]: "
    ).strip().lower()
    if confirm not in {"y", "yes"}:
        print("Cancelled -- nothing removed.")
        return []

    removed = remove_mechanics(encounter_id, names_to_remove, path, verbose=True)
    if removed:
        print(f"\nRemoved {len(removed)} mechanic(s) from encounter {encounter_id}.")
        print(f"Backup saved as: {path}.bak")
    else:
        print("\nNothing matched -- file left untouched.")
    return removed


def cmd_list(args: argparse.Namespace) -> None:
    encounters = _load_or_exit(args.path)

    if not encounters:
        print(f"No encounters configured yet in {args.path}.")
        return

    if args.encounter_id is not None:
        config_obj = encounters.get(args.encounter_id)
        if config_obj is None:
            print(f"Encounter {args.encounter_id} is not configured in {args.path}.")
            return
        print_encounter_detail(config_obj)
        return

    print(f"{len(encounters)} encounter(s) configured in {args.path}:\n")
    for encounter_id in sorted(encounters):
        config_obj = encounters[encounter_id]
        print(
            f"  {encounter_id:>6}  {config_obj.encounter_name}  "
            f"({len(config_obj.mechanics)} mechanic(s))"
        )
    print("\nUse 'list <encounter_id>' to see the mechanics for one boss.")


def cmd_remove(args: argparse.Namespace) -> None:
    encounters = _load_or_exit(args.path)

    if not encounters.get(args.encounter_id) or not encounters[args.encounter_id].mechanics:
        print(f"Encounter {args.encounter_id} has no mechanics configured -- nothing to remove.")
        return

    interactive_remove_mechanics(args.encounter_id, args.path, encounters=encounters)


def cmd_remove_encounter(args: argparse.Namespace) -> None:
    encounters = _load_or_exit(args.path)

    config_obj = encounters.get(args.encounter_id)
    if config_obj is None:
        print(f"Encounter {args.encounter_id} is not configured -- nothing to remove.")
        return

    print_encounter_detail(config_obj)

    if not args.yes:
        confirm = input(
            f"\nThis deletes ALL {len(config_obj.mechanics)} mechanic(s) above for "
            f"'{config_obj.encounter_name}'. A backup will be saved as {args.path}.bak "
            f"first. Proceed? [y/N]: "
        ).strip().lower()
        if confirm not in {"y", "yes"}:
            print("Cancelled -- nothing removed.")
            return

    removed = remove_encounter(args.encounter_id, args.path, verbose=True)
    if removed:
        print(f"\nRemoved encounter {args.encounter_id}. Backup saved as: {args.path}.bak")
    else:
        print("Nothing removed (encounter not found).")


def cmd_validate(args: argparse.Namespace) -> None:
    ok, message = validate_file(args.path)
    print(message)
    if not ok:
        raise SystemExit(1)


def cmd_repair(args: argparse.Namespace) -> None:
    ok, message = repair_trailing_commas(args.path, verbose=True)
    print(message)
    if not ok:
        raise SystemExit(1)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage boss_mechanics.generated.json without needing a Warcraft Logs report."
    )
    parser.add_argument(
        "--path",
        default=str(DEFAULT_CONFIG_PATH),
        help="Path to the generated JSON config (default: boss_mechanics.generated.json)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List configured encounters/mechanics")
    list_parser.add_argument("encounter_id", nargs="?", type=int, help="Show detail for one encounter")
    list_parser.set_defaults(func=cmd_list)

    remove_parser = subparsers.add_parser("remove", help="Remove specific mechanics from one encounter")
    remove_parser.add_argument("encounter_id", type=int)
    remove_parser.set_defaults(func=cmd_remove)

    remove_encounter_parser = subparsers.add_parser(
        "remove-encounter", help="Remove an entire encounter's configuration"
    )
    remove_encounter_parser.add_argument("encounter_id", type=int)
    remove_encounter_parser.add_argument(
        "--yes", action="store_true", help="Skip the confirmation prompt"
    )
    remove_encounter_parser.set_defaults(func=cmd_remove_encounter)

    validate_parser = subparsers.add_parser("validate", help="Check the JSON file for syntax errors")
    validate_parser.set_defaults(func=cmd_validate)

    repair_parser = subparsers.add_parser(
        "repair", help="Attempt to auto-fix a common syntax error (a trailing comma)"
    )
    repair_parser.set_defaults(func=cmd_repair)

    return parser


def main() -> None:
    args = build_argument_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
