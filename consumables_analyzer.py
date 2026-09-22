"""
consumables_analyzer.py

Tracks consumable usage per player within a fight.

======================================================================
"oil" DETECTION -- the story so far, and why it's now ID-agnostic:
======================================================================
Round 1 bug: oils were checked against player AURA names / Casts
events. Oils are applied to a weapon BEFORE combat as a "temporary
weapon enchant" -- they never appear as an aura or a cast at all. This
made "oil" show as missing for 100% of every roster, regardless of who
actually used one. (Root-caused from a real report showing 21/21
players missing oil -- a 100% miss rate is the signature of a
detection-METHOD bug, not real non-usage.)

Round 2 attempt: check GearItem.temporary_enchant_id on the weapon
slot against a list of expected enchant IDs (sourced from Wowhead
spell pages). This STILL didn't work in practice -- Wowhead's spell ID
for an oil's buff effect and Blizzard's internal item-enchantment ID
(what WCL's temporaryEnchant field actually reports) are frequently
DIFFERENT numbers, with no reliable public table mapping one to the
other to build a config against.

Round 3 (this fix) -- LOOSE, ID-AGNOSTIC MATCH: since a weapon
temporary enchant is, in practice, ALWAYS an oil in current content
(older stackable weightstones/sharpening stones were removed from the
game long ago), checking is simplified to: does this player's Main
Hand or Off Hand weapon have ANY non-empty temporary_enchant_id at
all? If yes, the "oil" category is satisfied -- no ID matching
required, so an unknown/unexpected enchant ID can no longer cause a
silent false "missing" result. The SPECIFIC oil name reported (for
display purposes) still tries to match a known ability_id first, and
falls back to a generic "(unidentified weapon oil)" label if the
exact ID isn't in our list -- but the "used oil" TRUE/FALSE result no
longer depends on that match succeeding.

Pure function over a ParsedFight -- no network calls.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from consumable_schema import ConsumableDefinition
from data_models import ParsedFight
import roster

_MAIN_HAND_SLOT = 15
_OFF_HAND_SLOT = 16
_WEAPON_SLOTS = (_MAIN_HAND_SLOT, _OFF_HAND_SLOT)

UNIDENTIFIED_OIL_LABEL = "(unidentified weapon oil)"


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
    """Every non-empty temporary_enchant_id found on this player's Main Hand/Off Hand, in slot order. Empty list if none/no gear data."""
    combatant_info = parsed_fight.combatant_info.get(player_id)
    if combatant_info is None:
        return []
    return [
        gear_item.temporary_enchant_id
        for gear_item in combatant_info.gear
        if gear_item.slot in _WEAPON_SLOTS and gear_item.temporary_enchant_id is not None
    ]


def _has_weapon_enchant_match(
    definition: ConsumableDefinition, player_id: int, parsed_fight: ParsedFight,
) -> bool:
    """
    True if this player has ANY temporary weapon enchant at all
    (loose_weapon_enchant_match=True, the default and now-recommended
    mode -- no ID comparison, since the ID mapping is unreliable), OR
    (if loose matching is explicitly turned off) their weapon's
    temporary_enchant_id specifically matches one of
    definition.ability_ids.
    """
    enchant_ids = get_weapon_temporary_enchant_ids(player_id, parsed_fight)
    if not enchant_ids:
        return False
    if definition.loose_weapon_enchant_match:
        return True
    return any(eid in definition.ability_ids for eid in enchant_ids)


def _has_consumable(
    definition: ConsumableDefinition,
    player_id: int,
    parsed_fight: ParsedFight,
    cast_names_by_player: dict[int, set[str]],
) -> bool:
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


def _identify_weapon_enchant_name(
    player_id: int,
    parsed_fight: ParsedFight,
    weapon_enchant_definitions: list[ConsumableDefinition],
) -> str | None:
    """
    Best-effort: if this player's actual weapon enchant ID happens to
    match one specific definition's ability_ids, return THAT
    definition's name (e.g. "Oil of Dawn"). Otherwise, if they have
    SOME enchant but it doesn't match any known ID, return the generic
    UNIDENTIFIED_OIL_LABEL. Returns None if they have no weapon
    enchant at all. This always returns AT MOST ONE name per player --
    it does NOT list every configured oil definition just because all
    of them independently satisfy the "loose" any-enchant check (that
    was a real bug caught in testing: a player with exactly one weapon
    enchant was showing all three configured oil NAMES, as if they'd
    used all three simultaneously).
    """
    enchant_ids = get_weapon_temporary_enchant_ids(player_id, parsed_fight)
    if not enchant_ids:
        return None
    for definition in weapon_enchant_definitions:
        if any(eid in definition.ability_ids for eid in enchant_ids):
            return definition.name
    return UNIDENTIFIED_OIL_LABEL


def analyze_consumables(
    parsed_fight: ParsedFight,
    definitions: list[ConsumableDefinition],
) -> list[PlayerConsumables]:
    """
    Build a PlayerConsumables entry for every player in the fight
    roster. Weapon-enchant-detected definitions (oils) are handled as
    ONE combined check per player, not per-definition -- a player with
    a single weapon enchant is credited with exactly one entry (the
    specific oil name if its ID happens to match, otherwise
    UNIDENTIFIED_OIL_LABEL), never with every configured oil name at
    once.
    """
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
        # All weapon-enchant definitions are assumed to share one
        # category (normally "oil") -- if a config ever mixes
        # categories here, each player still gets one identified/
        # unidentified name per distinct category present.
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
    players_with: list[str]
    players_missing: list[str]


def check_vantus_rune(parsed_fight: ParsedFight, vantus_rune_name: str) -> VantusRuneCheck:
    fight_roster = roster.get_fight_roster(parsed_fight)
    if not fight_roster:
        return VantusRuneCheck(threshold_met=False, players_with=[], players_missing=[])
    cast_names_by_player = _build_cast_names_by_player(parsed_fight)
    definition = ConsumableDefinition(vantus_rune_name, "vantus_rune", detection_via_weapon_enchant=False)
    with_rune: list[str] = []
    without_rune: list[str] = []
    for player_id, actor in fight_roster.items():
        if _has_consumable(definition, player_id, parsed_fight, cast_names_by_player):
            with_rune.append(actor.name)
        else:
            without_rune.append(actor.name)
    threshold_met = len(with_rune) >= len(fight_roster) / 2
    return VantusRuneCheck(
        threshold_met=threshold_met,
        players_with=sorted(with_rune),
        players_missing=sorted(without_rune) if threshold_met else [],
    )


def summarize_consumables(
    parsed_fight: ParsedFight,
    results: list[PlayerConsumables],
    tracked_categories: list[str],
    vantus_check: VantusRuneCheck | None = None,
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
        if not vantus_check.threshold_met:
            lines.append(f"Vantus Rune: not majority-used ({len(vantus_check.players_with)} had it).")
        elif vantus_check.players_missing:
            lines.append(f"Vantus Rune: majority using it -- missing: {', '.join(vantus_check.players_missing)}")
        else:
            lines.append("Vantus Rune: everyone who should have it, has it.")
    return "\n".join(lines)
