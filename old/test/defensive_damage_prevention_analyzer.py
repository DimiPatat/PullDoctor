"""
defensive_damage_prevention_analyzer.py

Estimates how much damage was actually PREVENTED, per player, per use
of a tracked defensive cooldown -- not just whether/how-often it was
used (that's cooldown_analyzer.py's job; this module builds on top of
it, using the same Casts events, but adds the damage-taken math).

THE CORE PROBLEM THIS SOLVES: Warcraft Logs only ever logs damage AFTER
mitigation has already been applied. If a player is standing behind a
30% damage reduction and takes a hit logged as 100 damage, that 100 is
ALREADY the reduced number -- the hit that would have landed with no
defensive up was 100 / (1 - 0.30) = ~142.9, meaning ~42.9 damage was
prevented, NOT 100 * 0.30 = 30 (a common but wrong first guess). This
module does the correct back-calculation:

    original_hit  = actual_hit / (1 - reduction_fraction)
    prevented     = original_hit - actual_hit
                  = actual_hit * (reduction_percent / (100 - reduction_percent))

For each cast of a tracked "percent_reduction" defensive, this module:
  1. Opens a window from the cast timestamp to (cast timestamp +
     duration_seconds).
  2. Sums every DamageTaken event on that player within the window --
     this is the damage they ACTUALLY took while the effect was active.
  3. Back-calculates how much MORE damage would have landed with no
     defensive active, using the formula above.
  4. Reports both numbers per window, so you can see "took 4,200
     damage during this Shield Wall, ~2,800 of which was prevented"
     for every single use, plus a per-player total across every window
     they used ANY tracked percent_reduction defensive.

"immunity"-type defensives (Ice Block, Divine Shield, etc.) are handled
separately and CANNOT get a "prevented" estimate -- there's no
percentage to back-calculate from. Any damage a player still takes
during an immunity window is reported as "residual damage" instead,
with no fabricated prevented number attached.

"unmodeled" defensives (absorb shields, heals, avoidance, redirects --
see defensive_cooldown_schema.py's docstring for the full list and
reasoning) are skipped entirely by this module. They're still fully
tracked for plain usage/efficiency via cooldown_analyzer.py; this
module only adds an estimate on top for the subset where a flat-%
model is actually a reasonable fit.

Pure function over a ParsedFight -- no network calls.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from data_models import ParsedFight
from defensive_cooldown_schema import DefensiveCooldownDefinition
import roster


@dataclass
class DefensiveWindow:
    """One single use of a tracked defensive: the cast, its window, and what happened during it."""
    player_id: int
    player_name: str
    ability_name: str
    mitigation_type: str  # "percent_reduction" or "immunity" -- this module never builds one for "unmodeled"
    cast_timestamp: int
    window_end_timestamp: int
    damage_reduction_percent: float | None  # only set for "percent_reduction"
    actual_damage_taken: int  # sum of DamageTaken events on this player within the window (already-mitigated)
    damage_prevented: int | None = None  # only populated for "percent_reduction"

    @property
    def window_duration_seconds(self) -> float:
        return (self.window_end_timestamp - self.cast_timestamp) / 1000

    @property
    def estimated_damage_without_defensive(self) -> int | None:
        """actual_damage_taken + damage_prevented -- None if damage_prevented couldn't be estimated (immunity)."""
        if self.damage_prevented is None:
            return None
        return self.actual_damage_taken + self.damage_prevented


@dataclass
class PlayerDamagePrevention:
    """One player's defensive usage rolled up: every window, plus running totals."""
    player_id: int
    player_name: str
    windows: list[DefensiveWindow] = field(default_factory=list)

    @property
    def total_damage_prevented(self) -> int:
        """Sums only windows where a prevented estimate exists (percent_reduction) -- immunity windows contribute 0 here, not an unknown/error."""
        return sum(w.damage_prevented for w in self.windows if w.damage_prevented is not None)

    @property
    def total_actual_damage_taken_during_windows(self) -> int:
        return sum(w.actual_damage_taken for w in self.windows)

    @property
    def windows_with_estimate(self) -> list[DefensiveWindow]:
        """Only the percent_reduction windows -- the ones with a real damage_prevented number."""
        return [w for w in self.windows if w.damage_prevented is not None]

    @property
    def immunity_windows(self) -> list[DefensiveWindow]:
        return [w for w in self.windows if w.mitigation_type == "immunity"]


def _calculate_prevented(actual_damage: int, reduction_percent: float) -> int:
    """
    actual_damage is the ALREADY-REDUCED number WCL logged. Returns how
    much MORE damage would have landed at 0% reduction. Guards against
    reduction_percent >= 100 (would be a division by zero / undefined,
    and shouldn't happen for a well-formed "percent_reduction" entry --
    100% belongs in mitigation_type="immunity" instead) by returning 0
    rather than raising, since a config mistake here shouldn't crash a
    whole report.
    """
    if reduction_percent <= 0 or reduction_percent >= 100:
        return 0
    return round(actual_damage * (reduction_percent / (100 - reduction_percent)))


