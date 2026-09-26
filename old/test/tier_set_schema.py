"""
tier_set_schema.py
Just the TierSetPieceDefinition / TierTrackBreakpoint dataclasses --
kept in their own module with NO other project dependencies, same
philosophy as gear_schema.py.

Tier set pieces are keyed by (class_name, slot) -> item_id: exactly
FIVE armor slots make up a class's tier set (Head, Shoulder, Chest,
Gloves, Legs), same convention Blizzard has used every tier for years.

IMPORTANT LIMITATION (same one gear_analyzer.py already documents):
this app has no item database. tier_set_data.py does NOT ship with a
hardcoded item_id catalog -- it can't verify one, the same way it
can't verify enchant/gem IDs. Instead, TRACKED_TIER_SET_PIECES starts
EMPTY and gets populated by YOU, via tier_set_explorer.py's heuristic
"candidate" detection + manage_tier_sets.py's add/remove CLI.

CHANGED: TierTrackBreakpoint now has a max_item_level field, in
addition to min_item_level. See tier_set_data.py's module docstring
for exactly why this was needed -- in short, Blizzard's own upgrade
system defines certain item levels (e.g. 318, 321) as belonging to
BOTH of two adjacent tracks at once (the top 2 ranks of the lower
track are numerically identical to the bottom 2 ranks of the next
track up), so classifying by "floor only" always resolves that
ambiguity in favor of the HIGHER track. Tracking each track's own
ceiling lets the classifier instead default to the LOWER (more
conservative) track whenever an item level is genuinely ambiguous.
max_item_level defaults to None (treated as "no ceiling", i.e.
unbounded above) so older saved JSON files without this field, or a
manually-added breakpoint that only specifies a floor, still load
without crashing -- see tier_set_config_io.py.
"""
from __future__ import annotations
from dataclasses import dataclass, field

# The five armor slots that make up a tier set, and their Blizzard
# inventory slot indices (same indices gear_analyzer.py's
# ENCHANTABLE_SLOTS already uses for Head/Shoulder/Chest/Legs; Gloves
# -- slot 9 -- is the one additional slot tier sets cover that enchant
# tracking doesn't).
TIER_SET_SLOTS: dict[int, str] = {
    0: "Head", 2: "Shoulder", 4: "Chest", 9: "Gloves", 6: "Legs",
}

# Canonical DISPLAY order for the 5-character track string (e.g.
# "MMHCC"): Head, Shoulder, Chest, Gloves, Legs -- a plain dict's
# iteration order happens to already match this by insertion, but this
# tuple is the explicit, intentional contract other modules should
# import and rely on rather than assuming dict ordering.
TIER_SET_SLOT_ORDER: tuple[int, ...] = (0, 2, 4, 9, 6)

# Placeholder character shown for a tier slot the player has EQUIPPED
# something in, but that item is NOT one of the tracked tier-set piece
# IDs for their class -- i.e. "not wearing a tier piece in this slot",
# as distinct from having a slot literally empty (which normally can't
# happen for real players in a raid, but is handled the same way).
MISSING_TIER_PIECE_CHAR = "_"


@dataclass
class TierSetPieceDefinition:
    """
    One tracked tier-set item: for THIS class, wearing item_id in slot
    counts as a tier piece.
    """
    class_name: str
    slot: int  # must be a key in TIER_SET_SLOTS
    item_id: int
    notes: str | None = None

    @property
    def slot_name(self) -> str:
        return TIER_SET_SLOTS.get(self.slot, f"Slot {self.slot}")


@dataclass
class TierTrackBreakpoint:
    """
    One item-track's item-level RANGE: an item is considered to be on
    this track if min_item_level <= item_level <= max_item_level.
    track_letter is a single uppercase character used in the 5-char
    per-player track string (e.g. "M" for Myth); color_hex is the
    color that letter renders in, in the HTML report.

    max_item_level=None means "no ceiling" (unbounded above) -- used
    for the TOP track (Myth), whose own highest special reward ranks
    (e.g. the "Very Rare"/last-2-boss 344 equivalent) sit well above
    its normal 6/6 cap and have no higher track to be confused with.
    """
    min_item_level: float
    track_letter: str
    track_name: str
    color_hex: str
    max_item_level: float | None = None

    def contains(self, item_level: float) -> bool:
        if item_level < self.min_item_level:
            return False
        if self.max_item_level is not None and item_level > self.max_item_level:
            return False
        return True
