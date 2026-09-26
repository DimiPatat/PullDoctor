"""
target_label_schema.py

Just the TargetLabel / EncounterTargetLabels dataclasses -- kept in
their own module with NO other project dependencies. Scoped PER
ENCOUNTER (keyed by encounter_id) -- a raw NPC name might mean
different things on different bosses.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TargetLabel:
    """One relabeling rule for a single raw NPC name, scoped to one encounter."""
    target_name: str  # raw WCL NPC name -- the matching key, must be exact
    display_name: str  # your custom label, e.g. "Boss A", "Priority Add A"
    category: str | None = None  # e.g. "boss", "miniboss", "priority_add", "add" -- used for rollups
    notes: str | None = None


@dataclass
class EncounterTargetLabels:
    encounter_id: int
    encounter_name: str
    labels: list[TargetLabel] = field(default_factory=list)
