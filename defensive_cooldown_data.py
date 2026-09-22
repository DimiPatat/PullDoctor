"""
defensive_cooldown_data.py

SEED_REFERENCE_DEFENSIVE_COOLDOWNS is used only by
defensive_cooldown_explorer.py to auto-detect known abilities during
"add" -- never fed directly into the analyzer. The ACTUAL tracked list
is loaded from defensive_cooldowns.generated.json, exposed as
TRACKED_DEFENSIVE_COOLDOWNS. as_cooldown_definitions() converts that
list into plain cooldown_analyzer.CooldownDefinition objects -- so
defensive cooldowns reuse the EXACT SAME usage/efficiency engine as
raid cooldowns, no separate analyzer needed for THAT part.

mitigation_type legend (see defensive_cooldown_schema.py for the full
rationale): "percent_reduction" = flat known % reduction, gets a
back-calculated "damage prevented" estimate via
defensive_damage_prevention_analyzer.py. "immunity" = ~100% negation,
reports residual damage but never a fabricated "prevented" number.
"unmodeled" (default) = absorb shields, heals, avoidance, redirects, or
anything too variable to model as a flat percentage -- excluded
entirely from damage-prevention estimates, still tracked for plain
usage/efficiency.

damage_reduction_percent / duration_seconds values below are
reasonable MODERN-RETAIL baselines (no talent/covenant/legendary
adjustments) -- they vary by spec, talent choices, and patch. Treat
these as a starting point to verify/correct via
explore_defensive_cooldowns.py's "add" flow, not ground truth.
"""
from __future__ import annotations

from cooldown_analyzer import CooldownDefinition
from defensive_cooldown_config_io import DefensiveCooldownConfigError, load_generated_defensive_cooldowns
from defensive_cooldown_schema import DefensiveCooldownDefinition

