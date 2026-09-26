"""
target_label_config_io.py

Reads/writes the generated target-labeling config
(damage_targets.generated.json), built via explore_damage_targets.py /
manage_damage_targets.py. Self-contained.

Scoped PER ENCOUNTER, same shape as boss_mechanics.generated.json:

{
  "version": 1,
  "encounters": [
      {
          "encounter_id": 3492,
          "encounter_name": "Ula'tek",
          "labels": [
              {"target_name": "Ula'tek", "display_name": "Boss A",
               "category": "boss", "notes": null},
              {"target_name": "Zealous Aspirant", "display_name": "Priority Add A",
               "category": "priority_add", "notes": "Kill on sight"}
          ]
      }
  ]
}
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from target_label_schema import EncounterTargetLabels, TargetLabel

DEFAULT_CONFIG_PATH = Path(__file__).with_name("damage_targets.generated.json")

_TRAILING_COMMA_RE = re.compile(r",(\s*[\]}])")


class TargetLabelConfigError(Exception):
    """Raised when damage_targets.generated.json can't be parsed or doesn't have the expected shape."""


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
            "Likely cause: a trailing comma before a closing ] or } -- JSON "
            "(unlike Python) doesn't allow a comma right before the closing "
            "bracket. Delete that comma, or run:\n"
            "  python manage_damage_targets.py repair"
        )
    elif "expecting property name" in message_lower:
        hint = (
            "Likely cause: a trailing comma before a closing } -- delete it, or run:\n"
            "  python manage_damage_targets.py repair"
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
        raise TargetLabelConfigError(
            f"{config_path} is not valid JSON:\n\n"
            f"{_describe_json_error(text, exc)}\n\n"
            f"You can also check/fix it without touching a report:\n"
            f"  python manage_damage_targets.py validate\n"
            f"  python manage_damage_targets.py repair"
        ) from exc
    if not isinstance(raw, dict):
        raise TargetLabelConfigError(f"{config_path} does not contain a JSON object at the top level.")
    return raw


def backup_file(path: str | Path) -> Path | None:
    config_path = Path(path)
    if not config_path.exists():
        return None
    backup_path = config_path.with_suffix(config_path.suffix + ".bak")
    shutil.copy2(config_path, backup_path)
    return backup_path


def load_generated_target_labels(
    path: str | Path = DEFAULT_CONFIG_PATH,
) -> dict[int, EncounterTargetLabels]:
    """Load generated per-encounter target labels. Missing file returns {}."""
    config_path = Path(path)
    if not config_path.exists():
        return {}

    raw = _load_raw_or_raise(config_path)

    encounters: dict[int, EncounterTargetLabels] = {}
    for entry in raw.get("encounters", []):
        encounter_id = int(entry["encounter_id"])
        labels = [
            TargetLabel(
                target_name=item["target_name"],
                display_name=item["display_name"],
                category=item.get("category"),
                notes=item.get("notes"),
            )
            for item in entry.get("labels", [])
        ]
        encounters[encounter_id] = EncounterTargetLabels(
            encounter_id=encounter_id,
            encounter_name=entry.get("encounter_name", f"Encounter {encounter_id}"),
            labels=labels,
        )
    return encounters


def _label_to_json(label: TargetLabel) -> dict:
    return {
        "target_name": label.target_name,
        "display_name": label.display_name,
        "category": label.category,
        "notes": label.notes,
    }


def _write_json(config_path: Path, data: dict) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with config_path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def save_target_labels(
    encounter_id: int,
    encounter_name: str,
    labels: list[TargetLabel],
    path: str | Path = DEFAULT_CONFIG_PATH,
    replace: bool = False,
    verbose: bool = False,
) -> Path:
    """
    Save one encounter's target labels. Other encounters are ALWAYS
    preserved regardless of `replace`. Makes a .bak backup before
    writing.

    Default (replace=False) is a MERGE: a label whose target_name isn't
    already stored is added; one that IS stored has display_name
    REPLACED, category/notes REPLACED only if non-blank given.

    Pass replace=True to wipe this encounter's labels and replace them
    entirely with `labels`.
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
            print(f"Replacing all previously stored target labels for encounter {encounter_id}.")
        merged_by_name: dict[str, dict] = {}
    else:
        merged_by_name = {item["target_name"]: dict(item) for item in encounter_entry.get("labels", [])}

    for label in labels:
        new_json = _label_to_json(label)
        prior = merged_by_name.get(label.target_name)
        if prior is None:
            if verbose:
                print(f"  + Added new label: {label.target_name} -> {label.display_name}")
        else:
            if new_json["category"] is None:
                new_json["category"] = prior.get("category")
            if new_json["notes"] is None:
                new_json["notes"] = prior.get("notes")
            if verbose:
                print(f"  ~ Updated label: {label.target_name} -> {label.display_name}")
        merged_by_name[label.target_name] = new_json

    replacement_entry = {
        "encounter_id": encounter_id,
        "encounter_name": encounter_name,
        "labels": list(merged_by_name.values()),
    }
    if encounter_index is not None:
        encounters[encounter_index] = replacement_entry
    else:
        encounters.append(replacement_entry)

    encounters.sort(key=lambda item: int(item["encounter_id"]))
    backup_file(config_path)
    _write_json(config_path, existing)
    return config_path


def remove_labels(
    encounter_id: int,
    target_names: list[str],
    path: str | Path = DEFAULT_CONFIG_PATH,
    verbose: bool = False,
) -> list[str]:
    """Remove the given target_names' labels from one encounter. Makes a .bak backup BEFORE writing."""
    config_path = Path(path)
    if not config_path.exists():
        return []

    existing = _load_raw_or_raise(config_path)
    target_set = set(target_names)
    removed: list[str] = []

    for entry in existing.get("encounters", []):
        if int(entry.get("encounter_id", -1)) != encounter_id:
            continue
        kept = []
        for label_json in entry.get("labels", []):
            if label_json.get("target_name") in target_set:
                removed.append(label_json["target_name"])
                if verbose:
                    print(f"  - Removed label for: {label_json['target_name']}")
            else:
                kept.append(label_json)
        entry["labels"] = kept
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
    """Delete an entire encounter's label set. Makes a .bak backup BEFORE writing."""
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
        print(f"  - Removed encounter {encounter_id} ({removed_entry.get('encounter_name')}).")

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
