"""
damage_to_target_analyzer.py

Answers "how much damage did each player do to EACH enemy target",
for fights with more than one damageable NPC -- boss+add fights,
council/multi-boss encounters, priority-add mechanics, etc.

Works immediately, with zero configuration required -- targets are
identified by their raw Warcraft Logs NPC name. If you want to relabel
raw names into cleaner display names or roll several raw names up into
one category, see target_label_config.py / explore_damage_targets.py
for that OPTIONAL labeling layer -- this module works fully without it.

THREE complementary views are provided:
  1. TargetSummary (analyze_damage_by_target) -- one row per TARGET,
     total damage + top contributing players.
  2. PlayerTargetBreakdown (analyze_player_damage_breakdown) -- one
     row per PLAYER, their damage split across every target.
  3. PlayerTargetMatrix (build_player_target_matrix) -- a full
     player-by-target GRID (every player as a row, every target as a
     column, one cell per player-target pair) -- the most useful shape
     for directly comparing every player's contribution to every
     target side-by-side.

Grouping is by TARGET NAME, not by individual actor ID: if the same add
type spawns multiple times during a fight, their damage is combined
into one total, while the distinct underlying actor IDs are still
tracked (TargetSummary.target_ids / instance_count).

Pet/summon damage is folded into the owning player. Only real ENEMY
npcs count as targets -- a player's own totems/pets are excluded.

Pure function over a ParsedFight -- no network calls.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from data_models import Actor, ParsedFight
import roster


def _is_enemy_npc(actor: Actor | None) -> bool:
    """
    True only for a real enemy NPC -- excludes players, and excludes
    NPC-typed actors that are actually a player's own pet/totem/guardian
    (a genuine enemy has no owner_id at all).
    """
    if actor is None:
        return False
    return actor.type == "NPC" and actor.owner_id is None


@dataclass
class TargetSummary:
    """One distinct enemy NPC NAME (may cover several spawned instances)."""
    target_name: str
    target_ids: set[int] = field(default_factory=set)
    total_damage: int = 0
    damage_by_player_id: dict[int, int] = field(default_factory=dict)
    player_names: dict[int, str] = field(default_factory=dict)
    first_seen_ms: int | None = None
    last_seen_ms: int | None = None

    @property
    def instance_count(self) -> int:
        return len(self.target_ids)

    def damage_by(self, player_id: int) -> int:
        return self.damage_by_player_id.get(player_id, 0)

    def ranked_players(self) -> list[tuple[int, str, int]]:
        """(player_id, player_name, damage), sorted by damage descending."""
        return sorted(
            ((pid, self.player_names.get(pid, "Unknown"), dmg) for pid, dmg in self.damage_by_player_id.items()),
            key=lambda row: row[2], reverse=True,
        )


@dataclass
class PlayerTargetBreakdown:
    """One player's damage split out across every enemy target they hit."""
    player_id: int
    player_name: str
    damage_by_target_name: dict[str, int] = field(default_factory=dict)
    total_damage: int = 0

    def damage_to(self, target_name: str) -> int:
        return self.damage_by_target_name.get(target_name, 0)

    def ranked_targets(self) -> list[tuple[str, int]]:
        """(target_name, damage), sorted by damage descending."""
        return sorted(self.damage_by_target_name.items(), key=lambda row: row[1], reverse=True)


