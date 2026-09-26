"""
boss_mechanics_config.py

Manual and generated per-boss avoidable-mechanic data. If the generated
JSON file is currently broken, this warns and falls back to empty
rather than crashing every script that imports it.
"""
from __future__ import annotations

from avoidable_damage_data import AvoidableMechanic, EncounterConfig
from mechanics_config_io import MechanicsConfigError, load_generated_encounters

# Add carefully reviewed, hand-written encounter rules here when needed.
# These OVERRIDE generated entries with the same encounter_id.
MANUAL_ENCOUNTERS: dict[int, EncounterConfig] = {
    # Example:
    # 3492: EncounterConfig(
    #     encounter_id=3492,
    #     encounter_name="Ula'tek",
    #     mechanics=[
    #         AvoidableMechanic(
    #             ability_name="Volatile Purge",
    #             category="spread",
    #             track_via=("damage",),
    #             require_source_differs_from_target=True,
    #             notes="Only counts nearby players who failed to spread.",
    #         ),
    #     ],
    # ),
}

try:
    _generated_encounters = load_generated_encounters()
except MechanicsConfigError as exc:
    print(
        "WARNING: boss_mechanics.generated.json could not be read -- "
        "ignoring it for this run.\n"
        f"{exc}\n"
    )
    _generated_encounters = {}

ENCOUNTERS: dict[int, EncounterConfig] = dict(_generated_encounters)
ENCOUNTERS.update(MANUAL_ENCOUNTERS)
