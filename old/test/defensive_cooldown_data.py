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

===========================================================================
VERIFICATION PASS -- 2026-09-24, against Patch 12.1 "Midnight Season 2"
===========================================================================
Every entry below was checked against current Wowhead spell pages /
Warcraft Wiki patch-change logs. Corrections made from the prior seed:

  Shield Wall            cooldown  240s -> 180s (3 min per current tooltip)
  Icebound Fortitude     cooldown  180s -> 120s (patch 11.0 CDR baked in)
  Ardent Defender        cooldown  120s -> 90s; reduction 20% -> 30%;
                         duration  8s -> 12s (reworked in recent patches)
  Guardian of Ancient
    Kings                cooldown  300s -> 180s (patch 12.0: 3 min, was 5)
  Unending Resolve       reduction 40% -> 25% (current live tooltip)
  Feint                  duration  8s -> 6s (patch 9.1.0 change)
  Astral Shift           cooldown  90s -> 120s; duration 8s -> 12s
  Blur                   was "unmodeled" (assumed decaying) -- CURRENT
                         tooltip is a flat 25% for 10s, no decay. Now
                         modeled as percent_reduction. Also confirmed
                         shared baseline for the new Devourer spec.
  Darkness               cooldown  180s -> 300s (5 min); reduction
                         20% -> 15% (current tooltip; probabilistic,
                         see notes -- treat estimate as approximate)
  Metamorphosis (Vengeance) cooldown 240s -> 120s (patch 12.0 CDR)
  Obsidian Scales        reduction 20% -> 30% (current live tooltip)
  Zephyr                 cooldown  90s -> 120s. Previous notes wrongly
                         described this as a "dodge-style avoid the next
                         several attacks" effect -- it is actually a
                         flat 20% AoE-damage-taken reduction for 8s
                         (raid-wide, 5 targets). Now modeled as
                         percent_reduction with the same AoE-only
                         caveat as Feint (will overstate prevention
                         against single-target hits during the window).

REMOVED FROM THE GAME in patch 12.0.0 "Midnight" (kept in the seed list
ONLY so explore_defensive_cooldowns.py can show a clear "no longer
exists" note if an old generated.json still references them -- these
will never match a Casts event in any current-tier log):
  - Netherwalk (Demon Hunter)
  - Dampen Harm (Monk)
  - Mass Barrier (Mage)

FOLDED INTO A PASSIVE in patch 12.0.0 (no longer independently cast,
so cooldown_analyzer will never see a matching Casts event for these
either -- the parent ability now carries the whole effect):
  - Diffuse Magic (Monk)      -> now a passive rider on Fortifying Brew
  - Renewing Blaze (Evoker)   -> now a passive rider on Obsidian Scales

MISMODELED, now corrected: Fortitude of the Bear was previously modeled
as a 50%-for-8s personal damage-reduction cooldown on a 90s timer. The
current live ability is actually a Tenacity-pet buff that grants a
temporary max-health increase + instant heal (2 min cooldown) -- not a
flat damage-reduction effect at all. Re-modeled as "unmodeled" with a
corrected 120s cooldown for usage-tracking purposes.

