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
    type: Optional[str] = None  # WCL's rough ability category, e.g. "1" (magic), "2" (physical)


@dataclass
class Actor:
    id: int
    name: str
    type: str  # "Player", "NPC", or "Pet"
    subtype: Optional[str] = None  # class name for players (e.g. "Priest"), NPC subtype otherwise
    server: Optional[str] = None
    owner_id: Optional[int] = None  # for Pet actors: the player actor ID that owns this pet


@dataclass
class Fight:
    id: int
    name: str
    difficulty: Optional[int]
    kill: bool
    start_time: int  # ms, relative to report start
    end_time: int  # ms, relative to report start
    encounter_id: int

    @property
    def duration_ms(self) -> int:
        return self.end_time - self.start_time


@dataclass
class Event:
    """
    A single normalized combat log event. Common fields are modeled
    explicitly; anything WCL includes that isn't modeled here is still
    available in `raw` so analyzers aren't blocked waiting on a new field.
    """
    timestamp: int  # ms, relative to report start (same scale as Fight.start_time/end_time)
    event_type: str  # WCL's raw event "type", e.g. "cast", "heal", "damage", "death"
    data_type: str  # which fetch category this came from: "Casts", "Healing", etc.

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
class ParsedFight:
    """Everything log_parser produces for a single fight, bundled together."""
    fight: Fight
    actors: dict[int, Actor]
    abilities: dict[int, Ability]
    events: list[Event]