def analyze_damage_by_target(parsed_fight: ParsedFight) -> list[TargetSummary]:
    """
    Build a TargetSummary per distinct enemy NPC name that took any
    damage from a player (pets folded to owner). Sorted by total_damage
    descending.
    """
    summaries: dict[str, TargetSummary] = {}
    for event in parsed_fight.events:
        if event.data_type != "DamageDone":
            continue
        target_actor = parsed_fight.actors.get(event.target_id) if event.target_id is not None else None
        if not _is_enemy_npc(target_actor):
            continue
        player = roster.resolve_to_player(event.source_id, parsed_fight.actors)
        if player is None:
            continue

        target_name = event.target_name or "Unknown"
        if target_name not in summaries:
            summaries[target_name] = TargetSummary(target_name=target_name)
        summary = summaries[target_name]

        summary.target_ids.add(event.target_id)
        amount = event.amount or 0
        summary.total_damage += amount
        summary.damage_by_player_id[player.id] = summary.damage_by_player_id.get(player.id, 0) + amount
        summary.player_names[player.id] = player.name

        if summary.first_seen_ms is None or event.timestamp < summary.first_seen_ms:
            summary.first_seen_ms = event.timestamp
        if summary.last_seen_ms is None or event.timestamp > summary.last_seen_ms:
            summary.last_seen_ms = event.timestamp

    return sorted(summaries.values(), key=lambda s: s.total_damage, reverse=True)


def analyze_player_damage_breakdown(parsed_fight: ParsedFight) -> list[PlayerTargetBreakdown]:
    """Build a PlayerTargetBreakdown per player who dealt any damage to an enemy target."""
    breakdowns: dict[int, PlayerTargetBreakdown] = {}
    for event in parsed_fight.events:
        if event.data_type != "DamageDone":
            continue
        target_actor = parsed_fight.actors.get(event.target_id) if event.target_id is not None else None
        if not _is_enemy_npc(target_actor):
            continue
        player = roster.resolve_to_player(event.source_id, parsed_fight.actors)
        if player is None:
            continue

        if player.id not in breakdowns:
            breakdowns[player.id] = PlayerTargetBreakdown(player_id=player.id, player_name=player.name)
        breakdown = breakdowns[player.id]

        target_name = event.target_name or "Unknown"
        amount = event.amount or 0
        breakdown.damage_by_target_name[target_name] = (
            breakdown.damage_by_target_name.get(target_name, 0) + amount
        )
        breakdown.total_damage += amount

    return sorted(breakdowns.values(), key=lambda b: b.total_damage, reverse=True)


def get_player_breakdown(
    breakdowns: list[PlayerTargetBreakdown], player_name: str
) -> PlayerTargetBreakdown | None:
    """Convenience lookup: find one player's breakdown by name (case-insensitive)."""
    for breakdown in breakdowns:
        if breakdown.player_name and breakdown.player_name.lower() == player_name.lower():
            return breakdown
    return None


# ---------------------------------------------------------------------
# Player x Target MATRIX
# ---------------------------------------------------------------------
@dataclass
class PlayerTargetMatrix:
    """
    A dense player-by-target grid. target_names order matches
    analyze_damage_by_target's output (highest total damage first);
    player_names is sorted by each player's OWN total damage
    descending. A cell for a player who never hit a given target is
    simply absent -- use .get_cell() for a clean 0.
    """
    player_names: list[str] = field(default_factory=list)
    target_names: list[str] = field(default_factory=list)
    damage: dict[tuple[str, str], int] = field(default_factory=dict)
    player_totals: dict[str, int] = field(default_factory=dict)
    target_totals: dict[str, int] = field(default_factory=dict)

    def get_cell(self, player_name: str, target_name: str) -> int:
        return self.damage.get((player_name, target_name), 0)

    def row_for(self, player_name: str) -> list[int]:
        """This player's damage across every target, in target_names column order."""
        return [self.get_cell(player_name, target_name) for target_name in self.target_names]

    def column_for(self, target_name: str) -> list[int]:
        """Every player's damage to this one target, in player_names row order."""
        return [self.get_cell(player_name, target_name) for player_name in self.player_names]


