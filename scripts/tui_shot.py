#!/usr/bin/env python3
"""Headless TUI frames: run the real TUI against the API, drive keys, save SVG screenshots (Textual's own renderer).

    .venv/bin/python scripts/tui_shot.py --api-url http://127.0.0.1:8791 --size 180x50 --out demo/tui --wait 5 --keys "up,up,enter"
Frames are exact terminal renderings (same cells, same colours) exported as SVG; convert to PNG with scripts/svg2png.mjs.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

# colours: the exporter must not be told to go monochrome by the calling shell
os.environ.pop("NO_COLOR", None)
os.environ.setdefault("TEXTUAL_COLOR_SYSTEM", "truecolor")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stampede.tui.app import StampedeTUI  # noqa: E402
from stampede.tui.client import ApiClient  # noqa: E402


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api-url", default="http://127.0.0.1:8791")
    ap.add_argument("--size", default="180x50")
    ap.add_argument("--out", default="demo/tui")
    ap.add_argument("--wait", type=float, default=4.0, help="seconds to let the first poll land")
    ap.add_argument("--keys", default="", help="comma separated keys pressed after the wait, one frame after each")
    ap.add_argument("--frames", type=int, default=0, help="extra timed frames every --every seconds after the keys")
    ap.add_argument("--every", type=float, default=1.0)
    ap.add_argument("--prefix", default="tui")
    ap.add_argument("--key-wait", type=float, default=1.2, help="seconds between a key press and its frame")
    a = ap.parse_args()
    w, h = (int(x) for x in a.size.split("x"))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    app = StampedeTUI(ApiClient(a.api_url), poll_s=1.0)
    n = 0
    async with app.run_test(size=(w, h)) as pilot:
        await asyncio.sleep(a.wait)
        if not a.frames:
            await pilot.pause()
        app.save_screenshot(f"{a.prefix}-{n:02d}.svg", path=str(out))
        n += 1
        for k in [k.strip() for k in a.keys.split(",") if k.strip()]:
            if k.startswith("type:"):
                for ch in k[5:]:
                    await pilot.press(ch)
            else:
                await pilot.press(k)
            await asyncio.sleep(a.key_wait)
            await pilot.pause()
            app.save_screenshot(f"{a.prefix}-{n:02d}.svg", path=str(out))
            n += 1
        for _ in range(a.frames):
            # timed frames: no pilot.pause() here, it would wait for an idle app and hide the streaming phase
            await asyncio.sleep(a.every)
            app.save_screenshot(f"{a.prefix}-{n:02d}.svg", path=str(out))
            n += 1
    print(f"wrote {n} frames to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
