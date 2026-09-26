"""
fight_filters.py

Filters a report's RAW fight list (from wcl_api.get_report_fights(),
which is one cheap call regardless of how many fights the report has)
down to just the fights worth spending further API budget on -- BEFORE
any per-fight event data is fetched. This is the entire point: every
fight you skip here is one full set of Casts/Healing/DamageTaken/
DamageDone/Deaths/CombatantInfo/Debuffs queries you never have to make.

Two independent criteria, both opt-out-able:

  1. only_boss_fights -- Warcraft Logs marks every trash pull with
     encounterID == 0; a real boss pull always has a non-zero
     encounterID identifying which boss it was. Trash pulls carry
     essentially zero analytical value for this project (no consumable
     coverage worth checking, no avoidable-mechanic config exists for
     "trash", cooldown usage against trash isn't meaningful) -- so
     they're excluded by default.

  2. min_duration_seconds -- filters out near-instant wipes/resets
     (e.g. someone pulls early, tank dies in 3 seconds, raid resets).
     These ARE real boss pulls (non-zero encounterID) but are too short
     to contain anything worth analyzing, and reliably show up as
     "everyone missing every consumable/cooldown" noise if included.
     Default is 15 seconds, matching the threshold requested when this
     module was built, but fully overridable per call.

Both filters operate on the RAW dict shape returned by
wcl_api.get_report_fights() (keys: id, name, difficulty, kill,
startTime, endTime, encounterID, friendlyPlayers) -- deliberately BEFORE
log_parser.py's Fight dataclass exists for these entries, since the
whole point is to decide which fights are even worth building a Fight/
ParsedFight for in the first place.

Pure function over already-fetched data -- no network calls.
"""
from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_MIN_DURATION_SECONDS = 15.0


@dataclass
class FightFilterCriteria:
    only_boss_fights: bool = True
    min_duration_seconds: float = DEFAULT_MIN_DURATION_SECONDS

    @property
    def is_a_no_op(self) -> bool:
        """True if this criteria would let every fight through unchanged -- useful for skipping filter-summary output entirely."""
        return not self.only_boss_fights and self.min_duration_seconds <= 0


@dataclass
class SkippedFight:
    fight_id: int
    name: str
    reason: str  # "trash" or "too_short"
    duration_seconds: float


@dataclass
class FightFilterResult:
    kept: list[dict] = field(default_factory=list)
    skipped: list[SkippedFight] = field(default_factory=list)

    @property
    def skipped_trash_count(self) -> int:
        return sum(1 for s in self.skipped if s.reason == "trash")

    @property
    def skipped_too_short_count(self) -> int:
        return sum(1 for s in self.skipped if s.reason == "too_short")


def filter_fights(raw_fights: list[dict], criteria: FightFilterCriteria) -> FightFilterResult:
    """
    Apply criteria to a report's raw fight list. A fight failing BOTH
    checks is recorded under "trash" (checked first) rather than
    double-counted -- in practice trash pulls are also usually short,
    so this avoids a fight showing up as "skipped for two reasons" in
    the summary.
    """
    result = FightFilterResult()
    for raw_fight in raw_fights:
        encounter_id = raw_fight.get("encounterID", 0) or 0
        duration_seconds = (raw_fight.get("endTime", 0) - raw_fight.get("startTime", 0)) / 1000

        if criteria.only_boss_fights and encounter_id == 0:
            result.skipped.append(SkippedFight(
                fight_id=raw_fight["id"], name=raw_fight.get("name", "Unknown"),
                reason="trash", duration_seconds=duration_seconds,
            ))
            continue

        if duration_seconds < criteria.min_duration_seconds:
            result.skipped.append(SkippedFight(
                fight_id=raw_fight["id"], name=raw_fight.get("name", "Unknown"),
                reason="too_short", duration_seconds=duration_seconds,
            ))
            continue

        result.kept.append(raw_fight)

    return result


def format_filter_summary(result: FightFilterResult, criteria: FightFilterCriteria) -> str:
    """Human-readable summary of what was kept/skipped and why -- shown before any expensive per-fight fetching begins."""
    total = len(result.kept) + len(result.skipped)
    if not result.skipped:
        return f"{len(result.kept)}/{total} fight(s) will be fetched (no fights filtered out)."

    lines = [f"{len(result.kept)}/{total} fight(s) will be fetched (API budget saved on {len(result.skipped)} skipped):"]
    if result.skipped_trash_count:
        lines.append(f"  {result.skipped_trash_count} trash pull(s) skipped (encounterID == 0)")
    if result.skipped_too_short_count:
        lines.append(
            f"  {result.skipped_too_short_count} short boss pull(s) skipped "
            f"(< {criteria.min_duration_seconds:.0f}s -- likely an early reset/wipe)"
        )
    return "\n".join(lines)
