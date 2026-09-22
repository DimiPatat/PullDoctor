"""
migrate_consumable_names.py

One-time repair tool for consumables.generated.json.

CHANGE (this update): the previous version of this migration flipped
on detection_via_weapon_enchant but still relied on an EXACT enchant
ID match against ability_ids -- which, per real-world testing, turned
out to still not work (Wowhead spell IDs don't reliably match
Blizzard's internal item-enchantment IDs). This version instead
ensures loose_weapon_enchant_match=True is ALSO set for any oil
already tracked -- meaning "used oil" is now satisfied by ANY
temporary weapon enchant being present at all, with no ID comparison
required. See consumables_analyzer.py's module docstring for the full
3-round debugging story.

If you already ran the PREVIOUS version of this migration and oil is
STILL showing as missing, run this updated version again -- it is
safe to re-run; it only touches entries that still need the loose-
match flag enabled.

Usage:
    python migrate_consumable_names.py
    python migrate_consumable_names.py --yes
    python migrate_consumable_names.py --path my.json
"""
from __future__ import annotations

import argparse

from consumable_config_io import (
    DEFAULT_CONFIG_PATH,
    ConsumablesConfigError,
    load_generated_consumables,
    rename_consumable,
    save_consumable_selection,
    set_weapon_enchant_detection,
)
from consumable_schema import ConsumableDefinition

RENAMES: list[tuple[str, str, str]] = [
    ("Potion Of Recklessness", "Potion of Recklessness", "lowercase 'of'"),
    ("Potion Of Zealotry", "Potion of Zealotry", "lowercase 'of'"),
    ("Draught Of Rampant Abandon", "Draught of Rampant Abandon", "lowercase 'of'"),
    ("Oil Of Dawn", "Oil of Dawn", "lowercase 'of'"),
]

# Any name that LOOKS like a tracked oil gets the loose-match fix,
# regardless of which specific oil name(s) you have -- this way a
# custom/manually-added oil name still gets fixed too.
KNOWN_OIL_NAMES = [
    "Thalassian Phoenix Oil",
    "Smuggler's Enchanted Edge",
    "Oil of Dawn",
]

FOOD_ADDITIONS: list[ConsumableDefinition] = [
    ConsumableDefinition("Hearty Well Fed", "food", notes="Added by migrate_consumable_names.py."),
    ConsumableDefinition("Well Fed", "food", notes="Added by migrate_consumable_names.py."),
]


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fix known consumable detection issues in consumables.generated.json.")
    parser.add_argument("--path", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    return parser


def main() -> None:
    args = build_argument_parser().parse_args()

    try:
        consumables, _ = load_generated_consumables(args.path)
    except ConsumablesConfigError as exc:
        print(f"Could not read {args.path}:\n\n{exc}")
        raise SystemExit(1)

    if not consumables:
        print(f"No consumables configured yet in {args.path} -- nothing to migrate.")
        return

    tracked_names = {c.name for c in consumables}
    applicable_renames = [(old, new, reason) for old, new, reason in RENAMES if old in tracked_names]

    names_after_rename = set(tracked_names)
    for old, new, _ in applicable_renames:
        names_after_rename.discard(old)
        names_after_rename.add(new)

    by_name = {c.name: c for c in consumables}
    all_oil_names_tracked = {c.name for c in consumables if c.category == "oil"} | (
        set(KNOWN_OIL_NAMES) & names_after_rename
    )
    applicable_loose_match_fixes = [
        name for name in all_oil_names_tracked
        if not (by_name.get(name) and by_name[name].detection_via_weapon_enchant and by_name[name].loose_weapon_enchant_match)
    ]

    applicable_food_additions = [c for c in FOOD_ADDITIONS if c.name not in tracked_names]

    if not applicable_renames and not applicable_loose_match_fixes and not applicable_food_additions:
        print(f"{args.path} doesn't need any of these fixes -- nothing to migrate.")
        return

    print(f"The following changes will be made to {args.path}:\n")
    for old, new, reason in applicable_renames:
        print(f"  RENAME  {old!r} -> {new!r}   ({reason})")
    for name in applicable_loose_match_fixes:
        print(f"  FIX     {name!r}  -- enable LOOSE weapon-enchant matching (no exact ID required)")
    for definition in applicable_food_additions:
        print(f"  ADD     {definition.name!r}  (category: food)")

    if not args.yes:
        confirm = input(f"\nA backup of {args.path} will be saved as {args.path}.bak first. Proceed? [y/N]: ").strip().lower()
        if confirm not in {"y", "yes"}:
            print("Cancelled -- nothing changed.")
            return

    for old, new, _reason in applicable_renames:
        rename_consumable(old, new, path=args.path, verbose=True)

    if applicable_loose_match_fixes:
        set_weapon_enchant_detection(applicable_loose_match_fixes, path=args.path, verbose=True)

    if applicable_food_additions:
        save_consumable_selection(applicable_food_additions, path=args.path, verbose=True)

    print(f"\nDone. Backup saved as: {args.path}.bak")
    print("Re-run your report -- oil should now be detected for anyone with ANY weapon enchant applied, regardless of the exact ID.")


if __name__ == "__main__":
    main()
