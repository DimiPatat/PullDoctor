"""
log_parser.py

Turns raw WCL JSON (from wcl_api.py) into this app's own data models
(data_models.py). No network calls here -- pure transformation.
"""
from __future__ import annotations

from data_models import (
    Ability, Actor, CombatantInfoSnapshot, Event, Fight, GearItem, ParsedFight,
)


def parse_fight(raw_fight: dict) -> Fight:
    return Fight(
        id=raw_fight["id"], name=raw_fight["name"], difficulty=raw_fight.get("difficulty"),
        kill=bool(raw_fight.get("kill", False)), start_time=raw_fight["startTime"],
        end_time=raw_fight["endTime"], encounter_id=raw_fight.get("encounterID", 0),
        friendly_player_ids=raw_fight.get("friendlyPlayers") or [],
    )


def parse_fights(raw_report: dict) -> list[Fight]:
    return [parse_fight(f) for f in raw_report.get("fights", [])]


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
        abilities[raw_ability["gameID"]] = Ability(
            id=raw_ability["gameID"], name=raw_ability.get("name", "Unknown"), type=raw_ability.get("type"),
        )
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
    """
    Convert one raw gear entry from a CombatantInfo event into a GearItem.

    IMPORTANT (fixed): Warcraft Logs' gear array entries do NOT contain
    a "slot" field at all -- there is no such key in the raw JSON.
    Instead, each player's 18-item gear array is always ordered by
    Blizzard's own fixed inventory slot layout (array position 0-17 =
    Head, Neck, Shoulder, Shirt, Chest, Waist, Legs, Feet, Wrist, Hands,
    Ring1, Ring2, Trinket1, Trinket2, Back, MainHand, OffHand,
    Ranged/Relic). The slot number MUST be derived from the item's
    position in that array (slot_index, passed in via enumerate()),
    never read from the raw dict itself.

    (Historical bug: an earlier version read raw_gear_item.get("slot",
    -1), which silently defaulted every gear piece to slot=-1 since
    that key never exists in real API responses -- breaking every
    downstream ENCHANTABLE_SLOTS-based check. Confirmed and root-caused
    against a real report where every player showed "missing enchants
    on all pieces".)
    """
    return GearItem(
        slot=slot_index, item_id=raw_gear_item.get("id", 0), quality=raw_gear_item.get("quality", 0),
        item_level=raw_gear_item.get("itemLevel"),
        permanent_enchant_id=raw_gear_item.get("permanentEnchant"),
        temporary_enchant_id=raw_gear_item.get("temporaryEnchant"),
        gem_ids=[g.get("id") for g in raw_gear_item.get("gems", []) if g.get("id")],
    )


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
            ability = abilities.get(ability_id)
            if ability:
                aura_names.add(ability.name)
        snapshots[player_id] = CombatantInfoSnapshot(
            player_id=player_id, gear=gear, aura_ability_ids=aura_ability_ids, aura_names=aura_names,
        )
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
