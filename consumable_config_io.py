"""consumable_config_io.py -- reads/writes consumables.generated.json (global list, category mandatory flags)."""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from consumable_schema import ConsumableDefinition

DEFAULT_CONFIG_PATH = Path(__file__).with_name("consumables.generated.json")
_TRAILING_COMMA_RE = re.compile(r",(\s*[\]}])")


class ConsumablesConfigError(Exception):
    """Raised when consumables.generated.json can't be parsed or doesn't have the expected shape."""


def _describe_json_error(text: str, error: json.JSONDecodeError) -> str:
    lines = text.splitlines()
    line_no, col_no = error.lineno, error.colno
    start = max(1, line_no - 2)
    end = min(len(lines), line_no + 2) if lines else line_no
    context = []
    for n in range(start, end + 1):
        if n - 1 >= len(lines):
            continue
        marker = ">>" if n == line_no else "  "
        label = f"{marker} {n:>4} | "
        context.append(f"{label}{lines[n - 1]}")
        if n == line_no:
            context.append(" " * (len(label) + max(0, col_no - 1)) + "^")
    return f"{error.msg} at line {line_no}, column {col_no}\n\n" + "\n".join(context)


def _load_raw_or_raise(config_path: Path) -> dict:
    text = config_path.read_text(encoding="utf-8")
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConsumablesConfigError(f"{config_path} is not valid JSON:\n\n{_describe_json_error(text, exc)}") from exc
    if not isinstance(raw, dict):
        raise ConsumablesConfigError(f"{config_path} does not contain a JSON object at the top level.")
    return raw


def backup_file(path: str | Path) -> Path | None:
    config_path = Path(path)
    if not config_path.exists():
        return None
    backup_path = config_path.with_suffix(config_path.suffix + ".bak")
    shutil.copy2(config_path, backup_path)
    return backup_path


def load_generated_consumables(
    path: str | Path = DEFAULT_CONFIG_PATH,
) -> tuple[list[ConsumableDefinition], dict[str, bool]]:
    config_path = Path(path)
    if not config_path.exists():
        return [], {}
    raw = _load_raw_or_raise(config_path)
    categories_raw = raw.get("categories", {})
    category_mandatory = {name: bool(info.get("mandatory", True)) for name, info in categories_raw.items()}
    consumables = [
        ConsumableDefinition(
            name=item["name"], category=item["category"], ability_ids=tuple(item.get("ability_ids") or []),
            notes=item.get("notes"), optional=bool(item.get("optional", False)),
            detection_via_weapon_enchant=bool(item.get("detection_via_weapon_enchant", False)),
            loose_weapon_enchant_match=bool(item.get("loose_weapon_enchant_match", True)),
        )
        for item in raw.get("consumables", [])
    ]
    return consumables, category_mandatory


def _consumable_to_json(consumable: ConsumableDefinition) -> dict:
    return {
        "name": consumable.name, "category": consumable.category,
        "ability_ids": sorted(set(consumable.ability_ids)), "notes": consumable.notes, "optional": consumable.optional,
        "detection_via_weapon_enchant": consumable.detection_via_weapon_enchant,
        "loose_weapon_enchant_match": consumable.loose_weapon_enchant_match,
    }


def _write_json(config_path: Path, data: dict) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with config_path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def save_consumable_selection(
    consumables: list[ConsumableDefinition],
    category_mandatory_updates: dict[str, bool] | None = None,
    path: str | Path = DEFAULT_CONFIG_PATH,
    replace: bool = False,
    verbose: bool = False,
) -> Path:
    category_mandatory_updates = category_mandatory_updates or {}
    config_path = Path(path)
    existing = {"version": 1, "categories": {}, "consumables": []}
    if config_path.exists():
        existing = _load_raw_or_raise(config_path)
        existing.setdefault("categories", {})
        existing.setdefault("consumables", [])

    if replace:
        merged_by_name: dict[str, dict] = {}
        categories: dict[str, dict] = {}
    else:
        merged_by_name = {item["name"]: dict(item) for item in existing["consumables"]}
        categories = dict(existing["categories"])

    for consumable in consumables:
        new_json = _consumable_to_json(consumable)
        prior = merged_by_name.get(consumable.name)
        if prior is not None:
            new_json["ability_ids"] = sorted(set(prior.get("ability_ids", [])) | set(new_json["ability_ids"]))
            if new_json["notes"] is None:
                new_json["notes"] = prior.get("notes")
            new_json["detection_via_weapon_enchant"] = (
                new_json["detection_via_weapon_enchant"] or prior.get("detection_via_weapon_enchant", False)
            )
        merged_by_name[consumable.name] = new_json

    for category, mandatory in category_mandatory_updates.items():
        categories[category] = {"mandatory": bool(mandatory)}

    used_categories = {item["category"] for item in merged_by_name.values()}
    for category in used_categories:
        categories.setdefault(category, {"mandatory": True})

    existing["categories"] = categories
    existing["consumables"] = sorted(merged_by_name.values(), key=lambda item: (item["category"], item["name"]))
    backup_file(config_path)
    _write_json(config_path, existing)
    return config_path


