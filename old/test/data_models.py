"""
data_models.py

Shared types used by every module downstream of wcl_api.py. These are
the app's own vocabulary -- log_parser.py is responsible for turning
raw WCL JSON into these; analyzers, report, and main only ever deal
with these types, never raw dicts.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Ability:
    id: int
    name: str
    type: Optional[str] = None


@dataclass
class Actor:
    id: int
    name: str
    type: str  # "Player", "NPC", or "Pet"
    subtype: Optional[str] = None
    server: Optional[str] = None
    owner_id: Optional[int] = None


@dataclass
class Fight:
    id: int
    name: str
    difficulty: Optional[int]
    kill: bool
    start_time: int
    end_time: int
    encounter_id: int
    friendly_player_ids: list[int] = field(default_factory=list)

    @property
    def duration_ms(self) -> int:
        return self.end_time - self.start_time


@dataclass
class Event:
    timestamp: int
    event_type: str
    data_type: str
    source_id: Optional[int] = None
    source_name: Optional[str] = None
    target_id: Optional[int] = None
    target_name: Optional[str] = None
    ability_id: Optional[int] = None
    ability_name: Optional[str] = None
    amount: Optional[int] = None
    overheal: Optional[int] = None
    absorbed: Optional[int] = None
    overkill: Optional[int] = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class GearItem:
    slot: int
    item_id: int
    quality: int
    item_level: Optional[int] = None
    permanent_enchant_id: Optional[int] = None
    temporary_enchant_id: Optional[int] = None
    gem_ids: list[int] = field(default_factory=list)


@dataclass
class CombatantInfoSnapshot:
    player_id: int
    gear: list[GearItem] = field(default_factory=list)
    aura_ability_ids: set[int] = field(default_factory=set)
    aura_names: set[str] = field(default_factory=set)


@dataclass
class ParsedFight:
    fight: Fight
    actors: dict[int, Actor]
    abilities: dict[int, Ability]
    events: list[Event]
    combatant_info: dict[int, CombatantInfoSnapshot] = field(default_factory=dict)
