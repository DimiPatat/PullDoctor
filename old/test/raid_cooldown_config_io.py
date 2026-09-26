"""
raid_cooldown_config_io.py
Reads/writes the generated raid-cooldown tracking file
(raid_cooldowns.generated.json), built via explore_raid_cooldowns.py /
manage_raid_cooldowns.py. Self-contained. Mirrors
defensive_cooldown_config_io.py / tier_set_config_io.py's structure
(backup-before-write, JSON error diagnostics with a trailing-comma-
aware hint, validate/repair helpers).

JSON shape:
{
  "version": 1,
  "cooldowns": [
    {"ability_name": "Tranquility", "cooldown_seconds": 180, "category": "healing_cd",
     "class_name": "Druid", "notes": null},
    {"ability_name": "Bloodlust", "cooldown_seconds": 600, "category": "utility",
     "class_name": "Shaman", "notes": "Same family as Heroism/Time Warp/Ancient Hysteria/Fury of the Aspects -- track separately per exact name seen in your logs."}
  ]
}
"""
from __future__ import annotations
import json
import re
import shutil
from pathlib import Path
from raid_cooldown_schema import RaidCooldownDefinition

DEFAULT_CONFIG_PATH = Path(__file__).with_name("raid_cooldowns.generated.json")
_TRAILING_COMMA_RE = re.compile(r",(\s*[\]}])")


class RaidCooldownConfigError(Exception):
    """Raised when raid_cooldowns.generated.json can't be parsed or doesn't have the expected shape."""


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
        hint = "Likely cause: a trailing comma before a closing ] or } -- delete it, or run:\n  python manage_raid_cooldowns.py repair"
    elif "expecting property name" in message_lower:
        hint = "Likely cause: a trailing comma before a closing } -- delete it, or run:\n  python manage_raid_cooldowns.py repair"
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
        raise RaidCooldownConfigError(
            f"{config_path} is not valid JSON:\n\n"
            f"{_describe_json_error(text, exc)}\n\n"
            f"You can also check/fix it without touching a report:\n"
            f"  python manage_raid_cooldowns.py validate\n"
            f"  python manage_raid_cooldowns.py repair"
        ) from exc
    if not isinstance(raw, dict):
        raise RaidCooldownConfigError(f"{config_path} does not contain a JSON object at the top level.")
    return raw


def backup_file(path: str | Path) -> Path | None:
    config_path = Path(path)
    if not config_path.exists():
        return None
    backup_path = config_path.with_suffix(config_path.suffix + ".bak")
    shutil.copy2(config_path, backup_path)
    return backup_path


def load_generated_raid_cooldowns(path: str | Path = DEFAULT_CONFIG_PATH) -> list[RaidCooldownDefinition]:
    """Load tracked raid cooldowns. Missing file returns an empty list (a complete no-op)."""
    config_path = Path(path)
    if not config_path.exists():
        return []
    raw = _load_raw_or_raise(config_path)
    return [
        RaidCooldownDefinition(
            ability_name=item["ability_name"], cooldown_seconds=float(item["cooldown_seconds"]),
            category=item.get("category"), class_name=item.get("class_name"), notes=item.get("notes"),
        )
        for item in raw.get("cooldowns", [])
    ]


def _cooldown_to_json(cd: RaidCooldownDefinition) -> dict:
    return {
        "ability_name": cd.ability_name, "cooldown_seconds": cd.cooldown_seconds,
        "category": cd.category, "class_name": cd.class_name, "notes": cd.notes,
    }


def _write_json(config_path: Path, data: dict) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with config_path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def _read_existing(config_path: Path) -> dict:
    if config_path.exists():
        existing = _load_raw_or_raise(config_path)
        existing.setdefault("cooldowns", [])
        return existing
    return {"version": 1, "cooldowns": []}


def save_raid_cooldowns(
    cooldown_updates: list[RaidCooldownDefinition] | None = None,
    path: str | Path = DEFAULT_CONFIG_PATH,
    verbose: bool = False,
) -> Path:
    """
    Add/update tracked raid cooldowns. Keyed by ability_name (case-
    sensitive, exact match against what the log actually calls it) --
    adding an update for an ability_name that's already tracked REPLACES
    it entirely (a single ability only ever has one true cooldown/
    category, so there's nothing to merge/union the way gear enchant
    requirements or gem-ID whitelists do).
    Makes a .bak backup before writing.
    """
    cooldown_updates = cooldown_updates or []
    config_path = Path(path)
    existing = _read_existing(config_path)
    by_name: dict[str, dict] = {item["ability_name"]: item for item in existing["cooldowns"]}
    for cd in cooldown_updates:
        is_new = cd.ability_name not in by_name
        by_name[cd.ability_name] = _cooldown_to_json(cd)
        if verbose:
            verb = "Added" if is_new else "Updated"
            print(f"  {'+' if is_new else '~'} {verb} {cd.ability_name}: {cd.cooldown_seconds:.0f}s cooldown")
    existing["cooldowns"] = sorted(by_name.values(), key=lambda item: item["ability_name"])
    backup_file(config_path)
    _write_json(config_path, existing)
    return config_path


def remove_raid_cooldown(
    ability_name: str, path: str | Path = DEFAULT_CONFIG_PATH, verbose: bool = False
) -> bool:
    """Stop tracking one ability entirely."""
    config_path = Path(path)
    if not config_path.exists():
        return False
    existing = _read_existing(config_path)
    remaining = [item for item in existing["cooldowns"] if item["ability_name"] != ability_name]
    if len(remaining) == len(existing["cooldowns"]):
        return False
    if verbose:
        print(f"  - Removed {ability_name}")
    existing["cooldowns"] = remaining
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
    count = len(raw.get("cooldowns", []))
    return True, f"{config_path} is valid -- {count} tracked raid cooldown(s)."


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
