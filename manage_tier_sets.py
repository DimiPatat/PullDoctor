"""
manage_tier_sets.py
CLI for viewing/editing tier_sets.generated.json directly -- no report
required. Mirrors the shape of the project's other manage_*.py tools
(list/add/remove/validate/repair), plus tier-set-specific
`set-breakpoint` commands for the item-level track table.

CHANGED: `set-breakpoint` now accepts BOTH --min-ilvl and --max-ilvl
(max is optional; omit it to leave that track's existing ceiling
unchanged, or pass an empty string to explicitly clear it to "no
ceiling"). This was needed to fix a real reported bug where a track's
top 2 upgrade ranks (which share the exact same item level as the next
track's bottom 2 ranks, by Blizzard's own design) were always shown as
the HIGHER track -- see tier_set_data.py's module docstring for the
full explanation. `set-breakpoint` previously only tracked a floor per
track, which couldn't express the fix at all.

Usage:
    python manage_tier_sets.py list
    python manage_tier_sets.py add --class "Paladin" --slot 0 --item-id 123456
    python manage_tier_sets.py add
        (no args -> interactive prompts)
    python manage_tier_sets.py remove --class "Paladin" --slot 0
    python manage_tier_sets.py set-breakpoint --track H --min-ilvl 305 --max-ilvl 321
    python manage_tier_sets.py set-breakpoint --track M --min-ilvl 318 --max-ilvl ""
        (empty string clears the ceiling entirely -- "no upper bound")
    python manage_tier_sets.py validate
    python manage_tier_sets.py repair
"""
import argparse
import sys

import tier_set_config_io
from tier_set_config_io import TierSetConfigError
from tier_set_data import SEED_TRACK_BREAKPOINTS, TRACK_BREAKPOINTS, TIER_SET_NAMES_BY_CLASS
from tier_set_schema import TIER_SET_SLOT_ORDER, TIER_SET_SLOTS, TierSetPieceDefinition, TierTrackBreakpoint

SLOT_NAME_TO_INDEX = {name.lower(): slot for slot, name in TIER_SET_SLOTS.items()}


def _resolve_slot(raw: str) -> int:
    """Accept either a raw slot index (e.g. "0") or a slot name (e.g. "Head", case-insensitive)."""
    try:
        slot = int(raw)
        if slot in TIER_SET_SLOTS:
            return slot
        raise ValueError
    except ValueError:
        key = raw.strip().lower()
        if key in SLOT_NAME_TO_INDEX:
            return SLOT_NAME_TO_INDEX[key]
        valid = ", ".join(f"{s} ({n})" for s, n in TIER_SET_SLOTS.items())
        raise SystemExit(f"Unrecognized slot '{raw}'. Valid tier slots: {valid}")


def _format_range(bp: TierTrackBreakpoint) -> str:
    if bp.max_item_level is None:
        return f"{bp.min_item_level:.0f}+ (no ceiling)"
    return f"{bp.min_item_level:.0f}-{bp.max_item_level:.0f}"


def cmd_list(_args) -> None:
    from tier_set_data import TRACKED_TIER_SET_PIECES
    if not TRACKED_TIER_SET_PIECES:
        print("No tier-set pieces tracked yet. Run `python explore_tier_sets.py` to find candidates, then `add` them here.")
    else:
        print(f"Tracked tier-set pieces ({len(TRACKED_TIER_SET_PIECES)}):")
        current_class = None
        for p in TRACKED_TIER_SET_PIECES:
            if p.class_name != current_class:
                current_class = p.class_name
                set_name = TIER_SET_NAMES_BY_CLASS.get(current_class)
                header = f"\n{current_class}" + (f"  ({set_name})" if set_name else "")
                print(header)
            note = f"  -- {p.notes}" if p.notes else ""
            print(f"  {p.slot_name:<9} item_id={p.item_id}{note}")

    print(f"\nTrack breakpoints ({'custom' if TRACK_BREAKPOINTS is not SEED_TRACK_BREAKPOINTS else 'seed default'}):")
    for bp in TRACK_BREAKPOINTS:
        print(f"  {bp.track_letter}  {bp.track_name:<11} ilvl {_format_range(bp)}")


