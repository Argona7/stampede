#!/usr/bin/env python3
"""Assemble demo v5 (one continuous take: 3D autopilot tour -> RADAR -> FLOW) with a short intro caption.

    .venv/bin/python demo/v5/build.py
Inputs : demo/v5/raw/web-walkthrough.webm, demo/v5/raw/marks.json
Outputs: demo/v5/out/stampede-v5-autopilot.mp4 (>= 30 s), demo/v5/out/cut-7s-stop.mp4, frames, SHA256SUMS.txt
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
RAW, OUT = HERE / "raw", HERE / "out"
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
stops = marks.get("stops", [])

# 0) terminal intro: the red block wordmark, the feed streaming the visible history, then Tab -> RADAR screen
TUI = HERE / "tui"
FRAME_S = 0.2
sel = [TUI / f"a-feed-{i:02d}.png" for i in range(0, 16)] + [TUI / f"a-feed-{i:02d}.png" for i in (20, 24, 29)] + [TUI / f"b-radar-{i:02d}.png" for i in range(1, 7)]
frames = [f for f in sel if f.exists()]
if frames:
    lst = OUT / "tui-frames.txt"
    lst.write_text("".join(f"file '{f}'\nduration {FRAME_S}\n" for f in frames) + f"file '{frames[-1]}'\n")
    caption(OUT / "cap-tui.png", f"TERMINAL  ·  stampede terminal  ·  the feed streams the visible history, then Tab → RADAR  ·  REPLAY {speed}×  ·  session {sid}", "Real Textual renders of the running TUI on the same replay session as the web take that follows. Caption added in post.")
    run(f"ffmpeg -v error -y -f concat -safe 0 -i '{lst}' -i '{OUT}/cap-tui.png' -filter_complex \"[0:v]scale=1440:-2,pad=1440:900:0:0:color=0x050505,format=yuv420p[v];[v][1:v]overlay=0:main_h-overlay_h\" -r 30 -c:v libx264 -crf 18 -pix_fmt yuv420p '{OUT}/seg-tui.mp4'")

web_in = 0.2
web_dur = m["end"] - web_in
caption(OUT / "cap-web.png", f"STAMPEDE  ·  Robinhood Chain memecoin rotations  ·  REPLAY {speed}× of a recorded hour  ·  session {sid}  ·  clock from {utc(start_ts)} UTC", "Autopilot flies to the strongest observed flows; every count is computed live from indexed trades. Same address, observed order of trades; not proof of money flow. Caption added in post.")
run(
    f"ffmpeg -v error -y -ss {web_in} -t {web_dur:.2f} -i '{RAW}/web-walkthrough.webm' -i '{OUT}/cap-web.png' -filter_complex \"[1:v]format=rgba,fade=t=out:st=3.4:d=0.5:alpha=1[c];[0:v][c]overlay=0:main_h-overlay_h:enable='lte(t,3.9)',format=yuv420p\" -r 30 -c:v libx264 -crf 18 -pix_fmt yuv420p '{OUT}/seg-web.mp4'"
)
parts = ([f"file '{OUT}/seg-tui.mp4'"] if frames else []) + [f"file '{OUT}/seg-web.mp4'"]
(OUT / "concat.txt").write_text("\n".join(parts) + "\n")
run(f"ffmpeg -v error -y -f concat -safe 0 -i '{OUT}/concat.txt' -c copy -movflags +faststart '{OUT}/stampede-v5-autopilot.mp4'")
# a 7-second cut: flight into stop 2 and its caption
s2 = m.get("stop2", m.get("stop1", 8.0))
cut_from = max(0.0, s2 - 4.6)
cap2 = stops[1]["caption"] if len(stops) > 1 else ""
caption(OUT / "cap-cut.png", f"REPLAY {speed}×  ·  autopilot stop: {cap2}", "Observed rotation: distinct wallets that sold A, then bought B within 30 minutes. Computed for this replay moment. Caption added in post.")
run(f"ffmpeg -v error -y -ss {cut_from:.2f} -t 7 -i '{RAW}/web-walkthrough.webm' -i '{OUT}/cap-cut.png' -filter_complex \"[0:v][1:v]overlay=0:main_h-overlay_h,format=yuv420p\" -r 30 -c:v libx264 -crf 18 -pix_fmt yuv420p -movflags +faststart '{OUT}/cut-7s-stop.mp4'")
for name in ("boot", "stop1", "stop2", "stop3", "stop4", "radar", "flow"):
    if (RAW / f"{name}.png").exists():
        run(f"cp '{RAW}/{name}.png' '{OUT}/frame-{name}.png'")
for t in (1.0, 3.5, 7.0, 12.0, 19.0, 26.0, 33.0, 40.0):
    run(f"ffmpeg -v error -y -ss {t} -i '{OUT}/stampede-v5-autopilot.mp4' -frames:v 1 '{OUT}/frame-t{int(t):02d}.png'")
run(f"ffmpeg -v error -y -i '{OUT}/frame-stop2.png' -vf scale=720:450 '{OUT}/frame-stop2-720x450.png'")
run(f"cd '{HERE}' && shasum -a 256 raw/web-walkthrough.webm out/*.mp4 > out/SHA256SUMS.txt")
dur = subprocess.run(f"ffprobe -v error -show_entries format=duration -of csv=p=0 '{OUT}/stampede-v5-autopilot.mp4'", shell=True, capture_output=True, text=True).stdout.strip()
(OUT / "cuts.txt").write_text(f"session {sid}, replay {speed}x, clock start {utc(start_ts)} UTC\nterminal intro: {len(frames)} Textual frames x {FRAME_S}s = {len(frames) * FRAME_S:.1f}s\nweb take from {web_in}s for {web_dur:.1f}s (marks {json.dumps(m)})\nstops: {json.dumps([s['caption'] for s in stops])}\ncut-7s-stop: from {cut_from:.1f}s\n")
print(f"walkthrough {dur}s; frames in {OUT}")
sys.exit(0)
