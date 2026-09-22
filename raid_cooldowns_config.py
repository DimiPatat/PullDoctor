"""
raid_cooldowns_config.py

The cooldowns cooldown_analyzer.py should track for your raid. Edit
this list to match your actual roster's cooldown kit -- names must
match WCL's ability names exactly (case-sensitive).
"""
from __future__ import annotations

from cooldown_analyzer import CooldownDefinition

TRACKED_COOLDOWNS: list[CooldownDefinition] = [
    CooldownDefinition("Revival", 180, category="raid_cd"),
    CooldownDefinition("Divine Hymn", 120, category="raid_cd"),
    CooldownDefinition("Tranquility", 180, category="raid_cd"),
    CooldownDefinition("Spirit Link Totem", 180, category="raid_cd"),
]
