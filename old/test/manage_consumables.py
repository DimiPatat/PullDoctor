"""
manage_consumables.py

Local-only maintenance tool for consumables.generated.json: list what's
tracked, remove specific items or a whole category, and check/repair the
file's JSON syntax -- all WITHOUT needing a Warcraft Logs report.

Usage:
    python manage_consumables.py list
    python manage_consumables.py remove
    python manage_consumables.py remove-category health_potion
    python manage_consumables.py validate
    python manage_consumables.py repair
"""
from __future__ import annotations

import argparse

from consumable_config_io import (
    DEFAULT_CONFIG_PATH,
    ConsumablesConfigError,
    load_generated_consumables,
    remove_category,
    remove_consumables,
    repair_trailing_commas,
    validate_file,
)
from consumable_schema import ConsumableDefinition
from selection_utils import parse_selection


def print_consumables_detail(
    consumables: list[ConsumableDefinition], category_mandatory: dict[str, bool]
) -> list[ConsumableDefinition]:
    """
    Print a numbered, category-grouped table of tracked consumables.
    Returns the list of ConsumableDefinition in PRINTED order.
    """
    if not consumables:
        print("  (none)")
        return []

    ordered: list[ConsumableDefinition] = []
    categories_present = sorted({c.category for c in consumables})
    number = 1
    for category in categories_present:
        mandatory = category_mandatory.get(category, True)
        label = "mandatory" if mandatory else "optional"
        print(f"category: {category} ({label})")
        items = sorted((c for c in consumables if c.category == category), key=lambda c: c.name)
        for consumable in items:
            ids_text = f" ids={list(consumable.ability_ids)}" if consumable.ability_ids else ""
            print(f"  {number:>2}. {consumable.name}{ids_text}")
            if consumable.notes:
                print(f"      notes: {consumable.notes}")
            ordered.append(consumable)
            number += 1
        print()
    return ordered


def _load_or_exit(path):
    try:
        return load_generated_consumables(path)
    except ConsumablesConfigError as exc:
        print(f"Could not read {path}:\n\n{exc}")
        raise SystemExit(1)


def interactive_remove_consumables(
    path,
    consumables: list[ConsumableDefinition] | None = None,
    category_mandatory: dict[str, bool] | None = None,
) -> list[str]:
    """Prompt to pick consumables to remove (by number) or an entire category, confirm, then remove."""
    if consumables is None or category_mandatory is None:
        try:
            consumables, category_mandatory = load_generated_consumables(path)
        except ConsumablesConfigError as exc:
            print(f"WARNING: could not read {path}:\n{exc}")
            return []

    if not consumables:
        print("No consumables configured yet -- nothing to remove.")
        return []

    ordered = print_consumables_detail(consumables, category_mandatory)

    raw = input(
        "Which item number(s) to remove (e.g. 2,5-7), or type 'c' to remove\n"
        "an entire category, or press Enter to cancel: "
    ).strip()
    if not raw:
        print("Cancelled -- nothing removed.")
        return []

    if raw.lower().startswith("c"):
        category = input("Which category do you want to remove entirely? ").strip()
        if not category:
            print("Cancelled -- nothing removed.")
            return []
        confirm = input(
            f"\nThis deletes ALL consumables in category '{category}'. A backup will "
            f"be saved as {path}.bak first. Proceed? [y/N]: "
        ).strip().lower()
        if confirm not in {"y", "yes"}:
            print("Cancelled -- nothing removed.")
            return []
        removed = remove_category(category, path, verbose=True)
        if removed:
            print(f"\nRemoved category '{category}'. Backup saved as: {path}.bak")
            return [category]
        print("\nCategory not found -- nothing removed.")
        return []

    try:
        indexes = parse_selection(raw, len(ordered))
    except ValueError as exc:
        print(f"Invalid selection: {exc}")
        return []
    if not indexes:
        print("Cancelled -- nothing removed.")
        return []

    names_to_remove = [ordered[i].name for i in indexes]
    print("\nAbout to remove:")
    for name in names_to_remove:
        print(f"  - {name}")

    confirm = input(
        f"\nA backup of {path} will be saved as {path}.bak first. Proceed? [y/N]: "
    ).strip().lower()
    if confirm not in {"y", "yes"}:
        print("Cancelled -- nothing removed.")
        return []

    removed = remove_consumables(names_to_remove, path, verbose=True)
    if removed:
        print(f"\nRemoved {len(removed)} consumable(s).")
        print(f"Backup saved as: {path}.bak")
    else:
        print("\nNothing matched -- file left untouched.")
    return removed


def cmd_list(args: argparse.Namespace) -> None:
    consumables, category_mandatory = _load_or_exit(args.path)
    if not consumables:
        print(f"No consumables configured yet in {args.path}.")
        return
    print(f"{len(consumables)} consumable(s) tracked in {args.path}:\n")
    print_consumables_detail(consumables, category_mandatory)


def cmd_remove(args: argparse.Namespace) -> None:
    consumables, category_mandatory = _load_or_exit(args.path)
    if not consumables:
        print(f"No consumables configured yet in {args.path} -- nothing to remove.")
        return
    interactive_remove_consumables(args.path, consumables=consumables, category_mandatory=category_mandatory)


def cmd_remove_category(args: argparse.Namespace) -> None:
    consumables, _ = _load_or_exit(args.path)
    if not any(c.category == args.category for c in consumables):
        print(f"Category '{args.category}' is not configured -- nothing to remove.")
        return
    if not args.yes:
        confirm = input(
            f"This deletes ALL consumables in category '{args.category}'. A backup "
            f"will be saved as {args.path}.bak first. Proceed? [y/N]: "
        ).strip().lower()
        if confirm not in {"y", "yes"}:
            print("Cancelled -- nothing removed.")
            return
    removed = remove_category(args.category, args.path, verbose=True)
    if removed:
        print(f"\nRemoved category '{args.category}'. Backup saved as: {args.path}.bak")
    else:
        print("Nothing removed (category not found).")


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
        description="Manage consumables.generated.json without needing a Warcraft Logs report."
    )
    parser.add_argument(
        "--path", default=str(DEFAULT_CONFIG_PATH),
        help="Path to the generated JSON config (default: consumables.generated.json)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List all tracked consumables")
    list_parser.set_defaults(func=cmd_list)

    remove_parser = subparsers.add_parser("remove", help="Remove specific consumables interactively")
    remove_parser.set_defaults(func=cmd_remove)

    remove_category_parser = subparsers.add_parser(
        "remove-category", help="Remove an entire category and all its consumables"
    )
    remove_category_parser.add_argument("category")
    remove_category_parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    remove_category_parser.set_defaults(func=cmd_remove_category)

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
