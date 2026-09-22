"""role_reference_data.py -- static role/spec classification tables, keyed by WCL's "Class-Spec" icon string."""
from __future__ import annotations

TANK_SPECS = {
    "Warrior-Protection",
    "Paladin-Protection",
    "DeathKnight-Blood",
    "Monk-Brewmaster",
    "Druid-Guardian",
    "DemonHunter-Vengeance",
}

HEALER_SPECS = {
    "Paladin-Holy",
    "Priest-Holy",
    "Priest-Discipline",
    "Druid-Restoration",
    "Shaman-Restoration",
    "Monk-Mistweaver",
    "Evoker-Preservation",
}

MELEE_SPECS = {
    "Warrior-Arms", "Warrior-Fury", "Paladin-Retribution",
    "DeathKnight-Frost", "DeathKnight-Unholy",
    "Rogue-Assassination", "Rogue-Outlaw", "Rogue-Subtlety",
    "Hunter-Survival", "Shaman-Enhancement", "Druid-Feral",
    "Monk-Windwalker", "DemonHunter-Havoc",
}

RANGED_SPECS = {
    "Hunter-BeastMastery", "Hunter-Marksmanship",
    "Mage-Arcane", "Mage-Fire", "Mage-Frost",
    "Warlock-Affliction", "Warlock-Demonology", "Warlock-Destruction",
    "Priest-Shadow", "Shaman-Elemental", "Druid-Balance",
    "Evoker-Devastation", "Evoker-Augmentation",
}


def classify_dps_spec(spec_icon: str | None) -> str:
    if spec_icon in MELEE_SPECS:
        return "melee"
    return "ranged"
