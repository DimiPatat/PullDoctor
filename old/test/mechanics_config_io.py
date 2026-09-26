"""
mechanics_config_io.py

Reads and writes generated avoidable-mechanic selections as JSON and
converts them into the EncounterConfig objects used by
avoidable_damage_analyzer.py.

Handles hand-editing safety:
  - Broken JSON raises MechanicsConfigError with a clear line/column
    pointer and a plain-English hint.
  - Every write function makes a ".bak" backup BEFORE writing.
  - validate_file() and repair_trailing_commas() let you check/fix the
    file without needing a Warcraft Logs report.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from avoidable_damage_data import AvoidableMechanic, EncounterConfig

DEFAULT_CONFIG_PATH = Path(__file__).with_name("boss_mechanics.generated.json")

_TRAILING_COMMA_RE = re.compile(r",(\s*[\]}])")


class MechanicsConfigError(Exception):
    """Raised when boss_mechanics.generated.json can't be parsed or doesn't have the expected shape."""


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
        hint = (
            "Likely cause: a trailing comma before a closing ] or } -- delete it, or run:\n"
            "  python manage_boss_mechanics.py repair"
        )
    elif "expecting property name" in message_lower:
        hint = (
            "Likely cause: a trailing comma before a closing } -- delete it, or run:\n"
            "  python manage_boss_mechanics.py repair"
        )
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
        raise MechanicsConfigError(
            f"{config_path} is not valid JSON:\n\n"
            f"{_describe_json_error(text, exc)}\n\n"
            f"You can also check/fix it without touching a report:\n"
            f"  python manage_boss_mechanics.py validate\n"
            f"  python manage_boss_mechanics.py repair"
        ) from exc
    if not isinstance(raw, dict):
        raise MechanicsConfigError(f"{config_path} does not contain a JSON object at the top level.")
    return raw


def backup_file(path: str | Path) -> Path | None:
    config_path = Path(path)
    if not config_path.exists():
        return None
    backup_path = config_path.with_suffix(config_path.suffix + ".bak")
    shutil.copy2(config_path, backup_path)
    return backup_path


def load_generated_encounters(path: str | Path = DEFAULT_CONFIG_PATH) -> dict[int, EncounterConfig]:
    """Load generated encounter configurations. Missing file returns {}."""
    config_path = Path(path)
    if not config_path.exists():
        return {}

    raw = _load_raw_or_raise(config_path)

    encounters: dict[int, EncounterConfig] = {}
    for entry in raw.get("encounters", []):
        encounter_id = int(entry["encounter_id"])
        mechanics = [
            AvoidableMechanic(
                ability_name=item["ability_name"],
                category=item.get("category"),
                track_via=tuple(item.get("track_via") or ["damage"]),
                require_source_differs_from_target=bool(
                    item.get("require_source_differs_from_target", False)
                ),
                ability_ids=tuple(item.get("ability_ids") or []),
                damage_multiplier_threshold=item.get("damage_multiplier_threshold"),
                notes=item.get("notes"),
            )
            for item in entry.get("mechanics", [])
        ]
        encounters[encounter_id] = EncounterConfig(
            encounter_id=encounter_id,
            encounter_name=entry.get("encounter_name", f"Encounter {encounter_id}"),
            mechanics=mechanics,
        )
    return encounters


def _mechanic_to_json(mechanic: AvoidableMechanic) -> dict:
    return {
        "ability_name": mechanic.ability_name,
        "category": mechanic.category,
        "track_via": list(mechanic.track_via),
        "require_source_differs_from_target": mechanic.require_source_differs_from_target,
        "ability_ids": sorted(set(mechanic.ability_ids)),
        "damage_multiplier_threshold": mechanic.damage_multiplier_threshold,
        "notes": mechanic.notes,
    }


def _write_json(config_path: Path, data: dict) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with config_path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def save_encounter_selection(
    encounter_id: int,
    encounter_name: str,
    mechanics: list[AvoidableMechanic],
    path: str | Path = DEFAULT_CONFIG_PATH,
    replace: bool = False,
    verbose: bool = False,
) -> Path:
    """
    Save one encounter's avoidable-mechanic selections. Other
    encounters are ALWAYS preserved regardless of `replace`. Makes a
    .bak backup before writing.

    Default (replace=False) is a MERGE: existing mechanics not
    re-selected are kept untouched; re-selected ones get track_via/
    require_source_differs_from_target REPLACED; ability_ids UNIONED;
    category/notes/damage_multiplier_threshold only replaced if a
    non-blank value is given.
    """
    config_path = Path(path)
    existing = {"version": 1, "encounters": []}
    if config_path.exists():
        existing = _load_raw_or_raise(config_path)
        existing.setdefault("encounters", [])

    encounters = existing.setdefault("encounters", [])
    encounter_entry = None
    encounter_index = None
    for index, entry in enumerate(encounters):
        if int(entry.get("encounter_id", -1)) == encounter_id:
            encounter_entry = entry
            encounter_index = index
            break

    if replace or encounter_entry is None:
        if verbose and encounter_entry is not None:
            print(f"Replacing all previously stored mechanics for encounter {encounter_id}.")
        merged_by_name: dict[str, dict] = {}
    else:
        merged_by_name = {
            item["ability_name"]: dict(item) for item in encounter_entry.get("mechanics", [])
        }

    for mechanic in mechanics:
        new_json = _mechanic_to_json(mechanic)
        prior = merged_by_name.get(mechanic.ability_name)
        if prior is None:
            if verbose:
                print(f"  + Added new mechanic: {mechanic.ability_name}")
        else:
            prior_ids = set(prior.get("ability_ids", []))
            new_ids = set(new_json["ability_ids"])
            added_ids = sorted(new_ids - prior_ids)
            new_json["ability_ids"] = sorted(prior_ids | new_ids)
            if new_json["category"] is None:
                new_json["category"] = prior.get("category")
            if new_json["notes"] is None:
                new_json["notes"] = prior.get("notes")
            if new_json["damage_multiplier_threshold"] is None:
                new_json["damage_multiplier_threshold"] = prior.get("damage_multiplier_threshold")
            if verbose:
                if added_ids:
                    print(
                        f"  ~ {mechanic.ability_name}: updated, recorded new spell ID(s) "
                        f"{added_ids} (previously known: {sorted(prior_ids)})"
                    )
                else:
                    print(f"  ~ Updated existing mechanic: {mechanic.ability_name}")
        merged_by_name[mechanic.ability_name] = new_json

    replacement_entry = {
        "encounter_id": encounter_id,
        "encounter_name": encounter_name,
        "mechanics": list(merged_by_name.values()),
    }
    if encounter_index is not None:
        encounters[encounter_index] = replacement_entry
    else:
        encounters.append(replacement_entry)

    encounters.sort(key=lambda item: int(item["encounter_id"]))
    backup_file(config_path)
    _write_json(config_path, existing)
    return config_path


def remove_mechanics(
    encounter_id: int,
    ability_names: list[str],
    path: str | Path = DEFAULT_CONFIG_PATH,
    verbose: bool = False,
) -> list[str]:
    """Remove the given ability_names from one encounter's mechanic list. Makes a .bak backup BEFORE writing."""
    config_path = Path(path)
    if not config_path.exists():
        return []

    existing = _load_raw_or_raise(config_path)
    encounters = existing.get("encounters", [])
    target_names = set(ability_names)
    removed: list[str] = []

    for entry in encounters:
        if int(entry.get("encounter_id", -1)) != encounter_id:
            continue
        kept_mechanics = []
        for mechanic_json in entry.get("mechanics", []):
            if mechanic_json.get("ability_name") in target_names:
                removed.append(mechanic_json["ability_name"])
                if verbose:
                    print(f"  - Removed: {mechanic_json['ability_name']}")
            else:
                kept_mechanics.append(mechanic_json)
        entry["mechanics"] = kept_mechanics
        break

    if removed:
        backup_file(config_path)
        _write_json(config_path, existing)

    return removed


def remove_encounter(
    encounter_id: int,
    path: str | Path = DEFAULT_CONFIG_PATH,
    verbose: bool = False,
) -> bool:
    """Delete an entire encounter entry. Makes a .bak backup BEFORE writing."""
    config_path = Path(path)
    if not config_path.exists():
        return False

    existing = _load_raw_or_raise(config_path)
    encounters = existing.get("encounters", [])
    remaining = [e for e in encounters if int(e.get("encounter_id", -1)) != encounter_id]

    if len(remaining) == len(encounters):
        return False

    if verbose:
        removed_entry = next(e for e in encounters if int(e.get("encounter_id", -1)) == encounter_id)
        print(
            f"  - Removed encounter {encounter_id} ({removed_entry.get('encounter_name')}) "
            f"and its {len(removed_entry.get('mechanics', []))} mechanic(s)."
        )

    backup_file(config_path)
    existing["encounters"] = remaining
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

    if not isinstance(raw, dict) or "encounters" not in raw:
        return False, f"{config_path} is valid JSON but is missing the expected 'encounters' list."

    count = len(raw.get("encounters", []))
    return True, f"{config_path} is valid -- {count} encounter(s) found."


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
