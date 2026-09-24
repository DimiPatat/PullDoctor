"""log_parser.py -- turns raw WCL JSON into this app's own data models. No network calls here."""
from __future__ import annotations

from data_models import Ability, Actor, CombatantInfoSnapshot, Event, Fight, GearItem, ParsedFight


def parse_fight(raw_fight: dict) -> Fight:
    return Fight(
        id=raw_fight["id"], name=raw_fight["name"], difficulty=raw_fight.get("difficulty"),
        kill=bool(raw_fight.get("kill", False)), start_time=raw_fight["startTime"],
        end_time=raw_fight["endTime"], encounter_id=raw_fight.get("encounterID", 0),
        friendly_player_ids=raw_fight.get("friendlyPlayers") or [],
    )


def parse_actors(raw_master_data: dict) -> dict[int, Actor]:
    actors: dict[int, Actor] = {}
    for raw_actor in raw_master_data.get("actors", []):
        actors[raw_actor["id"]] = Actor(
            id=raw_actor["id"], name=raw_actor.get("name", "Unknown"), type=raw_actor.get("type", "Unknown"),
            subtype=raw_actor.get("subType"), server=raw_actor.get("server"), owner_id=raw_actor.get("petOwner"),
        )
    return actors


def parse_abilities(raw_master_data: dict) -> dict[int, Ability]:
    abilities: dict[int, Ability] = {}
    for raw_ability in raw_master_data.get("abilities", []):
        abilities[raw_ability["gameID"]] = Ability(id=raw_ability["gameID"], name=raw_ability.get("name", "Unknown"), type=raw_ability.get("type"))
    return abilities


def parse_event(raw_event: dict, data_type: str, actors: dict[int, Actor], abilities: dict[int, Ability]) -> Event:
    source_id = raw_event.get("sourceID")
    target_id = raw_event.get("targetID")
    ability_id = raw_event.get("abilityGameID")
    source_actor = actors.get(source_id) if source_id is not None else None
    target_actor = actors.get(target_id) if target_id is not None else None
    ability = abilities.get(ability_id) if ability_id is not None else None
    return Event(
        timestamp=raw_event["timestamp"], event_type=raw_event.get("type", "unknown"), data_type=data_type,
        source_id=source_id, source_name=source_actor.name if source_actor else None,
        target_id=target_id, target_name=target_actor.name if target_actor else None,
        ability_id=ability_id, ability_name=ability.name if ability else None,
        amount=raw_event.get("amount"), overheal=raw_event.get("overheal"),
        absorbed=raw_event.get("absorbed"), overkill=raw_event.get("overkill"), raw=raw_event,
    )


def parse_events(raw_events_by_type: dict[str, list[dict]], actors: dict[int, Actor], abilities: dict[int, Ability]) -> list[Event]:
    events: list[Event] = []
    for data_type, raw_events in raw_events_by_type.items():
        for raw_event in raw_events:
            events.append(parse_event(raw_event, data_type, actors, abilities))
    events.sort(key=lambda e: e.timestamp)
    return events


def parse_gear_item(raw_gear_item: dict, slot_index: int) -> GearItem:
    return GearItem(
        slot=slot_index, item_id=raw_gear_item.get("id", 0), quality=raw_gear_item.get("quality", 0),
        item_level=raw_gear_item.get("itemLevel"),
        permanent_enchant_id=raw_gear_item.get("permanentEnchant"),
        temporary_enchant_id=raw_gear_item.get("temporaryEnchant"),
        gem_ids=[g.get("id") for g in raw_gear_item.get("gems", []) if g.get("id")],
    )


_UNRESOLVED_ABILITY_NAME_PREFIX = "Unknown Aura (ability id"


def _resolve_aura_name(raw_aura: dict, ability_id: int, abilities: dict[int, Ability]) -> str:
    """
    Resolve one CombatantInfo aura entry to a display name, trying
    every source available, in order of reliability:

      1. An inline "name" field on the raw aura entry itself, if WCL
         ever includes one directly (some API responses do -- this is
         the most direct source of truth when present, since it can't
         be missing from a SEPARATE query the way master data can be).
      2. The report's masterData.abilities lookup (the normal path).
      3. A synthetic placeholder using the raw numeric ability ID.

    WHY STEP 3 MATTERS (this is the actual bug fix): previously, if
    step 2 failed to find the ability (returned None), the aura was
    SILENTLY DROPPED from aura_names entirely -- with ZERO indication
    anything was wrong. This is a real, demonstrated failure mode:
    Warcraft Logs' masterData.abilities list is built from abilities
    that appear in the report's OWN event stream (casts/damage/healing/
    debuffs) -- a passive, always-on raid consumable buff like a
    Vantus Rune NEVER generates its own Cast/Damage/Healing event (it
    just sits there, applied once, for the whole raid night), so it
    can legitimately be MISSING from masterData.abilities even though
    it is very much genuinely active on every player. Name-based
    consumable detection (consumables_analyzer.py) would then silently
    report "0 had it" for a buff at 100% raid-wide uptime, with no
    error, warning, or visible symptom anywhere -- exactly what was
    reported and reproduced against real report data.

    Falling back to a synthetic name keeps the aura's PRESENCE visible
    (e.g. in a diagnostic dump) instead of invisible, even in the worst
    case where neither an inline name nor a master-data match exists.
    """
    inline_name = raw_aura.get("name")
    if inline_name:
        return inline_name
    ability = abilities.get(ability_id)
    if ability:
        return ability.name
    return f"{_UNRESOLVED_ABILITY_NAME_PREFIX} {ability_id})"


def parse_combatant_info(raw_combatant_info_events: list[dict], abilities: dict[int, Ability]) -> dict[int, CombatantInfoSnapshot]:
    snapshots: dict[int, CombatantInfoSnapshot] = {}
    for raw_event in raw_combatant_info_events:
        player_id = raw_event.get("sourceID")
        if player_id is None:
            continue
        gear = [parse_gear_item(g, idx) for idx, g in enumerate(raw_event.get("gear", []))]
        aura_ability_ids: set[int] = set()
        aura_names: set[str] = set()
        for raw_aura in raw_event.get("auras", []):
            ability_id = raw_aura.get("ability")
            if ability_id is None:
                continue
            aura_ability_ids.add(ability_id)
            # See _resolve_aura_name()'s docstring -- this NEVER silently
            # drops an aura anymore, even if master data can't name it.
            aura_names.add(_resolve_aura_name(raw_aura, ability_id, abilities))
        snapshots[player_id] = CombatantInfoSnapshot(player_id=player_id, gear=gear, aura_ability_ids=aura_ability_ids, aura_names=aura_names)
    return snapshots


def parse_fight_bundle(raw_fight: dict, raw_master_data: dict, raw_events_by_type: dict[str, list[dict]]) -> ParsedFight:
    actors = parse_actors(raw_master_data)
    abilities = parse_abilities(raw_master_data)
    events_by_type = dict(raw_events_by_type)
    raw_combatant_info = events_by_type.pop("CombatantInfo", [])
    return ParsedFight(
        fight=parse_fight(raw_fight), actors=actors, abilities=abilities,
        events=parse_events(events_by_type, actors, abilities),
        combatant_info=parse_combatant_info(raw_combatant_info, abilities),
    )