SEED_REFERENCE_DEFENSIVE_COOLDOWNS: list[DefensiveCooldownDefinition] = [
    # Warrior
    DefensiveCooldownDefinition("Shield Wall", 240, category="damage_reduction", class_name="Warrior",
                                 mitigation_type="percent_reduction", damage_reduction_percent=40, duration_seconds=8),
    DefensiveCooldownDefinition("Die by the Sword", 120, category="damage_reduction", class_name="Warrior",
                                 mitigation_type="percent_reduction", damage_reduction_percent=30, duration_seconds=8),
    DefensiveCooldownDefinition("Spell Reflection", 25, category="immunity", class_name="Warrior",
                                 notes="Reflects the next single spell rather than reducing damage over a window -- doesn't fit the flat-% model, left unmodeled."),
    DefensiveCooldownDefinition("Rallying Cry", 180, category="external", class_name="Warrior",
                                 notes="Raises max HP raid-wide rather than reducing incoming damage -- left unmodeled."),

    # Paladin
    DefensiveCooldownDefinition("Divine Shield", 300, category="immunity", class_name="Paladin",
                                 mitigation_type="immunity", duration_seconds=8),
    DefensiveCooldownDefinition("Divine Protection", 60, category="damage_reduction", class_name="Paladin",
                                 mitigation_type="percent_reduction", damage_reduction_percent=20, duration_seconds=8),
    DefensiveCooldownDefinition("Ardent Defender", 120, category="damage_reduction", class_name="Paladin",
                                 mitigation_type="percent_reduction", damage_reduction_percent=20, duration_seconds=8,
                                 notes="Also prevents a single fatal hit -- that safety-net effect isn't captured by the % estimate."),
    DefensiveCooldownDefinition("Guardian of Ancient Kings", 300, category="damage_reduction", class_name="Paladin",
                                 mitigation_type="percent_reduction", damage_reduction_percent=50, duration_seconds=8),
    DefensiveCooldownDefinition("Blessing of Protection", 300, category="external", class_name="Paladin",
                                 notes="Physical-damage immunity applied to ANOTHER player -- left unmodeled."),
    DefensiveCooldownDefinition("Blessing of Sacrifice", 120, category="external", class_name="Paladin",
                                 notes="Redirects a portion of another player's damage to the caster -- a transfer, not a reduction; left unmodeled."),
    DefensiveCooldownDefinition("Lay on Hands", 600, category="external", class_name="Paladin",
                                 notes="A full heal, not a damage reduction -- left unmodeled."),

    # Death Knight
    DefensiveCooldownDefinition("Icebound Fortitude", 180, category="damage_reduction", class_name="Death Knight",
                                 mitigation_type="percent_reduction", damage_reduction_percent=30, duration_seconds=8),
    DefensiveCooldownDefinition("Anti-Magic Shell", 60, category="magic_immunity", class_name="Death Knight",
                                 notes="An absorb shield with a fixed HP cap, not a % damage reduction -- left unmodeled."),
    DefensiveCooldownDefinition("Anti-Magic Zone", 120, category="magic_immunity", class_name="Death Knight",
                                 notes="Raid-wide absorb shield with a fixed HP cap -- left unmodeled."),
    DefensiveCooldownDefinition("Vampiric Blood", 90, category="damage_reduction", class_name="Death Knight",
                                 notes="Increases max HP and healing received rather than reducing incoming damage -- left unmodeled."),
    DefensiveCooldownDefinition("Lichborne", 120, category="damage_reduction", class_name="Death Knight",
                                 notes="Primarily a fear immunity/self-heal utility, not a flat damage reduction -- left unmodeled."),
    DefensiveCooldownDefinition("Dark Simulacrum", 60, category="utility", class_name="Death Knight",
                                 notes="A spell-steal utility, not a defensive at all -- left unmodeled (tracked for usage only)."),

    # Monk
    DefensiveCooldownDefinition("Fortifying Brew", 360, category="damage_reduction", class_name="Monk",
                                 mitigation_type="percent_reduction", damage_reduction_percent=20, duration_seconds=15),
    DefensiveCooldownDefinition("Diffuse Magic", 90, category="magic_immunity", class_name="Monk",
                                 mitigation_type="percent_reduction", damage_reduction_percent=60, duration_seconds=6,
                                 notes="Also reflects a portion of damage taken back at the source -- that reflected-damage effect isn't captured by the % estimate."),
    DefensiveCooldownDefinition("Dampen Harm", 120, category="damage_reduction", class_name="Monk",
                                 notes="Reduction scales with hit SIZE (bigger hits reduced more) rather than being flat -- doesn't fit the flat-% model, left unmodeled."),
    DefensiveCooldownDefinition("Touch of Karma", 90, category="damage_reduction", class_name="Monk",
                                 notes="Redirects a capped portion of damage back at the source rather than reducing it -- left unmodeled."),
    DefensiveCooldownDefinition("Life Cocoon", 120, category="external", class_name="Monk",
                                 notes="An absorb shield applied to another player -- left unmodeled."),

    # Druid
    DefensiveCooldownDefinition("Barkskin", 60, category="damage_reduction", class_name="Druid",
                                 mitigation_type="percent_reduction", damage_reduction_percent=20, duration_seconds=8),
    DefensiveCooldownDefinition("Survival Instincts", 180, category="damage_reduction", class_name="Druid",
                                 mitigation_type="percent_reduction", damage_reduction_percent=30, duration_seconds=6),
    DefensiveCooldownDefinition("Ironfur", 30, category="damage_reduction", class_name="Druid",
                                 notes="Increases armor (physical mitigation only, stacks, short GCD-only cast) rather than a flat all-damage %  -- left unmodeled."),

    # Demon Hunter
    DefensiveCooldownDefinition("Blur", 60, category="damage_reduction", class_name="Demon Hunter",
                                 notes="Reduction decays over the duration rather than staying flat -- doesn't fit the flat-% model, left unmodeled."),
    DefensiveCooldownDefinition("Darkness", 180, category="damage_reduction", class_name="Demon Hunter",
                                 mitigation_type="percent_reduction", damage_reduction_percent=20, duration_seconds=8,
                                 notes="Raid-wide chance-based partial avoidance, not a guaranteed flat reduction for every hit -- treat this estimate as approximate."),
    DefensiveCooldownDefinition("Netherwalk", 90, category="immunity", class_name="Demon Hunter",
                                 mitigation_type="immunity", duration_seconds=3),
    DefensiveCooldownDefinition("Metamorphosis", 240, category="damage_reduction", class_name="Demon Hunter",
                                 notes="Vengeance's passive tankiness boost varies too much by build to give one trustworthy number -- left unmodeled."),

    # Warlock
    DefensiveCooldownDefinition("Unending Resolve", 180, category="damage_reduction", class_name="Warlock",
                                 mitigation_type="percent_reduction", damage_reduction_percent=40, duration_seconds=8),
    DefensiveCooldownDefinition("Dark Pact", 60, category="damage_reduction", class_name="Warlock",
                                 notes="Grants an absorb shield (costing health) rather than a flat % reduction -- left unmodeled."),

    # Hunter
    DefensiveCooldownDefinition("Aspect of the Turtle", 180, category="immunity", class_name="Hunter",
                                 mitigation_type="immunity", duration_seconds=8),
    DefensiveCooldownDefinition("Exhilaration", 120, category="healing_cd", class_name="Hunter",
                                 notes="An instant self-heal, not a damage reduction -- left unmodeled."),
    DefensiveCooldownDefinition("Fortitude of the Bear", 90, category="damage_reduction", class_name="Hunter",
                                 mitigation_type="percent_reduction", damage_reduction_percent=50, duration_seconds=8),

    # Mage
    DefensiveCooldownDefinition("Ice Block", 240, category="immunity", class_name="Mage",
                                 mitigation_type="immunity", duration_seconds=10),
    DefensiveCooldownDefinition("Alter Time", 60, category="utility", class_name="Mage",
                                 notes="Rewinds health/position on expiry rather than reducing damage as it happens -- left unmodeled."),
    DefensiveCooldownDefinition("Mass Barrier", 180, category="external", class_name="Mage",
                                 notes="A raid-wide absorb shield, not a flat % reduction -- left unmodeled."),
    DefensiveCooldownDefinition("Greater Invisibility", 90, category="threat_drop", class_name="Mage",
                                 mitigation_type="percent_reduction", damage_reduction_percent=90, duration_seconds=3,
                                 notes="The 90% reduction only applies briefly before fading/stealth -- treat the duration as approximate."),

    # Priest
    DefensiveCooldownDefinition("Power Word: Shield", 0, category="self_shield", class_name="Priest",
                                 notes="An absorb shield, not a flat % reduction; also has no real cooldown (short GCD-only cast) -- left unmodeled."),
    DefensiveCooldownDefinition("Desperate Prayer", 90, category="damage_reduction", class_name="Priest",
                                 notes="An instant self-heal, not a damage reduction -- left unmodeled."),
    DefensiveCooldownDefinition("Pain Suppression", 180, category="external", class_name="Priest",
                                 mitigation_type="percent_reduction", damage_reduction_percent=40, duration_seconds=8,
                                 notes="Applied to ANOTHER player -- this tool tracks the caster's cast usage, not the recipient's damage window."),
    DefensiveCooldownDefinition("Guardian Spirit", 180, category="external", class_name="Priest",
                                 notes="Increases healing received and prevents a single fatal hit on ANOTHER player, rather than a flat % reduction -- left unmodeled."),
    DefensiveCooldownDefinition("Vampiric Embrace", 90, category="external", class_name="Priest",
                                 notes="A healing-conversion effect, not a damage reduction -- left unmodeled."),

    # Rogue
    DefensiveCooldownDefinition("Cloak of Shadows", 120, category="magic_immunity", class_name="Rogue",
                                 mitigation_type="immunity", duration_seconds=5,
                                 notes="Removes/immunes magic effects specifically, not all damage -- residual PHYSICAL damage during the window is expected and normal."),
    DefensiveCooldownDefinition("Evasion", 120, category="damage_reduction", class_name="Rogue",
                                 notes="A dodge-CHANCE increase (probabilistic per-hit avoidance), not a guaranteed flat reduction -- doesn't fit the model, left unmodeled."),
    DefensiveCooldownDefinition("Feint", 15, category="aoe_reduction", class_name="Rogue",
                                 mitigation_type="percent_reduction", damage_reduction_percent=40, duration_seconds=8,
                                 notes="Specifically reduces AoE/splash damage, not single-target -- the estimate will overstate prevention against single-target hits."),
    DefensiveCooldownDefinition("Crimson Vial", 30, category="healing_cd", class_name="Rogue",
                                 notes="An instant self-heal, not a damage reduction -- left unmodeled."),

    # Shaman
    DefensiveCooldownDefinition("Astral Shift", 90, category="damage_reduction", class_name="Shaman",
                                 mitigation_type="percent_reduction", damage_reduction_percent=40, duration_seconds=8),
    DefensiveCooldownDefinition("Earth Elemental", 300, category="threat_drop", class_name="Shaman",
                                 notes="A threat-reduction cooldown, not a personal damage reduction -- left unmodeled."),

    # Evoker
    DefensiveCooldownDefinition("Obsidian Scales", 90, category="damage_reduction", class_name="Evoker",
                                 mitigation_type="percent_reduction", damage_reduction_percent=20, duration_seconds=12),
    DefensiveCooldownDefinition("Renewing Blaze", 90, category="healing_cd", class_name="Evoker",
                                 notes="A heal-over-time effect, not a damage reduction -- left unmodeled."),
    DefensiveCooldownDefinition("Zephyr", 90, category="damage_reduction", class_name="Evoker",
                                 notes="Avoids the next several attacks entirely (a dodge-style effect) rather than a flat % reduction -- left unmodeled."),
]

