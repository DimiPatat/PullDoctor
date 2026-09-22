"""
log_parser.py

Turns raw WCL JSON (from wcl_api.py) into this app's own data models
(data_models.py). No network calls here -- this module is pure
transformation: raw dicts in, Fight/Actor/Ability/Event objects out.
Everything downstream (analyzers, report) works with these models only.
"""

from __future__ import annotations

from data_models import Ability, Actor, Event, Fight, ParsedFight


def parse_fight(raw_fight: dict) -> Fight:
    """Convert one raw fight dict (from wcl_api.get_report_fights) into a Fight."""
    return Fight(
        id=raw_fight["id"],
        name=raw_fight["name"],
        difficulty=raw_fight.get("difficulty"),
        kill=bool(raw_fight.get("kill", False)),
        start_time=raw_fight["startTime"],
        end_time=raw_fight["endTime"],
        encounter_id=raw_fight.get("encounterID", 0),
    )


def parse_fights(raw_report: dict) -> list[Fight]:
    """Convert every fight in a raw report dict into a list of Fight objects."""
    return [parse_fight(f) for f in raw_report.get("fights", [])]


def parse_actors(raw_master_data: dict) -> dict[int, Actor]:
    """
    Convert raw masterData.actors into a dict keyed by actor ID, for
    resolving event sourceID/targetID into names during event parsing.
    """
    actors: dict[int, Actor] = {}
    for raw_actor in raw_master_data.get("actors", []):
        actors[raw_actor["id"]] = Actor(
            id=raw_actor["id"],
            name=raw_actor.get("name", "Unknown"),
            type=raw_actor.get("type", "Unknown"),
            subtype=raw_actor.get("subType"),
            server=raw_actor.get("server"),
            owner_id=raw_actor.get("petOwner"),
        )
    return actors


def parse_abilities(raw_master_data: dict) -> dict[int, Ability]:
    """
    Convert raw masterData.abilities into a dict keyed by ability game ID,
    for resolving event abilityGameID into names during event parsing.
    """
    abilities: dict[int, Ability] = {}
    for raw_ability in raw_master_data.get("abilities", []):
        abilities[raw_ability["gameID"]] = Ability(
            id=raw_ability["gameID"],
            name=raw_ability.get("name", "Unknown"),
            type=raw_ability.get("type"),
        )
    return abilities


def parse_event(
    raw_event: dict,
    data_type: str,
    actors: dict[int, Actor],
    abilities: dict[int, Ability],
) -> Event:
    """
    Convert a single raw event dict into an Event, resolving source/target/
    ability IDs into names where the lookups have them. Any raw fields not
    explicitly modeled are preserved in Event.raw for analyzer modules that
    need something not yet promoted to a first-class field.
    """
    source_id = raw_event.get("sourceID")
    target_id = raw_event.get("targetID")
    ability_id = raw_event.get("abilityGameID")

    source_actor = actors.get(source_id) if source_id is not None else None
    target_actor = actors.get(target_id) if target_id is not None else None
    ability = abilities.get(ability_id) if ability_id is not None else None

    return Event(
        timestamp=raw_event["timestamp"],
        event_type=raw_event.get("type", "unknown"),
        data_type=data_type,
        source_id=source_id,
        source_name=source_actor.name if source_actor else None,
        target_id=target_id,
        target_name=target_actor.name if target_actor else None,
        ability_id=ability_id,
        ability_name=ability.name if ability else None,
        amount=raw_event.get("amount"),
        overheal=raw_event.get("overheal"),
        absorbed=raw_event.get("absorbed"),
        overkill=raw_event.get("overkill"),
        raw=raw_event,
    )


def parse_events(
    raw_events_by_type: dict[str, list[dict]],
    actors: dict[int, Actor],
    abilities: dict[int, Ability],
) -> list[Event]:
    """
    Convert the dict-of-lists produced by wcl_api.get_report_events_multi
    (keyed by data_type, e.g. "Casts" -> [raw events]) into one flat,
    chronologically sorted list of Event objects.
    """
    events: list[Event] = []
    for data_type, raw_events in raw_events_by_type.items():
        for raw_event in raw_events:
            events.append(parse_event(raw_event, data_type, actors, abilities))

    events.sort(key=lambda e: e.timestamp)
    return events


def parse_fight_bundle(
    raw_fight: dict,
    raw_master_data: dict,
    raw_events_by_type: dict[str, list[dict]],
) -> ParsedFight:
    """
    Convenience entry point: given the raw pieces for one fight (fight
    metadata, master data, and events-by-type -- all straight from
    wcl_api.py), produce a single ParsedFight with everything resolved
    and ready for the analyzer modules.
    """
    actors = parse_actors(raw_master_data)
    abilities = parse_abilities(raw_master_data)

    return ParsedFight(
        fight=parse_fight(raw_fight),
        actors=actors,
        abilities=abilities,
        events=parse_events(raw_events_by_type, actors, abilities),
    )
