#!/usr/bin/env python3
"""Turn a generated "pixel-look" render into a true pixel-grid master.

Image generators draw pixel art as soft, slightly irregular squares. This script snaps such a render to a real
grid: it estimates the logical cell size and offset (the alignment with the fewest mixed cells), maps every
cell to the majority colour of a fixed brand palette (or transparent), and writes

  <out>-grid.png        the logical-size master (one image pixel per logical pixel, real alpha)
  <out>-x<k>.png        nearest-neighbour upscales for review / README use
  <out>.txt             grid size, cell size, offset, palette usage

    python3 scripts/pixel_master.py assets/brand/pixel/raw-pixel-64.png assets/brand/pixel/bison --cell 20 --scales 8 16

Only Pillow + numpy. Nothing here reads .env.
"""
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image

PALETTE = {  # brand colours; the render's own near-colours snap to these
    "body": (0x0D, 0x0A, 0x0A),
    "red": (0xFF, 0x33, 0x44),
    "light": (0xA3, 0xA3, 0xA3),
    "mid": (0x4A, 0x4A, 0x4A),
    "shadow": (0x35, 0x14, 0x19),
}
TRANSPARENT = -1


def snap_colors(rgb: np.ndarray, names: list[str]) -> np.ndarray:
    pal = np.array([PALETTE[n] for n in names], dtype=float)
    d = ((rgb[..., None, :].astype(float) - pal[None, None, :, :]) ** 2).sum(-1)
    return d.argmin(-1)


def mixedness(idx: np.ndarray, cell: int, ox: int, oy: int) -> int:
    """Number of cells whose pixels disagree on the colour index (lower = better aligned grid)."""
    h, w = idx.shape
    sub = idx[oy : oy + ((h - oy) // cell) * cell, ox : ox + ((w - ox) // cell) * cell]
    cells = sub.reshape(sub.shape[0] // cell, cell, sub.shape[1] // cell, cell).transpose(0, 2, 1, 3).reshape(-1, cell * cell)
    return int((cells != cells[:, :1]).any(axis=1).sum())


def snap(src: Path, out: Path, cell: int | None, names: list[str], scales: list[int], alpha_thr: int = 128) -> dict:
    im = np.array(Image.open(src).convert("RGBA"))
    alpha = im[..., 3] >= alpha_thr
    idx = snap_colors(im[..., :3], names)
    idx = np.where(alpha, idx, TRANSPARENT)
    h, w = idx.shape
    if cell is None:  # estimate: most common horizontal run length between colour changes (>= 6 px)
        runs: Counter = Counter()
        for y in range(0, h, 7):
            ch = np.flatnonzero(np.diff(idx[y]))
            if len(ch) > 1:
                runs.update(int(r) for r in np.diff(ch) if r >= 6)
        cell = runs.most_common(1)[0][0]
    best = min(((mixedness(idx, cell, ox, oy), ox, oy) for ox in range(cell) for oy in range(cell)), key=lambda t: t[0])
    _, ox, oy = best
    gh, gw = (h - oy) // cell, (w - ox) // cell
    grid = np.full((gh, gw), TRANSPARENT, dtype=int)
    for gy in range(gh):
        for gx in range(gw):
            block = idx[oy + gy * cell : oy + (gy + 1) * cell, ox + gx * cell : ox + (gx + 1) * cell].ravel()
            vals, counts = np.unique(block, return_counts=True)
            grid[gy, gx] = vals[counts.argmax()]
    # crop to content, keep a 1-cell transparent margin
    ys, xs = np.where(grid != TRANSPARENT)
    grid = grid[max(0, ys.min() - 1) : ys.max() + 2, max(0, xs.min() - 1) : xs.max() + 2]
    rgba = np.zeros(grid.shape + (4,), dtype=np.uint8)
    for i, n in enumerate(names):
        rgba[grid == i, :3] = PALETTE[n]
        rgba[grid == i, 3] = 255
    master = Image.fromarray(rgba, "RGBA")
    master.save(f"{out}-grid.png")
    for k in scales:
        master.resize((master.width * k, master.height * k), Image.NEAREST).save(f"{out}-x{k}.png")
    usage = {n: int((grid == i).sum()) for i, n in enumerate(names)}
    info = {"source": str(src), "cell_px": cell, "offset": [ox, oy], "grid": [master.width, master.height], "mixed_cells": best[0], "palette_cells": usage}
    Path(f"{out}.txt").write_text("\n".join(f"{k}: {v}" for k, v in info.items()) + "\n")
    return info


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("out", help="output prefix")
    ap.add_argument("--cell", type=int, default=None, help="logical pixel size in source pixels (estimated when omitted)")
    ap.add_argument("--palette", default="body,red,light,mid,shadow", help="palette names to use, comma-separated")
    ap.add_argument("--scales", type=int, nargs="*", default=[8])
    a = ap.parse_args()
    print(snap(Path(a.src), Path(a.out), a.cell, a.palette.split(","), a.scales))