def cmd_add(args) -> None:
    if args.class_name and args.slot is not None and args.item_id is not None:
        class_name, slot, item_id, notes = args.class_name, _resolve_slot(args.slot), args.item_id, args.notes
    else:
        print("Interactive add (press Ctrl+C to cancel):")
        class_name = input("  Class name (e.g. Paladin): ").strip()
        slot_raw = input(f"  Slot ({', '.join(TIER_SET_SLOTS.values())}): ").strip()
        slot = _resolve_slot(slot_raw)
        item_id = int(input("  Item ID: ").strip())
        notes = input("  Notes (optional, press Enter to skip): ").strip() or None

    piece = TierSetPieceDefinition(class_name=class_name, slot=slot, item_id=item_id, notes=notes)
    path = tier_set_config_io.save_tier_set_pieces([piece], verbose=True)
    print(f"\nSaved to {path}")


def cmd_remove(args) -> None:
    slot = _resolve_slot(args.slot)
    removed = tier_set_config_io.remove_tier_set_piece(args.class_name, slot, verbose=True)
    if not removed:
        print(f"No tracked piece found for {args.class_name}, slot {slot} ({TIER_SET_SLOTS.get(slot, '?')}).")


def cmd_set_breakpoint(args) -> None:
    valid_letters = {bp.track_letter for bp in SEED_TRACK_BREAKPOINTS}
    if args.track not in valid_letters:
        raise SystemExit(f"Unrecognized track letter '{args.track}'. Valid: {sorted(valid_letters)}")

    current = {bp.track_letter: bp for bp in TRACK_BREAKPOINTS}
    seed_by_letter = {bp.track_letter: bp for bp in SEED_TRACK_BREAKPOINTS}
    existing = current.get(args.track) or seed_by_letter[args.track]

    new_min = args.min_ilvl if args.min_ilvl is not None else existing.min_item_level
    if args.max_ilvl is None:
        new_max = existing.max_item_level  # leave unchanged
    elif args.max_ilvl == "":
        new_max = None  # explicitly cleared -- "no ceiling"
    else:
        new_max = float(args.max_ilvl)

    updated = TierTrackBreakpoint(
        min_item_level=new_min, max_item_level=new_max,
        track_letter=existing.track_letter, track_name=existing.track_name, color_hex=existing.color_hex,
    )
    current[args.track] = updated
    new_breakpoints = sorted(current.values(), key=lambda b: b.min_item_level)
    path = tier_set_config_io.save_track_breakpoints(new_breakpoints, verbose=True)
    print(f"\n{updated.track_name} now covers ilvl {_format_range(updated)}. Saved to {path}")


def cmd_validate(_args) -> None:
    ok, message = tier_set_config_io.validate_file()
    print(message)
    sys.exit(0 if ok else 1)


def cmd_repair(_args) -> None:
    ok, message = tier_set_config_io.repair_trailing_commas(verbose=True)
    print(message)
    sys.exit(0 if ok else 1)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage tracked tier-set pieces and track breakpoints.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list", help="List all tracked tier pieces and current track breakpoints.").set_defaults(func=cmd_list)

    p_add = subparsers.add_parser("add", help="Track a new tier-set piece (or update an existing class+slot).")
    p_add.add_argument("--class", dest="class_name", help="Class name, e.g. Paladin")
    p_add.add_argument("--slot", help="Slot index or name, e.g. 0 or Head")
    p_add.add_argument("--item-id", dest="item_id", type=int, help="Item ID")
    p_add.add_argument("--notes", default=None)
    p_add.set_defaults(func=cmd_add)

    p_remove = subparsers.add_parser("remove", help="Stop tracking a class+slot's tier piece.")
    p_remove.add_argument("--class", dest="class_name", required=True)
    p_remove.add_argument("--slot", required=True)
    p_remove.set_defaults(func=cmd_remove)

    p_bp = subparsers.add_parser("set-breakpoint", help="Override one track's item-level floor and/or ceiling.")
    p_bp.add_argument("--track", required=True, help="Track letter: A, V, C, H, or M")
    p_bp.add_argument("--min-ilvl", dest="min_ilvl", type=float, default=None, help="New floor (leave unset to keep current)")
    p_bp.add_argument(
        "--max-ilvl", dest="max_ilvl", default=None,
        help="New ceiling (leave unset to keep current; pass an empty string \"\" to explicitly clear it to 'no ceiling')",
    )
    p_bp.set_defaults(func=cmd_set_breakpoint)

    subparsers.add_parser("validate", help="Check tier_sets.generated.json is valid JSON.").set_defaults(func=cmd_validate)
    subparsers.add_parser("repair", help="Attempt to auto-fix a trailing-comma JSON error.").set_defaults(func=cmd_repair)

    return parser


def main():
    args = build_argument_parser().parse_args()
    try:
        args.func(args)
    except TierSetConfigError as exc:
        print(str(exc))
        sys.exit(1)


if __name__ == "__main__":
    main()
