"""
tier_sets.py

MERGED MODULE -- combines what used to be five separate files into one,
as part of the PullDoctor file-count reduction pass:
    - tier_set_schema.py     (TierSetPieceDefinition / TierTrackBreakpoint dataclasses)
    - tier_set_config_io.py  (load/save/validate/repair .generated.json)
    - tier_set_data.py       (SEED_TRACK_BREAKPOINTS + TRACKED_TIER_SET_PIECES merge)
    - tier_set_analyzer.py   (per-player tier completion + track-letter string)
    - tier_set_explorer.py   (heuristic discovery of tier-piece candidates)
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

from core import (
    FightFilterCriteria,
    filter_fights,
    format_filter_summary,
    get_fight_roster,
    parse_fight_bundle,
    resolve_fight_id,
)
from wcl import WCLClient, WCLAPIError, get_credentials

TIER_SET_SLOTS: dict[int, str] = {
    0: "Head", 2: "Shoulder", 4: "Chest", 9: "Gloves", 6: "Legs",
}
TIER_SET_SLOT_ORDER: tuple[int, ...] = (0, 2, 4, 9, 6)
MISSING_TIER_PIECE_CHAR = "_"


@dataclass
class TierSetPieceDefinition:
    class_name: str
    slot: int
    item_id: int
    notes: str | None = None

    @property
    def slot_name(self) -> str:
        return TIER_SET_SLOTS.get(self.slot, f"Slot {self.slot}")


@dataclass
class TierTrackBreakpoint:
    min_item_level: float
    track_letter: str
    track_name: str
    color_hex: str
    max_item_level: float | None = None

    def contains(self, item_level: float) -> bool:
        if item_level < self.min_item_level:
            return False
        if self.max_item_level is not None and item_level > self.max_item_level:
            return False
        return True


DEFAULT_CONFIG_PATH = Path(__file__).with_name("tier_sets.generated.json")
_TRAILING_COMMA_RE = re.compile(r",(\s*[\]}])")


class TierSetConfigError(Exception):
    """Raised when tier_sets.generated.json can't be parsed or doesn't have the expected shape."""


def _looks_like_trailing_comma(text: str, pos: int) -> bool:
    left = pos
    while left > 0 and text[left - 1].isspace():
        left -= 1
    left_char = text[left - 1] if left > 0 else ""
    right = pos
    while right < len(text) and text[right].isspace():
        right += 1
    right_char = text[right] if right < len(text) else ""
    if left_char == "," and right_char in "]}":
        return True
    if pos < len(text) and text[pos] == ",":
        after_comma = pos + 1
        while after_comma < len(text) and text[after_comma].isspace():
            after_comma += 1
        after_comma_char = text[after_comma] if after_comma < len(text) else ""
        if after_comma_char in "]}":
            return True
    return False


def _describe_json_error(text: str, error: json.JSONDecodeError) -> str:
    lines = text.splitlines()
    line_no = error.lineno
    col_no = error.colno
    start = max(1, line_no - 2)
    end = min(len(lines), line_no + 2) if lines else line_no
    context: list[str] = []
    for n in range(start, end + 1):
        if n - 1 >= len(lines):
            continue
        marker = ">>" if n == line_no else "  "
        label = f"{marker} {n:>4} | "
        context.append(f"{label}{lines[n - 1]}")
        if n == line_no:
            context.append(" " * (len(label) + max(0, col_no - 1)) + "^")
    message_lower = (error.msg or "").lower()
    if "trailing comma" in message_lower or _looks_like_trailing_comma(text, error.pos):
        hint = "Likely cause: a trailing comma before a closing ] or } -- delete it, or run:\n  python tier_sets_cli.py repair"
    elif "expecting property name" in message_lower:
        hint = "Likely cause: a trailing comma before a closing } -- delete it, or run:\n  python tier_sets_cli.py repair"
    elif "expecting ',' delimiter" in message_lower:
        hint = "Likely cause: a comma is MISSING between two items."
    elif "expecting value" in message_lower:
        hint = "Likely cause: an empty spot where a value was expected -- check for a stray comma."
    else:
        hint = "Check the line pointed to above for a syntax mistake."
    return f"{error.msg} at line {line_no}, column {col_no}\n\n" + "\n".join(context) + "\n\n" + hint


def _load_raw_or_raise(config_path: Path) -> dict:
    text = config_path.read_text(encoding="utf-8")
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise TierSetConfigError(
            f"{config_path} is not valid JSON:\n\n"
            f"{_describe_json_error(text, exc)}\n\n"
            f"You can also check/fix it without touching a report:\n"
            f"  python tier_sets_cli.py validate\n"
            f"  python tier_sets_cli.py repair"
        ) from exc
    if not isinstance(raw, dict):
        raise TierSetConfigError(f"{config_path} does not contain a JSON object at the top level.")
    return raw


def backup_file(path: str | Path) -> Path | None:
    config_path = Path(path)
    if not config_path.exists():
        return None
    backup_path = config_path.with_suffix(config_path.suffix + ".bak")
    shutil.copy2(config_path, backup_path)
    return backup_path


def load_generated_tier_set_pieces(path: str | Path = DEFAULT_CONFIG_PATH) -> list[TierSetPieceDefinition]:
    config_path = Path(path)
    if not config_path.exists():
        return []
    raw = _load_raw_or_raise(config_path)
    return [
        TierSetPieceDefinition(
            class_name=item["class_name"], slot=int(item["slot"]),
            item_id=int(item["item_id"]), notes=item.get("notes"),
        )
        for item in raw.get("pieces", [])
    ]


def load_generated_track_breakpoints(path: str | Path = DEFAULT_CONFIG_PATH) -> list[TierTrackBreakpoint]:
    config_path = Path(path)
    if not config_path.exists():
        return []
    raw = _load_raw_or_raise(config_path)
    return [
        TierTrackBreakpoint(
            min_item_level=float(item["min_item_level"]), track_letter=item["track_letter"],
            track_name=item["track_name"], color_hex=item["color_hex"],
            max_item_level=(float(item["max_item_level"]) if item.get("max_item_level") is not None else None),
        )
        for item in raw.get("track_breakpoints", [])
    ]


def _piece_to_json(piece: TierSetPieceDefinition) -> dict:
    return {"class_name": piece.class_name, "slot": piece.slot, "item_id": piece.item_id, "notes": piece.notes}


def _breakpoint_to_json(bp: TierTrackBreakpoint) -> dict:
    return {
        "min_item_level": bp.min_item_level, "max_item_level": bp.max_item_level,
        "track_letter": bp.track_letter, "track_name": bp.track_name, "color_hex": bp.color_hex,
    }


def _write_json(config_path: Path, data: dict) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with config_path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def _read_existing(config_path: Path) -> dict:
    if config_path.exists():
        existing = _load_raw_or_raise(config_path)
        existing.setdefault("pieces", [])
        existing.setdefault("track_breakpoints", [])
        return existing
    return {"version": 1, "pieces": [], "track_breakpoints": []}


def save_tier_set_pieces(
    piece_updates: list[TierSetPieceDefinition] | None = None,
    path: str | Path = DEFAULT_CONFIG_PATH,
    verbose: bool = False,
) -> Path:
    piece_updates = piece_updates or []
    config_path = Path(path)
    existing = _read_existing(config_path)
    by_key: dict[tuple[str, int], dict] = {
        (item["class_name"], int(item["slot"])): item for item in existing["pieces"]
    }
    for piece in piece_updates:
        key = (piece.class_name, piece.slot)
        is_new = key not in by_key
        by_key[key] = _piece_to_json(piece)
        if verbose:
            verb = "Added" if is_new else "Updated"
            print(f"  {'+' if is_new else '~'} {verb} {piece.class_name} {piece.slot_name}: item_id={piece.item_id}")
    existing["pieces"] = sorted(by_key.values(), key=lambda item: (item["class_name"], item["slot"]))
    backup_file(config_path)
    _write_json(config_path, existing)
    return config_path


def remove_tier_set_piece(
    class_name: str, slot: int, path: str | Path = DEFAULT_CONFIG_PATH, verbose: bool = False
) -> bool:
    config_path = Path(path)
    if not config_path.exists():
        return False
    existing = _read_existing(config_path)
    remaining = [
        item for item in existing["pieces"]
        if not (item["class_name"] == class_name and int(item["slot"]) == slot)
    ]
    if len(remaining) == len(existing["pieces"]):
        return False
    if verbose:
        print(f"  - Removed tracked tier piece for {class_name}, slot {slot}")
    existing["pieces"] = remaining
    backup_file(config_path)
    _write_json(config_path, existing)
    return True


def save_track_breakpoints(
    breakpoints: list[TierTrackBreakpoint], path: str | Path = DEFAULT_CONFIG_PATH, verbose: bool = False
) -> Path:
    config_path = Path(path)
    existing = _read_existing(config_path)
    existing["track_breakpoints"] = [_breakpoint_to_json(bp) for bp in sorted(breakpoints, key=lambda b: b.min_item_level)]
    if verbose:
        print(f"  Track breakpoints replaced: {len(breakpoints)} track(s) saved.")
    backup_file(config_path)
    _write_json(config_path, existing)
    return config_path


def validate_file(path: str | Path = DEFAULT_CONFIG_PATH) -> tuple[bool, str]:
    config_path = Path(path)
    if not config_path.exists():
        return True, f"{config_path} does not exist yet -- nothing to validate."
    text = config_path.read_text(encoding="utf-8")
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        return False, f"{config_path} is NOT valid JSON:\n\n{_describe_json_error(text, exc)}"
    if not isinstance(raw, dict):
        return False, f"{config_path} is valid JSON but is not an object at the top level."
    piece_count = len(raw.get("pieces", []))
    breakpoint_count = len(raw.get("track_breakpoints", []))
    return True, (
        f"{config_path} is valid -- {piece_count} tracked tier piece(s), "
        f"{breakpoint_count} custom track breakpoint(s) (0 means using the built-in seed defaults)."
    )


def repair_trailing_commas(
    path: str | Path = DEFAULT_CONFIG_PATH, verbose: bool = False
) -> tuple[bool, str]:
    config_path = Path(path)
    if not config_path.exists():
        return True, f"{config_path} does not exist yet -- nothing to repair."
    text = config_path.read_text(encoding="utf-8")
    original_error: json.JSONDecodeError | None = None
    try:
        json.loads(text)
        return True, f"{config_path} is already valid JSON -- nothing to repair."
    except json.JSONDecodeError as exc:
        original_error = exc
    fixed_text = _TRAILING_COMMA_RE.sub(r"\1", text)
    if fixed_text == text:
        return False, f"No trailing comma found to auto-fix.\n\n{_describe_json_error(text, original_error)}"
    try:
        parsed = json.loads(fixed_text)
    except json.JSONDecodeError as still_broken:
        return False, (
            f"Removed a trailing comma, but the file still isn't valid JSON after "
            f"that -- there may be more than one problem.\n\n"
            f"{_describe_json_error(fixed_text, still_broken)}"
        )
    backup_path = backup_file(config_path)
    _write_json(config_path, parsed)
    if verbose:
        print(f"  - Removed a trailing comma from {config_path}")
    backup_note = f" A backup of the original was saved to {backup_path}." if backup_path else ""
    return True, f"Fixed {config_path}.{backup_note}"


SEED_TRACK_BREAKPOINTS: list[TierTrackBreakpoint] = [
    TierTrackBreakpoint(min_item_level=266, max_item_level=282, track_letter="A", track_name="Adventurer", color_hex="#ffffff"),
    TierTrackBreakpoint(min_item_level=279, max_item_level=295, track_letter="V", track_name="Veteran", color_hex="#1eff00"),
    TierTrackBreakpoint(min_item_level=292, max_item_level=308, track_letter="C", track_name="Champion", color_hex="#0070dd"),
    TierTrackBreakpoint(min_item_level=305, max_item_level=321, track_letter="H", track_name="Hero", color_hex="#a335ee"),
    TierTrackBreakpoint(min_item_level=318, max_item_level=None, track_letter="M", track_name="Myth", color_hex="#ff8000"),
]

TIER_SET_NAMES_BY_CLASS: dict[str, str] = {
    "Death Knight": "Baleful Grave-Knight's Crucible",
    "Demon Hunter": "Abyssal Doomhound's Pursuit",
    "Druid": "Bark of the Enigmatic Dreamwatcher",
    "Evoker": "Echo of Calamity",
    "Hunter": "Skulking Viper's Ambush",
    "Mage": "Primal Leywarden's Attire",
    "Monk": "Guile of the Monkey King",
    "Paladin": "Radiance of the Consecrated Flame",
    "Priest": "Cosmic Penitent's Raiment",
    "Rogue": "Chosen Bloodslayer's Hexweave",
    "Shaman": "Ophidian Oracle's Prophecy",
    "Warlock": "Damned Necrolyte's Shattered Restraints",
    "Warrior": "Jade Warlord's Dominion",
}

MANUAL_TIER_SET_PIECES: list[TierSetPieceDefinition] = [
]

try:
    _generated_pieces = load_generated_tier_set_pieces()
except TierSetConfigError as exc:
    print(
        "WARNING: tier_sets.generated.json could not be read -- ignoring it "
        "for this run (every player will show 0/5 tier pieces until it's fixed).\n"
        f"{exc}\n"
    )
    _generated_pieces = []

_pieces_by_key: dict[tuple[str, int], TierSetPieceDefinition] = {
    (p.class_name, p.slot): p for p in _generated_pieces
}
for _manual in MANUAL_TIER_SET_PIECES:
    _pieces_by_key[(_manual.class_name, _manual.slot)] = _manual

TRACKED_TIER_SET_PIECES: list[TierSetPieceDefinition] = sorted(
    _pieces_by_key.values(), key=lambda p: (p.class_name, p.slot)
)

try:
    _generated_breakpoints = load_generated_track_breakpoints()
except TierSetConfigError:
    _generated_breakpoints = []

TRACK_BREAKPOINTS: list[TierTrackBreakpoint] = (
    sorted(_generated_breakpoints, key=lambda b: b.min_item_level)
    if _generated_breakpoints
    else SEED_TRACK_BREAKPOINTS
)


def track_letter_for_item_level(item_level: float | None) -> str | None:
    if item_level is None:
        return None
    matching = [bp for bp in TRACK_BREAKPOINTS if bp.contains(item_level)]
    if not matching:
        return None
    lowest_match = min(matching, key=lambda bp: bp.min_item_level)
    return lowest_match.track_letter


def track_color_for_letter(track_letter: str) -> str:
    for bp in TRACK_BREAKPOINTS:
        if bp.track_letter == track_letter:
            return bp.color_hex
    return "#8a8d9c"


TOTAL_TIER_SLOTS = len(TIER_SET_SLOT_ORDER)


@dataclass
class PlayerTierSetReport:
    player_id: int | None
    player_name: str | None
    class_name: str | None
    has_data: bool = True
    track_letters_by_slot: dict[int, str] = field(default_factory=dict)

    @property
    def pieces_worn(self) -> int:
        return len(self.track_letters_by_slot)

    @property
    def track_string(self) -> str:
        return "".join(
            self.track_letters_by_slot.get(slot, MISSING_TIER_PIECE_CHAR)
            for slot in TIER_SET_SLOT_ORDER
        )


def _pieces_by_class_and_slot() -> dict[tuple[str, int], int]:
    return {(p.class_name, p.slot): p.item_id for p in TRACKED_TIER_SET_PIECES}


def analyze_tier_sets(parsed_fight) -> list[PlayerTierSetReport]:
    fight_roster = get_fight_roster(parsed_fight)
    pieces_by_key = _pieces_by_class_and_slot()
    reports: list[PlayerTierSetReport] = []

    for player_id, actor in fight_roster.items():
        class_name = getattr(actor, "subtype", None)
        snapshot = parsed_fight.combatant_info.get(player_id)
        if snapshot is None:
            reports.append(PlayerTierSetReport(
                player_id=player_id, player_name=actor.name, class_name=class_name, has_data=False,
            ))
            continue

        gear_by_slot = {g.slot: g for g in snapshot.gear}
        track_letters_by_slot: dict[int, str] = {}
        for slot in TIER_SET_SLOT_ORDER:
            gear_item = gear_by_slot.get(slot)
            if gear_item is None:
                continue
            tracked_item_id = pieces_by_key.get((class_name, slot))
            if tracked_item_id is None or gear_item.item_id != tracked_item_id:
                continue
            letter = track_letter_for_item_level(gear_item.item_level)
            if letter is not None:
                track_letters_by_slot[slot] = letter

        reports.append(PlayerTierSetReport(
            player_id=player_id, player_name=actor.name, class_name=class_name,
            track_letters_by_slot=track_letters_by_slot,
        ))

    return sorted(
        reports,
        key=lambda r: (not r.has_data, r.pieces_worn if r.has_data else 0, r.player_name or ""),
    )


def summarize_tier_sets(reports: list[PlayerTierSetReport]) -> str:
    if not reports:
        return "No tier-set data available (no CombatantInfo snapshots for this fight)."
    if not TRACKED_TIER_SET_PIECES:
        return (
            "Tier-set check: no tier pieces are configured yet -- run "
            "`python tier_sets_cli.py explore` and `python tier_sets_cli.py add` "
            "to start tracking your roster's tier sets."
        )
    lines = [f"Tier-set check ({TOTAL_TIER_SLOTS} slots: {', '.join(TIER_SET_SLOTS[s] for s in TIER_SET_SLOT_ORDER)}):"]
    for report in reports:
        name = (report.player_name or "Unknown")[:15]
        if not report.has_data:
            lines.append(f"  {name:<15} no gear data (no CombatantInfo snapshot)")
            continue
        lines.append(f"  {name:<15} {report.pieces_worn}/{TOTAL_TIER_SLOTS}  [{report.track_string}]")
    return "\n".join(lines)


@dataclass
class CandidateTierPiece:
    class_name: str
    slot: int
    item_id: int
    num_players_seen: int
    example_item_levels: list[int] = field(default_factory=list)

    @property
    def slot_name(self) -> str:
        return TIER_SET_SLOTS.get(self.slot, f"Slot {self.slot}")

    @property
    def likely_set_name(self) -> str | None:
        return TIER_SET_NAMES_BY_CLASS.get(self.class_name)


def discover_candidate_tier_pieces(parsed_fight, min_players_sharing: int = 2) -> list[CandidateTierPiece]:
    already_tracked = {(p.class_name, p.slot) for p in TRACKED_TIER_SET_PIECES}
    fight_roster = get_fight_roster(parsed_fight)

    seen: dict[tuple[str, int, int], list[int]] = {}
    for player_id, actor in fight_roster.items():
        class_name = getattr(actor, "subtype", None)
        if class_name is None:
            continue
        snapshot = parsed_fight.combatant_info.get(player_id)
        if snapshot is None:
            continue
        gear_by_slot = {g.slot: g for g in snapshot.gear}
        for slot in TIER_SET_SLOT_ORDER:
            gear_item = gear_by_slot.get(slot)
            if gear_item is None or not gear_item.item_id:
                continue
            key = (class_name, slot, gear_item.item_id)
            seen.setdefault(key, []).append(gear_item.item_level or 0)

    candidates: list[CandidateTierPiece] = []
    for (class_name, slot, item_id), item_levels in seen.items():
        if (class_name, slot) in already_tracked:
            continue
        if len(item_levels) < min_players_sharing:
            continue
        candidates.append(CandidateTierPiece(
            class_name=class_name, slot=slot, item_id=item_id,
            num_players_seen=len(item_levels), example_item_levels=sorted(set(item_levels)),
        ))

    return sorted(candidates, key=lambda c: (c.class_name, c.slot, -c.num_players_seen))


def format_candidates(candidates: list[CandidateTierPiece]) -> str:
    if not candidates:
        return "No tier-set candidates found in this fight (or everything found is already tracked)."
    lines = [f"Found {len(candidates)} candidate tier-set piece(s):"]
    for c in candidates:
        set_hint = f"  (likely: {c.likely_set_name})" if c.likely_set_name else ""
        lines.append(
            f"  {c.class_name:<14} {c.slot_name:<9} item_id={c.item_id:<10} "
            f"seen on {c.num_players_seen} player(s), ilvl {c.example_item_levels}{set_hint}"
        )
    lines.append("\nRun `python tier_sets_cli.py add` to confirm and start tracking any of these.")
    return "\n".join(lines)


# =======================================================================
# SECTION 6 -- CLI (explore/list/add/remove/set-breakpoint/validate/repair)
# (originally tier_sets_cli.py -- merged in so the whole tier-set system,
# engine + CLI, lives in one file.)
# =======================================================================

EVENT_TYPES_NEEDED = ["CombatantInfo"]

SLOT_NAME_TO_INDEX = {name.lower(): slot for slot, name in TIER_SET_SLOTS.items()}


def _resolve_slot(raw: str) -> int:
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
    if not TRACKED_TIER_SET_PIECES:
        print("No tier-set pieces tracked yet. Run `python tier_sets_cli.py explore <report_code>` to find candidates, then `add` them here.")
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
    path = save_tier_set_pieces([piece], verbose=True)
    print(f"\nSaved to {path}")


def cmd_remove(args) -> None:
    slot = _resolve_slot(args.slot)
    removed = remove_tier_set_piece(args.class_name, slot, verbose=True)
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
        new_max = existing.max_item_level
    elif args.max_ilvl == "":
        new_max = None
    else:
        new_max = float(args.max_ilvl)

    updated = TierTrackBreakpoint(
        min_item_level=new_min, max_item_level=new_max,
        track_letter=existing.track_letter, track_name=existing.track_name, color_hex=existing.color_hex,
    )
    current[args.track] = updated
    new_breakpoints = sorted(current.values(), key=lambda b: b.min_item_level)
    path = save_track_breakpoints(new_breakpoints, verbose=True)
    print(f"\n{updated.track_name} now covers ilvl {_format_range(updated)}. Saved to {path}")


def cmd_validate(_args) -> None:
    ok, message = validate_file()
    print(message)
    sys.exit(0 if ok else 1)


def cmd_repair(_args) -> None:
    ok, message = repair_trailing_commas(verbose=True)
    print(message)
    sys.exit(0 if ok else 1)


def cmd_explore(args) -> None:
    try:
        client_id, client_secret = get_credentials()
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

    fight_id = resolve_fight_id(filter_result.kept, args.fight_id, all_raw_fights=all_raw_fights)
    raw_fight = next(f for f in all_raw_fights if f["id"] == fight_id)

    try:
        raw_master_data = client.get_report_master_data(args.report_code)
        raw_events_by_type = client.get_report_events_multi(
            args.report_code, fight_id, event_types=EVENT_TYPES_NEEDED
        )
        parsed = parse_fight_bundle(raw_fight, raw_master_data, raw_events_by_type)
    except WCLAPIError as exc:
        print(f"Error fetching fight data: {exc}")
        sys.exit(1)

    print(f"\nScanning {parsed.fight.name} (fight {fight_id}) for tier-set candidates...\n")
    candidates = discover_candidate_tier_pieces(parsed, min_players_sharing=args.min_players)
    print(format_candidates(candidates))


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Explore, manage, and configure tracked tier-set pieces and track breakpoints (merged CLI)."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    explore_parser = subparsers.add_parser(
        "explore", help="Scan one fight's CombatantInfo for likely tier-set item candidates."
    )
    explore_parser.add_argument("report_code")
    explore_parser.add_argument("fight_id", nargs="?", type=int, default=None)
    explore_parser.add_argument(
        "--min-players", type=int, default=2, metavar="N",
        help="Minimum number of same-class players who must share an identical "
             "item_id in a tier slot before it's flagged as a candidate (default 2). "
             "Lower to 1 only for a single-player log, at the cost of more false positives.",
    )
    explore_parser.add_argument("--include-trash", action="store_true")
    explore_parser.add_argument("--min-duration", type=float, default=15.0)
    explore_parser.set_defaults(func=cmd_explore)

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
