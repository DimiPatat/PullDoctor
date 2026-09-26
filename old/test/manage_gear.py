"""
manage_gear.py

Local-only maintenance tool for gear_requirements.generated.json: list
the current compliance profile, remove specific enchant-slot rules or
reset a threshold, and check/repair the file's JSON syntax -- all
WITHOUT needing a Warcraft Logs report.

Usage:
    python manage_gear.py list
    python manage_gear.py remove
    python manage_gear.py validate
    python manage_gear.py repair
"""
from __future__ import annotations

import argparse

from gear_config_io import (
    DEFAULT_CONFIG_PATH,
    GearConfigError,
    clear_threshold,
    load_generated_gear_requirements,
    remove_enchant_requirement,
    repair_trailing_commas,
    validate_file,
)
from gear_schema import GearRequirements

_THRESHOLD_LABELS = {
    "min_item_level": "Minimum average item level",
    "min_quality": "Minimum quality tier",
    "min_gem_count": "Minimum gem count",
    "required_gem_ids": "Gem quality whitelist",
    "notes": "Profile notes",
}


def print_gear_requirements_detail(requirements: GearRequirements) -> list[tuple[str, str]]:
    """
    Print the current gear compliance profile as a numbered list, and
    return the printed items as (kind, key) tuples in DISPLAY order.
    """
    import gear_analyzer

    ordered: list[tuple[str, str]] = []
    number = 1

    print("Global thresholds:")
    any_threshold_set = False
    for field_name, label in _THRESHOLD_LABELS.items():
        value = getattr(requirements, field_name)
        if not value:
            continue
        any_threshold_set = True
        if field_name == "min_quality":
            display_value = gear_analyzer.quality_name(value)
        elif field_name == "required_gem_ids":
            display_value = list(value)
        else:
            display_value = value
        print(f"  {number:>2}. {label}: {display_value}")
        ordered.append(("threshold", field_name))
        number += 1
    if not any_threshold_set:
        print("  (none configured)")

    print("\nPer-slot enchant rules:")
    if not requirements.enchant_requirements:
        print("  (none configured -- every enchantable slot uses the default 'any enchant counts' check)")
    else:
        for requirement in requirements.enchant_requirements:
            if requirement.skip_check:
                detail = "excluded from compliance checking"
            elif requirement.required_enchant_ids:
                detail = f"must be one of: {list(requirement.required_enchant_ids)}"
            else:
                detail = "any enchant counts (just must not be missing)"
            print(f"  {number:>2}. {requirement.slot_name}: {detail}")
            if requirement.notes:
                print(f"      notes: {requirement.notes}")
            ordered.append(("slot", str(requirement.slot)))
            number += 1

    return ordered


def _load_or_exit(path) -> GearRequirements:
    try:
        return load_generated_gear_requirements(path)
    except GearConfigError as exc:
        print(f"Could not read {path}:\n\n{exc}")
        raise SystemExit(1)


def interactive_remove_gear_setting(path, requirements: GearRequirements | None = None) -> bool:
    """Prompt to pick ONE threshold or per-slot rule to clear/remove, confirm, then remove."""
    if requirements is None:
        try:
            requirements = load_generated_gear_requirements(path)
        except GearConfigError as exc:
            print(f"WARNING: could not read {path}:\n{exc}")
            return False

    ordered = print_gear_requirements_detail(requirements)
    if not ordered:
        print("\nNothing configured yet -- nothing to remove.")
        return False

    raw = input(
        "\nWhich number to clear/remove? Press Enter to cancel: "
    ).strip()
    if not raw:
        print("Cancelled -- nothing removed.")
        return False

    try:
        index = int(raw) - 1
    except ValueError:
        print(f"'{raw}' isn't a valid number.")
        return False
    if not (0 <= index < len(ordered)):
        print(f"'{raw}' is out of range.")
        return False

    kind, key = ordered[index]
    if kind == "threshold":
        label = _THRESHOLD_LABELS[key]
        confirm = input(
            f"\nThis clears '{label}'. A backup of {path} will be saved as {path}.bak "
            f"first. Proceed? [y/N]: "
        ).strip().lower()
        if confirm not in {"y", "yes"}:
            print("Cancelled -- nothing removed.")
            return False
        cleared = clear_threshold(key, path, verbose=True)
        if cleared:
            print(f"\nCleared {label}. Backup saved as: {path}.bak")
        else:
            print("\nNothing to clear (it wasn't set).")
        return cleared

    slot = int(key)
    requirement = next(r for r in requirements.enchant_requirements if r.slot == slot)
    confirm = input(
        f"\nThis removes the rule for slot '{requirement.slot_name}' (reverts to the "
        f"default 'any enchant counts' check). A backup will be saved as {path}.bak "
        f"first. Proceed? [y/N]: "
    ).strip().lower()
    if confirm not in {"y", "yes"}:
        print("Cancelled -- nothing removed.")
        return False
    removed = remove_enchant_requirement(slot, path, verbose=True)
    if removed:
        print(f"\nRemoved rule for '{requirement.slot_name}'. Backup saved as: {path}.bak")
    else:
        print("\nNothing matched -- file left untouched.")
    return removed


def cmd_list(args: argparse.Namespace) -> None:
    requirements = _load_or_exit(args.path)
    print_gear_requirements_detail(requirements)


def cmd_remove(args: argparse.Namespace) -> None:
    requirements = _load_or_exit(args.path)
    interactive_remove_gear_setting(args.path, requirements)


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
        description="Manage gear_requirements.generated.json without needing a Warcraft Logs report."
    )
    parser.add_argument(
        "--path", default=str(DEFAULT_CONFIG_PATH),
        help="Path to the generated JSON config (default: gear_requirements.generated.json)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="Show the current gear compliance profile")
    list_parser.set_defaults(func=cmd_list)

    remove_parser = subparsers.add_parser("remove", help="Clear a threshold or remove a per-slot rule")
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
