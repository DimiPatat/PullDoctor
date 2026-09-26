"""
tier_set_config_io.py
Reads/writes the generated tier-set tracking file
(tier_sets.generated.json), built via explore_tier_sets.py /
manage_tier_sets.py. Self-contained. Mirrors gear_config_io.py's
structure (backup-before-write, JSON error diagnostics with a
trailing-comma-aware hint, validate/repair helpers).

CHANGED: track_breakpoints entries now also persist max_item_level
(see tier_set_schema.py's docstring for why this field was added --
short version: without it, an item at certain "crossing" item levels
between two tracks always got classified as the HIGHER track, which
is the bug this fix addresses). Reading an OLDER saved file that only
has min_item_level (no max_item_level key at all) still works fine --
it's treated as an unbounded-above range, same as before this change,
so nothing breaks for existing generated.json files.

JSON shape:
{
  "version": 1,
  "pieces": [
    {"class_name": "Paladin", "slot": 0, "item_id": 123456, "notes": null},
    {"class_name": "Paladin", "slot": 2, "item_id": 123457, "notes": null}
  ],
  "track_breakpoints": [
    {"min_item_level": 266, "max_item_level": 292, "track_letter": "A", "track_name": "Adventurer", "color_hex": "#ffffff"},
    {"min_item_level": 279, "max_item_level": 305, "track_letter": "V", "track_name": "Veteran",    "color_hex": "#1eff00"},
    {"min_item_level": 292, "max_item_level": 318, "track_letter": "C", "track_name": "Champion",  "color_hex": "#0070dd"},
    {"min_item_level": 305, "max_item_level": 321, "track_letter": "H", "track_name": "Hero",      "color_hex": "#a335ee"},
    {"min_item_level": 318, "max_item_level": null, "track_letter": "M", "track_name": "Myth",     "color_hex": "#ff8000"}
  ]
}
"""
from __future__ import annotations
import json
import re
import shutil
from pathlib import Path
from tier_set_schema import TierSetPieceDefinition, TierTrackBreakpoint

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
        hint = "Likely cause: a trailing comma before a closing ] or } -- delete it, or run:\n  python manage_tier_sets.py repair"
    elif "expecting property name" in message_lower:
        hint = "Likely cause: a trailing comma before a closing } -- delete it, or run:\n  python manage_tier_sets.py repair"
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
            f"  python manage_tier_sets.py validate\n"
            f"  python manage_tier_sets.py repair"
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
    """Load the tracked tier-set pieces. Missing file returns an empty list (a complete no-op)."""
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
    """
    Load custom track breakpoints, if any were saved. Missing file (or
    missing key) returns an empty list, letting the caller fall back to
    the verified SEED default in tier_set_data.py. max_item_level
    defaults to None (unbounded above) if that key is absent from an
    older saved entry -- see module docstring.
    """
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
    """
    Add/update tracked tier-set pieces. Keyed by (class_name, slot) --
    adding a new item_id for a class+slot that's already tracked
    REPLACES the old one. Makes a .bak backup before writing.
    """
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
    """Remove one class+slot's tracked tier piece entirely."""
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
    """
    REPLACE the entire track-breakpoint table with `breakpoints` (not a
    merge -- the five tracks always come as a complete, ordered set).
    """
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
