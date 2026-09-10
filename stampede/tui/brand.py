"""The STAMPEDE wordmark as block glyphs (no external font). 5 rows, 55 columns."""
from __future__ import annotations

GLYPHS = {
    "S": ["██████", "██    ", "██████", "    ██", "██████"],
    "T": ["██████", "  ██  ", "  ██  ", "  ██  ", "  ██  "],
    "A": ["██████", "██  ██", "██████", "██  ██", "██  ██"],
    "M": ["██  ██", "██████", "██████", "██  ██", "██  ██"],
    "P": ["██████", "██  ██", "██████", "██    ", "██    "],
    "E": ["██████", "██    ", "█████ ", "██    ", "██████"],
    "D": ["█████ ", "██  ██", "██  ██", "██  ██", "█████ "],
}

WORD = "STAMPEDE"


def big() -> list[str]:
    rows = []
    for i in range(5):
        rows.append(" ".join(GLYPHS[ch][i] for ch in WORD))
    return rows


def compact() -> str:
    return "S T A M P E D E"


BIG_WIDTH = len(big()[0])
