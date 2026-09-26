"""consumable_schema.py -- ConsumableDefinition dataclass, no other project deps."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ConsumableDefinition:
    name: str
    category: str
    ability_ids: tuple[int, ...] = field(default_factory=tuple)
    notes: str | None = None
    optional: bool = False

    # Weapon oils are applied to a weapon BEFORE combat and reported by
    # Warcraft Logs as GearItem.temporary_enchant_id on the weapon slot
    # in CombatantInfo -- NEVER as a player aura or a Casts event. See
    # consumable_data.py's module docstring for the full story.
    detection_via_weapon_enchant: bool = False

    # IMPORTANT (found the hard way): the "obvious" fix -- matching
    # ability_ids against GearItem.temporary_enchant_id -- turned out to
    # be unreliable in practice. The ability_ids used here were sourced
    # from Wowhead SPELL pages (the buff/effect ID), but WCL's
    # temporaryEnchant field reports Blizzard's internal ITEM-ENCHANTMENT
    # ID -- a DIFFERENT numbering space that frequently does not match
    # the spell ID at all, and there is no reliable public mapping
    # between the two to build a config against.
    #
    # Because a weapon temporary enchant is, in practice, ALWAYS an oil
    # in current content (weightstones/sharpening stones were removed
    # from the game in earlier expansions), the robust fix is to stop
    # requiring an exact ID match entirely for detection purposes: any
    # non-empty temporary_enchant_id on a weapon slot is treated as
    # "used SOME oil", satisfying the "oil" category, regardless of
    # which specific enchant ID it turns out to be.
    #
    # When True, this ConsumableDefinition's entry contributes to
    # "which SPECIFIC oil was it" reporting ONLY if its own
    # ability_ids happens to match (informational, best-effort) --
    # but the oil CATEGORY as a whole is satisfied by the looser
    # any-weapon-enchant check regardless. See
    # consumables_analyzer.check_any_weapon_enchant_present() and its
    # use in analyze_consumables().
    loose_weapon_enchant_match: bool = True
