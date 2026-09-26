"""
defensive_cooldown_schema.py

Just the DefensiveCooldownDefinition dataclass -- kept in its own
module with NO other project dependencies.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Valid values for DefensiveCooldownDefinition.mitigation_type -- see
# that field's docstring below and defensive_damage_prevention_analyzer.py
# for how each type is (or isn't) used to estimate damage prevented.
MITIGATION_TYPES = ("percent_reduction", "immunity", "unmodeled")


@dataclass
class DefensiveCooldownDefinition:
    """
    One tracked personal defensive cooldown. ability_name must match
    WCL's logged Casts ability name exactly. cooldown_seconds drives
    the usage/efficiency math -- fed into cooldown_analyzer.CooldownDefinition,
    the SAME generic engine already used for raid cooldowns.

    mitigation_type:
      "percent_reduction" -- a flat, known percentage damage reduction
          for the duration -- e.g. Shield Wall (~40% for ~8s). The ONLY
          type that gets a back-calculated "damage prevented" estimate.
      "immunity" -- a 100%/near-100% damage-negating effect (Ice Block,
          Divine Shield, Aspect of the Turtle, etc.). Residual damage
          during the window is reported as-is, but NO "prevented"
          number is estimated -- there is no known baseline to scale from.
      "unmodeled" (the default) -- absorb shields, heals, avoidance/
          dodge, damage redirection, or anything too variable/talent-
          dependent to give a single trustworthy number for. Still
          tracked for plain usage/efficiency, but excluded entirely
          from damage-prevention estimates.

    damage_reduction_percent: e.g. 30.0 for a 30% damage reduction.
      Only meaningful when mitigation_type == "percent_reduction".
      IMPORTANT: WCL only logs damage AFTER the reduction was already
      applied -- if a player took 100 damage while reduced by 30%, the
      damage that WOULD have landed with no defensive up was actually
      100 / (1 - 0.30) = ~142.9, meaning ~42.9 was prevented, NOT
      100 * 0.30 = 30.

    duration_seconds: how long the damage-reduction effect actually
      lasts once cast (e.g. 8.0 for an 8-second Shield Wall) -- this is
      DIFFERENT from cooldown_seconds (how long until it can be used
      again).

    All of these vary by expansion/talents/gear -- treat the seed
    values in defensive_cooldown_data.py as a reasonable starting point
    to verify and correct for your own raid tier, not ground truth.
    """
    ability_name: str
    cooldown_seconds: float
    category: str | None = None
    class_name: str | None = None
    ability_ids: tuple[int, ...] = field(default_factory=tuple)
    notes: str | None = None
    mitigation_type: str = "unmodeled"
    damage_reduction_percent: float | None = None
    duration_seconds: float | None = None
