"""
manage_raid_cooldowns.py
CLI for viewing/editing raid_cooldowns.generated.json directly -- no
report required. Mirrors the shape of the project's other manage_*.py
tools (list/add/remove/validate/repair).

Usage:
    python manage_raid_cooldowns.py list
    python manage_raid_cooldowns.py add --name "Tranquility" --cooldown 180 --category healing_cd --class Druid
    python manage_raid_cooldowns.py add
        (no args -> interactive prompts)
    python manage_raid_cooldowns.py remove --name "Tranquility"
    python manage_raid_cooldowns.py validate
    python manage_raid_cooldowns.py repair
"""
import argparse
import sys

import raid_cooldown_config_io
from raid_cooldown_config_io import RaidCooldownConfigError
from raid_cooldown_schema import RaidCooldownDefinition


def cmd_list(_args) -> None:
    tracked = raid_cooldown_config_io.load_generated_raid_cooldowns()
    if not tracked:
        print(
            "No raid cooldowns tracked yet. Run "
            "`python explore_raid_cooldowns.py <report_code> [fight_id]` to find "
            "candidates from a real log, then `add` them here."
        )
        return
    print(f"Tracked raid cooldowns ({len(tracked)}):")
    for cd in sorted(tracked, key=lambda c: (c.category or "", c.ability_name)):
        class_str = f" ({cd.class_name})" if cd.class_name else ""
        category_str = f"[{cd.category}]" if cd.category else "[uncategorized]"
        note_str = f"\n      note: {cd.notes}" if cd.notes else ""
        print(f"  {cd.ability_name:<28} {cd.cooldown_seconds:>5.0f}s  {category_str}{class_str}{note_str}")


def cmd_add(args) -> None:
    if args.name and args.cooldown is not None:
        ability_name, cooldown_seconds = args.name, args.cooldown
        category, class_name, notes = args.category, args.class_name, args.notes
    else:
        print("Interactive add (press Ctrl+C to cancel):")
        ability_name = input("  Ability name (exact, as it appears in the log): ").strip()
        cooldown_seconds = float(input("  Cooldown, in seconds: ").strip())
        category = input("  Category (e.g. healing_cd, damage_reduction_cd, utility -- optional): ").strip() or None
        class_name = input("  Class (informational only, optional): ").strip() or None
        notes = input("  Notes (optional): ").strip() or None

    cd = RaidCooldownDefinition(
        ability_name=ability_name, cooldown_seconds=cooldown_seconds,
        category=category, class_name=class_name, notes=notes,
    )
    path = raid_cooldown_config_io.save_raid_cooldowns([cd], verbose=True)
    print(f"\nSaved to {path}")
    print(
        "\nNOTE: this only updates raid_cooldowns.generated.json. For it to actually "
        "affect reports, raid_cooldowns_config.py's TRACKED_COOLDOWNS must load from "
        "this file -- see that module's docstring / the wiring snippet you were given "
        "if it doesn't yet."
    )


def cmd_remove(args) -> None:
    removed = raid_cooldown_config_io.remove_raid_cooldown(args.name, verbose=True)
    if not removed:
        print(f"No tracked raid cooldown found named '{args.name}'.")


def cmd_validate(_args) -> None:
    ok, message = raid_cooldown_config_io.validate_file()
    print(message)
    sys.exit(0 if ok else 1)


def cmd_repair(_args) -> None:
    ok, message = raid_cooldown_config_io.repair_trailing_commas(verbose=True)
    print(message)
    sys.exit(0 if ok else 1)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage tracked raid cooldowns.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list", help="List all tracked raid cooldowns.").set_defaults(func=cmd_list)

    p_add = subparsers.add_parser("add", help="Track a new raid cooldown (or update an existing one).")
    p_add.add_argument("--name", help="Exact ability name as it appears in the log")
    p_add.add_argument("--cooldown", type=float, help="Cooldown, in seconds")
    p_add.add_argument("--category", default=None, help="e.g. healing_cd, damage_reduction_cd, utility")
    p_add.add_argument("--class", dest="class_name", default=None, help="Informational only")
    p_add.add_argument("--notes", default=None)
    p_add.set_defaults(func=cmd_add)

    p_remove = subparsers.add_parser("remove", help="Stop tracking an ability.")
    p_remove.add_argument("--name", required=True)
    p_remove.set_defaults(func=cmd_remove)

    subparsers.add_parser("validate", help="Check raid_cooldowns.generated.json is valid JSON.").set_defaults(func=cmd_validate)
    subparsers.add_parser("repair", help="Attempt to auto-fix a trailing-comma JSON error.").set_defaults(func=cmd_repair)

    return parser


def main():
    args = build_argument_parser().parse_args()
    try:
        args.func(args)
    except RaidCooldownConfigError as exc:
        print(str(exc))
        sys.exit(1)


if __name__ == "__main__":
    main()
