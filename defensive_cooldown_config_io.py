"""
defensive_cooldown_config_io.py

Reads/writes the generated defensive-cooldowns config
(defensive_cooldowns.generated.json), built via
explore_defensive_cooldowns.py / manage_defensive_cooldowns.py.

Tracked GLOBALLY: one flat list, not scoped per encounter_id -- a
Paladin's Divine Shield is the same tracked ability regardless of which
boss it was used against.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from defensive_cooldown_schema import DefensiveCooldownDefinition

DEFAULT_CONFIG_PATH = Path(__file__).with_name("defensive_cooldowns.generated.json")

_TRAILING_COMMA_RE = re.compile(r",(\s*[\]}])")


class DefensiveCooldownConfigError(Exception):
    """Raised when defensive_cooldowns.generated.json can't be parsed or doesn't have the expected shape."""


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
    return f"{error.msg} at line {line_no}, column {col_no}\n\n" + "\n".join(context)


def _load_raw_or_raise(config_path: Path) -> dict:
    text = config_path.read_text(encoding="utf-8")
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DefensiveCooldownConfigError(
            f"{config_path} is not valid JSON:\n\n{_describe_json_error(text, exc)}\n\n"
            f"You can also check/fix it without touching a report:\n"
            f"  python manage_defensive_cooldowns.py validate\n"
            f"  python manage_defensive_cooldowns.py repair"
        ) from exc
    if not isinstance(raw, dict):
        raise DefensiveCooldownConfigError(f"{config_path} does not contain a JSON object at the top level.")
    return raw


def backup_file(path: str | Path) -> Path | None:
    config_path = Path(path)
    if not config_path.exists():
        return None
    backup_path = config_path.with_suffix(config_path.suffix + ".bak")
    shutil.copy2(config_path, backup_path)
    return backup_path


def load_generated_defensive_cooldowns(
    path: str | Path = DEFAULT_CONFIG_PATH,
) -> list[DefensiveCooldownDefinition]:
    config_path = Path(path)
    if not config_path.exists():
        return []
    raw = _load_raw_or_raise(config_path)
    return [
        DefensiveCooldownDefinition(
            ability_name=item["ability_name"],
            cooldown_seconds=float(item["cooldown_seconds"]),
            category=item.get("category"),
            class_name=item.get("class_name"),
            ability_ids=tuple(item.get("ability_ids") or []),
            notes=item.get("notes"),
            mitigation_type=item.get("mitigation_type", "unmodeled"),
            damage_reduction_percent=item.get("damage_reduction_percent"),
            duration_seconds=item.get("duration_seconds"),
        )
        for item in raw.get("defensive_cooldowns", [])
    ]


def _definition_to_json(definition: DefensiveCooldownDefinition) -> dict:
    return {
        "ability_name": definition.ability_name,
        "cooldown_seconds": definition.cooldown_seconds,
        "category": definition.category,
        "class_name": definition.class_name,
        "ability_ids": sorted(set(definition.ability_ids)),
        "notes": definition.notes,
        "mitigation_type": definition.mitigation_type,
        "damage_reduction_percent": definition.damage_reduction_percent,
        "duration_seconds": definition.duration_seconds,
    }


def _write_json(config_path: Path, data: dict) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with config_path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def save_defensive_cooldown_selection(
    definitions: list[DefensiveCooldownDefinition],
    path: str | Path = DEFAULT_CONFIG_PATH,
    replace: bool = False,
    verbose: bool = False,
) -> Path:
    """
    Save defensive-cooldown selections (GLOBAL, not per-encounter).
    Makes a .bak backup before writing.

    Default (replace=False) MERGES: a definition whose ability_name
    isn't already stored is added; one that IS already stored gets its
    fields REPLACED by the newest explicit values given here (except
    ability_ids, which is UNIONED with whatever was already recorded).
    """
    config_path = Path(path)
    existing = {"version": 1, "defensive_cooldowns": []}
    if config_path.exists():
        existing = _load_raw_or_raise(config_path)
        existing.setdefault("defensive_cooldowns", [])

    if replace:
        merged_by_name: dict[str, dict] = {}
    else:
        merged_by_name = {item["ability_name"]: dict(item) for item in existing["defensive_cooldowns"]}

    for definition in definitions:
        new_json = _definition_to_json(definition)
        prior = merged_by_name.get(definition.ability_name)
        if prior is not None:
            prior_ids = set(prior.get("ability_ids", []))
            new_ids = set(new_json["ability_ids"])
            new_json["ability_ids"] = sorted(prior_ids | new_ids)
            if new_json["category"] is None:
                new_json["category"] = prior.get("category")
            if new_json["class_name"] is None:
                new_json["class_name"] = prior.get("class_name")
            if new_json["notes"] is None:
                new_json["notes"] = prior.get("notes")
            if verbose:
                print(f"  ~ Updated existing defensive cooldown: {definition.ability_name}")
        elif verbose:
            print(f"  + Added new defensive cooldown: {definition.ability_name} ({definition.cooldown_seconds}s)")
        merged_by_name[definition.ability_name] = new_json

    existing["defensive_cooldowns"] = sorted(merged_by_name.values(), key=lambda item: item["ability_name"])
    backup_file(config_path)
    _write_json(config_path, existing)
    return config_path


def remove_defensive_cooldowns(
    ability_names: list[str],
    path: str | Path = DEFAULT_CONFIG_PATH,
    verbose: bool = False,
) -> list[str]:
    config_path = Path(path)
    if not config_path.exists():
        return []
    existing = _load_raw_or_raise(config_path)
    target_names = set(ability_names)
    kept = []
    removed: list[str] = []
    for item in existing.get("defensive_cooldowns", []):
        if item.get("ability_name") in target_names:
            removed.append(item["ability_name"])
            if verbose:
                print(f"  - Removed: {item['ability_name']}")
        else:
            kept.append(item)
    if removed:
        existing["defensive_cooldowns"] = kept
        backup_file(config_path)
        _write_json(config_path, existing)
    return removed


def validate_file(path: str | Path = DEFAULT_CONFIG_PATH) -> tuple[bool, str]:
    config_path = Path(path)
    if not config_path.exists():
        return True, f"{config_path} does not exist yet -- nothing to validate."
    text = config_path.read_text(encoding="utf-8")
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        return False, f"{config_path} is NOT valid JSON:\n\n{_describe_json_error(text, exc)}"
    if not isinstance(raw, dict) or "defensive_cooldowns" not in raw:
        return False, f"{config_path} is valid JSON but is missing the expected 'defensive_cooldowns' list."
    count = len(raw.get("defensive_cooldowns", []))
    return True, f"{config_path} is valid -- {count} defensive cooldown(s) tracked."


def repair_trailing_commas(path: str | Path = DEFAULT_CONFIG_PATH, verbose: bool = False) -> tuple[bool, str]:
    config_path = Path(path)
    if not config_path.exists():
        return True, f"{config_path} does not exist yet -- nothing to repair."
    text = config_path.read_text(encoding="utf-8")
    try:
        json.loads(text)
        return True, f"{config_path} is already valid JSON -- nothing to repair."
    except json.JSONDecodeError as original_error:
        pass
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
    backup_note = f" A backup of the original was saved to {backup_path}." if backup_path else ""
    return True, f"Fixed {config_path}.{backup_note}"
