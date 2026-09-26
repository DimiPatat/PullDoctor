"""
avoidable_damage_data.py

Schema for per-encounter avoidable-mechanic config -- just the
AvoidableMechanic/EncounterConfig definitions. Keyed by
Fight.encounter_id, since avoidable mechanics belong to a specific boss.

track_via controls how a mechanic is detected:
  "damage"  -- count DamageTaken events for this ability
  "debuff"  -- count Debuffs "applydebuff" events for this ability
A mechanic can track via both if useful.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AvoidableMechanic:
    ability_name: str  # matching key -- must match WCL's ability name exactly
    category: str | None = None  # e.g. "ground_effect", "dot", "stacking_debuff", "cone", "spread"
    track_via: tuple[str, ...] = ("damage",)

    # For mechanics logged with a PLAYER as the event's source (not the
    # boss) -- e.g. a delayed eruption off a soaker. WCL logs the
    # CARRIER as source; source_id == target_id is the carrier's own
    # (expected) hit, source_id != target_id is a bystander who failed
    # to spread away (the mistake).
    require_source_differs_from_target: bool = False

    ability_ids: tuple[int, ...] = ()  # spell IDs observed, accumulated across sessions -- informational only

    # For SHARED/SPLIT damage mechanics: a boss ability whose total
    # damage divides among however many players are standing in it. A
    # hit only counts as avoidable if its amount is >= this many times
    # the MEDIAN hit amount recorded for this ability in that same pull
    # (median, not average -- a small number of oversized "clumped"
    # hits would otherwise drag a simple average up with them).
    damage_multiplier_threshold: float | None = None

    notes: str | None = None


@dataclass
class EncounterConfig:
    encounter_id: int
    encounter_name: str
    mechanics: list[AvoidableMechanic] = field(default_factory=list)