def build_player_target_matrix(target_summaries: list[TargetSummary]) -> PlayerTargetMatrix:
    """
    Build a PlayerTargetMatrix from analyze_damage_by_target's output,
    so the grid is always consistent with the target-ranked view.
    """
    matrix = PlayerTargetMatrix()
    matrix.target_names = [summary.target_name for summary in target_summaries]

    player_totals: dict[str, int] = {}
    for summary in target_summaries:
        matrix.target_totals[summary.target_name] = summary.total_damage
        for player_id, player_name, damage in summary.ranked_players():
            matrix.damage[(player_name, summary.target_name)] = damage
            player_totals[player_name] = player_totals.get(player_name, 0) + damage

    matrix.player_totals = player_totals
    matrix.player_names = sorted(player_totals, key=lambda name: player_totals[name], reverse=True)
    return matrix


def format_player_target_matrix_table(
    matrix: PlayerTargetMatrix,
    max_target_column_width: int = 12,
    max_player_name_width: int = 14,
) -> str:
    """Render the matrix as a plain-text, fixed-width grid, for terminal output."""
    if not matrix.player_names or not matrix.target_names:
        return "No player-vs-target data available for this fight."

    name_col_width = max_player_name_width
    col_width = max(max_target_column_width, 8)

    header_cells = [t[:col_width].rjust(col_width) for t in matrix.target_names]
    header = f"{'Player':<{name_col_width}} " + " ".join(header_cells) + " " + "Total".rjust(col_width)
    lines = [header, "-" * len(header)]

    for player_name in matrix.player_names:
        row_cells = [f"{matrix.get_cell(player_name, t):,}".rjust(col_width) for t in matrix.target_names]
        total_cell = f"{matrix.player_totals.get(player_name, 0):,}".rjust(col_width)
        lines.append(f"{player_name[:name_col_width]:<{name_col_width}} " + " ".join(row_cells) + " " + total_cell)

    lines.append("-" * len(header))
    footer_cells = [f"{matrix.target_totals.get(t, 0):,}".rjust(col_width) for t in matrix.target_names]
    grand_total = sum(matrix.target_totals.values())
    lines.append(f"{'Total':<{name_col_width}} " + " ".join(footer_cells) + " " + f"{grand_total:,}".rjust(col_width))

    return "\n".join(lines)


def format_player_target_matrix_markdown(matrix: PlayerTargetMatrix) -> str:
    """Render the matrix as a Markdown table (full names, no truncation)."""
    if not matrix.player_names or not matrix.target_names:
        return "_No player-vs-target data available for this fight._"

    headers = ["Player"] + matrix.target_names + ["Total"]
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    for player_name in matrix.player_names:
        row = [player_name] + [f"{matrix.get_cell(player_name, t):,}" for t in matrix.target_names]
        row.append(f"{matrix.player_totals.get(player_name, 0):,}")
        lines.append("| " + " | ".join(row) + " |")
    footer = ["**Total**"] + [f"**{matrix.target_totals.get(t, 0):,}**" for t in matrix.target_names]
    footer.append(f"**{sum(matrix.target_totals.values()):,}**")
    lines.append("| " + " | ".join(footer) + " |")
    return "\n".join(lines)


def format_target_summary_table(target_summaries: list[TargetSummary], top_n_players: int = 5) -> str:
    """Render a plain-text report: one block per target, showing total damage and top N contributing players."""
    if not target_summaries:
        return "No enemy targets took damage in this fight."
    lines = ["Damage by target:\n"]
    for summary in target_summaries:
        instance_note = f" ({summary.instance_count} instance(s))" if summary.instance_count > 1 else ""
        lines.append(f"{summary.target_name}{instance_note} -- {summary.total_damage:,} total damage")
        for player_id, player_name, damage in summary.ranked_players()[:top_n_players]:
            share = (damage / summary.total_damage * 100) if summary.total_damage else 0
            lines.append(f"  {player_name:<15} {damage:>12,}  ({share:>4.1f}%)")
        lines.append("")
    return "\n".join(lines)


