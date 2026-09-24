"""
consumable_data.py

SEED_REFERENCE_CONSUMABLES feeds consumable_explorer.py's auto-detect.
ALL_TRACKED_CONSUMABLES loads from consumables.generated.json.

VANTUS_RUNE_NAME -- CHANGED: this is now used only as an optional
FALLBACK exact-match check inside consumables_analyzer.check_vantus_rune(),
not as the primary detection mechanism. Primary detection now
auto-detects any aura name matching "Vantus Rune: *" directly from
each fight's own data, since the exact suffix (e.g. "Tides",
"Ula'tek", or any other raid/boss name) varies by raid tier -- and
sometimes TWO distinct Vantus Rune names are simultaneously active
raid-wide (a leftover one from last tier plus the current one). See
consumables_analyzer.py's module docstring for the full story behind
this fix (a real reported bug: a report showing 100% raid-wide uptime
on two Vantus Rune buffs was nonetheless reported as "0 had it").

Oils are detected via "does this player have ANY temporary weapon
enchant at all" (loose_weapon_enchant_match=True, the default) rather
than matching a specific enchant ID -- because the IDs that would be
needed for exact matching (Blizzard's internal item-enchantment IDs)
don't reliably correspond to the Wowhead spell IDs used to source
ability_ids below.
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
        notes="A fished/dropped healing potion (restores 50% health) -- confirmed via Wowhead (spell=1262857, item=258138).",
    ),

    # --- Combat Potions ---
    ConsumableDefinition("Potion of Recklessness", "combat_potion", ability_ids=(1230859,)),
    ConsumableDefinition("Potion of Zealotry", "combat_potion", ability_ids=(1230863,)),
    ConsumableDefinition("Light's Potential", "combat_potion", ability_ids=(1230869,)),
    ConsumableDefinition("Liquid Luster", "combat_potion", ability_ids=(1289745,)),
    ConsumableDefinition("Draught of Rampant Abandon", "combat_potion", ability_ids=(1230860,)),

    # --- Food ---
    ConsumableDefinition("Hearty Well Fed", "food", notes="The buff gained from eating a Hearty feast for 10+ seconds."),
    ConsumableDefinition("Well Fed", "food", notes="Non-Hearty version of the well-fed buff."),
    ConsumableDefinition(
        "Hearty Harandar Celebration", "food", ability_ids=(266996,),
        notes="This is the CAST of placing the feast, not the eaten-food buff -- secondary signal only.",
    ),

    # --- Oils --- detection_via_weapon_enchant=True, loose_weapon_enchant_match=True (default).
    ConsumableDefinition("Thalassian Phoenix Oil", "oil", ability_ids=(1236491,), detection_via_weapon_enchant=True),
    ConsumableDefinition("Smuggler's Enchanted Edge", "oil", ability_ids=(1236493,), detection_via_weapon_enchant=True),
    ConsumableDefinition("Oil of Dawn", "oil", ability_ids=(1236492,), detection_via_weapon_enchant=True),

    # --- Healthstone --- Warlock-provided, not a crafted consumable.
    ConsumableDefinition("Healthstone", "healthstone"),
    ConsumableDefinition("Demonic Healthstone", "healthstone", notes="Warlock's own healthstone variant."),

    # --- Augment Rune --- optional by default.
    ConsumableDefinition("Void-Touched Augment Rune", "augment_rune", optional=True),

    # --- Vantus Rune --- OPTIONAL (per-raid, not everyone always buys one).
    # See module docstring: this name is now a FALLBACK ONLY. Primary
    # detection auto-detects "Vantus Rune: *" directly from each
    # fight's own data via consumables_analyzer.check_vantus_rune().
    ConsumableDefinition("Vantus Rune: Tides", "vantus_rune", optional=True),
]

# Kept for backward compatibility with any code that still passes a
# specific name explicitly -- but check_vantus_rune() no longer
# REQUIRES this to be correct; it's only consulted as a fallback if
# auto-detection (prefix-matching "Vantus Rune: *") finds nothing at
# all. Safe to leave as-is, or update to match your current tier.
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
