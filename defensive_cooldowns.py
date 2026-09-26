"""
defensive_cooldowns.py

MERGED MODULE -- combines what used to be six separate files into one,
as part of the PullDoctor file-count reduction pass:
    - cooldown_analyzer.py           (shared generic engine -- see NOTE below)
    - defensive_cooldown_schema.py   (DefensiveCooldownDefinition dataclass)
    - defensive_cooldown_config_io.py (load/save/validate/repair .generated.json)
    - defensive_cooldown_data.py     (seed reference list + TRACKED_DEFENSIVE_COOLDOWNS)
    - defensive_cooldown_explorer.py (detect known defensives actually used in a fight)
    - defensive_damage_prevention_analyzer.py (estimates damage PREVENTED per use)

NOTE on cooldown_analyzer.py: this generic usage/efficiency engine
(CooldownDefinition / CooldownUsage / analyze_cooldown_usage /
summarize_cooldown_usage) is ALSO used by raid_cooldowns.py -- it is
intentionally duplicated into BOTH merged files rather than kept as a
13th standalone shared module, per an explicit request to minimize file
count above all else.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from core import format_timestamp, parse_fight_bundle, parse_selection, resolve_fight_id, resolve_to_player
from wcl import WCLClient, WCLAPIError, get_credentials

@dataclass
class CooldownDefinition:
    ability_name: str
    cooldown_seconds: float
    category: str | None = None


@dataclass
class CooldownUsage:
    player_id: int | None
    player_name: str | None
    ability_name: str
    cooldown_seconds: float
    category: str | None
    cast_timestamps: list[int] = field(default_factory=list)

    @property
    def num_casts(self) -> int:
        return len(self.cast_timestamps)

    def theoretical_max_casts(self, fight_duration_ms: int) -> int:
        if self.cooldown_seconds <= 0 or fight_duration_ms <= 0:
            return 0
        return int((fight_duration_ms / 1000) // self.cooldown_seconds) + 1

    def efficiency(self, fight_duration_ms: int) -> float:
        max_casts = self.theoretical_max_casts(fight_duration_ms)
        return min(self.num_casts / max_casts, 1.0) if max_casts else 0.0


def analyze_cooldown_usage(parsed_fight, cooldown_definitions: list[CooldownDefinition]) -> list[CooldownUsage]:
    definitions_by_name = {d.ability_name: d for d in cooldown_definitions}
    usages: dict[tuple[int, str], CooldownUsage] = {}
    for event in parsed_fight.events:
        if event.data_type != "Casts" or event.ability_name not in definitions_by_name:
            continue
        player = resolve_to_player(event.source_id, parsed_fight.actors)
        if player is None:
            continue
        definition = definitions_by_name[event.ability_name]
        key = (player.id, definition.ability_name)
        if key not in usages:
            usages[key] = CooldownUsage(player_id=player.id, player_name=player.name, ability_name=definition.ability_name, cooldown_seconds=definition.cooldown_seconds, category=definition.category)
        usages[key].cast_timestamps.append(event.timestamp)
    for usage in usages.values():
        usage.cast_timestamps.sort()
    return sorted(usages.values(), key=lambda u: (u.player_name or "", u.ability_name))


def summarize_cooldown_usage(parsed_fight, usages: list[CooldownUsage]) -> str:
    if not usages:
        return f"{parsed_fight.fight.name}: no tracked cooldown usage recorded."
    duration_ms = parsed_fight.fight.duration_ms
    lines = [f"{parsed_fight.fight.name} -- cooldown usage ({format_timestamp(duration_ms)}):"]
    for usage in usages:
        max_casts = usage.theoretical_max_casts(duration_ms)
        lines.append(f"  {(usage.player_name or 'Unknown')[:15]:<15} {usage.ability_name[:25]:<25} {usage.num_casts}/{max_casts} casts  ({usage.efficiency(duration_ms) * 100:>5.1f}% efficiency)")
    return "\n".join(lines)


MITIGATION_TYPES = ("percent_reduction", "immunity", "unmodeled")


@dataclass
class DefensiveCooldownDefinition:
    ability_name: str
    cooldown_seconds: float
    category: str | None = None
    class_name: str | None = None
    ability_ids: tuple[int, ...] = field(default_factory=tuple)
    notes: str | None = None
    mitigation_type: str = "unmodeled"
    damage_reduction_percent: float | None = None
    duration_seconds: float | None = None


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
            f"  python defensive_cooldowns_cli.py validate\n"
            f"  python defensive_cooldowns_cli.py repair"
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


SEED_REFERENCE_DEFENSIVE_COOLDOWNS: list[DefensiveCooldownDefinition] = [
    DefensiveCooldownDefinition("Shield Wall", 180, category="damage_reduction", class_name="Warrior",
                                 mitigation_type="percent_reduction", damage_reduction_percent=40, duration_seconds=8),
    DefensiveCooldownDefinition("Die by the Sword", 120, category="damage_reduction", class_name="Warrior",
                                 mitigation_type="percent_reduction", damage_reduction_percent=30, duration_seconds=8),
    DefensiveCooldownDefinition("Spell Reflection", 25, category="immunity", class_name="Warrior",
                                 notes="Reflects the next single spell rather than reducing damage over a window -- doesn't fit the flat-% model, left unmodeled."),
    DefensiveCooldownDefinition("Rallying Cry", 180, category="external", class_name="Warrior",
                                 notes="Raises max HP raid-wide rather than reducing incoming damage -- left unmodeled."),
    DefensiveCooldownDefinition("Divine Shield", 300, category="immunity", class_name="Paladin",
                                 mitigation_type="immunity", duration_seconds=8),
    DefensiveCooldownDefinition("Divine Protection", 60, category="damage_reduction", class_name="Paladin",
                                 mitigation_type="percent_reduction", damage_reduction_percent=20, duration_seconds=8),
    DefensiveCooldownDefinition("Ardent Defender", 90, category="damage_reduction", class_name="Paladin",
                                 mitigation_type="percent_reduction", damage_reduction_percent=30, duration_seconds=12,
                                 notes="Also prevents a single fatal hit -- that safety-net effect isn't captured by the % estimate."),
    DefensiveCooldownDefinition("Guardian of Ancient Kings", 180, category="damage_reduction", class_name="Paladin",
                                 mitigation_type="percent_reduction", damage_reduction_percent=50, duration_seconds=8),
    DefensiveCooldownDefinition("Blessing of Protection", 300, category="external", class_name="Paladin",
                                 notes="Physical-damage immunity applied to ANOTHER player -- left unmodeled."),
    DefensiveCooldownDefinition("Blessing of Sacrifice", 120, category="external", class_name="Paladin",
                                 notes="Redirects a portion of another player's damage to the caster -- a transfer, not a reduction; left unmodeled."),
    DefensiveCooldownDefinition("Lay on Hands", 600, category="external", class_name="Paladin",
                                 notes="A full heal, not a damage reduction -- left unmodeled."),
    DefensiveCooldownDefinition("Icebound Fortitude", 120, category="damage_reduction", class_name="Death Knight",
                                 mitigation_type="percent_reduction", damage_reduction_percent=30, duration_seconds=8),
    DefensiveCooldownDefinition("Anti-Magic Shell", 60, category="magic_immunity", class_name="Death Knight",
                                 notes="An absorb shield with a fixed HP cap, not a % damage reduction -- left unmodeled."),
    DefensiveCooldownDefinition("Anti-Magic Zone", 120, category="magic_immunity", class_name="Death Knight",
                                 notes="Raid-wide absorb shield with a fixed HP cap -- left unmodeled."),
    DefensiveCooldownDefinition("Vampiric Blood", 90, category="damage_reduction", class_name="Death Knight",
                                 notes="Increases max HP and healing received rather than reducing incoming damage -- left unmodeled."),
    DefensiveCooldownDefinition("Lichborne", 120, category="damage_reduction", class_name="Death Knight",
                                 notes="Primarily a fear/charm/sleep immunity + Leech utility, not a flat damage reduction -- left unmodeled."),
    DefensiveCooldownDefinition("Dark Simulacrum", 60, category="utility", class_name="Death Knight",
                                 notes="A spell-steal utility, not a defensive at all -- left unmodeled (tracked for usage only)."),
    DefensiveCooldownDefinition("Fortifying Brew", 360, category="damage_reduction", class_name="Monk",
                                 mitigation_type="percent_reduction", damage_reduction_percent=20, duration_seconds=15,
                                 notes="6 min cooldown confirmed for Brewmaster; Mistweaver/Windwalker have a separate 2 min cooldown (patch 11.0) -- use the Brewmaster value if tracking a tank."),
    DefensiveCooldownDefinition("Diffuse Magic", 90, category="magic_immunity", class_name="Monk",
                                 mitigation_type="unmodeled",
                                 notes="REMOVED AS AN INDEPENDENT CAST in patch 12.0.0 -- it is now a passive effect automatically triggered by Fortifying Brew (transfers harmful magic effects back to caster), no longer has its own cooldown, cast, or damage-reduction value. Will never appear as its own Casts event in a current-tier log; kept here only so old generated.json entries surface a clear explanation instead of silently doing nothing."),
    DefensiveCooldownDefinition("Dampen Harm", 90, category="damage_reduction", class_name="Monk",
                                 notes="REMOVED FROM THE GAME in patch 12.0.0 -- will never appear in a current-tier log. Previously reduced the next 3 big hits by 50%, scaling-reduction doesn't fit the flat-% model anyway."),
    DefensiveCooldownDefinition("Touch of Karma", 90, category="damage_reduction", class_name="Monk",
                                 notes="Redirects a capped portion of damage back at the source rather than reducing it -- left unmodeled."),
    DefensiveCooldownDefinition("Life Cocoon", 120, category="external", class_name="Monk",
                                 notes="An absorb shield applied to another player -- left unmodeled."),
    DefensiveCooldownDefinition("Barkskin", 60, category="damage_reduction", class_name="Druid",
                                 mitigation_type="percent_reduction", damage_reduction_percent=20, duration_seconds=8),
    DefensiveCooldownDefinition("Survival Instincts", 180, category="damage_reduction", class_name="Druid",
                                 mitigation_type="percent_reduction", damage_reduction_percent=50, duration_seconds=6,
                                 notes="Guardian has 2 charges inherently as of patch 12.0 -- theoretical_max_casts doesn't currently account for multiple charges, so efficiency may read conservatively for Guardian druids."),
    DefensiveCooldownDefinition("Ironfur", 30, category="damage_reduction", class_name="Druid",
                                 notes="Increases armor (physical mitigation only, stacks, short GCD-only cast) rather than a flat all-damage %  -- left unmodeled."),
    DefensiveCooldownDefinition("Blur", 60, category="damage_reduction", class_name="Demon Hunter",
                                 mitigation_type="percent_reduction", damage_reduction_percent=25, duration_seconds=10,
                                 notes="Confirmed flat (not decaying) on the current live tooltip. As of patch 12.0, Blur is also the primary personal defensive for the new Devourer specialization, not just Havoc."),
    DefensiveCooldownDefinition("Darkness", 300, category="damage_reduction", class_name="Demon Hunter",
                                 mitigation_type="percent_reduction", damage_reduction_percent=15, duration_seconds=8,
                                 notes="Raid-wide chance-based partial avoidance (15% chance per hit to avoid ALL damage from that attack), not a guaranteed flat reduction for every hit -- treat this estimate as approximate."),
    DefensiveCooldownDefinition("Netherwalk", 90, category="immunity", class_name="Demon Hunter",
                                 mitigation_type="immunity", duration_seconds=3,
                                 notes="REMOVED FROM THE GAME in patch 12.0.0 -- will never appear in a current-tier log. Kept here only so old generated.json entries surface a clear explanation."),
    DefensiveCooldownDefinition("Metamorphosis", 120, category="damage_reduction", class_name="Demon Hunter",
                                 notes="Vengeance's passive tankiness boost (max HP/heal + armor) varies too much by build to give one trustworthy number -- left unmodeled."),
    DefensiveCooldownDefinition("Unending Resolve", 180, category="damage_reduction", class_name="Warlock",
                                 mitigation_type="percent_reduction", damage_reduction_percent=25, duration_seconds=8),
    DefensiveCooldownDefinition("Dark Pact", 60, category="damage_reduction", class_name="Warlock",
                                 notes="Grants an absorb shield (costing health) rather than a flat % reduction -- left unmodeled."),
    DefensiveCooldownDefinition("Aspect of the Turtle", 180, category="immunity", class_name="Hunter",
                                 mitigation_type="immunity", duration_seconds=8),
    DefensiveCooldownDefinition("Exhilaration", 120, category="healing_cd", class_name="Hunter",
                                 notes="An instant self-heal, not a damage reduction -- left unmodeled."),
    DefensiveCooldownDefinition("Fortitude of the Bear", 120, category="healing_cd", class_name="Hunter",
                                 notes="RE-MODELED: this is a Tenacity-pet buff granting a temporary +20% max-health increase plus an instant heal for that amount -- NOT a flat damage-reduction effect as previously modeled here. Left unmodeled; cooldown corrected to 2 min."),
    DefensiveCooldownDefinition("Ice Block", 240, category="immunity", class_name="Mage",
                                 mitigation_type="immunity", duration_seconds=10),
    DefensiveCooldownDefinition("Alter Time", 60, category="utility", class_name="Mage",
                                 notes="Rewinds health/position on expiry rather than reducing damage as it happens -- left unmodeled."),
    DefensiveCooldownDefinition("Mass Barrier", 180, category="external", class_name="Mage",
                                 notes="REMOVED FROM THE GAME in patch 12.0.0 -- will never appear in a current-tier log. Was a raid-wide absorb shield, not a flat % reduction, even before removal."),
    DefensiveCooldownDefinition("Greater Invisibility", 120, category="threat_drop", class_name="Mage",
                                 notes="Grants ~60% damage reduction while invisible and for 3 sec after reappearing, but the invisibility itself (and therefore the reduction window) ends the instant the mage takes any action -- duration is genuinely variable (anywhere from a few seconds to the full 20s), so left unmodeled rather than guessing a fixed window."),
    DefensiveCooldownDefinition("Power Word: Shield", 0, category="self_shield", class_name="Priest",
                                 notes="An absorb shield, not a flat % reduction; also has no real cooldown (short GCD-only cast) -- left unmodeled."),
    DefensiveCooldownDefinition("Desperate Prayer", 90, category="damage_reduction", class_name="Priest",
                                 notes="An instant self-heal, not a damage reduction -- left unmodeled."),
    DefensiveCooldownDefinition("Pain Suppression", 180, category="external", class_name="Priest",
                                 mitigation_type="percent_reduction", damage_reduction_percent=40, duration_seconds=8,
                                 notes="Applied to ANOTHER player -- this tool tracks the caster's cast usage, not the recipient's damage window."),
    DefensiveCooldownDefinition("Guardian Spirit", 180, category="external", class_name="Priest",
                                 notes="Increases healing received and prevents a single fatal hit on ANOTHER player, rather than a flat % reduction -- left unmodeled."),
    DefensiveCooldownDefinition("Vampiric Embrace", 90, category="external", class_name="Priest",
                                 notes="A healing-conversion effect, not a damage reduction -- left unmodeled."),
    DefensiveCooldownDefinition("Cloak of Shadows", 120, category="magic_immunity", class_name="Rogue",
                                 mitigation_type="immunity", duration_seconds=5,
                                 notes="Removes/immunes magic effects specifically, not all damage -- residual PHYSICAL damage during the window is expected and normal."),
    DefensiveCooldownDefinition("Evasion", 120, category="damage_reduction", class_name="Rogue",
                                 notes="A dodge-CHANCE increase (probabilistic per-hit avoidance), not a guaranteed flat reduction -- doesn't fit the model, left unmodeled."),
    DefensiveCooldownDefinition("Feint", 15, category="aoe_reduction", class_name="Rogue",
                                 mitigation_type="percent_reduction", damage_reduction_percent=40, duration_seconds=6,
                                 notes="Specifically reduces AoE/splash damage, not single-target -- the estimate will overstate prevention against single-target hits."),
    DefensiveCooldownDefinition("Crimson Vial", 30, category="healing_cd", class_name="Rogue",
                                 notes="An instant self-heal, not a damage reduction -- left unmodeled."),
    DefensiveCooldownDefinition("Astral Shift", 120, category="damage_reduction", class_name="Shaman",
                                 mitigation_type="percent_reduction", damage_reduction_percent=40, duration_seconds=12),
    DefensiveCooldownDefinition("Earth Elemental", 300, category="threat_drop", class_name="Shaman",
                                 notes="A threat-reduction cooldown, not a personal damage reduction -- left unmodeled."),
    DefensiveCooldownDefinition("Obsidian Scales", 90, category="damage_reduction", class_name="Evoker",
                                 mitigation_type="percent_reduction", damage_reduction_percent=30, duration_seconds=12),
    DefensiveCooldownDefinition("Renewing Blaze", 90, category="healing_cd", class_name="Evoker",
                                 mitigation_type="unmodeled",
                                 notes="REMODELED as of patch 12.0.0 into a passive rider on Obsidian Scales (heals back 100% of the damage Obsidian Scales prevented, over 8 sec) -- it no longer has its own cast, cooldown, or independent trigger. Will never appear as its own Casts event in a current-tier log; kept here only so old generated.json entries surface a clear explanation."),
    DefensiveCooldownDefinition("Zephyr", 120, category="aoe_reduction", class_name="Evoker",
                                 mitigation_type="percent_reduction", damage_reduction_percent=20, duration_seconds=8,
                                 notes="CORRECTED: previously mismodeled here as an 'avoid the next several attacks' dodge effect -- it is actually a flat 20% reduction to AoE-tagged damage specifically (raid-wide, self + 4 nearest allies), not single-target damage. Like Feint, the prevention estimate will overstate savings against single-target hits taken during the window."),
]

MANUAL_DEFENSIVE_COOLDOWNS: list[DefensiveCooldownDefinition] = []
try:
    _generated_defensive_cooldowns = load_generated_defensive_cooldowns()
except DefensiveCooldownConfigError as exc:
    print(f"WARNING: defensive_cooldowns.generated.json could not be read -- ignoring it.\n{exc}\n")
    _generated_defensive_cooldowns = []

_by_name: dict[str, DefensiveCooldownDefinition] = {d.ability_name: d for d in _generated_defensive_cooldowns}
for _manual in MANUAL_DEFENSIVE_COOLDOWNS:
    _by_name[_manual.ability_name] = _manual
TRACKED_DEFENSIVE_COOLDOWNS: list[DefensiveCooldownDefinition] = list(_by_name.values())


def as_cooldown_definitions(definitions: list[DefensiveCooldownDefinition] | None = None) -> list[CooldownDefinition]:
    source = definitions if definitions is not None else TRACKED_DEFENSIVE_COOLDOWNS
    return [
        CooldownDefinition(ability_name=d.ability_name, cooldown_seconds=d.cooldown_seconds, category=d.category)
        for d in source
    ]


@dataclass
class DetectedDefensiveCooldown:
    definition: DefensiveCooldownDefinition
    casts_by_player: dict[str, int] = field(default_factory=dict)
    ability_ids_seen: set[int] = field(default_factory=set)
    first_seen_ms: int | None = None

    @property
    def player_count(self) -> int:
        return len(self.casts_by_player)

    @property
    def total_casts(self) -> int:
        return sum(self.casts_by_player.values())


def analyze_known_defensive_cooldown_matches(
    parsed_fight,
    seed_definitions: list[DefensiveCooldownDefinition],
) -> list[DetectedDefensiveCooldown]:
    by_name = {definition.ability_name: definition for definition in seed_definitions}
    detected: dict[str, DetectedDefensiveCooldown] = {}
    for event in parsed_fight.events:
        if event.data_type != "Casts" or not event.ability_name:
            continue
        definition = by_name.get(event.ability_name)
        if definition is None:
            continue
        player = resolve_to_player(event.source_id, parsed_fight.actors)
        if player is None:
            continue
        entry = detected.setdefault(
            definition.ability_name, DetectedDefensiveCooldown(definition=definition)
        )
        entry.casts_by_player[player.name] = entry.casts_by_player.get(player.name, 0) + 1
        if event.ability_id:
            entry.ability_ids_seen.add(event.ability_id)
        if entry.first_seen_ms is None or event.timestamp < entry.first_seen_ms:
            entry.first_seen_ms = event.timestamp
    return sorted(
        detected.values(),
        key=lambda d: (d.definition.class_name or "", d.definition.ability_name),
    )


def format_detected_table(detected: list[DetectedDefensiveCooldown]) -> str:
    if not detected:
        return "No known defensive cooldowns detected in this pull."
    header = f"{'Ability':<28} {'Class':<14} {'CD(s)':>6} {'Players':>8} {'TotalCasts':>11}"
    lines = ["Detected defensive cooldowns (matched against the known reference list):", header, "-" * len(header)]
    for entry in detected:
        lines.append(
            f"{entry.definition.ability_name:<28.28} {(entry.definition.class_name or ''):<14.14} "
            f"{entry.definition.cooldown_seconds:>6.0f} {entry.player_count:>8} {entry.total_casts:>11}"
        )
    return "\n".join(lines)


@dataclass
class DefensiveWindow:
    player_id: int
    player_name: str
    ability_name: str
    mitigation_type: str
    cast_timestamp: int
    window_end_timestamp: int
    damage_reduction_percent: float | None
    actual_damage_taken: int
    damage_prevented: int | None = None

    @property
    def window_duration_seconds(self) -> float:
        return (self.window_end_timestamp - self.cast_timestamp) / 1000

    @property
    def estimated_damage_without_defensive(self) -> int | None:
        if self.damage_prevented is None:
            return None
        return self.actual_damage_taken + self.damage_prevented


@dataclass
class PlayerDamagePrevention:
    player_id: int
    player_name: str
    windows: list[DefensiveWindow] = field(default_factory=list)

    @property
    def total_damage_prevented(self) -> int:
        return sum(w.damage_prevented for w in self.windows if w.damage_prevented is not None)

    @property
    def total_actual_damage_taken_during_windows(self) -> int:
        return sum(w.actual_damage_taken for w in self.windows)

    @property
    def windows_with_estimate(self) -> list[DefensiveWindow]:
        return [w for w in self.windows if w.damage_prevented is not None]

    @property
    def immunity_windows(self) -> list[DefensiveWindow]:
        return [w for w in self.windows if w.mitigation_type == "immunity"]


def _calculate_prevented(actual_damage: int, reduction_percent: float) -> int:
    if reduction_percent <= 0 or reduction_percent >= 100:
        return 0
    return round(actual_damage * (reduction_percent / (100 - reduction_percent)))


def analyze_damage_prevention(
    parsed_fight,
    definitions: list[DefensiveCooldownDefinition],
) -> list[PlayerDamagePrevention]:
    eligible = {
        d.ability_name: d
        for d in definitions
        if d.mitigation_type in ("percent_reduction", "immunity") and d.duration_seconds is not None
    }
    if not eligible:
        return []
    results: dict[int, PlayerDamagePrevention] = {}
    for event in parsed_fight.events:
        if event.data_type != "Casts" or event.ability_name not in eligible:
            continue
        player = resolve_to_player(event.source_id, parsed_fight.actors)
        if player is None:
            continue
        definition = eligible[event.ability_name]
        window_start = event.timestamp
        window_end = event.timestamp + int(definition.duration_seconds * 1000)
        actual_damage = sum(
            e.amount or 0
            for e in parsed_fight.events
            if e.data_type == "DamageTaken"
            and e.target_id == player.id
            and window_start <= e.timestamp <= window_end
        )
        damage_prevented = None
        if definition.mitigation_type == "percent_reduction" and definition.damage_reduction_percent is not None:
            damage_prevented = _calculate_prevented(actual_damage, definition.damage_reduction_percent)
        window = DefensiveWindow(
            player_id=player.id, player_name=player.name, ability_name=definition.ability_name,
            mitigation_type=definition.mitigation_type, cast_timestamp=window_start, window_end_timestamp=window_end,
            damage_reduction_percent=definition.damage_reduction_percent,
            actual_damage_taken=actual_damage, damage_prevented=damage_prevented,
        )
        if player.id not in results:
            results[player.id] = PlayerDamagePrevention(player_id=player.id, player_name=player.name)
        results[player.id].windows.append(window)
    for entry in results.values():
        entry.windows.sort(key=lambda w: w.cast_timestamp)
    return sorted(results.values(), key=lambda p: p.total_damage_prevented, reverse=True)


def get_player_prevention(
    entries: list[PlayerDamagePrevention], player_name: str
) -> PlayerDamagePrevention | None:
    for entry in entries:
        if entry.player_name and entry.player_name.lower() == player_name.lower():
            return entry
    return None


def format_damage_prevention_table(entries: list[PlayerDamagePrevention]) -> str:
    if not entries:
        return "No damage-prevention data available (no tracked percent_reduction/immunity defensives were used, or none are configured with a known duration)."
    header = f"{'Player':<15} {'Prevented':>12} {'Dmg Taken (windows)':>20} {'Windows':>8}"
    lines = ["Damage prevented by defensive cooldowns (ranked by total prevented):", header, "-" * len(header)]
    for entry in entries:
        lines.append(
            f"{entry.player_name[:15]:<15} {entry.total_damage_prevented:>12,} "
            f"{entry.total_actual_damage_taken_during_windows:>20,} {len(entry.windows):>8}"
        )
    return "\n".join(lines)


def format_player_windows_detail(entry: PlayerDamagePrevention) -> str:
    if not entry.windows:
        return f"{entry.player_name}: no tracked defensive windows this fight."
    lines = [f"{entry.player_name} -- {len(entry.windows)} defensive window(s), {entry.total_damage_prevented:,} total damage prevented:"]
    for window in entry.windows:
        seconds_in = window.cast_timestamp / 1000
        if window.mitigation_type == "immunity":
            lines.append(
                f"  {seconds_in:>6.1f}s  {window.ability_name:<24} (immunity, {window.window_duration_seconds:.0f}s window) -- "
                f"residual damage taken: {window.actual_damage_taken:,} (no 'prevented' estimate possible for immunity -- see Section 6 notes)"
            )
        else:
            lines.append(
                f"  {seconds_in:>6.1f}s  {window.ability_name:<24} "
                f"({window.damage_reduction_percent:.0f}% for {window.window_duration_seconds:.0f}s) -- "
                f"took {window.actual_damage_taken:,}, prevented ~{window.damage_prevented:,} "
                f"(would-have-been ~{window.estimated_damage_without_defensive:,})"
            )
    return "\n".join(lines)


def summarize_damage_prevention(parsed_fight, entries: list[PlayerDamagePrevention]) -> str:
    if not entries:
        return f"{parsed_fight.fight.name}: {format_damage_prevention_table(entries)}"
    lines = [f"{parsed_fight.fight.name} -- damage prevented by defensives:", "", format_damage_prevention_table(entries), ""]
    for entry in entries:
        lines.append(format_player_windows_detail(entry))
        lines.append("")
    return "\n".join(lines).rstrip()


# =======================================================================
# SECTION 7 -- CLI (explore/list/remove/validate/repair)
# (originally defensive_cooldowns_cli.py -- merged in so the whole
# defensive-cooldowns system, engine + CLI, lives in one file.)
# =======================================================================

def _mitigation_summary(definition: DefensiveCooldownDefinition) -> str:
    if definition.mitigation_type == "percent_reduction":
        pct = f"{definition.damage_reduction_percent:.0f}%" if definition.damage_reduction_percent is not None else "?%"
        dur = f"{definition.duration_seconds:.0f}s" if definition.duration_seconds is not None else "?s"
        return f"percent_reduction: {pct} for {dur}"
    if definition.mitigation_type == "immunity":
        dur = f"{definition.duration_seconds:.0f}s" if definition.duration_seconds is not None else "?s"
        return f"immunity: {dur}"
    return "unmodeled (no damage-prevention estimate)"


def print_defensive_cooldowns_detail(
    definitions: list[DefensiveCooldownDefinition],
) -> list[DefensiveCooldownDefinition]:
    if not definitions:
        print("  (none)")
        return []

    ordered: list[DefensiveCooldownDefinition] = []
    classes_present = sorted({d.class_name or "(unspecified class)" for d in definitions})
    number = 1
    for class_name in classes_present:
        print(f"class: {class_name}")
        items = sorted(
            (d for d in definitions if (d.class_name or "(unspecified class)") == class_name),
            key=lambda d: d.ability_name,
        )
        for definition in items:
            ids_text = f" ids={list(definition.ability_ids)}" if definition.ability_ids else ""
            category_text = f" ({definition.category})" if definition.category else ""
            print(f"  {number:>2}. {definition.ability_name}{category_text} [{definition.cooldown_seconds:.0f}s cooldown]{ids_text}")
            print(f"      mitigation: {_mitigation_summary(definition)}")
            if definition.notes:
                print(f"      notes: {definition.notes}")
            ordered.append(definition)
            number += 1
        print()
    return ordered


def _load_or_exit(path) -> list[DefensiveCooldownDefinition]:
    try:
        return load_generated_defensive_cooldowns(path)
    except DefensiveCooldownConfigError as exc:
        print(f"Could not read {path}:\n\n{exc}")
        raise SystemExit(1)


def interactive_remove_defensive_cooldowns(
    path,
    definitions: list[DefensiveCooldownDefinition] | None = None,
) -> list[str]:
    if definitions is None:
        try:
            definitions = load_generated_defensive_cooldowns(path)
        except DefensiveCooldownConfigError as exc:
            print(f"WARNING: could not read {path}:\n{exc}")
            return []

    if not definitions:
        print("No defensive cooldowns configured yet -- nothing to remove.")
        return []

    ordered = print_defensive_cooldowns_detail(definitions)

    raw = input("Which item number(s) to remove (e.g. 2,5-7), or press Enter to cancel: ").strip()
    if not raw:
        print("Cancelled -- nothing removed.")
        return []

    try:
        indexes = parse_selection(raw, len(ordered))
    except ValueError as exc:
        print(f"Invalid selection: {exc}")
        return []
    if not indexes:
        print("Cancelled -- nothing removed.")
        return []

    names_to_remove = [ordered[i].ability_name for i in indexes]
    print("\nAbout to remove:")
    for name in names_to_remove:
        print(f"  - {name}")

    confirm = input(
        f"\nA backup of {path} will be saved as {path}.bak first. Proceed? [y/N]: "
    ).strip().lower()
    if confirm not in {"y", "yes"}:
        print("Cancelled -- nothing removed.")
        return []

    removed = remove_defensive_cooldowns(names_to_remove, path, verbose=True)
    if removed:
        print(f"\nRemoved {len(removed)} defensive cooldown(s).")
        print(f"Backup saved as: {path}.bak")
    else:
        print("\nNothing matched -- file left untouched.")
    return removed


def cmd_list(args: argparse.Namespace) -> None:
    definitions = _load_or_exit(args.path)
    if not definitions:
        print(f"No defensive cooldowns configured yet in {args.path}.")
        return
    print(f"{len(definitions)} defensive cooldown(s) tracked in {args.path}:\n")
    print_defensive_cooldowns_detail(definitions)


def cmd_remove(args: argparse.Namespace) -> None:
    definitions = _load_or_exit(args.path)
    if not definitions:
        print(f"No defensive cooldowns configured yet in {args.path} -- nothing to remove.")
        return
    interactive_remove_defensive_cooldowns(args.path, definitions=definitions)


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


def get_tracked_ability_names(path) -> set[str]:
    try:
        definitions = load_generated_defensive_cooldowns(path)
    except DefensiveCooldownConfigError as exc:
        print(
            f"\nWARNING: {path} has a problem and couldn't be read -- "
            f"skipping the 'already tracked' highlighting for this run.\n{exc}\n"
        )
        return set()
    return {d.ability_name for d in definitions}


def print_detected_table(detected, tracked_names: set[str] = frozenset()) -> None:
    if not detected:
        print("No known defensive cooldowns detected in this pull.")
        return
    if tracked_names:
        print("(* in the T column = already tracked)")
    header = f"{'#':>3} T {'Ability':<28} {'Class':<14} {'CD(s)':>6} {'Players':>8} {'TotalCasts':>11}"
    print(header)
    print("-" * len(header))
    for index, entry in enumerate(detected, start=1):
        marker = "*" if entry.definition.ability_name in tracked_names else " "
        print(
            f"{index:>3} {marker} {entry.definition.ability_name:<28.28} "
            f"{(entry.definition.class_name or ''):<14.14} {entry.definition.cooldown_seconds:>6.0f} "
            f"{entry.player_count:>8} {entry.total_casts:>11}"
        )


def fetch_and_detect(report_code: str | None, fight_id: int | None) -> dict | None:
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
            report_code, resolved_fight_id, event_types=["Casts"],
        )
    except WCLAPIError as exc:
        print(f"Error fetching fight data: {exc}")
        return None

    parsed = parse_fight_bundle(raw_fight, raw_master_data, raw_events_by_type)
    print(f"\nParsed: {parsed.fight.name}  ({'kill' if parsed.fight.kill else 'wipe'})")

    detected = analyze_known_defensive_cooldown_matches(
        parsed, SEED_REFERENCE_DEFENSIVE_COOLDOWNS
    )

    return {"parsed": parsed, "detected": detected}


def _prompt_float(prompt_text: str, default: float | None) -> float | None:
    default_label = f"{default:.0f}" if default is not None else "none"
    while True:
        raw = input(f"{prompt_text} (default {default_label}): ").strip()
        if not raw:
            return default
        try:
            return float(raw)
        except ValueError:
            print("  Couldn't parse that as a number -- try again, or press Enter to keep the default.")


def _prompt_mitigation_profile(
    ability_name: str,
    default_mitigation_type: str = "unmodeled",
    default_damage_reduction_percent: float | None = None,
    default_duration_seconds: float | None = None,
    default_notes: str | None = None,
) -> tuple[str, float | None, float | None, str | None]:
    print(f"\n  Damage-mitigation profile for {ability_name!r}:")
    print(f"    (this feeds defensive_cooldowns.py's damage-prevention analyzer section's 'damage prevented' estimate -- optional)")
    if default_notes:
        print(f"    Seed list notes: {default_notes}")

    type_labels = {
        "percent_reduction": "flat % damage reduction (e.g. Shield Wall)",
        "immunity": "near-100% immunity (e.g. Ice Block)",
        "unmodeled": "doesn't fit either model -- absorb/heal/avoidance/redirect (skip damage-prevention estimate)",
    }
    print("    Mitigation type:")
    for key in MITIGATION_TYPES:
        marker = " <- detected default" if key == default_mitigation_type else ""
        print(f"      [{key[0]}] {key} -- {type_labels[key]}{marker}")

    default_letter = default_mitigation_type[0]
    while True:
        raw = input(f"    Choose p/i/u (default {default_letter}): ").strip().lower()
        if not raw:
            mitigation_type = default_mitigation_type
            break
        matches = [t for t in MITIGATION_TYPES if t.startswith(raw)]
        if len(matches) == 1:
            mitigation_type = matches[0]
            break
        print("    Enter p, i, or u (or press Enter to keep the default).")

    if mitigation_type == "unmodeled":
        return mitigation_type, None, None, default_notes

    damage_reduction_percent = default_damage_reduction_percent
    if mitigation_type == "percent_reduction":
        damage_reduction_percent = _prompt_float(
            "    Damage reduction percent (e.g. 30 for 30%)", default_damage_reduction_percent,
        )
        if damage_reduction_percent is not None and not (0 < damage_reduction_percent < 100):
            print("    Warning: reduction percent should be strictly between 0 and 100 for a back-calculation "
                  "to make sense -- 100% belongs under 'immunity' instead.")

    duration_seconds = _prompt_float(
        "    Effect duration in seconds (how long the mitigation window lasts once cast)", default_duration_seconds,
    )

    notes = input(f"    Notes for {ability_name!r} (optional, Enter to keep existing): ").strip()
    if not notes:
        notes = default_notes

    return mitigation_type, damage_reduction_percent, duration_seconds, notes


def _prompt_manual_additions() -> list[DefensiveCooldownDefinition]:
    manual: list[DefensiveCooldownDefinition] = []
    while True:
        answer = input("\nManually add a defensive cooldown not listed above? [y/N]: ").strip().lower()
        if answer not in {"y", "yes"}:
            break
        name = input("  Ability name (must match the exact log name): ").strip()
        if not name:
            print("  Name cannot be blank -- skipping.")
            continue
        raw_cd = input("  Cooldown in seconds (e.g. 180): ").strip()
        try:
            cooldown_seconds = float(raw_cd)
        except ValueError:
            print("  Couldn't parse that as a number -- skipping this entry.")
            continue
        class_name = input("  Class (optional, e.g. Paladin): ").strip() or None
        category = input("  Category (optional, e.g. immunity/damage_reduction/external): ").strip() or None
        mitigation_type, damage_reduction_percent, duration_seconds, notes = _prompt_mitigation_profile(name)
        manual.append(DefensiveCooldownDefinition(
            ability_name=name, cooldown_seconds=cooldown_seconds,
            category=category, class_name=class_name, notes=notes,
            mitigation_type=mitigation_type, damage_reduction_percent=damage_reduction_percent,
            duration_seconds=duration_seconds,
        ))
    return manual


def _do_add(session: dict, export_path, replace: bool) -> bool:
    detected = session["detected"]
    tracked_names = get_tracked_ability_names(export_path)

    print()
    print_detected_table(detected, tracked_names)

    to_add: list[DefensiveCooldownDefinition] = []
    if detected:
        raw = input(
            "\nSelect detected defensive cooldowns to add by number (e.g. 1,3,5-7). "
            "Press Enter to select none: "
        )
        try:
            indexes = parse_selection(raw, len(detected))
        except ValueError as exc:
            print(f"Invalid selection: {exc}")
            indexes = []
        for index in indexes:
            entry = detected[index]
            raw_cd = input(
                f"\n  Cooldown in seconds for {entry.definition.ability_name!r} "
                f"(detected default {entry.definition.cooldown_seconds:.0f}s, press Enter to keep): "
            ).strip()
            if raw_cd:
                try:
                    cooldown_seconds = float(raw_cd)
                except ValueError:
                    print("  Couldn't parse that as a number -- keeping the detected default.")
                    cooldown_seconds = entry.definition.cooldown_seconds
            else:
                cooldown_seconds = entry.definition.cooldown_seconds
            category = input(
                f"  Category for {entry.definition.ability_name!r} "
                f"(detected as {entry.definition.category!r}, press Enter to keep): "
            ).strip() or entry.definition.category

            mitigation_type, damage_reduction_percent, duration_seconds, notes = _prompt_mitigation_profile(
                entry.definition.ability_name,
                default_mitigation_type=entry.definition.mitigation_type,
                default_damage_reduction_percent=entry.definition.damage_reduction_percent,
                default_duration_seconds=entry.definition.duration_seconds,
                default_notes=entry.definition.notes,
            )

            to_add.append(DefensiveCooldownDefinition(
                ability_name=entry.definition.ability_name,
                cooldown_seconds=cooldown_seconds,
                category=category,
                class_name=entry.definition.class_name,
                ability_ids=tuple(entry.ability_ids_seen),
                notes=notes,
                mitigation_type=mitigation_type,
                damage_reduction_percent=damage_reduction_percent,
                duration_seconds=duration_seconds,
            ))

    to_add.extend(_prompt_manual_additions())

    if not to_add:
        print("\nNo defensive cooldowns selected -- nothing saved.")
        return False

    print()
    try:
        path = save_defensive_cooldown_selection(to_add, path=export_path, replace=replace, verbose=True)
    except DefensiveCooldownConfigError as exc:
        print(
            f"Could not save -- {export_path} currently has a problem and wasn't "
            f"touched (nothing was overwritten):\n\n{exc}\n\n"
            f"Fix it, then try again:\n"
            f"  python defensive_cooldowns_cli.py validate\n"
            f"  python defensive_cooldowns_cli.py repair"
        )
        return False

    print(f"\nSaved to: {path}")
    return True


def _do_remove_menu(path) -> None:
    interactive_remove_defensive_cooldowns(path)


def _do_list_menu(path) -> None:
    try:
        definitions = load_generated_defensive_cooldowns(path)
    except DefensiveCooldownConfigError as exc:
        print(f"WARNING: could not read {path}:\n{exc}")
        return
    if not definitions:
        print(f"No defensive cooldowns configured yet in {path}.")
        print("Choose 'add' first to explore a report and select some.")
        return
    print(f"{len(definitions)} defensive cooldown(s) tracked in {path}:\n")
    print_defensive_cooldowns_detail(definitions)


def run_interactive_session(args: argparse.Namespace) -> None:
    session: dict = {"parsed": None, "detected": None}
    replace_pending = args.replace

    while True:
        clear_screen()
        print("PullDoctor -- Defensive Cooldowns Explorer\n")
        print(
            "What would you like to do?\n"
            "  [a] Add defensive cooldowns to the tracked list\n"
            "  [r] Remove defensive cooldowns from the tracked list\n"
            "  [l] List currently tracked defensive cooldowns\n"
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
            print("\nDone. defensive_cooldowns.py will load any saved changes automatically.")
            return

        else:
            print("Enter a, r, l, or d.")
            _pause()


def cmd_explore(args: argparse.Namespace) -> None:
    run_interactive_session(args)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Explore and manage tracked defensive cooldowns (merged CLI)."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    explore_parser = subparsers.add_parser(
        "explore", help="Interactive menu: add (via a report, incl. mitigation-profile prompts), remove, or list."
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
        help="Generated JSON config path (default: defensive_cooldowns.generated.json)",
    )
    explore_parser.add_argument(
        "--replace", action="store_true",
        help="Wipe all previously tracked defensive cooldowns on the FIRST successful 'add' this session.",
    )
    explore_parser.set_defaults(func=cmd_explore, path_attr="export")

    list_parser = subparsers.add_parser("list", help="List all tracked defensive cooldowns")
    list_parser.add_argument("--path", default=str(DEFAULT_CONFIG_PATH))
    list_parser.set_defaults(func=cmd_list)

    remove_parser = subparsers.add_parser("remove", help="Remove specific defensive cooldowns interactively")
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
