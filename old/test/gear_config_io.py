"""
gear_config_io.py

Reads/writes the generated gear compliance profile
(gear_requirements.generated.json), built via explore_gear.py /
manage_gear.py. Self-contained.

This is a single GLOBAL profile: one JSON object. JSON shape:

{
  "version": 1,
  "min_item_level": 320.0,
  "min_quality": 4,
  "min_gem_count": 1,
  "required_gem_ids": [1230459, 1230460, 1230458],
  "notes": "Optional free-text notes for the whole profile.",
  "enchant_requirements": [
    {"slot": 4, "slot_name": "Chest", "required_enchant_ids": [7452, 7453],
     "skip_check": false, "notes": "Either accepted"},
    {"slot": 11, "slot_name": "Ring 2", "required_enchant_ids": [],
     "skip_check": true, "notes": "Not enforced this tier"}
  ]
}
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from gear_schema import EnchantRequirement, GearRequirements

DEFAULT_CONFIG_PATH = Path(__file__).with_name("gear_requirements.generated.json")

_TRAILING_COMMA_RE = re.compile(r",(\s*[\]}])")


class GearConfigError(Exception):
    """Raised when gear_requirements.generated.json can't be parsed or doesn't have the expected shape."""


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
        hint = "Likely cause: a trailing comma before a closing ] or } -- delete it, or run:\n  python manage_gear.py repair"
    elif "expecting property name" in message_lower:
        hint = "Likely cause: a trailing comma before a closing } -- delete it, or run:\n  python manage_gear.py repair"
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
        raise GearConfigError(
            f"{config_path} is not valid JSON:\n\n"
            f"{_describe_json_error(text, exc)}\n\n"
            f"You can also check/fix it without touching a report:\n"
            f"  python manage_gear.py validate\n"
            f"  python manage_gear.py repair"
        ) from exc
    if not isinstance(raw, dict):
        raise GearConfigError(f"{config_path} does not contain a JSON object at the top level.")
    return raw


def backup_file(path: str | Path) -> Path | None:
    config_path = Path(path)
    if not config_path.exists():
        return None
    backup_path = config_path.with_suffix(config_path.suffix + ".bak")
    shutil.copy2(config_path, backup_path)
    return backup_path


def load_generated_gear_requirements(path: str | Path = DEFAULT_CONFIG_PATH) -> GearRequirements:
    """
    Load the gear compliance profile. Missing file returns a fresh,
    unconfigured GearRequirements() (a complete no-op).
    """
    config_path = Path(path)
    if not config_path.exists():
        return GearRequirements()

    raw = _load_raw_or_raise(config_path)

    enchant_requirements = [
        EnchantRequirement(
            slot=int(item["slot"]),
            slot_name=item.get("slot_name", f"Slot {item['slot']}"),
            required_enchant_ids=tuple(item.get("required_enchant_ids") or []),
            skip_check=bool(item.get("skip_check", False)),
            notes=item.get("notes"),
        )
        for item in raw.get("enchant_requirements", [])
    ]
    return GearRequirements(
        min_item_level=raw.get("min_item_level"),
        min_quality=raw.get("min_quality"),
        min_gem_count=raw.get("min_gem_count"),
        required_gem_ids=tuple(raw.get("required_gem_ids") or []),
        enchant_requirements=enchant_requirements,
        notes=raw.get("notes"),
    )


def _enchant_requirement_to_json(requirement: EnchantRequirement) -> dict:
    return {
        "slot": requirement.slot,
        "slot_name": requirement.slot_name,
        "required_enchant_ids": sorted(set(requirement.required_enchant_ids)),
        "skip_check": requirement.skip_check,
        "notes": requirement.notes,
    }


def _write_json(config_path: Path, data: dict) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with config_path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def save_gear_requirements(
    enchant_requirement_updates: list[EnchantRequirement] | None = None,
    min_item_level: float | None = None,
    min_quality: int | None = None,
    min_gem_count: int | None = None,
    required_gem_ids_updates: tuple[int, ...] | list[int] | None = None,
    notes: str | None = None,
    path: str | Path = DEFAULT_CONFIG_PATH,
    replace: bool = False,
    verbose: bool = False,
) -> Path:
    """
    Save gear compliance settings. Makes a .bak backup before writing.

    Default (replace=False) MERGES:
      - enchant_requirement_updates: for each given slot, required_enchant_ids
        UNIONED with the prior set; skip_check/notes REPLACED.
      - min_item_level / min_quality / min_gem_count: None means "didn't change it".
      - required_gem_ids_updates: UNIONED with whatever was already whitelisted.
      - notes: None means "didn't change it".

    replace=True wipes the ENTIRE profile.
    """
    enchant_requirement_updates = enchant_requirement_updates or []
    required_gem_ids_updates = set(required_gem_ids_updates or [])
    config_path = Path(path)
    existing = {
        "version": 1, "min_item_level": None, "min_quality": None,
        "min_gem_count": None, "required_gem_ids": [], "notes": None,
        "enchant_requirements": [],
    }
    if config_path.exists():
        existing = _load_raw_or_raise(config_path)
        existing.setdefault("enchant_requirements", [])
        existing.setdefault("required_gem_ids", [])

    if replace:
        if verbose and existing.get("enchant_requirements"):
            print("Replacing the entire gear compliance profile.")
        merged_by_slot: dict[int, dict] = {}
        existing["min_item_level"] = None
        existing["min_quality"] = None
        existing["min_gem_count"] = None
        existing["required_gem_ids"] = []
        existing["notes"] = None
    else:
        merged_by_slot = {int(item["slot"]): dict(item) for item in existing.get("enchant_requirements", [])}

    for requirement in enchant_requirement_updates:
        new_json = _enchant_requirement_to_json(requirement)
        prior = merged_by_slot.get(requirement.slot)
        if prior is None:
            if verbose:
                print(f"  + Added requirement for slot: {requirement.slot_name}")
        else:
            prior_ids = set(prior.get("required_enchant_ids", []))
            new_ids = set(new_json["required_enchant_ids"])
            new_json["required_enchant_ids"] = sorted(prior_ids | new_ids)
            if verbose:
                print(f"  ~ Updated requirement for slot: {requirement.slot_name}")
        merged_by_slot[requirement.slot] = new_json

    if min_item_level is not None:
        existing["min_item_level"] = min_item_level
        if verbose:
            print(f"  min_item_level set to {min_item_level}")
    if min_quality is not None:
        existing["min_quality"] = min_quality
        if verbose:
            print(f"  min_quality set to {min_quality}")
    if min_gem_count is not None:
        existing["min_gem_count"] = min_gem_count
        if verbose:
            print(f"  min_gem_count set to {min_gem_count}")
    if required_gem_ids_updates:
        prior_gem_ids = set(existing.get("required_gem_ids") or [])
        added = sorted(required_gem_ids_updates - prior_gem_ids)
        existing["required_gem_ids"] = sorted(prior_gem_ids | required_gem_ids_updates)
        if verbose and added:
            print(f"  required_gem_ids: added {added} (now {existing['required_gem_ids']})")
    if notes is not None:
        existing["notes"] = notes
        if verbose:
            print(f"  notes set to: {notes}")

    existing["enchant_requirements"] = sorted(merged_by_slot.values(), key=lambda item: item["slot"])
    backup_file(config_path)
    _write_json(config_path, existing)
    return config_path


def remove_enchant_requirement(
    slot: int, path: str | Path = DEFAULT_CONFIG_PATH, verbose: bool = False
) -> bool:
    """Remove one slot's enchant requirement entirely, reverting that slot to the default 'any enchant counts' fallback."""
    config_path = Path(path)
    if not config_path.exists():
        return False

    existing = _load_raw_or_raise(config_path)
    requirements = existing.get("enchant_requirements", [])
    remaining = [r for r in requirements if int(r.get("slot", -1)) != slot]
    if len(remaining) == len(requirements):
        return False

    if verbose:
        removed = next(r for r in requirements if int(r.get("slot", -1)) == slot)
        print(f"  - Removed requirement for slot: {removed.get('slot_name', slot)}")

    existing["enchant_requirements"] = remaining
    backup_file(config_path)
    _write_json(config_path, existing)
    return True


def clear_threshold(
    field_name: str, path: str | Path = DEFAULT_CONFIG_PATH, verbose: bool = False
) -> bool:
    """
    Reset one global setting back to its unconfigured default: None for
    min_item_level / min_quality / min_gem_count / notes, or an empty
    list for required_gem_ids.
    """
    valid_fields = {"min_item_level", "min_quality", "min_gem_count", "required_gem_ids", "notes"}
    if field_name not in valid_fields:
        raise ValueError(f"Unknown threshold field: {field_name!r}")

    config_path = Path(path)
    if not config_path.exists():
        return False

    existing = _load_raw_or_raise(config_path)
    current_value = existing.get(field_name)
    empty_default = [] if field_name == "required_gem_ids" else None
    if not current_value:
        return False

    if verbose:
        print(f"  - Cleared {field_name} (was {current_value})")
    existing[field_name] = empty_default
    backup_file(config_path)
    _write_json(config_path, existing)
    return True


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

    slot_count = len(raw.get("enchant_requirements", []))
    gem_id_count = len(raw.get("required_gem_ids", []))
    return True, (
        f"{config_path} is valid -- {slot_count} enchant slot rule(s), "
        f"{gem_id_count} whitelisted gem ID(s) configured."
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
