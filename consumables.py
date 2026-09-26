"""
consumables.py

MERGED MODULE -- combines what used to be five separate files into one,
as part of the PullDoctor file-count reduction pass:

    - consumable_schema.py      (ConsumableDefinition dataclass)
    - consumable_config_io.py   (load/save/validate/repair .generated.json)
    - consumable_data.py        (seed reference list + ALL_TRACKED_CONSUMABLES)
    - consumable_explorer.py    (detect known consumables actually used in a fight)
    - consumables_analyzer.py   (per-player usage tracking + Vantus Rune detection)

This mirrors the exact same merge pattern already used for
defensive_cooldowns.py and raid_cooldowns.py: schema -> config I/O ->
seed/tracked data -> explorer -> analyzer, all in one file, in that
dependency order.

VANTUS RUNE DETECTION -- a Vantus Rune's exact buff NAME changes per
raid tier (e.g. "Vantus Rune: Tides" in one raid, "Vantus Rune: Ula'tek"
in another) and sometimes even TWO distinct Vantus Rune names are
simultaneously active raid-wide (e.g. a leftover one from last tier
plus this tier's). check_vantus_rune() auto-detects ANY aura name
matching the "Vantus Rune: <anything>" pattern directly from the
fight's own CombatantInfo data, rather than trusting one hardcoded
string -- a previously hardcoded exact-name check was fragile enough
to cause a real reported bug (100% real raid-wide uptime nonetheless
reported as "0 had it" because the tier's actual name didn't match the
one hardcoded string).

OIL DETECTION -- weapon oils are applied to a weapon BEFORE combat and
reported by Warcraft Logs as GearItem.temporary_enchant_id on the
weapon slot in CombatantInfo -- NEVER as a player aura or a Casts
event. The "obvious" fix of matching ability_ids against
GearItem.temporary_enchant_id turned out to be unreliable in practice:
the ability_ids here are sourced from Wowhead SPELL pages (the
buff/effect ID), but WCL's temporaryEnchant field reports Blizzard's
internal ITEM-ENCHANTMENT ID -- a different numbering space that
frequently does not match the spell ID at all, with no reliable public
mapping between the two. Because a weapon temporary enchant is, in
practice, ALWAYS an oil in current content, detection instead treats
ANY non-empty temporary_enchant_id on a weapon slot as "used SOME oil"
(loose_weapon_enchant_match=True, the default), satisfying the "oil"
category regardless of which specific enchant ID it turns out to be.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from core import ParsedFight, get_fight_roster, resolve_to_player
from wcl import WCLClient, WCLAPIError, get_credentials
from core import parse_fight_bundle, parse_selection, resolve_fight_id

# =======================================================================
# SECTION 1 -- schema
# (originally consumable_schema.py -- no other project deps)
# =======================================================================

@dataclass
class ConsumableDefinition:
    name: str
    category: str
    ability_ids: tuple[int, ...] = field(default_factory=tuple)
    notes: str | None = None
    optional: bool = False

    # Weapon oils are applied to a weapon BEFORE combat and reported by
    # Warcraft Logs as GearItem.temporary_enchant_id on the weapon slot
    # in CombatantInfo -- NEVER as a player aura or a Casts event. See
    # this module's top docstring for the full story.
    detection_via_weapon_enchant: bool = False

    # IMPORTANT (found the hard way): the "obvious" fix -- matching
    # ability_ids against GearItem.temporary_enchant_id -- turned out to
    # be unreliable in practice. The ability_ids used here were sourced
    # from Wowhead SPELL pages (the buff/effect ID), but WCL's
    # temporaryEnchant field reports Blizzard's internal ITEM-ENCHANTMENT
    # ID -- a DIFFERENT numbering space that frequently does not match
    # the spell ID at all, and there is no reliable public mapping
    # between the two to build a config against.
    #
    # Because a weapon temporary enchant is, in practice, ALWAYS an oil
    # in current content (weightstones/sharpening stones were removed
    # from the game in earlier expansions), the robust fix is to stop
    # requiring an exact ID match entirely for detection purposes: any
    # non-empty temporary_enchant_id on a weapon slot is treated as
    # "used SOME oil", satisfying the "oil" category, regardless of
    # which specific enchant ID it turns out to be.
    #
    # When True, this ConsumableDefinition's entry contributes to
    # "which SPECIFIC oil was it" reporting ONLY if its own
    # ability_ids happens to match (informational, best-effort) --
    # but the oil CATEGORY as a whole is satisfied by the looser
    # any-weapon-enchant check regardless. See
    # check_any_weapon_enchant_present-equivalent logic in
    # _has_weapon_enchant_match() (Section 5) and its use in
    # analyze_consumables().
    loose_weapon_enchant_match: bool = True


# =======================================================================
# SECTION 2 -- config I/O (load/save/validate/repair .generated.json)
# (originally consumable_config_io.py -- reads/writes
# consumables.generated.json: global list, category mandatory flags)
# =======================================================================

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


# =======================================================================
# SECTION 3 -- seed reference data + tracked list
# (originally consumable_data.py)
#
# SEED_REFERENCE_CONSUMABLES feeds the explorer's auto-detect (Section 4).
# ALL_TRACKED_CONSUMABLES loads from consumables.generated.json, merged
# with any hand-edited MANUAL_CONSUMABLES below (manual wins by name).
#
# VANTUS_RUNE_NAME is kept only as an optional FALLBACK exact-match
# check inside check_vantus_rune() (Section 5), not the primary
# detection mechanism -- see this module's top docstring for why.
# =======================================================================

SEED_REFERENCE_CONSUMABLES: list[ConsumableDefinition] = [
    # --- Flasks ---
    ConsumableDefinition("Flask of the Blood Knights", "flask", ability_ids=(1230877,)),
    ConsumableDefinition("Flask of the Magisters", "flask", ability_ids=(1230876,)),
    ConsumableDefinition("Flask of the Shattered Sun", "flask", ability_ids=(1230878,)),
    ConsumableDefinition("Flask of Thalassian Resistance", "flask", ability_ids=(1230875,)),

    # --- Health Potions ---
    ConsumableDefinition("Concentrated Silvermoon Health Potion", "health_potion", ability_ids=(1289744,)),
    ConsumableDefinition("Silvermoon Health Potion", "health_potion", ability_ids=(1230866,)),
    ConsumableDefinition("Amani Extract", "health_potion", ability_ids=(1230864,)),
    ConsumableDefinition(
        "Potent Healing Potion", "health_potion", ability_ids=(1262857,),
        notes="A fished/dropped healing potion (restores 50% health) -- confirmed via Wowhead (spell=1262857, item=258138).",
    ),

    # --- Combat Potions ---
    ConsumableDefinition("Potion of Recklessness", "combat_potion", ability_ids=(1230859,)),
    ConsumableDefinition("Potion of Zealotry", "combat_potion", ability_ids=(1230863,)),
    ConsumableDefinition("Light's Potential", "combat_potion", ability_ids=(1230869,)),
    ConsumableDefinition("Liquid Luster", "combat_potion", ability_ids=(1289745,)),
    ConsumableDefinition("Draught of Rampant Abandon", "combat_potion", ability_ids=(1230860,)),

    # --- Food ---
    ConsumableDefinition("Hearty Well Fed", "food", notes="The buff gained from eating a Hearty feast for 10+ seconds."),
    ConsumableDefinition("Well Fed", "food", notes="Non-Hearty version of the well-fed buff."),
    ConsumableDefinition(
        "Hearty Harandar Celebration", "food", ability_ids=(266996,),
        notes="This is the CAST of placing the feast, not the eaten-food buff -- secondary signal only.",
    ),

    # --- Oils --- detection_via_weapon_enchant=True, loose_weapon_enchant_match=True (default).
    ConsumableDefinition("Thalassian Phoenix Oil", "oil", ability_ids=(1236491,), detection_via_weapon_enchant=True),
    ConsumableDefinition("Smuggler's Enchanted Edge", "oil", ability_ids=(1236493,), detection_via_weapon_enchant=True),
    ConsumableDefinition("Oil of Dawn", "oil", ability_ids=(1236492,), detection_via_weapon_enchant=True),

    # --- Healthstone --- Warlock-provided, not a crafted consumable.
    ConsumableDefinition("Healthstone", "healthstone"),
    ConsumableDefinition("Demonic Healthstone", "healthstone", notes="Warlock's own healthstone variant."),

    # --- Augment Rune --- optional by default.
    ConsumableDefinition("Void-Touched Augment Rune", "augment_rune", optional=True),

    # --- Vantus Rune --- OPTIONAL (per-raid, not everyone always buys one).
    # See module docstring: this name is now a FALLBACK ONLY. Primary
    # detection auto-detects "Vantus Rune: *" directly from each
    # fight's own data via check_vantus_rune() (Section 5).
    ConsumableDefinition("Vantus Rune: Tides", "vantus_rune", optional=True),
]

# Kept for backward compatibility with any code that still passes a
# specific name explicitly -- but check_vantus_rune() no longer
# REQUIRES this to be correct; it's only consulted as a fallback if
# auto-detection (prefix-matching "Vantus Rune: *") finds nothing at
# all. Safe to leave as-is, or update to match your current tier.
VANTUS_RUNE_NAME = "Vantus Rune: Tides"

DEFAULT_CATEGORY_MANDATORY: dict[str, bool] = {
    "flask": True, "health_potion": True, "combat_potion": True, "food": True, "oil": True,
    "healthstone": True, "augment_rune": False, "vantus_rune": False,
}

MANUAL_CONSUMABLES: list[ConsumableDefinition] = []
MANUAL_CATEGORY_MANDATORY: dict[str, bool] = {}

try:
    _generated_consumables, _generated_category_mandatory = load_generated_consumables()
except ConsumablesConfigError as exc:
    print(f"WARNING: consumables.generated.json could not be read -- ignoring it.\n{exc}\n")
    _generated_consumables, _generated_category_mandatory = [], {}

_by_name: dict[str, ConsumableDefinition] = {c.name: c for c in _generated_consumables}
for _manual in MANUAL_CONSUMABLES:
    _by_name[_manual.name] = _manual
ALL_TRACKED_CONSUMABLES: list[ConsumableDefinition] = list(_by_name.values())

CATEGORY_MANDATORY: dict[str, bool] = {**_generated_category_mandatory, **MANUAL_CATEGORY_MANDATORY}
MANDATORY_CATEGORIES: list[str] = sorted(c for c, m in CATEGORY_MANDATORY.items() if m)


# =======================================================================
# SECTION 4 -- explorer (detect known consumables actually used in a fight)
# (originally consumable_explorer.py)
#
# Scans a ParsedFight for consumables matching SEED_REFERENCE_CONSUMABLES,
# so consumables_cli.py's "explore" subcommand can show "here's what
# this log actually shows you using" instead of asking you to type
# every item name from memory.
#
# Detection uses two sources:
#   - CombatantInfo aura_names (pre-pull buffs -- food, oils, runes,
#     flasks popped before the pull began)
#   - Casts events during the fight (mid-fight reactive use -- a health
#     potion drunk during a damage phase, a Healthstone used on cooldown)
#
# Pure function over a ParsedFight -- no network calls.
# =======================================================================

@dataclass
class DetectedConsumable:
    definition: ConsumableDefinition
    player_names: set[str] = field(default_factory=set)
    detected_via: set[str] = field(default_factory=set)  # {"pre-pull aura", "mid-fight cast"}
    ability_ids_seen: set[int] = field(default_factory=set)
    first_seen_ms: int | None = None

    @property
    def player_count(self) -> int:
        return len(self.player_names)


def analyze_known_consumable_matches(
    parsed_fight: ParsedFight,
    seed_definitions: list[ConsumableDefinition],
) -> list[DetectedConsumable]:
    """
    Build a DetectedConsumable for every seed_definitions entry that was
    actually observed at least once in this fight. Definitions never
    observed are simply absent from the result. Sorted by (category,
    name) for a stable, readable grouping when displayed.
    """
    by_name = {definition.name: definition for definition in seed_definitions}
    detected: dict[str, DetectedConsumable] = {}
    fight_roster = get_fight_roster(parsed_fight)

    for player_id, actor in fight_roster.items():
        snapshot = parsed_fight.combatant_info.get(player_id)
        if snapshot is None:
            continue
        for aura_name in snapshot.aura_names:
            definition = by_name.get(aura_name)
            if definition is None:
                continue
            entry = detected.setdefault(definition.name, DetectedConsumable(definition=definition))
            entry.player_names.add(actor.name)
            entry.detected_via.add("pre-pull aura")

    for event in parsed_fight.events:
        if event.data_type != "Casts" or not event.ability_name:
            continue
        definition = by_name.get(event.ability_name)
        if definition is None:
            continue
        player = resolve_to_player(event.source_id, parsed_fight.actors)
        if player is None:
            continue
        entry = detected.setdefault(definition.name, DetectedConsumable(definition=definition))
        entry.player_names.add(player.name)
        entry.detected_via.add("mid-fight cast")
        if event.ability_id:
            entry.ability_ids_seen.add(event.ability_id)
        if entry.first_seen_ms is None or event.timestamp < entry.first_seen_ms:
            entry.first_seen_ms = event.timestamp

    return sorted(detected.values(), key=lambda d: (d.definition.category, d.definition.name))


def format_detected_table(detected: list[DetectedConsumable], roster_size: int) -> str:
    """Render a plain-text table of detected consumables, for terminal output."""
    if not detected:
        return "No known consumables detected in this pull."
    header = f"{'Item':<32} {'Category':<15} {'Players':>9}  {'Detected Via':<28}"
    lines = ["Detected consumables (matched against the known reference list):", header, "-" * len(header)]
    for entry in detected:
        via_text = "/".join(sorted(entry.detected_via))
        coverage = f"{entry.player_count}/{roster_size}"
        lines.append(
            f"{entry.definition.name:<32.32} {entry.definition.category:<15.15} "
            f"{coverage:>9}  {via_text:<28}"
        )
    return "\n".join(lines)


# =======================================================================
# SECTION 5 -- analyzer (per-player usage tracking + Vantus Rune detection)
# (originally consumables_analyzer.py)
#
# Tracks consumable usage per player within a fight (flask, food,
# potions, healthstone, oil, vantus rune, augment rune).
# =======================================================================

_MAIN_HAND_SLOT = 15
_OFF_HAND_SLOT = 16
_WEAPON_SLOTS = (_MAIN_HAND_SLOT, _OFF_HAND_SLOT)

UNIDENTIFIED_OIL_LABEL = "(unidentified weapon oil)"

# Matches "Vantus Rune: <anything>", "Vantus Rune : <anything>" (with a
# space before the colon), case-insensitively -- deliberately a PREFIX
# match, not a fixed full-name match, since the part after the colon is
# different for every raid tier and is exactly what was hardcoded wrong
# in the reported bug (see this module's top docstring).
_VANTUS_RUNE_PREFIX_RE = re.compile(r"^vantus\s*rune\s*:", re.IGNORECASE)


def is_vantus_rune_aura_name(name: str | None) -> bool:
    """True if `name` looks like a Vantus Rune buff name (matched by PREFIX, not one hardcoded exact string -- see module docstring)."""
    return bool(name) and bool(_VANTUS_RUNE_PREFIX_RE.match(name.strip()))


def find_vantus_rune_aura_names(parsed_fight: ParsedFight) -> set[str]:
    """
    Scan every player's CombatantInfo aura_names for anything matching
    the Vantus Rune naming pattern. Returns the set of DISTINCT actual
    names found -- there can genuinely be MORE THAN ONE (e.g. a raid-
    wide rune plus a boss-specific one, both at 100% uptime, is a
    valid real-world scenario, not a bug).
    """
    found: set[str] = set()
    fight_roster = get_fight_roster(parsed_fight)
    for player_id in fight_roster:
        combatant_info = parsed_fight.combatant_info.get(player_id)
        if combatant_info is None:
            continue
        for name in combatant_info.aura_names:
            if is_vantus_rune_aura_name(name):
                found.add(name)
    return found


@dataclass
class PlayerConsumables:
    player_id: int
    player_name: str
    items_by_category: dict[str, list[str]] = field(default_factory=dict)

    def used(self, category: str) -> bool:
        return bool(self.items_by_category.get(category))

    def missing_categories(self, categories: list[str]) -> list[str]:
        return [c for c in categories if not self.used(c)]


def get_weapon_temporary_enchant_ids(player_id: int, parsed_fight: ParsedFight) -> list[int]:
    combatant_info = parsed_fight.combatant_info.get(player_id)
    if combatant_info is None:
        return []
    return [
        gear_item.temporary_enchant_id
        for gear_item in combatant_info.gear
        if gear_item.slot in _WEAPON_SLOTS and gear_item.temporary_enchant_id is not None
    ]


def _has_weapon_enchant_match(definition: ConsumableDefinition, player_id: int, parsed_fight: ParsedFight) -> bool:
    enchant_ids = get_weapon_temporary_enchant_ids(player_id, parsed_fight)
    if not enchant_ids:
        return False
    if definition.loose_weapon_enchant_match:
        return True
    return any(eid in definition.ability_ids for eid in enchant_ids)


def _has_consumable(definition: ConsumableDefinition, player_id: int, parsed_fight: ParsedFight, cast_names_by_player: dict[int, set[str]]) -> bool:
    combatant_info = parsed_fight.combatant_info.get(player_id)
    if combatant_info and definition.name in combatant_info.aura_names:
        return True
    if definition.name in cast_names_by_player.get(player_id, set()):
        return True
    if definition.detection_via_weapon_enchant:
        return _has_weapon_enchant_match(definition, player_id, parsed_fight)
    return False


def _build_cast_names_by_player(parsed_fight: ParsedFight) -> dict[int, set[str]]:
    cast_names: dict[int, set[str]] = {}
    for event in parsed_fight.events:
        if event.data_type != "Casts" or not event.ability_name:
            continue
        player = resolve_to_player(event.source_id, parsed_fight.actors)
        if player is None:
            continue
        cast_names.setdefault(player.id, set()).add(event.ability_name)
    return cast_names


def _identify_weapon_enchant_name(player_id: int, parsed_fight: ParsedFight, weapon_enchant_definitions: list[ConsumableDefinition]) -> str | None:
    enchant_ids = get_weapon_temporary_enchant_ids(player_id, parsed_fight)
    if not enchant_ids:
        return None
    for definition in weapon_enchant_definitions:
        if any(eid in definition.ability_ids for eid in enchant_ids):
            return definition.name
    return UNIDENTIFIED_OIL_LABEL


def analyze_consumables(parsed_fight: ParsedFight, definitions: list[ConsumableDefinition]) -> list[PlayerConsumables]:
    fight_roster = get_fight_roster(parsed_fight)
    cast_names_by_player = _build_cast_names_by_player(parsed_fight)
    results: dict[int, PlayerConsumables] = {
        player_id: PlayerConsumables(player_id=player_id, player_name=actor.name)
        for player_id, actor in fight_roster.items()
    }

    weapon_enchant_definitions = [d for d in definitions if d.detection_via_weapon_enchant]
    non_weapon_enchant_definitions = [d for d in definitions if not d.detection_via_weapon_enchant]

    for definition in non_weapon_enchant_definitions:
        for player_id, player_consumables in results.items():
            if _has_consumable(definition, player_id, parsed_fight, cast_names_by_player):
                player_consumables.items_by_category.setdefault(definition.category, []).append(definition.name)

    if weapon_enchant_definitions:
        categories_present = {d.category for d in weapon_enchant_definitions}
        for category in categories_present:
            defs_for_category = [d for d in weapon_enchant_definitions if d.category == category]
            for player_id, player_consumables in results.items():
                name = _identify_weapon_enchant_name(player_id, parsed_fight, defs_for_category)
                if name is not None:
                    player_consumables.items_by_category.setdefault(category, []).append(name)

    return sorted(results.values(), key=lambda p: p.player_name or "")


@dataclass
class VantusRuneCheck:
    threshold_met: bool
    players_with: list[str] = field(default_factory=list)
    players_missing: list[str] = field(default_factory=list)
    # The ACTUAL Vantus Rune aura name(s) detected in this fight's own
    # data -- surfaced for transparency, so it's immediately visible
    # (in the report, or while debugging) exactly what was matched,
    # rather than silently trusting a hardcoded assumption.
    detected_names: set[str] = field(default_factory=set)


def check_vantus_rune(parsed_fight: ParsedFight, vantus_rune_name: str | None = None) -> VantusRuneCheck:
    """
    Checks Vantus Rune usage across the fight roster.

    PRIMARY detection: auto-detects the actual Vantus Rune aura name(s)
    present in THIS fight's own CombatantInfo data (any name matching
    "Vantus Rune: *"), and considers a player covered if they have ANY
    detected name -- correctly handling the case where more than one
    distinct Vantus Rune is active raid-wide simultaneously.

    vantus_rune_name is now an OPTIONAL FALLBACK ONLY -- an additional
    exact-match check used in case a fight's buff doesn't follow the
    standard naming convention at all. It no longer gates detection the
    way it previously did (see module docstring for why hardcoding one
    exact name was fragile enough to cause a real reported bug).
    """
    fight_roster = get_fight_roster(parsed_fight)
    if not fight_roster:
        return VantusRuneCheck(threshold_met=False)

    detected_names = find_vantus_rune_aura_names(parsed_fight)
    cast_names_by_player = _build_cast_names_by_player(parsed_fight)
    fallback_definition = ConsumableDefinition(vantus_rune_name, "vantus_rune") if vantus_rune_name else None

    with_rune: list[str] = []
    without_rune: list[str] = []
    for player_id, actor in fight_roster.items():
        combatant_info = parsed_fight.combatant_info.get(player_id)
        has_it = bool(combatant_info and detected_names & combatant_info.aura_names)
        if not has_it and fallback_definition is not None:
            has_it = _has_consumable(fallback_definition, player_id, parsed_fight, cast_names_by_player)
        (with_rune if has_it else without_rune).append(actor.name)

    threshold_met = len(with_rune) >= len(fight_roster) / 2
    return VantusRuneCheck(
        threshold_met=threshold_met,
        players_with=sorted(with_rune),
        players_missing=sorted(without_rune) if threshold_met else [],
        detected_names=detected_names,
    )


def summarize_consumables(
    parsed_fight: ParsedFight, results: list[PlayerConsumables], tracked_categories: list[str], vantus_check: VantusRuneCheck | None = None,
) -> str:
    if not results:
        return f"{parsed_fight.fight.name}: no roster data available for consumable checks."
    lines = [f"{parsed_fight.fight.name} -- consumables:"]
    ranked = sorted(results, key=lambda p: len(p.missing_categories(tracked_categories)), reverse=True)
    for player in ranked:
        missing = player.missing_categories(tracked_categories)
        name = (player.player_name or "Unknown")[:15]
        if missing:
            lines.append(f"  {name:<15} missing ({len(missing)}): {', '.join(missing)}")
        else:
            lines.append(f"  {name:<15} all covered")
    if vantus_check is not None:
        lines.append("")
        names_note = f" (detected: {', '.join(sorted(vantus_check.detected_names))})" if vantus_check.detected_names else " (no 'Vantus Rune: *' aura detected in this pull)"
        if not vantus_check.threshold_met:
            lines.append(f"Vantus Rune: not majority-used ({len(vantus_check.players_with)} had it){names_note}.")
        elif vantus_check.players_missing:
            lines.append(f"Vantus Rune: majority using it{names_note} -- missing: {', '.join(vantus_check.players_missing)}")
        else:
            lines.append(f"Vantus Rune: everyone who should have it, has it{names_note}.")
    return "\n".join(lines)


# =======================================================================
# SECTION 6 -- CLI (explore/list/remove/remove-category/validate/repair/migrate/dump)
# (originally consumables_cli.py -- merged in so the whole consumables
# system, engine + CLI, lives in one file. See build_argument_parser()
# at the bottom for the full subcommand list.)
# =======================================================================

def print_consumables_detail(
    consumables: list[ConsumableDefinition], category_mandatory: dict[str, bool]
) -> list[ConsumableDefinition]:
    if not consumables:
        print("  (none)")
        return []
    ordered: list[ConsumableDefinition] = []
    categories_present = sorted({c.category for c in consumables})
    number = 1
    for category in categories_present:
        mandatory = category_mandatory.get(category, True)
        label = "mandatory" if mandatory else "optional"
        print(f"category: {category} ({label})")
        items = sorted((c for c in consumables if c.category == category), key=lambda c: c.name)
        for consumable in items:
            ids_text = f" ids={list(consumable.ability_ids)}" if consumable.ability_ids else ""
            print(f"  {number:>2}. {consumable.name}{ids_text}")
            if consumable.notes:
                print(f"      notes: {consumable.notes}")
            ordered.append(consumable)
            number += 1
        print()
    return ordered


def _load_or_exit(path):
    try:
        return load_generated_consumables(path)
    except ConsumablesConfigError as exc:
        print(f"Could not read {path}:\n\n{exc}")
        raise SystemExit(1)


def interactive_remove_consumables(
    path,
    consumables: list[ConsumableDefinition] | None = None,
    category_mandatory: dict[str, bool] | None = None,
) -> list[str]:
    if consumables is None or category_mandatory is None:
        try:
            consumables, category_mandatory = load_generated_consumables(path)
        except ConsumablesConfigError as exc:
            print(f"WARNING: could not read {path}:\n{exc}")
            return []
    if not consumables:
        print("No consumables configured yet -- nothing to remove.")
        return []
    ordered = print_consumables_detail(consumables, category_mandatory)
    raw = input(
        "Which item number(s) to remove (e.g. 2,5-7), or type 'c' to remove\n"
        "an entire category, or press Enter to cancel: "
    ).strip()
    if not raw:
        print("Cancelled -- nothing removed.")
        return []
    if raw.lower().startswith("c"):
        category = input("Which category do you want to remove entirely? ").strip()
        if not category:
            print("Cancelled -- nothing removed.")
            return []
        confirm = input(
            f"\nThis deletes ALL consumables in category '{category}'. A backup will "
            f"be saved as {path}.bak first. Proceed? [y/N]: "
        ).strip().lower()
        if confirm not in {"y", "yes"}:
            print("Cancelled -- nothing removed.")
            return []
        removed = remove_category(category, path, verbose=True)
        if removed:
            print(f"\nRemoved category '{category}'. Backup saved as: {path}.bak")
            return [category]
        print("\nCategory not found -- nothing removed.")
        return []
    try:
        indexes = parse_selection(raw, len(ordered))
    except ValueError as exc:
        print(f"Invalid selection: {exc}")
        return []
    if not indexes:
        print("Cancelled -- nothing removed.")
        return []
    names_to_remove = [ordered[i].name for i in indexes]
    print("\nAbout to remove:")
    for name in names_to_remove:
        print(f"  - {name}")
    confirm = input(
        f"\nA backup of {path} will be saved as {path}.bak first. Proceed? [y/N]: "
    ).strip().lower()
    if confirm not in {"y", "yes"}:
        print("Cancelled -- nothing removed.")
        return []
    removed = remove_consumables(names_to_remove, path, verbose=True)
    if removed:
        print(f"\nRemoved {len(removed)} consumable(s).")
        print(f"Backup saved as: {path}.bak")
    else:
        print("\nNothing matched -- file left untouched.")
    return removed


def cmd_list(args: argparse.Namespace) -> None:
    consumables, category_mandatory = _load_or_exit(args.path)
    if not consumables:
        print(f"No consumables configured yet in {args.path}.")
        return
    print(f"{len(consumables)} consumable(s) tracked in {args.path}:\n")
    print_consumables_detail(consumables, category_mandatory)


def cmd_remove(args: argparse.Namespace) -> None:
    consumables, category_mandatory = _load_or_exit(args.path)
    if not consumables:
        print(f"No consumables configured yet in {args.path} -- nothing to remove.")
        return
    interactive_remove_consumables(args.path, consumables=consumables, category_mandatory=category_mandatory)


def cmd_remove_category(args: argparse.Namespace) -> None:
    consumables, _ = _load_or_exit(args.path)
    if not any(c.category == args.category for c in consumables):
        print(f"Category '{args.category}' is not configured -- nothing to remove.")
        return
    if not args.yes:
        confirm = input(
            f"This deletes ALL consumables in category '{args.category}'. A backup "
            f"will be saved as {args.path}.bak first. Proceed? [y/N]: "
        ).strip().lower()
        if confirm not in {"y", "yes"}:
            print("Cancelled -- nothing removed.")
            return
    removed = remove_category(args.category, args.path, verbose=True)
    if removed:
        print(f"\nRemoved category '{args.category}'. Backup saved as: {args.path}.bak")
    else:
        print("Nothing removed (category not found).")


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


def get_tracked_consumable_names(path) -> set[str]:
    try:
        consumables, _ = load_generated_consumables(path)
    except ConsumablesConfigError as exc:
        print(
            f"\nWARNING: {path} has a problem and couldn't be read -- "
            f"skipping the 'already tracked' highlighting for this run.\n{exc}\n"
        )
        return set()
    return {consumable.name for consumable in consumables}


def print_detected_table(detected, roster_size: int, tracked_names: set[str] = frozenset()) -> None:
    if not detected:
        print("No known consumables detected in this pull.")
        return
    if tracked_names:
        print("(* in the T column = already tracked)")
    header = f"{'#':>3} T {'Item':<32} {'Category':<15} {'Players':>9}  {'Detected Via':<28}"
    print(header)
    print("-" * len(header))
    for index, entry in enumerate(detected, start=1):
        marker = "*" if entry.definition.name in tracked_names else " "
        via_text = "/".join(sorted(entry.detected_via))
        coverage = f"{entry.player_count}/{roster_size}"
        print(
            f"{index:>3} {marker} {entry.definition.name:<32.32} "
            f"{entry.definition.category:<15.15} {coverage:>9}  {via_text:<28}"
        )


def fetch_and_detect(report_code: str | None, fight_id: int | None) -> dict | None:
    """The ONLY function in this module that talks to the Warcraft Logs API."""
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
            report_code, resolved_fight_id, event_types=["Casts", "CombatantInfo"],
        )
    except WCLAPIError as exc:
        print(f"Error fetching fight data: {exc}")
        return None

    parsed = parse_fight_bundle(raw_fight, raw_master_data, raw_events_by_type)
    print(f"\nParsed: {parsed.fight.name}  ({'kill' if parsed.fight.kill else 'wipe'})")

    detected = analyze_known_consumable_matches(parsed, SEED_REFERENCE_CONSUMABLES)
    roster_size = len(get_fight_roster(parsed))
    return {"parsed": parsed, "detected": detected, "roster_size": roster_size}


def _prompt_category_mandatory_for_new_categories(new_categories: list[str]) -> dict[str, bool]:
    updates: dict[str, bool] = {}
    for category in new_categories:
        suggested = DEFAULT_CATEGORY_MANDATORY.get(category, True)
        default_label = "Y" if suggested else "n"
        while True:
            answer = input(
                f"Category '{category}' is new. Flag it as MANDATORY (missing = "
                f"fails the check for that player)? [y/n] (default {default_label}): "
            ).strip().lower()
            if not answer:
                updates[category] = suggested
                break
            if answer in {"y", "yes"}:
                updates[category] = True
                break
            if answer in {"n", "no"}:
                updates[category] = False
                break
            print("Enter y or n, or press Enter for the default.")
    return updates


def _prompt_manual_additions() -> list[ConsumableDefinition]:
    manual: list[ConsumableDefinition] = []
    while True:
        answer = input("\nManually add an item not listed above? [y/N]: ").strip().lower()
        if answer not in {"y", "yes"}:
            break
        name = input("  Item name (must match the exact log name): ").strip()
        if not name:
            print("  Name cannot be blank -- skipping.")
            continue
        category = input(
            "  Category (e.g. health_potion/combat_potion/oil/food/healthstone/flask/augment_rune): "
        ).strip()
        if not category:
            print("  Category cannot be blank -- skipping.")
            continue
        notes = input("  Notes (optional): ").strip() or None
        manual.append(ConsumableDefinition(name=name, category=category, notes=notes))
    return manual


def _do_add(session: dict, export_path, replace: bool) -> bool:
    detected = session["detected"]
    roster_size = session["roster_size"]
    tracked_names = get_tracked_consumable_names(export_path)

    print()
    print_detected_table(detected, roster_size, tracked_names)

    to_add: list[ConsumableDefinition] = []
    if detected:
        raw = input(
            "\nSelect detected items to add by number (e.g. 1,3,5-7). "
            "Press Enter to select none: "
        )
        try:
            indexes = parse_selection(raw, len(detected))
        except ValueError as exc:
            print(f"Invalid selection: {exc}")
            indexes = []
        for index in indexes:
            entry = detected[index]
            category = input(
                f"  Category for {entry.definition.name!r} "
                f"(detected as {entry.definition.category!r}, press Enter to keep): "
            ).strip() or entry.definition.category
            notes = input(f"  Notes for {entry.definition.name!r} (optional): ").strip() or None
            to_add.append(ConsumableDefinition(
                name=entry.definition.name, category=category,
                ability_ids=tuple(entry.ability_ids_seen), notes=notes,
            ))

    to_add.extend(_prompt_manual_additions())

    if not to_add:
        print("\nNo consumables selected -- nothing saved.")
        return False

    try:
        _existing_consumables, existing_category_mandatory = load_generated_consumables(export_path)
    except ConsumablesConfigError as exc:
        print(f"WARNING: could not read existing categories from {export_path}:\n{exc}")
        existing_category_mandatory = {}
    new_categories = sorted({c.category for c in to_add} - set(existing_category_mandatory))
    category_updates = _prompt_category_mandatory_for_new_categories(new_categories)

    print()
    try:
        path = save_consumable_selection(
            to_add, category_updates, path=export_path, replace=replace, verbose=True,
        )
    except ConsumablesConfigError as exc:
        print(
            f"Could not save -- {export_path} currently has a problem and wasn't "
            f"touched (nothing was overwritten):\n\n{exc}\n\n"
            f"Fix it, then try again:\n"
            f"  python consumables_cli.py validate\n"
            f"  python consumables_cli.py repair"
        )
        return False

    print(f"\nSaved to: {path}")
    return True


def _do_remove_menu(path) -> None:
    interactive_remove_consumables(path)


def _do_list_menu(path) -> None:
    try:
        consumables, category_mandatory = load_generated_consumables(path)
    except ConsumablesConfigError as exc:
        print(f"WARNING: could not read {path}:\n{exc}")
        return
    if not consumables:
        print(f"No consumables configured yet in {path}.")
        print("Choose 'add' first to explore a report and select some.")
        return
    print(f"{len(consumables)} consumable(s) tracked in {path}:\n")
    print_consumables_detail(consumables, category_mandatory)


def run_interactive_session(args: argparse.Namespace) -> None:
    session: dict = {"parsed": None, "detected": None, "roster_size": None}
    replace_pending = args.replace

    while True:
        clear_screen()
        print("PullDoctor -- Consumables Explorer\n")
        print(
            "What would you like to do?\n"
            "  [a] Add consumables to the tracked list\n"
            "  [r] Remove consumables from the tracked list\n"
            "  [l] List currently tracked consumables\n"
            "  [d] Done"
        )
        choice = input("> ").strip().lower()

        if choice in {"a", "add"}:
            if session["parsed"] is None:
                result = fetch_and_detect(args.report_code, args.fight_id)
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
            print("\nDone. consumables.py will load any saved changes automatically.")
            return
        else:
            print("Enter a, r, l, or d.")
            _pause()


def cmd_explore(args: argparse.Namespace) -> None:
    run_interactive_session(args)


RENAMES: list[tuple[str, str, str]] = [
    ("Potion Of Recklessness", "Potion of Recklessness", "lowercase 'of'"),
    ("Potion Of Zealotry", "Potion of Zealotry", "lowercase 'of'"),
    ("Draught Of Rampant Abandon", "Draught of Rampant Abandon", "lowercase 'of'"),
    ("Oil Of Dawn", "Oil of Dawn", "lowercase 'of'"),
]

KNOWN_OIL_NAMES = [
    "Thalassian Phoenix Oil",
    "Smuggler's Enchanted Edge",
    "Oil of Dawn",
]

FOOD_ADDITIONS: list[ConsumableDefinition] = [
    ConsumableDefinition("Hearty Well Fed", "food", notes="Added by consumables_cli.py migrate."),
    ConsumableDefinition("Well Fed", "food", notes="Added by consumables_cli.py migrate."),
]


def cmd_migrate(args: argparse.Namespace) -> None:
    try:
        consumables, _ = load_generated_consumables(args.path)
    except ConsumablesConfigError as exc:
        print(f"Could not read {args.path}:\n\n{exc}")
        raise SystemExit(1)
    if not consumables:
        print(f"No consumables configured yet in {args.path} -- nothing to migrate.")
        return
    tracked_names = {c.name for c in consumables}
    applicable_renames = [(old, new, reason) for old, new, reason in RENAMES if old in tracked_names]
    names_after_rename = set(tracked_names)
    for old, new, _ in applicable_renames:
        names_after_rename.discard(old)
        names_after_rename.add(new)
    by_name = {c.name: c for c in consumables}
    all_oil_names_tracked = {c.name for c in consumables if c.category == "oil"} | (
        set(KNOWN_OIL_NAMES) & names_after_rename
    )
    applicable_loose_match_fixes = [
        name for name in all_oil_names_tracked
        if not (by_name.get(name) and by_name[name].detection_via_weapon_enchant and by_name[name].loose_weapon_enchant_match)
    ]
    applicable_food_additions = [c for c in FOOD_ADDITIONS if c.name not in tracked_names]
    if not applicable_renames and not applicable_loose_match_fixes and not applicable_food_additions:
        print(f"{args.path} doesn't need any of these fixes -- nothing to migrate.")
        return
    print(f"The following changes will be made to {args.path}:\n")
    for old, new, reason in applicable_renames:
        print(f"  RENAME  {old!r} -> {new!r}   ({reason})")
    for name in applicable_loose_match_fixes:
        print(f"  FIX     {name!r}  -- enable LOOSE weapon-enchant matching (no exact ID required)")
    for definition in applicable_food_additions:
        print(f"  ADD     {definition.name!r}  (category: food)")
    if not args.yes:
        confirm = input(f"\nA backup of {args.path} will be saved as {args.path}.bak first. Proceed? [y/N]: ").strip().lower()
        if confirm not in {"y", "yes"}:
            print("Cancelled -- nothing changed.")
            return
    for old, new, _reason in applicable_renames:
        rename_consumable(old, new, path=args.path, verbose=True)
    if applicable_loose_match_fixes:
        set_weapon_enchant_detection(applicable_loose_match_fixes, path=args.path, verbose=True)
    if applicable_food_additions:
        save_consumable_selection(applicable_food_additions, path=args.path, verbose=True)
    print(f"\nDone. Backup saved as: {args.path}.bak")
    print("Re-run your report -- oil should now be detected for anyone with ANY weapon enchant applied, regardless of the exact ID.")


_WEAPON_SLOT_NAMES = {15: "Main Hand", 16: "Off Hand"}


def cmd_dump(args: argparse.Namespace) -> None:
    try:
        client_id, client_secret = get_credentials()
    except RuntimeError as exc:
        print(exc)
        raise SystemExit(1)
    client = WCLClient(client_id, client_secret)
    try:
        raw_report = client.get_report_fights(args.report_code)
    except WCLAPIError as exc:
        print(f"Error fetching report: {exc}")
        raise SystemExit(1)
    raw_fight = next((f for f in raw_report["fights"] if f["id"] == args.fight_id), None)
    if raw_fight is None:
        print(f"Fight #{args.fight_id} not found in this report.")
        raise SystemExit(1)
    print(f"Report: {raw_report['title']}  ({raw_report['zone']['name']})")
    print(f"Fight: #{raw_fight['id']} {raw_fight['name']}  ({'KILL' if raw_fight.get('kill') else 'wipe'})\n")
    try:
        raw_master_data = client.get_report_master_data(args.report_code)
        raw_events_by_type = client.get_report_events_multi(
            args.report_code, args.fight_id, event_types=["CombatantInfo", "Casts"],
        )
    except WCLAPIError as exc:
        print(f"Error fetching fight data: {exc}")
        raise SystemExit(1)
    parsed = parse_fight_bundle(raw_fight, raw_master_data, raw_events_by_type)
    fight_roster = get_fight_roster(parsed)
    if args.player:
        matches = [a for a in fight_roster.values() if a.name.lower() == args.player.lower()]
        if not matches:
            print(f"Player {args.player!r} not found in this fight's roster.")
            raise SystemExit(1)
        fight_roster = {a.id: a for a in matches}
    aura_name_players: dict[str, set[str]] = {}
    for player_id, actor in fight_roster.items():
        snapshot = parsed.combatant_info.get(player_id)
        if snapshot is None:
            continue
        for aura_name in snapshot.aura_names:
            aura_name_players.setdefault(aura_name, set()).add(actor.name)
    cast_name_players: dict[str, set[str]] = {}
    for event in parsed.events:
        if event.data_type != "Casts" or not event.ability_name:
            continue
        player = resolve_to_player(event.source_id, parsed.actors)
        if player is None or player.id not in fight_roster:
            continue
        cast_name_players.setdefault(event.ability_name, set()).add(player.name)
    tracked_names = {c.name: c.category for c in ALL_TRACKED_CONSUMABLES}
    print("=" * 78)
    print("RAW PRE-PULL AURA NAMES (from CombatantInfo -- flasks/food/runes)")
    print("=" * 78)
    if not aura_name_players:
        print("  (none found)")
    else:
        for name in sorted(aura_name_players):
            players = sorted(aura_name_players[name])
            marker = f"  <- TRACKED as '{tracked_names[name]}'" if name in tracked_names else ""
            print(f"  {name!r:<45} ({len(players)}p: {', '.join(players)[:40]}){marker}")
    print()
    print("=" * 78)
    print("RAW MID-FIGHT CAST NAMES (from Casts -- potions, healthstone, etc.)")
    print("=" * 78)
    if not cast_name_players:
        print("  (none found)")
    else:
        for name in sorted(cast_name_players):
            players = sorted(cast_name_players[name])
            marker = f"  <- TRACKED as '{tracked_names[name]}'" if name in tracked_names else ""
            if name in tracked_names or any(k in name.lower() for k in ("potion", "stone", "rune", "elixir", "draught", "oil")):
                print(f"  {name!r:<45} ({len(players)}p: {', '.join(players)[:40]}){marker}")
    print()
    print("=" * 78)
    print("WEAPON TEMPORARY-ENCHANT IDs (this is what OILS actually are -- NOT an aura)")
    print("=" * 78)
    print("(Detection is now ID-AGNOSTIC: ANY non-empty value here counts as 'oil used',")
    print(" regardless of which specific ID it is. This section is purely informational --")
    print(" a blank row for a player means they genuinely have no weapon oil applied.)")
    any_enchants_found = False
    for player_id, actor in sorted(fight_roster.items(), key=lambda kv: kv[1].name):
        enchant_ids = get_weapon_temporary_enchant_ids(player_id, parsed)
        if enchant_ids:
            any_enchants_found = True
            print(f"  {actor.name:<15} enchant id(s): {enchant_ids}  -> oil DETECTED (loose match)")
        else:
            print(f"  {actor.name:<15} (no weapon temporary enchant found)")
    if not any_enchants_found:
        print("\n  *** NO player in this fight has ANY weapon temporary enchant at all. ***")
        print("  This could mean: (a) genuinely nobody used an oil this pull, (b) this")
        print("  fight has no CombatantInfo snapshot at all (check with dump_combatant_info.py),")
        print("  or (c) this specific report/server doesn't populate temporaryEnchant data.")
    print()
    print("=" * 78)
    print("TRACKED CONSUMABLES (NON-OIL) THAT NEVER APPEARED IN THIS FIGHT'S RAW DATA")
    print("=" * 78)
    all_raw_names = set(aura_name_players) | set(cast_name_players)
    never_appeared = sorted(
        name for name, category in tracked_names.items()
        if category != "oil" and name not in all_raw_names
    )
    if not never_appeared:
        print("  (every non-oil tracked consumable was seen at least once)")
    else:
        for name in never_appeared:
            print(f"  {name!r}  (category: {tracked_names[name]})")


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Explore, manage, migrate, and diagnose tracked consumables (merged CLI)."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    explore_parser = subparsers.add_parser(
        "explore", help="Interactive menu: add (via a report), remove, or list tracked consumables."
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
        help="Generated JSON config path (default: consumables.generated.json)",
    )
    explore_parser.add_argument(
        "--replace", action="store_true",
        help="Wipe all previously tracked consumables on the FIRST successful 'add' this session.",
    )
    explore_parser.set_defaults(func=cmd_explore, path_attr="export")

    list_parser = subparsers.add_parser("list", help="List all tracked consumables")
    list_parser.add_argument("--path", default=str(DEFAULT_CONFIG_PATH))
    list_parser.set_defaults(func=cmd_list)

    remove_parser = subparsers.add_parser("remove", help="Remove specific consumables interactively")
    remove_parser.add_argument("--path", default=str(DEFAULT_CONFIG_PATH))
    remove_parser.set_defaults(func=cmd_remove)

    remove_category_parser = subparsers.add_parser(
        "remove-category", help="Remove an entire category and all its consumables"
    )
    remove_category_parser.add_argument("category")
    remove_category_parser.add_argument("--path", default=str(DEFAULT_CONFIG_PATH))
    remove_category_parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    remove_category_parser.set_defaults(func=cmd_remove_category)

    validate_parser = subparsers.add_parser("validate", help="Check the JSON file for syntax errors")
    validate_parser.add_argument("--path", default=str(DEFAULT_CONFIG_PATH))
    validate_parser.set_defaults(func=cmd_validate)

    repair_parser = subparsers.add_parser(
        "repair", help="Attempt to auto-fix a common syntax error (a trailing comma)"
    )
    repair_parser.add_argument("--path", default=str(DEFAULT_CONFIG_PATH))
    repair_parser.set_defaults(func=cmd_repair)

    migrate_parser = subparsers.add_parser(
        "migrate", help="Apply known one-time fixes to consumables.generated.json (renames, oil loose-match flag, missing food entries)."
    )
    migrate_parser.add_argument("--path", default=str(DEFAULT_CONFIG_PATH))
    migrate_parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    migrate_parser.set_defaults(func=cmd_migrate)

    dump_parser = subparsers.add_parser(
        "dump", help="Dump raw aura/cast/weapon-enchant names for one fight (no changes made)."
    )
    dump_parser.add_argument("report_code", help="Warcraft Logs report code")
    dump_parser.add_argument("fight_id", type=int, help="Fight ID")
    dump_parser.add_argument("--player", default=None, help="Only show this one player (case-insensitive).")
    dump_parser.set_defaults(func=cmd_dump)

    return parser


def main() -> None:
    args = build_argument_parser().parse_args()
    if args.command == "explore":
        cmd_explore(args)
    else:
        args.func(args)


if __name__ == "__main__":
    main()
