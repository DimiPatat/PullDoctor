"""
gear.py

MERGED MODULE -- combines what used to be six separate files into one,
as part of the PullDoctor file-count reduction pass:
    - gear_schema.py               (EnchantRequirement / GearRequirements dataclasses)
    - gear_config_io.py            (load/save/validate/repair .generated.json)
    - gear_requirements_config.py  (MANUAL_* overrides + merge -> GEAR_REQUIREMENTS)
    - gear_analyzer.py             (raw per-player gear FACTS: ilvl, quality, enchants, gems)
    - gear_compliance_analyzer.py  (turns those facts into pass/fail COMPLIANCE)
    - gear_explorer.py             (roster-wide gear distribution, for building a profile)

This mirrors the exact same merge pattern already used for
defensive_cooldowns.py, raid_cooldowns.py, consumables.py, and
boss_mechanics.py: schema -> config I/O -> tracked/merged data ->
analyzer -> compliance layer -> explorer, all in one file, in that
dependency order.

This is a single GLOBAL profile (one JSON object), unlike raid/
defensive cooldowns (a flat list) or boss mechanics (keyed by
encounter_id) -- gear requirements apply to the whole tier, not a
specific pull or boss.

IMPORTANT LIMITATION (carried over from gear_analyzer.py): this app
has no item database, so enchants and gems are reported as
PRESENT/MISSING plus their raw IDs -- never by name. Quality is WCL's
own numeric rarity tier. Scope is ENCHANTS, GEMS, ITEM LEVEL, and
QUALITY TIER -- not specific items/trinkets/set bonuses/tier pieces
(see tier_sets.py for that).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from core import ParsedFight, get_fight_roster, parse_fight_bundle, parse_selection, resolve_fight_id
from wcl import WCLClient, WCLAPIError, get_credentials

ENCHANTABLE_SLOTS = {
    0: "Head", 2: "Shoulder", 4: "Chest", 6: "Legs", 7: "Feet",
    10: "Ring 1", 11: "Ring 2", 15: "Main Hand",
}
OFF_HAND_SLOT = 16
NON_ARMOR_SLOTS = {3, 17}  # Shirt, Ranged/Relic -- excluded from ilvl/quality averages

QUALITY_NAMES = {
    0: "Poor", 1: "Common", 2: "Uncommon", 3: "Rare",
    4: "Epic", 5: "Legendary", 6: "Artifact", 7: "Heirloom",
}


@dataclass
class EnchantRequirement:
    slot: int
    slot_name: str
    required_enchant_ids: tuple[int, ...] = field(default_factory=tuple)
    skip_check: bool = False
    notes: str | None = None


@dataclass
class GearRequirements:
    min_item_level: float | None = None
    min_quality: int | None = None
    min_gem_count: int | None = None
    required_gem_ids: tuple[int, ...] = field(default_factory=tuple)
    enchant_requirements: list[EnchantRequirement] = field(default_factory=list)
    notes: str | None = None


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
        hint = "Likely cause: a trailing comma before a closing ] or } -- delete it, or run:\n  python gear_cli.py repair"
    elif "expecting property name" in message_lower:
        hint = "Likely cause: a trailing comma before a closing } -- delete it, or run:\n  python gear_cli.py repair"
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
            f"  python gear_cli.py validate\n"
            f"  python gear_cli.py repair"
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


MANUAL_ENCHANT_REQUIREMENTS: list[EnchantRequirement] = [
]

MANUAL_MIN_ITEM_LEVEL: float | None = None
MANUAL_MIN_QUALITY: int | None = None
MANUAL_MIN_GEM_COUNT: int | None = None

MANUAL_REQUIRED_GEM_IDS: tuple[int, ...] = (
)

MANUAL_NOTES: str | None = None

try:
    _generated_gear_requirements = load_generated_gear_requirements()
except GearConfigError as exc:
    print(
        "WARNING: gear_requirements.generated.json could not be read -- "
        "ignoring it for this run (gear-compliance results will show "
        "every player passing every check until it's fixed).\n"
        f"{exc}\n"
    )
    _generated_gear_requirements = GearRequirements()

_enchant_by_slot: dict[int, EnchantRequirement] = {
    r.slot: r for r in _generated_gear_requirements.enchant_requirements
}
for _manual in MANUAL_ENCHANT_REQUIREMENTS:
    _enchant_by_slot[_manual.slot] = _manual

GEAR_REQUIREMENTS = GearRequirements(
    min_item_level=MANUAL_MIN_ITEM_LEVEL if MANUAL_MIN_ITEM_LEVEL is not None else _generated_gear_requirements.min_item_level,
    min_quality=MANUAL_MIN_QUALITY if MANUAL_MIN_QUALITY is not None else _generated_gear_requirements.min_quality,
    min_gem_count=MANUAL_MIN_GEM_COUNT if MANUAL_MIN_GEM_COUNT is not None else _generated_gear_requirements.min_gem_count,
    required_gem_ids=tuple(sorted(set(_generated_gear_requirements.required_gem_ids) | set(MANUAL_REQUIRED_GEM_IDS))),
    enchant_requirements=sorted(_enchant_by_slot.values(), key=lambda r: r.slot),
    notes=MANUAL_NOTES if MANUAL_NOTES is not None else _generated_gear_requirements.notes,
)


def quality_name(quality: int) -> str:
    return QUALITY_NAMES.get(quality, f"Unknown({quality})")


@dataclass
class GearPieceReport:
    slot: int
    slot_name: str
    item_id: int
    quality: int
    item_level: int | None
    is_enchantable_slot: bool
    has_enchant: bool
    enchant_id: int | None
    gem_count: int
    gem_ids: list[int] = field(default_factory=list)

    @property
    def quality_name(self) -> str:
        return quality_name(self.quality)


@dataclass
class PlayerGearReport:
    player_id: int
    player_name: str
    pieces: list[GearPieceReport] = field(default_factory=list)
    has_data: bool = True

    @property
    def average_item_level(self) -> float:
        levels = [
            p.item_level for p in self.pieces
            if p.item_level is not None and p.slot not in NON_ARMOR_SLOTS and p.item_id != 0
        ]
        return sum(levels) / len(levels) if levels else 0.0

    @property
    def total_gems(self) -> int:
        return sum(p.gem_count for p in self.pieces)

    @property
    def missing_enchant_slots(self) -> list[str]:
        return [p.slot_name for p in self.pieces if p.is_enchantable_slot and not p.has_enchant]

    @property
    def lowest_quality(self) -> int | None:
        qualities = [p.quality for p in self.pieces if p.slot not in NON_ARMOR_SLOTS]
        return min(qualities) if qualities else None


def _build_gear_piece_report(gear_item) -> GearPieceReport:
    is_enchantable = gear_item.slot in ENCHANTABLE_SLOTS
    slot_name = ENCHANTABLE_SLOTS.get(gear_item.slot, "Off Hand" if gear_item.slot == OFF_HAND_SLOT else f"Slot {gear_item.slot}")
    return GearPieceReport(
        slot=gear_item.slot, slot_name=slot_name, item_id=gear_item.item_id, quality=gear_item.quality,
        item_level=gear_item.item_level, is_enchantable_slot=is_enchantable,
        has_enchant=gear_item.permanent_enchant_id is not None, enchant_id=gear_item.permanent_enchant_id,
        gem_count=len(gear_item.gem_ids), gem_ids=list(gear_item.gem_ids),
    )


def analyze_gear(parsed_fight) -> list[PlayerGearReport]:
    fight_roster = get_fight_roster(parsed_fight)
    reports = []
    for player_id, actor in fight_roster.items():
        snapshot = parsed_fight.combatant_info.get(player_id)
        if snapshot is None:
            reports.append(PlayerGearReport(player_id=player_id, player_name=actor.name, has_data=False))
            continue
        pieces = [_build_gear_piece_report(g) for g in snapshot.gear]
        reports.append(PlayerGearReport(player_id=player_id, player_name=actor.name, pieces=pieces))
    return sorted(
        reports,
        key=lambda r: (not r.has_data, r.average_item_level if r.has_data else 0.0, r.player_name or ""),
    )


def summarize_gear(reports: list[PlayerGearReport]) -> str:
    if not reports:
        return "No gear data available (no CombatantInfo snapshots for this fight)."
    lines = ["Gear check (sorted by item level, lowest first):"]
    for report in reports:
        name = (report.player_name or "Unknown")[:15]
        if not report.has_data:
            lines.append(f"  {name:<15} no gear data (no CombatantInfo snapshot)")
            continue
        missing = report.missing_enchant_slots
        lowest = report.lowest_quality
        lowest_str = f"{quality_name(lowest)}" if lowest is not None else "n/a"
        lines.append(
            f"  {name:<15} avg ilvl {report.average_item_level:>6.1f}  "
            f"lowest quality: {lowest_str:<10}  gems: {report.total_gems}"
        )
        if missing:
            lines.append(f"    missing enchants: {', '.join(missing)}")
    return "\n".join(lines)


@dataclass
class PlayerGearCompliance:
    player_id: int
    player_name: str
    has_data: bool = True
    enchant_pass_by_slot: dict[str, bool] = field(default_factory=dict)
    gem_pass: bool = True
    gem_quality_pass: bool = True
    item_level_pass: bool = True
    quality_pass: bool = True
    average_item_level: float = 0.0
    lowest_quality: int | None = None
    total_gems: int = 0
    wrong_quality_gem_ids: list[int] = field(default_factory=list)

    @property
    def failed_enchant_slots(self) -> list[str]:
        return [slot for slot, passed in self.enchant_pass_by_slot.items() if not passed]

    @property
    def fully_compliant(self) -> bool:
        return (
            self.has_data
            and not self.failed_enchant_slots
            and self.gem_pass
            and self.gem_quality_pass
            and self.item_level_pass
            and self.quality_pass
        )


def _check_enchant_slot(
    piece, requirement: EnchantRequirement | None
) -> bool:
    if piece is None:
        return False
    if not piece.has_enchant:
        return False
    if requirement is None or not requirement.required_enchant_ids:
        return True
    return piece.enchant_id in requirement.required_enchant_ids


def analyze_gear_compliance(
    parsed_fight: ParsedFight,
    requirements: GearRequirements,
) -> list[PlayerGearCompliance]:
    gear_reports = analyze_gear(parsed_fight)
    requirement_by_slot = {r.slot: r for r in requirements.enchant_requirements}
    results: list[PlayerGearCompliance] = []
    for report in gear_reports:
        if not report.has_data:
            results.append(PlayerGearCompliance(
                player_id=report.player_id, player_name=report.player_name, has_data=False,
            ))
            continue
        compliance = PlayerGearCompliance(
            player_id=report.player_id, player_name=report.player_name,
            average_item_level=report.average_item_level,
            lowest_quality=report.lowest_quality,
            total_gems=report.total_gems,
        )
        pieces_by_slot = {p.slot: p for p in report.pieces}
        for slot, slot_name in ENCHANTABLE_SLOTS.items():
            requirement = requirement_by_slot.get(slot)
            if requirement is not None and requirement.skip_check:
                continue
            piece = pieces_by_slot.get(slot)
            compliance.enchant_pass_by_slot[slot_name] = _check_enchant_slot(piece, requirement)
        if requirements.min_gem_count is not None:
            compliance.gem_pass = report.total_gems >= requirements.min_gem_count
        if requirements.required_gem_ids:
            all_gem_ids = [gem_id for piece in report.pieces for gem_id in piece.gem_ids]
            required_set = set(requirements.required_gem_ids)
            compliance.wrong_quality_gem_ids = sorted(
                {gem_id for gem_id in all_gem_ids if gem_id not in required_set}
            )
            compliance.gem_quality_pass = not compliance.wrong_quality_gem_ids
        if requirements.min_item_level is not None:
            compliance.item_level_pass = report.average_item_level >= requirements.min_item_level
        if requirements.min_quality is not None:
            compliance.quality_pass = (
                report.lowest_quality is not None and report.lowest_quality >= requirements.min_quality
            )
        results.append(compliance)
    return sorted(results, key=lambda c: c.player_name or "")


def summarize_gear_compliance(
    parsed_fight: ParsedFight,
    results: list[PlayerGearCompliance],
    requirements: GearRequirements,
) -> str:
    if not results:
        return f"{parsed_fight.fight.name}: no roster data available for gear compliance checks."
    has_any_configured_check = (
        requirements.enchant_requirements
        or requirements.min_item_level is not None
        or requirements.min_quality is not None
        or requirements.min_gem_count is not None
        or requirements.required_gem_ids
    )
    lines = [f"{parsed_fight.fight.name} -- gear compliance:"]
    for compliance in results:
        name = (compliance.player_name or "Unknown")[:15]
        if not compliance.has_data:
            lines.append(f"  {name:<15} no gear data (no CombatantInfo snapshot)")
            continue
        problems: list[str] = []
        if compliance.failed_enchant_slots:
            problems.append(f"missing/wrong enchant: {', '.join(compliance.failed_enchant_slots)}")
        if not compliance.gem_pass:
            problems.append(f"gems {compliance.total_gems} < required {requirements.min_gem_count}")
        if not compliance.gem_quality_pass:
            problems.append(f"wrong-quality gem(s): {', '.join(str(g) for g in compliance.wrong_quality_gem_ids)}")
        if not compliance.item_level_pass:
            problems.append(
                f"ilvl {compliance.average_item_level:.1f} < required {requirements.min_item_level}"
            )
        if not compliance.quality_pass:
            lowest_name = (
                quality_name(compliance.lowest_quality)
                if compliance.lowest_quality is not None else "n/a"
            )
            problems.append(f"quality {lowest_name} below required tier {requirements.min_quality}")
        if problems:
            lines.append(f"  {name:<15} FAIL -- {'; '.join(problems)}")
        else:
            lines.append(f"  {name:<15} PASS")
    if not has_any_configured_check:
        lines.append("")
        lines.append(
            "  (No gear compliance rules configured yet -- every player passes by default. "
            "Use `python gear_cli.py explore` to set enchant/item-level/quality/gem requirements.)"
        )
    elif requirements.required_gem_ids:
        lines.append("")
        lines.append(
            f"  (Gem quality whitelist: {len(requirements.required_gem_ids)} accepted ID(s) -- "
            f"any socketed gem outside this set fails the check.)"
        )
    if requirements.notes:
        lines.append("")
        lines.append(f"  Profile notes: {requirements.notes}")
    return "\n".join(lines)


@dataclass
class SlotEnchantDetection:
    slot: int
    slot_name: str
    player_names_by_enchant_id: dict[int | None, set[str]] = field(default_factory=dict)

    @property
    def distinct_enchant_ids(self) -> list[int]:
        return sorted(eid for eid in self.player_names_by_enchant_id if eid is not None)

    @property
    def players_missing_enchant(self) -> set[str]:
        return self.player_names_by_enchant_id.get(None, set())


@dataclass
class GearDistributionSummary:
    slot_detections: list[SlotEnchantDetection] = field(default_factory=list)
    gem_count_distribution: dict[int, int] = field(default_factory=dict)
    gem_id_player_names: dict[int, set[str]] = field(default_factory=dict)
    player_average_item_levels: list[float] = field(default_factory=list)
    player_lowest_qualities: list[int] = field(default_factory=list)
    players_without_gear_data: list[str] = field(default_factory=list)
    roster_size: int = 0


def analyze_gear_distribution(parsed_fight: ParsedFight) -> GearDistributionSummary:
    reports = analyze_gear(parsed_fight)
    slot_detections: dict[int, SlotEnchantDetection] = {
        slot: SlotEnchantDetection(slot=slot, slot_name=slot_name)
        for slot, slot_name in ENCHANTABLE_SLOTS.items()
    }
    summary = GearDistributionSummary(roster_size=len(reports))
    for report in reports:
        if not report.has_data:
            summary.players_without_gear_data.append(report.player_name)
            continue
        summary.player_average_item_levels.append(report.average_item_level)
        if report.lowest_quality is not None:
            summary.player_lowest_qualities.append(report.lowest_quality)
        gem_count = report.total_gems
        summary.gem_count_distribution[gem_count] = summary.gem_count_distribution.get(gem_count, 0) + 1
        for piece in report.pieces:
            for gem_id in piece.gem_ids:
                summary.gem_id_player_names.setdefault(gem_id, set()).add(report.player_name)
        pieces_by_slot = {p.slot: p for p in report.pieces}
        for slot, detection in slot_detections.items():
            piece = pieces_by_slot.get(slot)
            enchant_key = piece.enchant_id if (piece and piece.has_enchant) else None
            detection.player_names_by_enchant_id.setdefault(enchant_key, set()).add(report.player_name)
    summary.slot_detections = [slot_detections[slot] for slot in sorted(slot_detections)]
    return summary


def format_gear_distribution_table(summary: GearDistributionSummary) -> str:
    if summary.roster_size == 0:
        return "No players found in this fight's roster."
    lines = [f"Gear distribution across {summary.roster_size} player(s):\n"]
    for detection in summary.slot_detections:
        lines.append(f"{detection.slot_name}:")
        for enchant_id in detection.distinct_enchant_ids:
            players = sorted(detection.player_names_by_enchant_id[enchant_id])
            lines.append(f"  enchant {enchant_id}: {len(players)} player(s) -- {', '.join(players)}")
        missing = sorted(detection.players_missing_enchant)
        if missing:
            lines.append(f"  (no enchant): {len(missing)} player(s) -- {', '.join(missing)}")
        lines.append("")
    if summary.gem_count_distribution:
        lines.append("Gem count distribution:")
        for count in sorted(summary.gem_count_distribution):
            lines.append(f"  {count} gem(s): {summary.gem_count_distribution[count]} player(s)")
        lines.append("")
    if summary.gem_id_player_names:
        lines.append("Gem IDs observed (across all equipped pieces):")
        for gem_id in sorted(summary.gem_id_player_names):
            players = sorted(summary.gem_id_player_names[gem_id])
            lines.append(f"  gem {gem_id}: {len(players)} player(s) -- {', '.join(players)}")
        lines.append("")
    if summary.player_average_item_levels:
        levels = summary.player_average_item_levels
        lines.append(
            f"Average item level -- min: {min(levels):.1f}  max: {max(levels):.1f}  "
            f"roster avg: {sum(levels) / len(levels):.1f}"
        )
    if summary.player_lowest_qualities:
        qualities = summary.player_lowest_qualities
        lines.append(
            f"Lowest quality tier seen -- min: {quality_name(min(qualities))}  "
            f"({min(qualities)}-{max(qualities)} range across roster)"
        )
    if summary.players_without_gear_data:
        lines.append(f"\nNo gear data at all for: {', '.join(summary.players_without_gear_data)}")
    return "\n".join(lines)


# =======================================================================
# SECTION 7 -- CLI (explore/list/remove/validate/repair)
# (originally gear_cli.py -- merged in so the whole gear system,
# engine + CLI, lives in one file.)
# =======================================================================

_THRESHOLD_LABELS = {
    "min_item_level": "Minimum average item level",
    "min_quality": "Minimum quality tier",
    "min_gem_count": "Minimum gem count",
    "required_gem_ids": "Gem quality whitelist",
    "notes": "Profile notes",
}


def print_gear_requirements_detail(requirements: GearRequirements) -> list[tuple[str, str]]:
    ordered: list[tuple[str, str]] = []
    number = 1
    print("Global thresholds:")
    any_threshold_set = False
    for field_name, label in _THRESHOLD_LABELS.items():
        value = getattr(requirements, field_name)
        if not value:
            continue
        any_threshold_set = True
        if field_name == "min_quality":
            display_value = quality_name(value)
        elif field_name == "required_gem_ids":
            display_value = list(value)
        else:
            display_value = value
        print(f"  {number:>2}. {label}: {display_value}")
        ordered.append(("threshold", field_name))
        number += 1
    if not any_threshold_set:
        print("  (none configured)")
    print("\nPer-slot enchant rules:")
    if not requirements.enchant_requirements:
        print("  (none configured -- every enchantable slot uses the default 'any enchant counts' check)")
    else:
        for requirement in requirements.enchant_requirements:
            if requirement.skip_check:
                detail = "excluded from compliance checking"
            elif requirement.required_enchant_ids:
                detail = f"must be one of: {list(requirement.required_enchant_ids)}"
            else:
                detail = "any enchant counts (just must not be missing)"
            print(f"  {number:>2}. {requirement.slot_name}: {detail}")
            if requirement.notes:
                print(f"      notes: {requirement.notes}")
            ordered.append(("slot", str(requirement.slot)))
            number += 1
    return ordered


def _load_or_exit(path) -> GearRequirements:
    try:
        return load_generated_gear_requirements(path)
    except GearConfigError as exc:
        print(f"Could not read {path}:\n\n{exc}")
        raise SystemExit(1)


def interactive_remove_gear_setting(path, requirements: GearRequirements | None = None) -> bool:
    if requirements is None:
        try:
            requirements = load_generated_gear_requirements(path)
        except GearConfigError as exc:
            print(f"WARNING: could not read {path}:\n{exc}")
            return False
    ordered = print_gear_requirements_detail(requirements)
    if not ordered:
        print("\nNothing configured yet -- nothing to remove.")
        return False
    raw = input(
        "\nWhich number to clear/remove? Press Enter to cancel: "
    ).strip()
    if not raw:
        print("Cancelled -- nothing removed.")
        return False
    try:
        index = int(raw) - 1
    except ValueError:
        print(f"'{raw}' isn't a valid number.")
        return False
    if not (0 <= index < len(ordered)):
        print(f"'{raw}' is out of range.")
        return False
    kind, key = ordered[index]
    if kind == "threshold":
        label = _THRESHOLD_LABELS[key]
        confirm = input(
            f"\nThis clears '{label}'. A backup of {path} will be saved as {path}.bak "
            f"first. Proceed? [y/N]: "
        ).strip().lower()
        if confirm not in {"y", "yes"}:
            print("Cancelled -- nothing removed.")
            return False
        cleared = clear_threshold(key, path, verbose=True)
        if cleared:
            print(f"\nCleared {label}. Backup saved as: {path}.bak")
        else:
            print("\nNothing to clear (it wasn't set).")
        return cleared
    slot = int(key)
    requirement = next(r for r in requirements.enchant_requirements if r.slot == slot)
    confirm = input(
        f"\nThis removes the rule for slot '{requirement.slot_name}' (reverts to the "
        f"default 'any enchant counts' check). A backup will be saved as {path}.bak "
        f"first. Proceed? [y/N]: "
    ).strip().lower()
    if confirm not in {"y", "yes"}:
        print("Cancelled -- nothing removed.")
        return False
    removed = remove_enchant_requirement(slot, path, verbose=True)
    if removed:
        print(f"\nRemoved rule for '{requirement.slot_name}'. Backup saved as: {path}.bak")
    else:
        print("\nNothing matched -- file left untouched.")
    return removed


def cmd_list(args: argparse.Namespace) -> None:
    requirements = _load_or_exit(args.path)
    print_gear_requirements_detail(requirements)


def cmd_remove(args: argparse.Namespace) -> None:
    requirements = _load_or_exit(args.path)
    interactive_remove_gear_setting(args.path, requirements)


def cmd_validate(args: argparse.Namespace) -> None:
    ok, message = validate_file(args.path)
    print(message)
    if not ok:
        raise SystemExit(1)


def cmd_repair(args: argparse.Namespace) -> None:
    ok, message = repair_trailing_commas(args.path, verbose=True)
    print(message)
    if not ok:
        raise SystemExit(1)


def clear_screen() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def _pause() -> None:
    input("\nPress Enter to return to the menu...")


def fetch_and_analyze_gear(report_code: str | None, fight_id: int | None) -> dict | None:
    """The ONLY function in this section that talks to the Warcraft Logs API."""
    if not report_code:
        report_code = input("Warcraft Logs report code (the part of the URL after /reports/): ").strip()
        if not report_code:
            print("No report code given -- cancelled.")
            return None
    try:
        client_id, client_secret = get_credentials()
    except RuntimeError as exc:
        print(exc)
        return None
    client = WCLClient(client_id, client_secret)
    try:
        raw_report = client.get_report_fights(report_code)
    except WCLAPIError as exc:
        print(f"Error fetching report: {exc}")
        return None
    print(f"Report: {raw_report['title']}  ({raw_report['zone']['name']})")
    try:
        resolved_fight_id = resolve_fight_id(raw_report["fights"], fight_id)
    except SystemExit:
        print("No valid fight selected -- cancelled.")
        return None
    raw_fight = next(f for f in raw_report["fights"] if f["id"] == resolved_fight_id)
    try:
        raw_master_data = client.get_report_master_data(report_code)
        raw_events_by_type = client.get_report_events_multi(
            report_code, resolved_fight_id, event_types=["CombatantInfo"],
        )
    except WCLAPIError as exc:
        print(f"Error fetching fight data: {exc}")
        return None
    parsed = parse_fight_bundle(raw_fight, raw_master_data, raw_events_by_type)
    print(f"\nParsed: {parsed.fight.name}  ({'kill' if parsed.fight.kill else 'wipe'})")
    distribution = analyze_gear_distribution(parsed)
    return {"parsed": parsed, "distribution": distribution}


def _print_slot_menu(distribution) -> None:
    print("\nEnchantable slots (pick a number to configure a rule for that slot):")
    for index, detection in enumerate(distribution.slot_detections, start=1):
        ids_summary = ", ".join(
            f"{eid} ({len(detection.player_names_by_enchant_id[eid])}p)"
            for eid in detection.distinct_enchant_ids
        ) or "none seen"
        missing_count = len(detection.players_missing_enchant)
        missing_note = f", {missing_count} missing enchant" if missing_count else ""
        print(f"  {index}. {detection.slot_name:<10} -- detected: {ids_summary}{missing_note}")


def _prompt_enchant_requirement_for_slot(detection) -> EnchantRequirement:
    print(f"\n--- {detection.slot_name} ---")
    for enchant_id in detection.distinct_enchant_ids:
        players = sorted(detection.player_names_by_enchant_id[enchant_id])
        print(f"  enchant {enchant_id}: {len(players)} player(s) -- {', '.join(players)}")
    missing = sorted(detection.players_missing_enchant)
    if missing:
        print(f"  (no enchant): {len(missing)} player(s) -- {', '.join(missing)}")
    skip = input(
        f"  Exclude {detection.slot_name} from compliance checking entirely? [y/N]: "
    ).strip().lower()
    if skip in {"y", "yes"}:
        return EnchantRequirement(slot=detection.slot, slot_name=detection.slot_name, skip_check=True)
    raw_ids = input(
        "  Which enchant ID(s) should count as compliant for this slot? "
        "(comma-separated, e.g. 7452,7453; press Enter to accept ANY enchant): "
    ).strip()
    required_ids: tuple[int, ...] = ()
    if raw_ids:
        try:
            required_ids = tuple(int(part.strip()) for part in raw_ids.split(",") if part.strip())
        except ValueError:
            print("  Couldn't parse that as numbers -- accepting ANY enchant instead.")
            required_ids = ()
    notes = input(f"  Notes for {detection.slot_name!r} (optional): ").strip() or None
    return EnchantRequirement(
        slot=detection.slot, slot_name=detection.slot_name,
        required_enchant_ids=required_ids, notes=notes,
    )


def _prompt_thresholds(distribution) -> dict:
    updates: dict = {}
    if distribution.player_average_item_levels:
        levels = distribution.player_average_item_levels
        print(
            f"\nItem level across roster -- min: {min(levels):.1f}  max: {max(levels):.1f}  "
            f"avg: {sum(levels) / len(levels):.1f}"
        )
    raw = input("Minimum average item level to require? (press Enter to skip): ").strip()
    if raw:
        try:
            updates["min_item_level"] = float(raw)
        except ValueError:
            print("  Couldn't parse that as a number -- skipping.")
    if distribution.player_lowest_qualities:
        qualities = distribution.player_lowest_qualities
        names = ", ".join(sorted({quality_name(q) for q in qualities}))
        print(f"\nLowest quality tiers seen on roster: {names}")
    raw = input(
        "Minimum quality tier to require? (0=Poor .. 4=Epic .. 5=Legendary; press Enter to skip): "
    ).strip()
    if raw:
        try:
            updates["min_quality"] = int(raw)
        except ValueError:
            print("  Couldn't parse that as a number -- skipping.")
    if distribution.gem_count_distribution:
        counts = ", ".join(
            f"{count} gem(s)={num_players}p"
            for count, num_players in sorted(distribution.gem_count_distribution.items())
        )
        print(f"\nGem count distribution: {counts}")
    raw = input("Minimum total gem count to require? (press Enter to skip): ").strip()
    if raw:
        try:
            updates["min_gem_count"] = int(raw)
        except ValueError:
            print("  Couldn't parse that as a number -- skipping.")
    return updates


def _prompt_gem_id_whitelist(distribution) -> tuple[int, ...]:
    if distribution.gem_id_player_names:
        print("\nGem IDs observed across the roster (any equipped piece, any slot):")
        for gem_id in sorted(distribution.gem_id_player_names):
            players = sorted(distribution.gem_id_player_names[gem_id])
            print(f"  gem {gem_id}: {len(players)} player(s) -- {', '.join(players)}")
    else:
        print("\nNo gems detected in this pull.")
    raw = input(
        "Which gem ID(s) should count as the accepted quality (a whitelist -- any "
        "socketed gem outside this set fails)? (comma-separated, e.g. 1230459,1230460; "
        "press Enter to skip): "
    ).strip()
    if not raw:
        return ()
    try:
        return tuple(int(part.strip()) for part in raw.split(",") if part.strip())
    except ValueError:
        print("  Couldn't parse that as numbers -- skipping.")
        return ()


def _prompt_profile_notes() -> str | None:
    return input("\nAny notes for this whole gear compliance profile? (optional): ").strip() or None


def _do_add(session: dict, export_path, replace: bool) -> bool:
    distribution = session["distribution"]
    if not distribution.slot_detections and not distribution.player_average_item_levels:
        print("\nNo gear data found for this fight.")
        return False
    _print_slot_menu(distribution)
    raw = input(
        "\nWhich slot number(s) do you want to configure (e.g. 1,3-4)? "
        "Press Enter to skip enchant rules: "
    )
    enchant_updates: list[EnchantRequirement] = []
    try:
        indexes = parse_selection(raw, len(distribution.slot_detections))
    except ValueError as exc:
        print(f"Invalid selection: {exc}")
        indexes = []
    for index in indexes:
        detection = distribution.slot_detections[index]
        enchant_updates.append(_prompt_enchant_requirement_for_slot(detection))
    threshold_updates = _prompt_thresholds(distribution)
    gem_id_updates = _prompt_gem_id_whitelist(distribution)
    profile_notes = _prompt_profile_notes()
    if not enchant_updates and not threshold_updates and not gem_id_updates and not profile_notes:
        print("\nNothing configured -- nothing saved.")
        return False
    print()
    try:
        path = save_gear_requirements(
            enchant_requirement_updates=enchant_updates,
            min_item_level=threshold_updates.get("min_item_level"),
            min_quality=threshold_updates.get("min_quality"),
            min_gem_count=threshold_updates.get("min_gem_count"),
            required_gem_ids_updates=gem_id_updates,
            notes=profile_notes,
            path=export_path, replace=replace, verbose=True,
        )
    except GearConfigError as exc:
        print(
            f"Could not save -- {export_path} currently has a problem and wasn't "
            f"touched (nothing was overwritten):\n\n{exc}\n\n"
            f"Fix it, then try again:\n"
            f"  python gear_cli.py validate\n"
            f"  python gear_cli.py repair"
        )
        return False
    print(f"\nSaved to: {path}")
    return True


def _do_remove_menu(path) -> None:
    interactive_remove_gear_setting(path)


def _do_list_menu(path) -> None:
    try:
        requirements = load_generated_gear_requirements(path)
    except GearConfigError as exc:
        print(f"WARNING: could not read {path}:\n{exc}")
        return
    print_gear_requirements_detail(requirements)


def run_interactive_session(args: argparse.Namespace) -> None:
    session: dict = {"parsed": None, "distribution": None}
    replace_pending = args.replace
    while True:
        clear_screen()
        print("PullDoctor -- Gear Compliance Explorer\n")
        print(
            "What would you like to do?\n"
            "  [a] Add / adjust gear compliance rules\n"
            "  [r] Remove a rule or reset a threshold\n"
            "  [l] List the current compliance profile\n"
            "  [d] Done"
        )
        choice = input("> ").strip().lower()
        if choice in {"a", "add"}:
            if session["parsed"] is None:
                result = fetch_and_analyze_gear(args.report_code, args.fight_id)
                if result is None:
                    _pause()
                    continue
                session.update(result)
            saved = _do_add(session, args.export, replace_pending)
            if saved:
                replace_pending = False
            _pause()
        elif choice in {"r", "remove"}:
            print()
            _do_remove_menu(args.export)
            _pause()
        elif choice in {"l", "list"}:
            print()
            _do_list_menu(args.export)
            _pause()
        elif choice in {"d", "done", "q", "quit"}:
            print("\nDone. gear.py will load any saved changes automatically.")
            return
        else:
            print("Enter a, r, l, or d.")
            _pause()


def cmd_explore(args: argparse.Namespace) -> None:
    run_interactive_session(args)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Explore and manage gear compliance rules (merged CLI)."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    explore_parser = subparsers.add_parser(
        "explore", help="Interactive menu: add/adjust rules (via a report), remove, or list."
    )
    explore_parser.add_argument(
        "report_code", nargs="?", default=None,
        help="Warcraft Logs report code -- only needed for 'add'; prompted for if omitted.",
    )
    explore_parser.add_argument(
        "fight_id", nargs="?", type=int, default=None,
        help="Optional fight ID -- only needed for 'add'; prompted for if omitted.",
    )
    explore_parser.add_argument(
        "--export", default=str(DEFAULT_CONFIG_PATH),
        help="Generated JSON config path (default: gear_requirements.generated.json)",
    )
    explore_parser.add_argument(
        "--replace", action="store_true",
        help="Wipe the ENTIRE gear compliance profile on the FIRST successful 'add' this session.",
    )
    explore_parser.set_defaults(func=cmd_explore)

    list_parser = subparsers.add_parser("list", help="Show the current gear compliance profile")
    list_parser.add_argument("--path", default=str(DEFAULT_CONFIG_PATH))
    list_parser.set_defaults(func=cmd_list)

    remove_parser = subparsers.add_parser("remove", help="Clear a threshold or remove a per-slot rule")
    remove_parser.add_argument("--path", default=str(DEFAULT_CONFIG_PATH))
    remove_parser.set_defaults(func=cmd_remove)

    validate_parser = subparsers.add_parser("validate", help="Check the JSON file for syntax errors")
    validate_parser.add_argument("--path", default=str(DEFAULT_CONFIG_PATH))
    validate_parser.set_defaults(func=cmd_validate)

    repair_parser = subparsers.add_parser(
        "repair", help="Attempt to auto-fix a common syntax error (a trailing comma)"
    )
    repair_parser.add_argument("--path", default=str(DEFAULT_CONFIG_PATH))
    repair_parser.set_defaults(func=cmd_repair)

    return parser


def main() -> None:
    args = build_argument_parser().parse_args()
    if args.command == "explore":
        cmd_explore(args)
    else:
        args.func(args)


if __name__ == "__main__":
    main()
