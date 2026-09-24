"""
consumables_analyzer.py

Tracks consumable usage per player within a fight (flask, food,
potions, healthstone, oil, vantus rune, augment rune).

VANTUS RUNE DETECTION -- FIXED (see module-level notes below): a
Vantus Rune's exact buff NAME changes per raid tier (e.g. "Vantus
Rune: Tides" in one raid, "Vantus Rune: Ula'tek" in another) and
sometimes even TWO distinct Vantus Rune names are simultaneously
active raid-wide (e.g. a leftover one from last tier plus this tier's).
Requiring an EXACT match against one hardcoded name
(consumable_data.VANTUS_RUNE_NAME) is fragile for exactly this reason
-- if it's even slightly wrong for the raid you're actually looking
at, EVERY player silently shows as missing it, even at 100% real
uptime. check_vantus_rune() now auto-detects ANY aura name matching
the "Vantus Rune: <anything>" pattern directly from the fight's own
CombatantInfo data, rather than trusting one hardcoded string. The
previously-required exact name is now used ONLY as an additional
fallback (in case some future implementation doesn't follow the
"Vantus Rune: X" naming convention at all).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from consumable_schema import ConsumableDefinition
from data_models import ParsedFight
import roster

_MAIN_HAND_SLOT = 15
_OFF_HAND_SLOT = 16
_WEAPON_SLOTS = (_MAIN_HAND_SLOT, _OFF_HAND_SLOT)

UNIDENTIFIED_OIL_LABEL = "(unidentified weapon oil)"

# Matches "Vantus Rune: <anything>", "Vantus Rune : <anything>" (with a
# space before the colon), case-insensitively -- deliberately a PREFIX
# match, not a fixed full-name match, since the part after the colon is
# different for every raid tier and is exactly what was hardcoded wrong
# in the reported bug.
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
    fight_roster = roster.get_fight_roster(parsed_fight)
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
        player = roster.resolve_to_player(event.source_id, parsed_fight.actors)
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
    fight_roster = roster.get_fight_roster(parsed_fight)
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
    fight_roster = roster.get_fight_roster(parsed_fight)
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
