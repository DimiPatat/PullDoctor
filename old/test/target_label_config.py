"""
target_label_config.py

Loads the generated per-encounter target labels
(damage_targets.generated.json) at import time, exposing a ready-to-use
ENCOUNTER_TARGET_LABELS dict. If the generated file is currently
broken, this prints a warning and falls back to empty instead of
crashing every script that imports it -- damage_to_target_analyzer.py
works perfectly well with zero labels configured (it just shows raw
WCL NPC names instead of your custom display names).
"""
from __future__ import annotations

from target_label_config_io import TargetLabelConfigError, load_generated_target_labels
from target_label_schema import EncounterTargetLabels, TargetLabel

# Add carefully reviewed, hand-written labels here when needed. These
# OVERRIDE generated entries with the same encounter_id.
MANUAL_ENCOUNTERS: dict[int, EncounterTargetLabels] = {
    # Example:
    # 3492: EncounterTargetLabels(
    #     encounter_id=3492,
    #     encounter_name="Ula'tek",
    #     labels=[
    #         TargetLabel("Ula'tek", "Boss A", category="boss"),
    #         TargetLabel("Zealous Aspirant", "Priority Add A", category="priority_add"),
    #     ],
    # ),
}

try:
    _generated_encounters = load_generated_target_labels()
except TargetLabelConfigError as exc:
    print(
        "WARNING: damage_targets.generated.json could not be read -- "
        "ignoring it for this run.\n"
        f"{exc}\n"
    )
    _generated_encounters = {}

ENCOUNTER_TARGET_LABELS: dict[int, EncounterTargetLabels] = dict(_generated_encounters)
ENCOUNTER_TARGET_LABELS.update(MANUAL_ENCOUNTERS)
