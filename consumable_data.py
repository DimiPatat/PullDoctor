"""
consumable_data.py

SEED_REFERENCE_CONSUMABLES feeds consumable_explorer.py's auto-detect.
ALL_TRACKED_CONSUMABLES loads from consumables.generated.json.

See consumables_analyzer.py's module docstring for the full 3-round
story of the "oil" detection bug and its fix. Short version: oils are
now detected via "does this player have ANY temporary weapon enchant
at all" (loose_weapon_enchant_match=True, the default) rather than
matching a specific enchant ID -- because the IDs that would be needed
for exact matching (Blizzard's internal item-enchantment IDs) don't
reliably correspond to the Wowhead spell IDs used to source
ability_ids below, and there's no public table mapping one to the
other. ability_ids are kept for BEST-EFFORT "which specific oil"
labeling only -- they no longer gate whether "oil" counts as used.
"""
from __future__ import annotations

from consumable_config_io import ConsumablesConfigError, load_generated_consumables
from consumable_schema import ConsumableDefinition

SEED_REFERENCE_CONSUMABLES: list[ConsumableDefinition] = [
    # --- Flasks ---
    ConsumableDefinition("Flask of the Blood Knights", "flask", ability_ids=(1230877,)),
    ConsumableDefinition("Flask of the Magisters", "flask", ability_ids=(1230876,)),
    ConsumableDefinition("Flask of the Shattered Sun", "flask", ability_ids=(1230878,)),
    ConsumableDefinition("Flask of Thalassian Resistance", "flask", ability_ids=(1230875,)),

    # --- Health Potions ---
    ConsumableDefinition("Concentrated Silvermoon Health Potion", "health_potion", ability_ids=(1289744,)),
    ConsumableDefinition("Silvermoon Health Potion", "health_potion", ability_ids=(1230866,)),
    ConsumableDefinition("Amani Extract", "health_potion", ability_ids=(1230864,)),
    ConsumableDefinition(
        "Potent Healing Potion", "health_potion", ability_ids=(1262857,),
        notes="A fished/dropped healing potion (restores 50% health) -- confirmed via Wowhead "
              "(spell=1262857, item=258138). Found missing from tracking after a real report "
              "showed a player casting it with no matching entry.",
    ),

    # --- Combat Potions --- (lowercase "of" -- verified against Wowhead)
    ConsumableDefinition("Potion of Recklessness", "combat_potion", ability_ids=(1230859,)),
    ConsumableDefinition("Potion of Zealotry", "combat_potion", ability_ids=(1230863,)),
    ConsumableDefinition("Light's Potential", "combat_potion", ability_ids=(1230869,)),
    ConsumableDefinition("Liquid Luster", "combat_potion", ability_ids=(1289745,)),
    ConsumableDefinition("Draught of Rampant Abandon", "combat_potion", ability_ids=(1230860,)),

    # --- Food --- (the real eaten-food buff, not the feast-placement cast)
    ConsumableDefinition("Hearty Well Fed", "food", notes="The buff gained from eating a Hearty feast for 10+ seconds."),
    ConsumableDefinition("Well Fed", "food", notes="Non-Hearty version of the well-fed buff."),
    ConsumableDefinition(
        "Hearty Harandar Celebration", "food", ability_ids=(266996,),
        notes="This is the CAST of placing the feast, not the eaten-food buff -- secondary signal only.",
    ),

    # --- Oils --- detection_via_weapon_enchant=True,
    # loose_weapon_enchant_match=True (default) -- see module docstring.
    # ability_ids kept for best-effort "which oil" labeling only; they
    # do NOT gate whether "oil" counts as used.
    ConsumableDefinition(
        "Thalassian Phoenix Oil", "oil", ability_ids=(1236491,), detection_via_weapon_enchant=True,
        notes="Detected via ANY weapon temporary enchant present (ID-agnostic) -- "
              "see consumables_analyzer.py's module docstring.",
    ),
    ConsumableDefinition(
        "Smuggler's Enchanted Edge", "oil", ability_ids=(1236493,), detection_via_weapon_enchant=True,
        notes="Detected via ANY weapon temporary enchant present (ID-agnostic).",
    ),
    ConsumableDefinition(
        "Oil of Dawn", "oil", ability_ids=(1236492,), detection_via_weapon_enchant=True,
        notes="Detected via ANY weapon temporary enchant present (ID-agnostic).",
    ),

    # --- Healthstone --- Warlock-provided, not a crafted consumable.
    ConsumableDefinition("Healthstone", "healthstone"),
    ConsumableDefinition(
        "Demonic Healthstone", "healthstone", ability_ids=(452930,),
        notes="The Warlock's own healthstone-equivalent (restores 25% health, +30% over 6s "
              "when Empowered) -- confirmed via Wowhead (spell=452930, item=224464). Found "
              "missing from tracking after a real report showed two Warlocks casting this "
              "and being incorrectly flagged as missing a healthstone.",
    ),

    # --- Augment Rune --- optional by default.
    ConsumableDefinition("Void-Touched Augment Rune", "augment_rune", optional=True),

    # --- Vantus Rune --- once-per-week; handled via check_vantus_rune().
    ConsumableDefinition("Vantus Rune: Tides", "vantus_rune"),
]

VANTUS_RUNE_NAME = "Vantus Rune: Tides"

DEFAULT_CATEGORY_MANDATORY: dict[str, bool] = {
    "flask": True, "health_potion": True, "combat_potion": True, "food": True, "oil": True,
    "healthstone": True, "augment_rune": False, "vantus_rune": False,
}

MANUAL_CONSUMABLES: list[ConsumableDefinition] = []
MANUAL_CATEGORY_MANDATORY: dict[str, bool] = {}

try:
    _generated_consumables, _generated_category_mandatory = load_generated_consumables()
except ConsumablesConfigError as exc:
    print(f"WARNING: consumables.generated.json could not be read -- ignoring it.\n{exc}\n")
    _generated_consumables, _generated_category_mandatory = [], {}

_by_name: dict[str, ConsumableDefinition] = {c.name: c for c in _generated_consumables}
for _manual in MANUAL_CONSUMABLES:
    _by_name[_manual.name] = _manual
ALL_TRACKED_CONSUMABLES: list[ConsumableDefinition] = list(_by_name.values())

CATEGORY_MANDATORY: dict[str, bool] = {**_generated_category_mandatory, **MANUAL_CATEGORY_MANDATORY}
MANDATORY_CATEGORIES: list[str] = sorted(c for c, m in CATEGORY_MANDATORY.items() if m)
