"""
selection_utils.py

Shared "1,3,5-8" style selection-string parsing, used by every
explore_*.py / manage_*.py interactive tool in this project, so the
parsing rules only need to live in one place.
"""
from __future__ import annotations

import re


def parse_selection(text: str, maximum: int) -> list[int]:
    """
    Parse a selection string like '1,3,5-8' into sorted, ZERO-based
    indexes in range [0, maximum). Raises ValueError with a readable
    message for anything malformed or out of range. An empty/blank
    string returns an empty list (the caller's "select nothing" case).
    """
    chosen: set[int] = set()
    compact = text.strip().replace(" ", "")
    if not compact:
        return []

    for part in compact.split(","):
        if not part:
            continue
        if re.fullmatch(r"\d+", part):
            start = end = int(part)
        elif re.fullmatch(r"\d+-\d+", part):
            start_text, end_text = part.split("-", 1)
            start, end = int(start_text), int(end_text)
            if start > end:
                start, end = end, start
        else:
            raise ValueError(f"Invalid selection component: {part!r}")

        if start < 1 or end > maximum:
            raise ValueError(f"Selection must be between 1 and {maximum}.")
        chosen.update(range(start - 1, end))
    return sorted(chosen)