def format_player_breakdown_table(breakdown: PlayerTargetBreakdown) -> str:
    """Render one player's damage across every target they hit, ranked."""
    if not breakdown.damage_by_target_name:
        return f"{breakdown.player_name}: no damage done to any enemy target this fight."
    lines = [f"{breakdown.player_name} -- damage by target ({breakdown.total_damage:,} total):"]
    for target_name, damage in breakdown.ranked_targets():
        share = (damage / breakdown.total_damage * 100) if breakdown.total_damage else 0
        lines.append(f"  {target_name:<28.28} {damage:>12,}  ({share:>4.1f}%)")
    return "\n".join(lines)


def summarize_damage_by_target(
    parsed_fight: ParsedFight, target_summaries: list[TargetSummary], top_n_players: int = 5
) -> str:
    """Report-ready summary string."""
    if not target_summaries:
        return f"{parsed_fight.fight.name}: no enemy targets took damage this fight."
    header = f"{parsed_fight.fight.name} -- damage by target:"
    return header + "\n" + format_target_summary_table(target_summaries, top_n_players=top_n_players)


# ---------------------------------------------------------------------
# Optional labeling layer -- see target_label_config.py.
# ---------------------------------------------------------------------
def apply_target_labels(
    target_summaries: list[TargetSummary],
    labels: list,  # list[target_label_schema.TargetLabel]
) -> list[TargetSummary]:
    """
    Return a NEW list of TargetSummary with each entry's target_name
    replaced by its configured display_name. Raw names with no matching
    label are passed through UNCHANGED. If two raw names share the same
    display_name, their entries are MERGED (this is the rollup mechanism).
    """
    display_name_by_raw = {label.target_name: label.display_name for label in labels}
    merged: dict[str, TargetSummary] = {}

    for summary in target_summaries:
        display_name = display_name_by_raw.get(summary.target_name, summary.target_name)
        if display_name not in merged:
            merged[display_name] = TargetSummary(target_name=display_name)
        combined = merged[display_name]

        combined.target_ids |= summary.target_ids
        combined.total_damage += summary.total_damage
        for player_id, damage in summary.damage_by_player_id.items():
            combined.damage_by_player_id[player_id] = combined.damage_by_player_id.get(player_id, 0) + damage
        combined.player_names.update(summary.player_names)

        if summary.first_seen_ms is not None:
            if combined.first_seen_ms is None or summary.first_seen_ms < combined.first_seen_ms:
                combined.first_seen_ms = summary.first_seen_ms
        if summary.last_seen_ms is not None:
            if combined.last_seen_ms is None or summary.last_seen_ms > combined.last_seen_ms:
                combined.last_seen_ms = summary.last_seen_ms

    return sorted(merged.values(), key=lambda s: s.total_damage, reverse=True)


def apply_target_labels_to_breakdowns(
    breakdowns: list[PlayerTargetBreakdown],
    labels: list,
) -> list[PlayerTargetBreakdown]:
    """Same relabeling/merging logic as apply_target_labels(), applied to the PER-PLAYER view instead."""
    display_name_by_raw = {label.target_name: label.display_name for label in labels}
    relabeled: list[PlayerTargetBreakdown] = []

    for breakdown in breakdowns:
        new_breakdown = PlayerTargetBreakdown(
            player_id=breakdown.player_id, player_name=breakdown.player_name,
            total_damage=breakdown.total_damage,
        )
        for target_name, damage in breakdown.damage_by_target_name.items():
            display_name = display_name_by_raw.get(target_name, target_name)
            new_breakdown.damage_by_target_name[display_name] = (
                new_breakdown.damage_by_target_name.get(display_name, 0) + damage
            )
        relabeled.append(new_breakdown)

    return relabeled


def rollup_by_category(
    target_summaries: list[TargetSummary],
    labels: list,
) -> dict[str, int]:
    """Total damage across ALL targets sharing the same label category. Unlabeled targets go under 'uncategorized'."""
    category_by_raw_name = {label.target_name: (label.category or "uncategorized") for label in labels}
    totals: dict[str, int] = {}
    for summary in target_summaries:
        category = category_by_raw_name.get(summary.target_name, "uncategorized")
        totals[category] = totals.get(category, 0) + summary.total_damage
    return totals