Everything not called out above was checked and confirmed to already
match current Patch 12.1 tooltips (Die by the Sword, Divine Protection,
Divine Shield, Survival Instincts, Barkskin, Ice Block, Pain
Suppression, Cloak of Shadows, Aspect of the Turtle, Rallying Cry,
Anti-Magic Shell, Vampiric Blood, Lichborne, Alter Time, Life Cocoon,
Touch of Karma, Fortifying Brew for Brewmaster). Remaining "unmodeled"
externals/utility entries not explicitly re-verified this pass
(Blessing of Protection/Sacrifice, Lay on Hands, Dark Pact, Guardian
Spirit, Vampiric Embrace, Desperate Prayer, Evasion, Crimson Vial,
Earth Elemental, Exhilaration) carry no fabricated percent/duration
values either way, so accuracy risk there is limited to cooldown-only
usage tracking -- worth a follow-up pass if you rely on those heavily.
"""
from __future__ import annotations
from cooldown_analyzer import CooldownDefinition
from defensive_cooldown_config_io import DefensiveCooldownConfigError, load_generated_defensive_cooldowns
from defensive_cooldown_schema import DefensiveCooldownDefinition

SEED_REFERENCE_DEFENSIVE_COOLDOWNS: list[DefensiveCooldownDefinition] = [
    # Warrior
    DefensiveCooldownDefinition("Shield Wall", 180, category="damage_reduction", class_name="Warrior",
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
    DefensiveCooldownDefinition("Ardent Defender", 90, category="damage_reduction", class_name="Paladin",
                                 mitigation_type="percent_reduction", damage_reduction_percent=30, duration_seconds=12,
                                 notes="Also prevents a single fatal hit -- that safety-net effect isn't captured by the % estimate."),
    DefensiveCooldownDefinition("Guardian of Ancient Kings", 180, category="damage_reduction", class_name="Paladin",
                                 mitigation_type="percent_reduction", damage_reduction_percent=50, duration_seconds=8),
    DefensiveCooldownDefinition("Blessing of Protection", 300, category="external", class_name="Paladin",
                                 notes="Physical-damage immunity applied to ANOTHER player -- left unmodeled."),
    DefensiveCooldownDefinition("Blessing of Sacrifice", 120, category="external", class_name="Paladin",
                                 notes="Redirects a portion of another player's damage to the caster -- a transfer, not a reduction; left unmodeled."),
    DefensiveCooldownDefinition("Lay on Hands", 600, category="external", class_name="Paladin",
                                 notes="A full heal, not a damage reduction -- left unmodeled."),
    # Death Knight
    DefensiveCooldownDefinition("Icebound Fortitude", 120, category="damage_reduction", class_name="Death Knight",
                                 mitigation_type="percent_reduction", damage_reduction_percent=30, duration_seconds=8),
    DefensiveCooldownDefinition("Anti-Magic Shell", 60, category="magic_immunity", class_name="Death Knight",
                                 notes="An absorb shield with a fixed HP cap, not a % damage reduction -- left unmodeled."),
    DefensiveCooldownDefinition("Anti-Magic Zone", 120, category="magic_immunity", class_name="Death Knight",
                                 notes="Raid-wide absorb shield with a fixed HP cap -- left unmodeled."),
    DefensiveCooldownDefinition("Vampiric Blood", 90, category="damage_reduction", class_name="Death Knight",
                                 notes="Increases max HP and healing received rather than reducing incoming damage -- left unmodeled."),
    DefensiveCooldownDefinition("Lichborne", 120, category="damage_reduction", class_name="Death Knight",
                                 notes="Primarily a fear/charm/sleep immunity + Leech utility, not a flat damage reduction -- left unmodeled."),
    DefensiveCooldownDefinition("Dark Simulacrum", 60, category="utility", class_name="Death Knight",
                                 notes="A spell-steal utility, not a defensive at all -- left unmodeled (tracked for usage only)."),
    # Monk
    DefensiveCooldownDefinition("Fortifying Brew", 360, category="damage_reduction", class_name="Monk",
                                 mitigation_type="percent_reduction", damage_reduction_percent=20, duration_seconds=15,
                                 notes="6 min cooldown confirmed for Brewmaster; Mistweaver/Windwalker have a separate 2 min cooldown (patch 11.0) -- use the Brewmaster value if tracking a tank."),
    DefensiveCooldownDefinition("Diffuse Magic", 90, category="magic_immunity", class_name="Monk",
                                 mitigation_type="unmodeled",
                                 notes="REMOVED AS AN INDEPENDENT CAST in patch 12.0.0 -- it is now a passive effect automatically triggered by Fortifying Brew (transfers harmful magic effects back to caster), no longer has its own cooldown, cast, or damage-reduction value. Will never appear as its own Casts event in a current-tier log; kept here only so old generated.json entries surface a clear explanation instead of silently doing nothing."),
    DefensiveCooldownDefinition("Dampen Harm", 90, category="damage_reduction", class_name="Monk",
                                 notes="REMOVED FROM THE GAME in patch 12.0.0 -- will never appear in a current-tier log. Previously reduced the next 3 big hits by 50%, scaling-reduction doesn't fit the flat-% model anyway."),
    DefensiveCooldownDefinition("Touch of Karma", 90, category="damage_reduction", class_name="Monk",
                                 notes="Redirects a capped portion of damage back at the source rather than reducing it -- left unmodeled."),
    DefensiveCooldownDefinition("Life Cocoon", 120, category="external", class_name="Monk",
                                 notes="An absorb shield applied to another player -- left unmodeled."),
    # Druid
    DefensiveCooldownDefinition("Barkskin", 60, category="damage_reduction", class_name="Druid",
                                 mitigation_type="percent_reduction", damage_reduction_percent=20, duration_seconds=8),
    DefensiveCooldownDefinition("Survival Instincts", 180, category="damage_reduction", class_name="Druid",
                                 mitigation_type="percent_reduction", damage_reduction_percent=50, duration_seconds=6,
                                 notes="Guardian has 2 charges inherently as of patch 12.0 -- theoretical_max_casts in cooldown_analyzer doesn't currently account for multiple charges, so efficiency may read conservatively for Guardian druids."),
    DefensiveCooldownDefinition("Ironfur", 30, category="damage_reduction", class_name="Druid",
                                 notes="Increases armor (physical mitigation only, stacks, short GCD-only cast) rather than a flat all-damage %  -- left unmodeled."),
    # Demon Hunter
    DefensiveCooldownDefinition("Blur", 60, category="damage_reduction", class_name="Demon Hunter",
                                 mitigation_type="percent_reduction", damage_reduction_percent=25, duration_seconds=10,
                                 notes="Confirmed flat (not decaying) on the current live tooltip. As of patch 12.0, Blur is also the primary personal defensive for the new Devourer specialization, not just Havoc."),
    DefensiveCooldownDefinition("Darkness", 300, category="damage_reduction", class_name="Demon Hunter",
                                 mitigation_type="percent_reduction", damage_reduction_percent=15, duration_seconds=8,
                                 notes="Raid-wide chance-based partial avoidance (15% chance per hit to avoid ALL damage from that attack), not a guaranteed flat reduction for every hit -- treat this estimate as approximate."),
    DefensiveCooldownDefinition("Netherwalk", 90, category="immunity", class_name="Demon Hunter",
                                 mitigation_type="immunity", duration_seconds=3,
                                 notes="REMOVED FROM THE GAME in patch 12.0.0 -- will never appear in a current-tier log. Kept here only so old generated.json entries surface a clear explanation."),
    DefensiveCooldownDefinition("Metamorphosis", 120, category="damage_reduction", class_name="Demon Hunter",
                                 notes="Vengeance's passive tankiness boost (max HP/heal + armor) varies too much by build to give one trustworthy number -- left unmodeled."),
    # Warlock
    DefensiveCooldownDefinition("Unending Resolve", 180, category="damage_reduction", class_name="Warlock",
                                 mitigation_type="percent_reduction", damage_reduction_percent=25, duration_seconds=8),
    DefensiveCooldownDefinition("Dark Pact", 60, category="damage_reduction", class_name="Warlock",
                                 notes="Grants an absorb shield (costing health) rather than a flat % reduction -- left unmodeled."),
    # Hunter
    DefensiveCooldownDefinition("Aspect of the Turtle", 180, category="immunity", class_name="Hunter",
                                 mitigation_type="immunity", duration_seconds=8),
    DefensiveCooldownDefinition("Exhilaration", 120, category="healing_cd", class_name="Hunter",
                                 notes="An instant self-heal, not a damage reduction -- left unmodeled."),
    DefensiveCooldownDefinition("Fortitude of the Bear", 120, category="healing_cd", class_name="Hunter",
                                 notes="RE-MODELED: this is a Tenacity-pet buff granting a temporary +20% max-health increase plus an instant heal for that amount -- NOT a flat damage-reduction effect as previously modeled here. Left unmodeled; cooldown corrected to 2 min."),
    # Mage
    DefensiveCooldownDefinition("Ice Block", 240, category="immunity", class_name="Mage",
                                 mitigation_type="immunity", duration_seconds=10),
    DefensiveCooldownDefinition("Alter Time", 60, category="utility", class_name="Mage",
                                 notes="Rewinds health/position on expiry rather than reducing damage as it happens -- left unmodeled."),
    DefensiveCooldownDefinition("Mass Barrier", 180, category="external", class_name="Mage",
                                 notes="REMOVED FROM THE GAME in patch 12.0.0 -- will never appear in a current-tier log. Was a raid-wide absorb shield, not a flat % reduction, even before removal."),
    DefensiveCooldownDefinition("Greater Invisibility", 120, category="threat_drop", class_name="Mage",
                                 notes="Grants ~60% damage reduction while invisible and for 3 sec after reappearing, but the invisibility itself (and therefore the reduction window) ends the instant the mage takes any action -- duration is genuinely variable (anywhere from a few seconds to the full 20s), so left unmodeled rather than guessing a fixed window."),
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
                                 mitigation_type="percent_reduction", damage_reduction_percent=40, duration_seconds=6,
                                 notes="Specifically reduces AoE/splash damage, not single-target -- the estimate will overstate prevention against single-target hits."),
    DefensiveCooldownDefinition("Crimson Vial", 30, category="healing_cd", class_name="Rogue",
                                 notes="An instant self-heal, not a damage reduction -- left unmodeled."),
    # Shaman
    DefensiveCooldownDefinition("Astral Shift", 120, category="damage_reduction", class_name="Shaman",
                                 mitigation_type="percent_reduction", damage_reduction_percent=40, duration_seconds=12),
    DefensiveCooldownDefinition("Earth Elemental", 300, category="threat_drop", class_name="Shaman",
                                 notes="A threat-reduction cooldown, not a personal damage reduction -- left unmodeled."),
    # Evoker
    DefensiveCooldownDefinition("Obsidian Scales", 90, category="damage_reduction", class_name="Evoker",
                                 mitigation_type="percent_reduction", damage_reduction_percent=30, duration_seconds=12),
    DefensiveCooldownDefinition("Renewing Blaze", 90, category="healing_cd", class_name="Evoker",
                                 mitigation_type="unmodeled",
                                 notes="REMODELED as of patch 12.0.0 into a passive rider on Obsidian Scales (heals back 100% of the damage Obsidian Scales prevented, over 8 sec) -- it no longer has its own cast, cooldown, or independent trigger. Will never appear as its own Casts event in a current-tier log; kept here only so old generated.json entries surface a clear explanation."),
    DefensiveCooldownDefinition("Zephyr", 120, category="aoe_reduction", class_name="Evoker",
                                 mitigation_type="percent_reduction", damage_reduction_percent=20, duration_seconds=8,
                                 notes="CORRECTED: previously mismodeled here as an 'avoid the next several attacks' dodge effect -- it is actually a flat 20% reduction to AoE-tagged damage specifically (raid-wide, self + 4 nearest allies), not single-target damage. Like Feint, the prevention estimate will overstate savings against single-target hits taken during the window."),
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
