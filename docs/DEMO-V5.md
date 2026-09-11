# Demo v5: terminal intro + one 39-second take — 3D autopilot tour, RADAR, FLOW

`demo/v5/out/stampede-v5-autopilot.mp4` (44.3 s, 1440×900, H.264, 16.7 MB): a 5.0-second terminal intro
(25 real Textual renders of `stampede terminal` on the same replay session: the red block wordmark, the feed
streaming the visible history, new rows arriving, `Tab` → RADAR) followed by the continuous browser take
(39.1 s). `demo/v5/out/cut-7s-stop.mp4` (7 s, one autopilot stop), frames `demo/v5/out/frame-t*.png`, raw
`demo/v5/raw/web-walkthrough.webm` + `marks.json`, terminal frames `demo/v5/tui/`. Single continuous
recording of the running product on one replay session (id in `marks.json`), replay 20×, no cuts inside the
take. Edits: the terminal segment in front, a caption bar under it, and a 3.9-second intro caption on the take.
Times below are in the final file (take time + 5.0 s − 0.2 s trim).

| Time | What happens |
|---|---|
| 0–5 s | Terminal intro: `stampede terminal` with the red block `STAMPEDE` wordmark; `STREAMING 535 / 6,705` → the feed fills in time order, then `+98 new` rows arrive on the replay clock, `Tab` opens the RADAR screen (presets, score parts, coin card) |
| 5–8 s | MAP in presentation opens: the tape streams the visible history on the right while the 3D network builds up in time order and the camera dollies in. Left: live HUD — sequences per minute, wallets rotating (10 min), coins with inflow, flows in range — and the TOP INFLOW list |
| 8 s → | Replay runs at 20×: pulses travel along edges that just received sequences, edges keep a red afterglow, coins with inflow get red halos and `+N` labels, HUD rows flash when a coin gains wallets |
| 11.5 s | Autopilot stop 1: `54 WALLETS · SOLD Piecoin·1a01 → BOUGHT TruffleHog·7b08` — the camera drops to A, travels along the route to B and settles; the caption is computed for this replay moment |
| 18.5 s | Stop 2: `37 WALLETS · SOLD PUCHATO → BOUGHT STEPPER` |
| 25.7 s | Stop 3: `29 WALLETS · SOLD egregore → BOUGHT credit` (inside the dense core: depth, occlusion, pulses around) |
| 32.5 s | Stop 4: `19 WALLETS · SOLD AUTON·5eab → BOUGHT egregore` |
| 34.8 s | `1` → RADAR: the same coins as a ranked board (score parts, sources, price, holders, alert journal), rows flashing as the replay runs |
| 39.9 s | FLOW of the top radar coin: sources left, coin centre, destinations right, ribbon width = distinct wallets |

Autopilot (`A` key or the button in the corner, `?autopilot=1`) visits the strongest observed flows of the
visible range in order, biggest first, one every 7 s; any click, drag or wheel stops it. It is a viewing
mode, not a signal.

## Frame timing

`PERF=1 node e2e/record-demo-v5.mjs` in headless Chromium forced onto the GPU (`--use-angle=metal`), 1440×900,
after four autopilot flights with the HUD, tape, halos and afterglow active: **60 fps (vsync), p50 16.7 ms,
p95 16.8 ms, 230 edges / 166 nodes** (`demo/v5/perf/perf.json`). The headed browser on this Mac runs at
120 Hz (p50 8.3 ms measured earlier).

## Reproduce

```sh
python scripts/serve_daemon.py start --mode replay --port 8791
cd web && node e2e/record-demo-v5.mjs && cd .. && .venv/bin/python demo/v5/build.py
```

By hand: `http://127.0.0.1:8791/?view=map&layout=presentation&autopilot=1`, press `space` to play.