def set_weapon_enchant_detection(
    names: list[str], path: str | Path = DEFAULT_CONFIG_PATH, verbose: bool = False,
) -> list[str]:
    """Flip detection_via_weapon_enchant=True (and loose_weapon_enchant_match=True) on already-tracked entries by name."""
    config_path = Path(path)
    if not config_path.exists():
        return []
    existing = _load_raw_or_raise(config_path)
    consumables = existing.get("consumables", [])
    target_names = set(names)
    updated: list[str] = []
    for item in consumables:
        if item.get("name") in target_names:
            changed = False
            if not item.get("detection_via_weapon_enchant", False):
                item["detection_via_weapon_enchant"] = True
                changed = True
            if not item.get("loose_weapon_enchant_match", True):
                item["loose_weapon_enchant_match"] = True
                changed = True
            if changed:
                updated.append(item["name"])
                if verbose:
                    print(f"  ~ Enabled loose weapon-enchant detection for: {item['name']}")
    if updated:
        existing["consumables"] = consumables
        backup_file(config_path)
        _write_json(config_path, existing)
    return updated


def remove_consumables(names: list[str], path: str | Path = DEFAULT_CONFIG_PATH, verbose: bool = False) -> list[str]:
    config_path = Path(path)
    if not config_path.exists():
        return []
    existing = _load_raw_or_raise(config_path)
    target_names = set(names)
    kept, removed = [], []
    for item in existing.get("consumables", []):
        if item.get("name") in target_names:
            removed.append(item["name"])
        else:
            kept.append(item)
    if removed:
        existing["consumables"] = kept
        backup_file(config_path)
        _write_json(config_path, existing)
    return removed


def rename_consumable(
    old_name: str, new_name: str, path: str | Path = DEFAULT_CONFIG_PATH, verbose: bool = False,
) -> bool:
    config_path = Path(path)
    if not config_path.exists():
        return False
    existing = _load_raw_or_raise(config_path)
    consumables = existing.get("consumables", [])
    old_entry = next((c for c in consumables if c.get("name") == old_name), None)
    if old_entry is None:
        return False
    existing_new_entry = next((c for c in consumables if c.get("name") == new_name), None)
    renamed_entry = dict(old_entry)
    renamed_entry["name"] = new_name
    if existing_new_entry is not None:
        merged_ids = sorted(set(existing_new_entry.get("ability_ids", [])) | set(old_entry.get("ability_ids", [])))
        renamed_entry["ability_ids"] = merged_ids
        if not renamed_entry.get("notes"):
            renamed_entry["notes"] = existing_new_entry.get("notes")
    remaining = [c for c in consumables if c.get("name") not in (old_name, new_name)]
    remaining.append(renamed_entry)
    existing["consumables"] = sorted(remaining, key=lambda item: (item["category"], item["name"]))
    if verbose:
        print(f"  ~ Renamed: {old_name!r} -> {new_name!r}")
    backup_file(config_path)
    _write_json(config_path, existing)
    return True


def remove_category(category: str, path: str | Path = DEFAULT_CONFIG_PATH, verbose: bool = False) -> bool:
    config_path = Path(path)
    if not config_path.exists():
        return False
    existing = _load_raw_or_raise(config_path)
    consumables = existing.get("consumables", [])
    categories = existing.get("categories", {})
    category_existed = category in categories or any(c.get("category") == category for c in consumables)
    if not category_existed:
        return False
    remaining = [c for c in consumables if c.get("category") != category]
    existing["consumables"] = remaining
    categories.pop(category, None)
    existing["categories"] = categories
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
    if not isinstance(raw, dict) or "consumables" not in raw:
        return False, f"{config_path} is valid JSON but is missing the expected 'consumables' list."
    count = len(raw.get("consumables", []))
    category_count = len(raw.get("categories", {}))
    return True, f"{config_path} is valid -- {count} consumable(s) in {category_count} categor(y/ies)."


def repair_trailing_commas(path: str | Path = DEFAULT_CONFIG_PATH, verbose: bool = False) -> tuple[bool, str]:
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
        return False, f"Still not valid.\n\n{_describe_json_error(fixed_text, still_broken)}"
    backup_path = backup_file(config_path)
    _write_json(config_path, parsed)
    backup_note = f" A backup was saved to {backup_path}." if backup_path else ""
    return True, f"Fixed {config_path}.{backup_note}"
