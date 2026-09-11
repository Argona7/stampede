#!/usr/bin/env python3
"""Build the README media in docs/assets/ from copies of the recorded demo (the source video is never modified).

    .venv/bin/python scripts/readme_media.py            # everything
    .venv/bin/python scripts/readme_media.py product     # only the product loop + poster
    .venv/bin/python scripts/readme_media.py views       # only the four-views grid

Requires ffmpeg and gifski on PATH. Budgets (project targets, not GitHub limits): product GIF <= 5 MB,
hero GIF <= 3 MB, all README media together <= 10 MB. Nothing here reads .env.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "demo" / "v5" / "raw" / "web-walkthrough.webm"  # the continuous take without post captions (not tracked)
FINAL = ROOT / "demo" / "v5" / "out" / "stampede-v5-autopilot.mp4"  # terminal intro (5.0 s) + the take from 0.2 s, with captions
OUT = ROOT / "docs" / "assets"
TMP = Path("/tmp/stampede-readme-media")

# The raw take is preferred (clean frames: the app's own REPLAY / clock labels stay, no post caption bar);
# a fresh clone without demo/v5/raw falls back to the final video, whose timeline is shifted by +4.8 s.
VIDEO = RAW if RAW.exists() else FINAL
SHIFT = 0.0 if RAW.exists() else 4.8

# product loop: the tape streams the visible history, the 3D network builds up in time order, replay starts
# (pulses, HUD), the autopilot flies to the strongest route and opens its evidence card. Times in the raw take.
PRODUCT_IN, PRODUCT_DUR = 1.0 + SHIFT, 8.0
POSTER_AT = 7.6 + SHIFT
VIEWS_AT = {"radar": 31.7 + SHIFT, "flow": 37.2 + SHIFT, "map": 7.6 + SHIFT}
TUI_FRAME = ROOT / "demo" / "v5" / "tui" / "a-feed-24.png"  # real Textual render of the same session (feed, new rows arriving)
FPS = 12
WIDTH = 960


def run(cmd: str) -> None:
    subprocess.run(cmd, shell=True, check=True)


def product() -> None:
    TMP.mkdir(parents=True, exist_ok=True)
    frames = TMP / "product"
    shutil.rmtree(frames, ignore_errors=True)
    frames.mkdir()
    run(f"ffmpeg -v error -y -ss {PRODUCT_IN} -t {PRODUCT_DUR} -i '{VIDEO}' -vf 'fps={FPS},scale={WIDTH}:-2:flags=lanczos' '{frames}/f-%03d.png'")
    run(f"gifski --quiet --fps {FPS} --width {WIDTH} --quality 80 -o '{OUT}/demo-preview.gif' {frames}/f-*.png")
    run(f"ffmpeg -v error -y -ss {POSTER_AT} -i '{VIDEO}' -frames:v 1 -vf 'scale=1280:-2:flags=lanczos' '{OUT}/demo-poster.png'")
    shrink_png(OUT / "demo-poster.png")


def views() -> None:
    """2x2 grid: TERMINAL (intro frame), RADAR, FLOW, MAP, each labelled by the product itself (no extra text)."""
    TMP.mkdir(parents=True, exist_ok=True)
    tiles = {"terminal": TUI_FRAME, **{k: TMP / f"v-{k}.png" for k in VIEWS_AT}}
    for k, t in VIEWS_AT.items():
        run(f"ffmpeg -v error -y -ss {t} -i '{VIDEO}' -frames:v 1 '{tiles[k]}'")
    order = ["terminal", "radar", "flow", "map"]
    inputs = " ".join(f"-i '{tiles[k]}'" for k in order)
    # 1280x800 grid, 4 px gutters in the panel colour so the tiles read as one image on both GitHub themes;
    # the terminal render is wider than 16:10, so it is fitted and padded with the app background instead of stretched
    fc = "[0:v]scale=636:-2:flags=lanczos,pad=636:398:0:(oh-ih)/2:color=0x050505[t0];" + "".join(f"[{i}:v]scale=636:398:flags=lanczos[t{i}];" for i in range(1, 4)) + "[t0][t1]hstack[r0];[t2][t3]hstack[r1];[r0][r1]vstack[g];[g]pad=1280:800:2:2:color=0x0D0A0A"
    run(f"ffmpeg -v error -y {inputs} -filter_complex \"{fc}\" -frames:v 1 '{OUT}/views.png'")
    shrink_png(OUT / "views.png")


def shrink_png(p: Path) -> None:
    """Lossless-ish size reduction with Pillow (quantize only if it stays visually clean for UI captures)."""
    try:
        from PIL import Image
    except ImportError:
        return
    im = Image.open(p).convert("RGB")
    im.save(p, optimize=True)


def report() -> None:
    total = 0
    for p in sorted(OUT.glob("*")):
        if p.suffix in (".gif", ".png", ".mp4", ".webp", ".svg"):
            total += p.stat().st_size
            print(f"{p.name:22s} {p.stat().st_size / 1e6:6.2f} MB")
    print(f"{'total visible media':22s} {total / 1e6:6.2f} MB")


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    what = sys.argv[1:] or ["product", "views"]
    if "product" in what:
        product()
    if "views" in what:
        views()
    report()
