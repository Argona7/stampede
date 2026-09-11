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

# The 16x16 mark: the bison head, hand-drawn (hump top-left, red face plane, grey horns, dark muzzle).
# B body #0D0A0A · R red #FF3344 · G grey #A3A3A3 · D dark red #351419 · . transparent.
# Single source for assets/brand/mark-*.png, mark.svg, web/public/favicon.svg and the terminal mark below.
MARK = [
    "...BBBBBBB......",
    ".BBBBBBBBBBB....",
    "BBBBBBBBBBBBB...",
    "BBBBBBBBBBBBBB..",
    "BBBBBBBBBBRRRR..",
    "GGBBBBBBBRRRRRGG",
    "GGBBBBBBBRRRRRGG",
    ".GBBBBBBRRRRRRG.",
    "..BBBBBBRBRRRBR.",
    "..BBBBBBRRRRRRR.",
    "..BBBBBBBRRRRRR.",
    "..BBBBBBBBRRRRR.",
    "...BBBBBBBRRRR..",
    "...BBBBBBBBDDD..",
    "....BBBB..BDD...",
    "....BBB.........",
]
MARK_COLORS = {"B": "#0D0A0A", "R": "#FF3344", "G": "#A3A3A3", "D": "#351419"}


def mark_halfblocks(bg: str = "#050505") -> list[str]:
    """The mark for a terminal: 16 columns x 8 rows of Rich markup, two logical pixels per character
    (upper half = foreground of '▀', lower half = background). Colours only; no external font."""
    lines = []
    for y in range(0, 16, 2):
        parts = []
        for x in range(16):
            top, bottom = MARK[y][x], MARK[y + 1][x]
            ct, cb = MARK_COLORS.get(top, bg), MARK_COLORS.get(bottom, bg)
            parts.append(f"[{ct} on {cb}]▀[/]")
        lines.append("".join(parts))
    return lines


def mark_plain() -> list[str]:
    """Colour-less fallback (1:1 cells, so it renders 2x too tall in a terminal): █ body, ▓ red, ░ grey/dark."""
    return ["".join({"B": "█", "R": "▓", "G": "░", "D": "░"}.get(ch, " ") for ch in row).rstrip() for row in MARK]


def big() -> list[str]:
    rows = []
    for i in range(5):
        rows.append(" ".join(GLYPHS[ch][i] for ch in WORD))
    return rows


def compact() -> str:
    return "S T A M P E D E"


BIG_WIDTH = len(big()[0])
