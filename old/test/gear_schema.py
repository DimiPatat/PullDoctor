"""
gear_schema.py

Just the EnchantRequirement / GearRequirements dataclasses -- kept in
their own module with NO other project dependencies. Gear requirements
are a single GLOBAL "compliance profile" for the whole tier.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class EnchantRequirement:
    """
    Compliance rule for ONE enchantable gear slot (see
    gear_analyzer.ENCHANTABLE_SLOTS for the fixed set of 8 slots this
    can apply to). Off-hand is deliberately never covered here.
    """
    slot: int  # Blizzard inventory slot index -- must match gear_analyzer.ENCHANTABLE_SLOTS
    slot_name: str  # human-readable, e.g. "Chest" -- for display only
    # Empty tuple means "any enchant is fine, it just must not be
    # missing" -- this is also the fallback for any enchantable slot
    # with NO EnchantRequirement configured at all.
    required_enchant_ids: tuple[int, ...] = field(default_factory=tuple)
    # When True, this slot is excluded from compliance checking
    # entirely (always passes).
    skip_check: bool = False
    notes: str | None = None


@dataclass
class GearRequirements:
    """
    One global compliance profile. Every field is optional (None/empty
    means "not configured, don't check it") so a freshly-created,
    unconfigured GearRequirements() behaves as a complete no-op.

    Gems are checked on TWO independent dimensions:
      - min_gem_count: roster-wide MINIMUM TOTAL gem count -- catches
        "didn't gem anything at all".
      - required_gem_ids: a QUALITY WHITELIST -- if non-empty, every
        gem actually socketed (by ANY player, in ANY slot) must have an
        ID in this set, or that player fails the gem-quality check.
    """
    min_item_level: float | None = None
    min_quality: int | None = None  # WCL's numeric quality tier, e.g. 4 = Epic
    min_gem_count: int | None = None
    required_gem_ids: tuple[int, ...] = field(default_factory=tuple)
    enchant_requirements: list[EnchantRequirement] = field(default_factory=list)
    # Free-text notes for the whole profile -- purely informational.
    notes: str | None = None
