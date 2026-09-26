"""
raid_cooldown_explorer.py
Scans a REAL parsed fight's Casts events to find likely raid-cooldown
candidates, since this app has no static spell database to verify
cooldown lengths against (same "verify before trusting" philosophy
already used for defensive cooldowns and tier sets). Mirrors the
discovery pattern in defensive_cooldown_explorer.py: flag candidates
for a human to confirm/add, never auto-add anything.

HEURISTIC: flag any Casts event whose ability name matches a broad,
keyword-based "sounds like a raid cooldown" pattern (covers common raid
utility CDs like Bloodlust/Heroism-family effects, and common raid-wide
healing CDs like Tranquility/Rapture/Revival/hymn/barrier-style
effects) AND isn't already in the tracked list. This is a DISCOVERY
heuristic only -- it doesn't verify or assert the actual cooldown
length of anything it finds; that's why every candidate below reports
an OBSERVED minimum gap between casts (when the same player cast it
2+ times in the scanned fight) rather than a claimed true cooldown.
An observed gap is a LOWER BOUND on the real cooldown at best (haste/
talents/legendaries can only ever shorten an observed gap versus the
spell's base cooldown, never lengthen it) -- always cross-check against
a real spell reference before committing a cooldown_seconds value via
manage_raid_cooldowns.py.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field

_RAID_COOLDOWN_KEYWORDS = re.compile(
    r"(bloodlust|heroism|time warp|ancient hysteria|fury of the aspects|"
    r"tranquility|revival|rapture|hymn|barrier|aura mastery|spirit link|"
    r"darkness|anti-magic zone|rallying cry|reincarnation|stampeding roar|"
    r"ancestral protection|guardian spirit|pain suppression|"
    r"power word|devouring plague|life cocoon|zephyr|vast oblivion|"
    r"mass barrier|blessing of|hand of|aegis|bulwark|sanctuary)",
    re.IGNORECASE,
)


@dataclass
class CandidateRaidCooldown:
    ability_name: str
    total_casts: int
    caster_names: set[str] = field(default_factory=set)
    caster_classes: set[str] = field(default_factory=set)
    observed_min_gap_seconds: float | None = None  # None if never cast twice by the same player


def discover_candidate_raid_cooldowns(
    parsed_fight, already_tracked_names: set[str] | None = None
) -> list[CandidateRaidCooldown]:
    """
    Scan one parsed fight's Casts events for raid-cooldown-sounding
    abilities not already tracked. Returns candidates sorted by
    total_casts descending (abilities seen more often are usually
    easier to confirm/verify).
    already_tracked_names: ability names to skip (exact match) --
    pass in whatever manage_raid_cooldowns.py currently has tracked so
    re-running discovery on a new log doesn't keep re-flagging
    abilities you've already confirmed.
    """
    already_tracked_names = already_tracked_names or set()
    by_name: dict[str, CandidateRaidCooldown] = {}
    # ability_name -> caster_name -> sorted list of cast timestamps,
    # used only to compute the observed minimum gap per ability.
    timestamps_by_name_caster: dict[str, dict[str, list[int]]] = {}

    for event in parsed_fight.events:
        if event.data_type != "Casts":
            continue
        ability_name = event.ability_name
        if not ability_name or ability_name in already_tracked_names:
            continue
        if not _RAID_COOLDOWN_KEYWORDS.search(ability_name):
            continue

        actor = parsed_fight.actors.get(event.source_id)
        caster_name = actor.name if actor is not None else (event.source_name or "Unknown")
        caster_class = getattr(actor, "subtype", None) if actor is not None else None

        if ability_name not in by_name:
            by_name[ability_name] = CandidateRaidCooldown(ability_name=ability_name, total_casts=0)
        candidate = by_name[ability_name]
        candidate.total_casts += 1
        candidate.caster_names.add(caster_name)
        if caster_class:
            candidate.caster_classes.add(caster_class)

        timestamps_by_name_caster.setdefault(ability_name, {}).setdefault(caster_name, []).append(event.timestamp)

    for ability_name, candidate in by_name.items():
        min_gap = None
        for caster_name, timestamps in timestamps_by_name_caster[ability_name].items():
            timestamps.sort()
            for a, b in zip(timestamps, timestamps[1:]):
                gap_seconds = (b - a) / 1000.0
                if min_gap is None or gap_seconds < min_gap:
                    min_gap = gap_seconds
        candidate.observed_min_gap_seconds = min_gap

    return sorted(by_name.values(), key=lambda c: -c.total_casts)


def format_candidates(candidates: list[CandidateRaidCooldown]) -> str:
    if not candidates:
        return "No raid-cooldown candidates found in this fight (or everything found is already tracked)."
    lines = [f"Found {len(candidates)} candidate raid cooldown(s):"]
    for c in candidates:
        classes = ", ".join(sorted(c.caster_classes)) or "unknown class"
        casters = ", ".join(sorted(c.caster_names))
        gap_str = f"{c.observed_min_gap_seconds:.0f}s (OBSERVED minimum gap -- a lower bound, not a confirmed cooldown)" \
            if c.observed_min_gap_seconds is not None \
            else "n/a (only cast once in this fight -- can't infer a gap)"
        lines.append(
            f"  {c.ability_name:<28} casts={c.total_casts:<3} class(es)=[{classes}] "
            f"cast by: {casters}\n"
            f"    observed min gap: {gap_str}"
        )
    lines.append(
        "\nVerify the TRUE base cooldown against a real spell reference before adding --\n"
        "the observed gap above can only ever UNDERSTATE the real cooldown (talents/haste\n"
        "shorten it, never lengthen it).\n"
        "Run `python manage_raid_cooldowns.py add` to confirm and start tracking any of these."
    )
    return "\n".join(lines)
