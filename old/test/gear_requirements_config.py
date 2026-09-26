"""
gear_requirements_config.py

Loads the gear compliance profile from gear_requirements.generated.json
at import time, exposing a ready-to-use GEAR_REQUIREMENTS object. If
the generated file is currently broken, this prints a warning and falls
back to an EMPTY GearRequirements() instead of crashing every script
that imports it.
"""
from __future__ import annotations

from gear_config_io import GearConfigError, load_generated_gear_requirements
from gear_schema import EnchantRequirement, GearRequirements

# Hand-curated overrides -- take precedence over the generated file.
MANUAL_ENCHANT_REQUIREMENTS: list[EnchantRequirement] = [
    # EnchantRequirement(
    #     slot=4, slot_name="Chest", required_enchant_ids=(7452, 7453),
    #     notes="Either accepted chest enchant this tier.",
    # ),
]
MANUAL_MIN_ITEM_LEVEL: float | None = None
MANUAL_MIN_QUALITY: int | None = None
MANUAL_MIN_GEM_COUNT: int | None = None
# Additional accepted gem IDs on top of whatever's in the generated file --
# UNIONED (not an override).
MANUAL_REQUIRED_GEM_IDS: tuple[int, ...] = (
    # 1230459, 1230460,
)
MANUAL_NOTES: str | None = None

try:
    _generated = load_generated_gear_requirements()
except GearConfigError as exc:
    print(
        "WARNING: gear_requirements.generated.json could not be read -- "
        "ignoring it for this run (gear-compliance results will show "
        "every player passing every check until it's fixed).\n"
        f"{exc}\n"
    )
    _generated = GearRequirements()

_enchant_by_slot: dict[int, EnchantRequirement] = {r.slot: r for r in _generated.enchant_requirements}
for _manual in MANUAL_ENCHANT_REQUIREMENTS:
    _enchant_by_slot[_manual.slot] = _manual

# IMPORTANT: this correctly passes through required_gem_ids and notes
# from the generated file (a prior version of this module silently
# dropped both fields when building the final GEAR_REQUIREMENTS object,
# which meant a configured gem-quality whitelist could never actually
# take effect no matter how it was set -- fixed here).
GEAR_REQUIREMENTS = GearRequirements(
    min_item_level=MANUAL_MIN_ITEM_LEVEL if MANUAL_MIN_ITEM_LEVEL is not None else _generated.min_item_level,
    min_quality=MANUAL_MIN_QUALITY if MANUAL_MIN_QUALITY is not None else _generated.min_quality,
    min_gem_count=MANUAL_MIN_GEM_COUNT if MANUAL_MIN_GEM_COUNT is not None else _generated.min_gem_count,
    required_gem_ids=tuple(sorted(set(_generated.required_gem_ids) | set(MANUAL_REQUIRED_GEM_IDS))),
    enchant_requirements=sorted(_enchant_by_slot.values(), key=lambda r: r.slot),
    notes=MANUAL_NOTES if MANUAL_NOTES is not None else _generated.notes,
)
