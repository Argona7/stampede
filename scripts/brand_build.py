#!/usr/bin/env python3
"""Build every brand derivative from the approved pixel master (assets/brand/pixel/bison-a-grid.png).

Outputs (assets/brand/ unless noted):
  mascot-grid.png            the master itself, one image pixel per logical pixel, real alpha (copied, never edited here)
  mascot-2048.png            nearest-neighbour upscale centred on a 2048x2048 transparent canvas
  mascot-on-black.png / mascot-on-light.png   1024x1024 previews
  avatar-1024.png / avatar-128.png            head + red plane, square, safe inside a circular crop
  mark-64.png / mark-32.png / mark-16.png     simplified head mark (majority-downsampled from the head crop, then cleaned)
  mark.svg                   the 16x16 mark as real vector rects (also used as web/public/favicon.svg)
  ascii-mark.txt             the terminal mark: 24 columns x 12 rows of block characters, hand-checked
  hero-static.png            1280x480: wordmark left, mascot right, one red stroke
  docs/assets/hero-static.png, docs/assets/hero.gif   README copies (GIF: one heavy step / lean and back, 3.5 s, seamless)

Typography is rendered here, never generated: the wordmark is the TUI's block glyph set (stampede/tui/brand.py),
captions are IBM Plex Mono from the web bundle. Palette: docs/BRAND-README-RELEASE-BRIEF-RU.md section 3.
Only Pillow + numpy + gifski. Nothing here reads .env.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from stampede.tui.brand import GLYPHS, MARK, MARK_COLORS, WORD, mark_halfblocks, mark_plain  # noqa: E402

BRAND = ROOT / "assets" / "brand"
MASTER = BRAND / "pixel" / "bison-a-grid.png"
DOCS = ROOT / "docs" / "assets"
TMP = Path("/tmp/stampede-brand")
MONO = ROOT / "web/node_modules/@fontsource/ibm-plex-mono/files/ibm-plex-mono-latin-500-normal.woff2"

BLACK = (0x05, 0x05, 0x05)
PANEL = (0x0D, 0x0A, 0x0A)
RED = (0xFF, 0x33, 0x44)
DARK_RED = (0x35, 0x14, 0x19)
TEXT = (0xF2, 0xF2, 0xF2)
SECOND = (0xA3, 0xA3, 0xA3)
LIGHT_BG = (0xF2, 0xF2, 0xF2)


def run(cmd: str) -> None:
    subprocess.run(cmd, shell=True, check=True)


def nearest(im: Image.Image, k: int) -> Image.Image:
    return im.resize((im.width * k, im.height * k), Image.NEAREST)


def on(bg: tuple[int, int, int], size: int, sprite: Image.Image) -> Image.Image:
    c = Image.new("RGBA", (size, size), bg + (255,))
    k = max(1, int(size * 0.86 // max(sprite.width, sprite.height)))
    s = nearest(sprite, k)
    c.alpha_composite(s, ((size - s.width) // 2, (size - s.height) // 2))
    return c.convert("RGB")


# ---------------------------------------------------------------- master, previews, avatar

def head_box(grid: Image.Image) -> tuple[int, int, int, int]:
    """Square box around the red face-and-chest plane plus the horns: the avatar crop, in logical pixels."""
    a = np.array(grid.convert("RGBA"))
    red = (a[..., 0] > 200) & (a[..., 1] < 90) & (a[..., 3] > 0)
    ys, xs = np.where(red)
    # the face is the upper part of the red plane: take the top 60 % of red rows
    top, bottom = ys.min(), ys.min() + int((ys.max() - ys.min()) * 0.6)
    sel = ys <= bottom
    cx = int((xs[sel].min() + xs[sel].max()) / 2)
    cy = int((top + bottom) / 2) - 2
    side = int(max(xs[sel].max() - xs[sel].min(), bottom - top) * 1.9)
    side = min(side, grid.width, grid.height)
    x0 = min(max(0, cx - side // 2), grid.width - side)
    y0 = min(max(0, cy - side // 2), grid.height - side)
    return x0, y0, x0 + side, y0 + side


def build_master(grid: Image.Image) -> None:
    grid.save(BRAND / "mascot-grid.png")
    k = 2048 // max(grid.width, grid.height)
    big = nearest(grid, k)
    canvas = Image.new("RGBA", (2048, 2048), (0, 0, 0, 0))
    canvas.alpha_composite(big, ((2048 - big.width) // 2, (2048 - big.height) // 2))
    canvas.save(BRAND / "mascot-2048.png")
    on(BLACK, 1024, grid).save(BRAND / "mascot-on-black.png")
    on(LIGHT_BG, 1024, grid).save(BRAND / "mascot-on-light.png")
    box = head_box(grid)
    head = grid.crop(box)
    for size in (1024, 128):
        c = Image.new("RGBA", (size, size), BLACK + (255,))
        k = max(1, int(size * 0.8 // head.width))
        s = nearest(head, k)
        c.alpha_composite(s, ((size - s.width) // 2, (size - s.height) // 2 + size // 40))
        c.convert("RGB").save(BRAND / f"avatar-{size}.png")
    return head


# ---------------------------------------------------------------- marks (hand-drawn 16 px head), favicon svg, terminal mark

def mark_image() -> Image.Image:
    rgba = np.zeros((16, 16, 4), dtype=np.uint8)
    for y, row in enumerate(MARK):
        for x, ch in enumerate(row):
            if ch in MARK_COLORS:
                h = MARK_COLORS[ch].lstrip("#")
                rgba[y, x] = (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 255)
    return Image.fromarray(rgba, "RGBA")


def build_marks() -> Image.Image:
    mark = mark_image()
    mark.save(BRAND / "mark-16.png")
    for size in (32, 64):
        nearest(mark, size // 16).save(BRAND / f"mark-{size}.png")
    rects = ['<rect width="16" height="16" fill="#050505"/>']
    for y, row in enumerate(MARK):
        for x, ch in enumerate(row):
            if ch in MARK_COLORS:
                rects.append(f'<rect x="{x}" y="{y}" width="1" height="1" fill="{MARK_COLORS[ch]}"/>')
    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="64" height="64" shape-rendering="crispEdges" role="img" aria-label="STAMPEDE">\n  ' + "\n  ".join(rects) + "\n</svg>\n"
    (BRAND / "mark.svg").write_text(svg)
    (ROOT / "web" / "public" / "favicon.svg").write_text(svg)
    return mark


def build_ascii() -> str:
    """Terminal mark: the same 16x16 matrix. `ascii-mark.txt` holds the plain 1:1 fallback; the coloured
    half-block version (16 columns x 8 rows) comes from stampede.tui.brand.mark_halfblocks()."""
    plain = "\n".join(mark_plain()) + "\n"
    (BRAND / "ascii-mark.txt").write_text(plain)
    (BRAND / "ascii-mark-halfblocks.rich.txt").write_text("\n".join(mark_halfblocks()) + "\n")
    return plain


# ---------------------------------------------------------------- wordmark + hero

def wordmark(cell_w: int, cell_h: int, color=RED, shadow=DARK_RED) -> Image.Image:
    rows = [" ".join(GLYPHS[ch][i] for ch in WORD) for i in range(5)]
    w, h = len(rows[0]) * cell_w, 5 * cell_h
    im = Image.new("RGBA", (w + cell_w, h + cell_h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    for dy, dx, col in ((cell_h // 2, cell_w // 2, shadow), (0, 0, color)):  # pressed offset copy behind the letters
        for y, row in enumerate(rows):
            for x, ch in enumerate(row):
                if ch == "█":
                    d.rectangle([x * cell_w + dx, y * cell_h + dy, (x + 1) * cell_w - 1 + dx, (y + 1) * cell_h - 1 + dy], fill=col + (255,))
    return im


def shifted_sprite(grid: Image.Image, lean: float) -> Image.Image:
    """Heavy lean: the top rows move toward the head (right) by up to 3 logical px, the body sinks up to 2 px.
    Row shifts only, so horns, legs and face never change shape."""
    a = np.array(grid.convert("RGBA"))
    h, w = a.shape[:2]
    out = np.zeros((h + 3, w + 4, 4), dtype=np.uint8)
    dip = int(round(lean * 2))  # 0..2 px down
    for y in range(h):
        dx = int(round(lean * 3 * (1 - y / h)))
        out[y + dip, dx : dx + w] = a[y]
    return Image.fromarray(out, "RGBA")


def hero_frame(grid: Image.Image, lean: float, stroke: float, font: ImageFont.FreeTypeFont) -> Image.Image:
    W, H = 1280, 480
    im = Image.new("RGBA", (W, H), BLACK + (255,))
    d = ImageDraw.Draw(im)
    # wordmark: block glyphs at the terminal's 1:2 cell aspect
    wm = wordmark(12, 24)
    im.alpha_composite(wm, (72, 150))
    # tagline
    d.text((72, 150 + wm.height + 18), "observed wallet rotations  ·  Robinhood Chain  ·  read-only", font=font, fill=SECOND + (255,))
    # the one directional stroke: under the wordmark, growing toward the mascot
    x0, y = 72, 150 + wm.height + 62
    x_end = 72 + int(stroke * (860 - 72))
    if stroke > 0:
        d.rectangle([x0, y, x_end, y + 3], fill=RED + (255,))
        d.polygon([(x_end + 1, y - 4), (x_end + 11, y + 1), (x_end + 1, y + 7)], fill=RED + (255,))
    # mascot
    sp = nearest(shifted_sprite(grid, lean), 4)
    im.alpha_composite(sp, (W - sp.width - 56, H - sp.height - 44))
    return im.convert("RGB")


def ease(t: float) -> float:
    return t * t * (3 - 2 * t)


def build_hero(grid: Image.Image) -> None:
    font = ImageFont.truetype(str(MONO), 22)
    hero_frame(grid, 0.0, 1.0, font).save(BRAND / "hero-static.png")  # static: base pose, stroke drawn
    shutil.copy(BRAND / "hero-static.png", DOCS / "hero-static.png")
    frames_dir = TMP / "hero"
    shutil.rmtree(frames_dir, ignore_errors=True)
    frames_dir.mkdir(parents=True)
    fps, dur = 8, 3.5
    n = int(fps * dur)
    for i in range(n):
        t = i / fps
        # 0.0-0.6 hold · 0.6-1.1 lean in · 1.1-1.5 hold low · 1.5-2.0 back · 2.0-3.5 hold; stroke sweeps 0.6-1.3, retracts 2.9-3.4
        if t < 0.6:
            lean = 0.0
        elif t < 1.1:
            lean = ease((t - 0.6) / 0.5)
        elif t < 1.5:
            lean = 1.0
        elif t < 2.0:
            lean = 1 - ease((t - 1.5) / 0.5)
        else:
            lean = 0.0
        if t < 0.6:
            stroke = 0.0
        elif t < 1.3:
            stroke = ease((t - 0.6) / 0.7)
        elif t < 2.9:
            stroke = 1.0
        elif t < 3.4:
            stroke = 1 - ease((t - 2.9) / 0.5)
        else:
            stroke = 0.0
        hero_frame(grid, lean, stroke, font).save(frames_dir / f"f-{i:03d}.png")
    run(f"gifski --quiet --fps {fps} --quality 90 -o '{DOCS}/hero.gif' {frames_dir}/f-*.png")


if __name__ == "__main__":
    BRAND.mkdir(parents=True, exist_ok=True)
    DOCS.mkdir(parents=True, exist_ok=True)
    grid = Image.open(MASTER).convert("RGBA")
    build_master(grid)
    build_marks()
    print(build_ascii())
    build_hero(grid)
    for p in sorted(list(BRAND.glob("*.*")) + [DOCS / "hero.gif", DOCS / "hero-static.png"]):
        if p.is_file():
            print(f"{str(p.relative_to(ROOT)):40s} {p.stat().st_size / 1e6:6.2f} MB")