def analyze_damage_prevention(
    parsed_fight: ParsedFight,
    definitions: list[DefensiveCooldownDefinition],
) -> list[PlayerDamagePrevention]:
    """
    Build a PlayerDamagePrevention per player who cast at least one
    "percent_reduction" or "immunity" defensive with a known
    duration_seconds. Definitions that are "unmodeled", or missing a
    duration_seconds, are silently skipped for THIS analysis (they're
    still tracked fine by cooldown_analyzer.py for plain usage/
    efficiency -- this module only adds the extra damage-prevention
    layer on top for the subset where it's meaningful).
    """
    eligible = {
        d.ability_name: d
        for d in definitions
        if d.mitigation_type in ("percent_reduction", "immunity") and d.duration_seconds is not None
    }
    if not eligible:
        return []

    results: dict[int, PlayerDamagePrevention] = {}

    for event in parsed_fight.events:
        if event.data_type != "Casts" or event.ability_name not in eligible:
            continue
        player = roster.resolve_to_player(event.source_id, parsed_fight.actors)
        if player is None:
            continue

        definition = eligible[event.ability_name]
        window_start = event.timestamp
        window_end = event.timestamp + int(definition.duration_seconds * 1000)

        actual_damage = sum(
            e.amount or 0
            for e in parsed_fight.events
            if e.data_type == "DamageTaken"
            and e.target_id == player.id
            and window_start <= e.timestamp <= window_end
        )

        damage_prevented = None
        if definition.mitigation_type == "percent_reduction" and definition.damage_reduction_percent is not None:
            damage_prevented = _calculate_prevented(actual_damage, definition.damage_reduction_percent)

        window = DefensiveWindow(
            player_id=player.id, player_name=player.name, ability_name=definition.ability_name,
            mitigation_type=definition.mitigation_type, cast_timestamp=window_start, window_end_timestamp=window_end,
            damage_reduction_percent=definition.damage_reduction_percent,
            actual_damage_taken=actual_damage, damage_prevented=damage_prevented,
        )

        if player.id not in results:
            results[player.id] = PlayerDamagePrevention(player_id=player.id, player_name=player.name)
        results[player.id].windows.append(window)

    for entry in results.values():
        entry.windows.sort(key=lambda w: w.cast_timestamp)

    return sorted(results.values(), key=lambda p: p.total_damage_prevented, reverse=True)


def get_player_prevention(
    entries: list[PlayerDamagePrevention], player_name: str
) -> PlayerDamagePrevention | None:
    """Convenience lookup: find one player's entry by name (case-insensitive)."""
    for entry in entries:
        if entry.player_name and entry.player_name.lower() == player_name.lower():
            return entry
    return None


def format_damage_prevention_table(entries: list[PlayerDamagePrevention]) -> str:
    """Render a plain-text overview: one row per player, ranked by total damage prevented descending."""
    if not entries:
        return "No damage-prevention data available (no tracked percent_reduction/immunity defensives were used, or none are configured with a known duration)."
    header = f"{'Player':<15} {'Prevented':>12} {'Dmg Taken (windows)':>20} {'Windows':>8}"
    lines = ["Damage prevented by defensive cooldowns (ranked by total prevented):", header, "-" * len(header)]
    for entry in entries:
        lines.append(
            f"{entry.player_name[:15]:<15} {entry.total_damage_prevented:>12,} "
            f"{entry.total_actual_damage_taken_during_windows:>20,} {len(entry.windows):>8}"
        )
    return "\n".join(lines)


def format_player_windows_detail(entry: PlayerDamagePrevention) -> str:
    """Render one player's every single defensive-use window, in chronological order."""
    if not entry.windows:
        return f"{entry.player_name}: no tracked defensive windows this fight."
    lines = [f"{entry.player_name} -- {len(entry.windows)} defensive window(s), {entry.total_damage_prevented:,} total damage prevented:"]
    for window in entry.windows:
        seconds_in = window.cast_timestamp / 1000
        if window.mitigation_type == "immunity":
            lines.append(
                f"  {seconds_in:>6.1f}s  {window.ability_name:<24} (immunity, {window.window_duration_seconds:.0f}s window) -- "
                f"residual damage taken: {window.actual_damage_taken:,} (no 'prevented' estimate possible for immunity -- see module notes)"
            )
        else:
            lines.append(
                f"  {seconds_in:>6.1f}s  {window.ability_name:<24} "
                f"({window.damage_reduction_percent:.0f}% for {window.window_duration_seconds:.0f}s) -- "
                f"took {window.actual_damage_taken:,}, prevented ~{window.damage_prevented:,} "
                f"(would-have-been ~{window.estimated_damage_without_defensive:,})"
            )
    return "\n".join(lines)


def summarize_damage_prevention(parsed_fight: ParsedFight, entries: list[PlayerDamagePrevention]) -> str:
    """Report-ready summary string: the overview table, followed by full window detail for every player."""
    if not entries:
        return f"{parsed_fight.fight.name}: {format_damage_prevention_table(entries)}"
    lines = [f"{parsed_fight.fight.name} -- damage prevented by defensives:", "", format_damage_prevention_table(entries), ""]
    for entry in entries:
        lines.append(format_player_windows_detail(entry))
        lines.append("")
    return "\n".join(lines).rstrip()