MANUAL_DEFENSIVE_COOLDOWNS: list[DefensiveCooldownDefinition] = []

try:
    _generated_defensive_cooldowns = load_generated_defensive_cooldowns()
except DefensiveCooldownConfigError as exc:
    print(f"WARNING: defensive_cooldowns.generated.json could not be read -- ignoring it.\n{exc}\n")
    _generated_defensive_cooldowns = []

_by_name: dict[str, DefensiveCooldownDefinition] = {d.ability_name: d for d in _generated_defensive_cooldowns}
for _manual in MANUAL_DEFENSIVE_COOLDOWNS:
    _by_name[_manual.ability_name] = _manual
TRACKED_DEFENSIVE_COOLDOWNS: list[DefensiveCooldownDefinition] = list(_by_name.values())


def as_cooldown_definitions(definitions: list[DefensiveCooldownDefinition] | None = None) -> list[CooldownDefinition]:
    """Convert tracked DefensiveCooldownDefinition objects into plain CooldownDefinition -- reuses cooldown_analyzer.py as-is."""
    source = definitions if definitions is not None else TRACKED_DEFENSIVE_COOLDOWNS
    return [
        CooldownDefinition(ability_name=d.ability_name, cooldown_seconds=d.cooldown_seconds, category=d.category)
        for d in source
    ]
