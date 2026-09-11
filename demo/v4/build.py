#!/usr/bin/env python3
"""Assemble demo v4 (RADAR -> coin -> FLOW -> evidence -> alerts) from raw recordings.

    .venv/bin/python demo/v4/build.py
Inputs : demo/v4/tui/*.png (Textual radar frames, 5 fps), demo/v4/raw/web-walkthrough.webm, demo/v4/raw/marks.json
Outputs: demo/v4/out/stampede-v4-radar.mp4, demo/v4/out/cut-5s-radar-to-flow.mp4, frames, SHA256SUMS.txt
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
RAW, TUI, OUT = HERE / "raw", HERE / "tui", HERE / "out"
OUT.mkdir(exist_ok=True)
FONT = "/System/Library/Fonts/Menlo.ttc"


def run(cmd: str) -> None:
    subprocess.run(cmd, shell=True, check=True)


def caption(path: Path, l1: str, l2: str, h: int = 64) -> None:
    img = Image.new("RGBA", (1440, h), (5, 5, 5, 236))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 1440, 1], fill=(53, 20, 25, 255))
    d.text((24, 10), l1, font=ImageFont.truetype(FONT, 18), fill=(242, 242, 242, 255))
    d.text((24, 38), l2, font=ImageFont.truetype(FONT, 13), fill=(163, 163, 163, 255))
    img.save(path)


def utc(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%H:%M:%S")


marks = json.loads((RAW / "marks.json").read_text())
m = {x["name"]: x["offset_s"] for x in marks["marks"]}
sid = marks["session_id"]
speed = marks["replay_speed"]
start_ts = marks["replay_clock_start_ts"]
top = marks.get("top_symbol", "")

frames = sorted(TUI.glob("*.png"))
FRAME_S = 0.2
if frames:
    lst = OUT / "tui-frames.txt"
    lst.write_text("".join(f"file '{f}'\nduration {FRAME_S}\n" for f in frames[:20]) + f"file '{frames[min(19, len(frames) - 1)]}'\n")
    caption(OUT / "cap-tui.png", f"TERMINAL  ·  stampede terminal  ·  RADAR screen (Tab)  ·  REPLAY {speed}×  ·  session {sid}", "Coins ranked by rotation inflow in the last 10 minutes; Enter opens the coin card. Caption added in post.")
    run(f"ffmpeg -v error -y -f concat -safe 0 -i '{lst}' -i '{OUT}/cap-tui.png' -filter_complex \"[0:v]scale=1440:-2,pad=1440:900:0:0:color=0x050505,format=yuv420p[v];[v][1:v]overlay=0:main_h-overlay_h\" -r 30 -c:v libx264 -crf 18 -pix_fmt yuv420p '{OUT}/seg-tui.mp4'")

web_in = max(0.0, m["radar"] - 1.0)
web_dur = m["end"] - web_in
caption(OUT / "cap-web.png", f"WEB  ·  RADAR → coin → FLOW → evidence → alerts  ·  same replay session {sid} at {speed}×  ·  clock from {utc(start_ts)} UTC", "Every number is computed by the running product from indexed trades as of the replay clock. External context is off in replay. Caption added in post.")
run(f"ffmpeg -v error -y -ss {web_in:.2f} -t {web_dur:.2f} -i '{RAW}/web-walkthrough.webm' -i '{OUT}/cap-web.png' -filter_complex \"[1:v]format=rgba,fade=t=out:st=2.6:d=0.4:alpha=1[c];[0:v][c]overlay=0:main_h-overlay_h:enable='lte(t,3.0)',format=yuv420p\" -r 30 -c:v libx264 -crf 18 -pix_fmt yuv420p '{OUT}/seg-web.mp4'")
parts = ([f"file '{OUT}/seg-tui.mp4'"] if frames else []) + [f"file '{OUT}/seg-web.mp4'"]
(OUT / "concat.txt").write_text("\n".join(parts) + "\n")
run(f"ffmpeg -v error -y -f concat -safe 0 -i '{OUT}/concat.txt' -c copy -movflags +faststart '{OUT}/stampede-v4-radar.mp4'")

cut_from = max(0.0, m["drawer"] - 2.2)
caption(OUT / "cap-cut.png", f"REPLAY {speed}×  ·  top radar coin {top}: coin card, then FLOW (where its wallets came from)", "Rotation inflow = same address sold another coin, then bought this one. Not proof of money flow or of future price. Caption added in post.")
run(f"ffmpeg -v error -y -ss {cut_from:.2f} -t 5 -i '{RAW}/web-walkthrough.webm' -i '{OUT}/cap-cut.png' -filter_complex \"[0:v][1:v]overlay=0:main_h-overlay_h,format=yuv420p\" -r 30 -c:v libx264 -crf 18 -pix_fmt yuv420p -movflags +faststart '{OUT}/cut-5s-radar-to-flow.mp4'")
for name in ("radar", "radar-playing", "drawer", "flow", "evidence", "evidence-rows", "alerts"):
    if (RAW / f"{name}.png").exists():
        run(f"cp '{RAW}/{name}.png' '{OUT}/frame-{name}.png'")
run(f"ffmpeg -v error -y -i '{OUT}/frame-radar-playing.png' -vf scale=720:450 '{OUT}/frame-radar-720x450.png'")
run(f"cd '{HERE}' && shasum -a 256 raw/web-walkthrough.webm out/*.mp4 > out/SHA256SUMS.txt")
dur = subprocess.run(f"ffprobe -v error -show_entries format=duration -of csv=p=0 '{OUT}/stampede-v4-radar.mp4'", shell=True, capture_output=True, text=True).stdout.strip()
(OUT / "cuts.txt").write_text(f"session {sid}, replay {speed}x, clock start {utc(start_ts)} UTC, top coin {top}\nweb segment from {web_in:.1f}s for {web_dur:.1f}s; marks: {json.dumps(m)}\ncut: from {cut_from:.1f}s for 5.0s\n")
print(f"walkthrough {dur}s; frames in {OUT}")
sys.exit(0)
