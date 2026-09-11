"""The STAMPEDE wordmark as block glyphs (no external font, 5 rows x 55 columns), the 16x16 head mark and the
README mascot as terminal half-blocks."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

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


MASCOT_PNG = Path(__file__).resolve().parents[2] / "assets" / "brand" / "mascot-grid.png"
_RGB_TO_KEY = {(13, 10, 10): "B", (255, 51, 68): "R", (163, 163, 163): "G", (53, 20, 25): "D"}


def _nearest_key(px: tuple[int, int, int, int]) -> str:
    """Snap one RGBA pixel to the closest of the four brand colours, '.' when (mostly) transparent."""
    if px[3] < 128:
        return "."
    rgb = px[:3]
    exact = _RGB_TO_KEY.get(rgb)
    if exact:
        return exact
    return _RGB_TO_KEY[min(_RGB_TO_KEY, key=lambda c: sum((a - b) ** 2 for a, b in zip(c, rgb)))]


@lru_cache(maxsize=None)
def bison_cells(rows: int = 12) -> tuple[str, ...] | None:
    """The README mascot (assets/brand/mascot-grid.png, 94x91 logical pixels) downsampled to 2*rows logical pixels
    tall (width keeps the aspect), one character per key (B/R/G/D/'.'): each output pixel is the majority colour of
    its source block, ties going to the outline/plane colours over the body so the silhouette survives.
    None when Pillow or the asset is unavailable."""
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - Pillow is a project dependency, but the TUI must not die without it
        return None
    if not MASCOT_PNG.exists():
        return None
    with Image.open(MASCOT_PNG) as im:
        src = im.convert("RGBA")
        sw, sh = src.size
        keys = [[_nearest_key(src.getpixel((x, y))) for x in range(sw)] for y in range(sh)]
    out_h = rows * 2
    out_w = max(1, round(sw * out_h / sh))
    prio = {"G": 4, "R": 3, "D": 2, "B": 1, ".": 0}
    lines = []
    for oy in range(out_h):
        y0, y1 = (oy * sh) // out_h, max((oy * sh) // out_h + 1, ((oy + 1) * sh) // out_h)
        row = []
        for ox in range(out_w):
            x0, x1 = (ox * sw) // out_w, max((ox * sw) // out_w + 1, ((ox + 1) * sw) // out_w)
            block = [keys[y][x] for y in range(y0, y1) for x in range(x0, x1)]
            counts = {c: block.count(c) for c in set(block)}
            row.append(max(counts, key=lambda c: (counts[c], prio[c])))
        lines.append("".join(row))
    return tuple(lines)


def _halfblocks(cells: tuple[str, ...], bg: str) -> list[str]:
    lines = []
    for y in range(0, len(cells) - 1, 2):
        parts = []
        for top, bottom in zip(cells[y], cells[y + 1]):
            ct, cb = MARK_COLORS.get(top, bg), MARK_COLORS.get(bottom, bg)
            parts.append(f"[{ct} on {cb}]▀[/]")
        lines.append("".join(parts))
    return lines


@lru_cache(maxsize=None)
def bison_halfblocks(rows: int = 12, bg: str = "#050505") -> tuple[str, ...] | None:
    """The mascot as `rows` lines of Rich markup ('▀' with fg = upper pixel, bg = lower pixel; transparent = bg).
    Pure and cached: computed once per (rows, bg), never per frame. None when it cannot be rendered."""
    cells = bison_cells(rows)
    return tuple(_halfblocks(cells, bg)) if cells else None


def bison_width(rows: int = 12) -> int:
    cells = bison_cells(rows)
    return len(cells[0]) if cells else 0


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
