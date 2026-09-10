#!/usr/bin/env python3
"""Assemble the v2 demo from the raw recordings (no retiming, straight cuts, caption bars added in post).

    .venv/bin/python demo/v2/build.py
Inputs : demo/v2/tui/tui-*.png (Textual frames, 2 fps), demo/v2/raw/web-walkthrough.webm, demo/v2/raw/marks.json
Outputs: demo/v2/out/stampede-v2-walkthrough.mp4 (TUI 4 s + web ~18 s), demo/v2/out/cut-5s-pair-count-evidence.mp4, frames, SHA256SUMS
"""
from __future__ import annotations

import json
import subprocess
import sys
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
    from datetime import datetime, timezone

    return datetime.fromtimestamp(ts, timezone.utc).strftime("%H:%M:%S")


marks = json.loads((RAW / "marks.json").read_text())
m = {x["name"]: x["offset_s"] for x in marks["marks"]}
sid = marks["session_id"]
start_ts = marks["replay_clock_start_ts"]
speed = marks["replay_speed"]
edge = marks["edge"]

# 1) TUI segment: 8 frames x 0.5 s = 4 s, scaled to 1440 wide and padded to 900
frames = sorted(TUI.glob("tui-*.png"))[1:9]
lst = OUT / "tui-frames.txt"
lst.write_text("".join(f"file '{f}'\nduration 0.5\n" for f in frames) + f"file '{frames[-1]}'\n")
caption(OUT / "cap-tui.png", f"TERMINAL  ·  stampede terminal  ·  REPLAY {speed}×  ·  session {sid}  ·  replay clock from {utc(start_ts)} UTC", "Rows arrive as the shared clock reaches their buy transactions. Red mark = received since the previous poll. Caption added in post.")
run(f"ffmpeg -v error -y -f concat -safe 0 -i '{lst}' -i '{OUT}/cap-tui.png' -filter_complex \"[0:v]scale=1440:-2,pad=1440:900:0:0:color=0x050505,format=yuv420p[v];[v][1:v]overlay=0:main_h-overlay_h\" -r 30 -c:v libx264 -crf 18 -pix_fmt yuv420p '{OUT}/seg-tui.mp4'")

# 2) Web segment: from after page load to the end; first 3 s carry the transition caption
web_in = 1.2
web_dur = m["end"] - web_in
caption(OUT / "cap-web.png", f"WEB SCENE  ·  same replay session {sid}  ·  clock reset to {utc(start_ts)} UTC and played again at {speed}×", "Recorded separately from the terminal clip; the cut between them is an edit, not a live continuation. Caption added in post.")
run(
    f"ffmpeg -v error -y -ss {web_in} -t {web_dur:.2f} -i '{RAW}/web-walkthrough.webm' -i '{OUT}/cap-web.png' -filter_complex \"[1:v]format=rgba,fade=t=out:st=2.6:d=0.4:alpha=1[c];[0:v][c]overlay=0:main_h-overlay_h:enable='lte(t,3.0)',format=yuv420p\" -r 30 -c:v libx264 -crf 18 -pix_fmt yuv420p '{OUT}/seg-web.mp4'"
)
run(f"printf \"file '{OUT}/seg-tui.mp4'\\nfile '{OUT}/seg-web.mp4'\\n\" > '{OUT}/concat.txt' && ffmpeg -v error -y -f concat -safe 0 -i '{OUT}/concat.txt' -c copy -movflags +faststart '{OUT}/stampede-v2-walkthrough.mp4'")

# 3) 5-second cut: selection -> flight -> caption
cut_from = m["select"] - 0.4
caption(OUT / "cap-cut.png", f"REPLAY {speed}× of the recorded PONS v2 sample  ·  pair with the most distinct wallets in the visible 30 min is selected", "Count and names are computed for this replay moment. Same address, observed order of trades; not proof of money flow or shared ownership. Caption added in post.")
run(f"ffmpeg -v error -y -ss {cut_from:.2f} -t 5 -i '{RAW}/web-walkthrough.webm' -i '{OUT}/cap-cut.png' -filter_complex \"[0:v][1:v]overlay=0:main_h-overlay_h,format=yuv420p\" -r 30 -c:v libx264 -crf 18 -pix_fmt yuv420p -movflags +faststart '{OUT}/cut-5s-pair-count-evidence.mp4'")

# 4) frames
run(f"ffmpeg -v error -y -ss 4.6 -i '{OUT}/cut-5s-pair-count-evidence.mp4' -frames:v 1 '{OUT}/frame-cut-end.png'")
run(f"ffmpeg -v error -y -ss 2.0 -i '{OUT}/seg-tui.mp4' -frames:v 1 '{OUT}/frame-tui.png'")
for name in ("web-overview", "web-pulses", "web-follow", "web-evidence", "web-evidence-rows"):
    run(f"cp '{RAW}/{name}.png' '{OUT}/frame-{name}.png'")
run(f"ffmpeg -v error -y -i '{OUT}/frame-web-evidence.png' -vf scale=720:450 '{OUT}/frame-web-evidence-720x450.png'")
run(f"cd '{HERE}' && shasum -a 256 raw/web-walkthrough.webm tui/tui-0*.png out/*.mp4 > out/SHA256SUMS.txt")
(OUT / "cuts.txt").write_text(
    f"session {sid}, replay {speed}x, clock start {utc(start_ts)} UTC\n"
    f"seg-tui.mp4: frames tui-01..tui-08 (Textual renders, 0.5 s each) = 4.0 s\n"
    f"seg-web.mp4: web-walkthrough.webm from {web_in}s for {web_dur:.1f}s, transition caption for 3 s\n"
    f"cut-5s-pair-count-evidence.mp4: web-walkthrough.webm from {cut_from:.1f}s for 5.0 s (select at {m['select']}s, evidence settled at {m['evidence']}s)\n"
    f"edge in the cut: {edge['from']} -> {edge['to']}, {edge['wallets_main']} distinct wallets at selection time\n"
)
dur = subprocess.run(f"ffprobe -v error -show_entries format=duration -of csv=p=0 '{OUT}/stampede-v2-walkthrough.mp4'", shell=True, capture_output=True, text=True).stdout.strip()
print(f"walkthrough {dur}s; cut 5.0s; frames in {OUT}")
sys.exit(0)
