"""
raid_cooldowns_config.py
The cooldowns cooldown_analyzer.py should track for your raid.

CHANGED: TRACKED_COOLDOWNS is now the MERGE of two sources, same
pattern already used by defensive_cooldown_data.py / tier_set_data.py:

  1. MANUAL_COOLDOWNS below -- your original hand-edited list. Still
     fully supported; add/edit entries here exactly as before if you
     prefer editing this file directly over the CLI.
  2. raid_cooldowns.generated.json -- built via
     `explore_raid_cooldowns.py` (scans a real log for candidates) and
     `manage_raid_cooldowns.py add/remove/list` (confirms/edits them
     without touching this file or needing a report).

Merged by ability_name: if the SAME ability_name appears in both,
MANUAL_COOLDOWNS wins (so you can always override a generated entry
by hand here, without needing to remove it from the JSON file first).
Names must match WCL's ability names exactly (case-sensitive) in
either source.
"""
from __future__ import annotations
from cooldown_analyzer import CooldownDefinition
from raid_cooldown_config_io import RaidCooldownConfigError, load_generated_raid_cooldowns
from raid_cooldown_schema import as_cooldown_definitions

MANUAL_COOLDOWNS: list[CooldownDefinition] = [
    CooldownDefinition("Revival", 180, category="raid_cd"),
    CooldownDefinition("Divine Hymn", 120, category="raid_cd"),
    CooldownDefinition("Tranquility", 180, category="raid_cd"),
    CooldownDefinition("Spirit Link Totem", 180, category="raid_cd"),
]

try:
    _generated_raid_cooldowns = load_generated_raid_cooldowns()
except RaidCooldownConfigError as exc:
    print(f"WARNING: raid_cooldowns.generated.json could not be read -- ignoring it.\n{exc}\n")
    _generated_raid_cooldowns = []

_by_name: dict[str, CooldownDefinition] = {
    cd.ability_name: cd for cd in as_cooldown_definitions(_generated_raid_cooldowns)
}
for _manual in MANUAL_COOLDOWNS:
    _by_name[_manual.ability_name] = _manual

TRACKED_COOLDOWNS: list[CooldownDefinition] = list(_by_name.values())
