"""
raid_cooldown_schema.py
Just the RaidCooldownDefinition dataclass -- kept in its own module
with NO other project dependencies, same philosophy as
defensive_cooldown_schema.py and tier_set_schema.py.

Deliberately SIMPLER than DefensiveCooldownDefinition: raid cooldowns
(Bloodlust/Heroism, Tranquility, Rapture, Devouring Plague, Aura
Mastery, Power Word: Barrier, Spirit Link Totem, Anti-Magic Zone,
Darkness, etc.) are only usage/efficiency-tracked here (via
cooldown_analyzer.py's generic engine) -- this project does not model
a "damage prevented" or "healing granted" estimate for raid cooldowns
the way defensive_damage_prevention_analyzer.py does for PERSONAL
defensives, so there's no mitigation_type/damage_reduction_percent
equivalent needed here.
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass
class RaidCooldownDefinition:
    """
    One tracked raid cooldown. class_name is informational only (which
    class typically brings this) -- NOT used for matching, since raid
    cooldowns are matched purely by ability_name against Casts events,
    same as cooldown_analyzer.CooldownDefinition already does.
    """
    ability_name: str
    cooldown_seconds: float
    category: str | None = None  # e.g. "healing_cd", "damage_reduction_cd", "utility"
    class_name: str | None = None
    notes: str | None = None


def as_cooldown_definitions(definitions: list["RaidCooldownDefinition"]):
    """
    Convert a list of RaidCooldownDefinition into plain
    cooldown_analyzer.CooldownDefinition objects -- reuses
    cooldown_analyzer.py's existing generic usage/efficiency engine
    as-is, exactly the same conversion defensive_cooldown_data.py's
    as_cooldown_definitions() already does for personal defensives.
    Imported lazily (inside the function) to avoid a hard dependency
    for callers that only need the dataclass/config_io layer.
    """
    from cooldown_analyzer import CooldownDefinition
    return [
        CooldownDefinition(ability_name=d.ability_name, cooldown_seconds=d.cooldown_seconds, category=d.category)
        for d in definitions
    ]
