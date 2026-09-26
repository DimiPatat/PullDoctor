"""
manage_defensive_cooldowns.py

Local-only maintenance tool for defensive_cooldowns.generated.json: list
what's tracked (including each ability's damage-mitigation profile),
remove specific abilities, and check/repair the file's JSON syntax --
all WITHOUT needing a Warcraft Logs report.

Usage:
    python manage_defensive_cooldowns.py list
    python manage_defensive_cooldowns.py remove
    python manage_defensive_cooldowns.py validate
    python manage_defensive_cooldowns.py repair
"""
from __future__ import annotations

import argparse

from defensive_cooldown_config_io import (
    DEFAULT_CONFIG_PATH,
    DefensiveCooldownConfigError,
    load_generated_defensive_cooldowns,
    remove_defensive_cooldowns,
    repair_trailing_commas,
    validate_file,
)
from defensive_cooldown_schema import DefensiveCooldownDefinition
from selection_utils import parse_selection


def _mitigation_summary(definition: DefensiveCooldownDefinition) -> str:
    if definition.mitigation_type == "percent_reduction":
        pct = f"{definition.damage_reduction_percent:.0f}%" if definition.damage_reduction_percent is not None else "?%"
        dur = f"{definition.duration_seconds:.0f}s" if definition.duration_seconds is not None else "?s"
        return f"percent_reduction: {pct} for {dur}"
    if definition.mitigation_type == "immunity":
        dur = f"{definition.duration_seconds:.0f}s" if definition.duration_seconds is not None else "?s"
        return f"immunity: {dur}"
    return "unmodeled (no damage-prevention estimate)"


def print_defensive_cooldowns_detail(
    definitions: list[DefensiveCooldownDefinition],
) -> list[DefensiveCooldownDefinition]:
    if not definitions:
        print("  (none)")
        return []

    ordered: list[DefensiveCooldownDefinition] = []
    classes_present = sorted({d.class_name or "(unspecified class)" for d in definitions})
    number = 1
    for class_name in classes_present:
        print(f"class: {class_name}")
        items = sorted(
            (d for d in definitions if (d.class_name or "(unspecified class)") == class_name),
            key=lambda d: d.ability_name,
        )
        for definition in items:
            ids_text = f" ids={list(definition.ability_ids)}" if definition.ability_ids else ""
            category_text = f" ({definition.category})" if definition.category else ""
            print(f"  {number:>2}. {definition.ability_name}{category_text} [{definition.cooldown_seconds:.0f}s cooldown]{ids_text}")
            print(f"      mitigation: {_mitigation_summary(definition)}")
            if definition.notes:
                print(f"      notes: {definition.notes}")
            ordered.append(definition)
            number += 1
        print()
    return ordered


def _load_or_exit(path) -> list[DefensiveCooldownDefinition]:
    try:
        return load_generated_defensive_cooldowns(path)
    except DefensiveCooldownConfigError as exc:
        print(f"Could not read {path}:\n\n{exc}")
        raise SystemExit(1)


def interactive_remove_defensive_cooldowns(
    path,
    definitions: list[DefensiveCooldownDefinition] | None = None,
) -> list[str]:
    if definitions is None:
        try:
            definitions = load_generated_defensive_cooldowns(path)
        except DefensiveCooldownConfigError as exc:
            print(f"WARNING: could not read {path}:\n{exc}")
            return []

    if not definitions:
        print("No defensive cooldowns configured yet -- nothing to remove.")
        return []

    ordered = print_defensive_cooldowns_detail(definitions)

    raw = input("Which item number(s) to remove (e.g. 2,5-7), or press Enter to cancel: ").strip()
    if not raw:
        print("Cancelled -- nothing removed.")
        return []

    try:
        indexes = parse_selection(raw, len(ordered))
    except ValueError as exc:
        print(f"Invalid selection: {exc}")
        return []
    if not indexes:
        print("Cancelled -- nothing removed.")
        return []

    names_to_remove = [ordered[i].ability_name for i in indexes]
    print("\nAbout to remove:")
    for name in names_to_remove:
        print(f"  - {name}")

    confirm = input(
        f"\nA backup of {path} will be saved as {path}.bak first. Proceed? [y/N]: "
    ).strip().lower()
    if confirm not in {"y", "yes"}:
        print("Cancelled -- nothing removed.")
        return []

    removed = remove_defensive_cooldowns(names_to_remove, path, verbose=True)
    if removed:
        print(f"\nRemoved {len(removed)} defensive cooldown(s).")
        print(f"Backup saved as: {path}.bak")
    else:
        print("\nNothing matched -- file left untouched.")
    return removed


def cmd_list(args: argparse.Namespace) -> None:
    definitions = _load_or_exit(args.path)
    if not definitions:
        print(f"No defensive cooldowns configured yet in {args.path}.")
        return
    print(f"{len(definitions)} defensive cooldown(s) tracked in {args.path}:\n")
    print_defensive_cooldowns_detail(definitions)


def cmd_remove(args: argparse.Namespace) -> None:
    definitions = _load_or_exit(args.path)
    if not definitions:
        print(f"No defensive cooldowns configured yet in {args.path} -- nothing to remove.")
        return
    interactive_remove_defensive_cooldowns(args.path, definitions=definitions)


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
        description="Manage defensive_cooldowns.generated.json without needing a Warcraft Logs report."
    )
    parser.add_argument(
        "--path", default=str(DEFAULT_CONFIG_PATH),
        help="Path to the generated JSON config (default: defensive_cooldowns.generated.json)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List all tracked defensive cooldowns")
    list_parser.set_defaults(func=cmd_list)

    remove_parser = subparsers.add_parser("remove", help="Remove specific defensive cooldowns interactively")
    remove_parser.set_defaults(func=cmd_remove)

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
