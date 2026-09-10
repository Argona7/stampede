# Demo v2: terminal + spatial scene

Files live in `demo/v2/`. Everything shown is the running product reading one replay session of the
recorded PONS v2 sample; no event, count or name was staged.

## What is on screen

| Time in `stampede-v2-walkthrough.mp4` | Surface | What happens |
|---|---|---|
| 0.0–4.0 s | terminal (`stampede terminal`) | Red block wordmark, status line `CHAIN · [REPLAY 10×] · CLOCK · RANGE · WINDOW · SESSION`, feed of observed sequences growing while the shared clock runs (`+N new`, red marks on the newest rows), selected pair on the right, hot rotations and the activity sparkline below |
| 4.0–7.5 s | web, presentation layout | OVERVIEW: the whole network in 3D from a 3/4 angle, depth via perspective and fog, top-degree coins labelled, `SHOWING N OF M EDGES` in the strip |
| 4.6 s → | web | Replay playing: pulses run along edges that just received a confirmed sequence, each with `+N sequences` |
| ~11.3 s | web | The pair with the most distinct wallets in the visible 30 min is selected. FOLLOW: camera drops to A (white ring, `A · SOLD`), travels along the red route to B (red, `B · BOUGHT`) |
| ~14.5 s | web | EVIDENCE: camera settles; caption `54 WALLETS / SOLD Piecoin → BOUGHT TruffleHog / within 30 min · observed sequences · REPLAY 10× · HH:MM:SS UTC` |
| ~18 s | web | `E`: evidence rows slide in (wallet, sold/bought amounts, UTC block time, block, tx links); the pair reframes to the left |
| ~20 s | web | Replay paused from the shared session; `Esc` returns to the overview |

The 5-second cut `cut-5s-pair-count-evidence.mp4` is the selection → flight → caption moment alone.

## Same session, two recordings

Both clips were recorded against the same server process and the same replay session id (printed in
the terminal status line and in the web strip; also in `demo/v2/raw/marks.json`). The terminal clip was
recorded first; the web clip then reset the same session clock to the same start (`to_ts − 330 s`) and
played again at 10×. The cut between the two surfaces is an edit, not a live continuation, and the
caption bar says so.

## Replay speed vs. detection

10× replay means five seconds of clip are fifty seconds of chain time. Nothing in the clip is a
detection latency. Measured live-mode head lag of the tail is 2–5 s (`/api/status` → `live.head_lag_s`).

## Frame timing (measured, not exported fps)

`web/e2e/record-demo-v2.mjs` with `PERF=1` reads the in-page frame-time readout (`?perf=1`) after 12 s of
playback plus a flight, in headless Chromium forced onto the real GPU (`--use-angle=metal`):

| Run | Pixels | Result |
|---|---|---|
| headed Chromium, 1440×900, dpr 1 (Playwright browser) | 1440×836 canvas | 120 fps (120 Hz display), p50 8.3 ms, p95 10 ms, 262 edges / 180 nodes |
| headless Chromium + Metal, dpr 1 | 1440×836 | 60 fps (vsync), p50 16.7 ms, p95 16.8 ms |
| headless Chromium + Metal, dpr 2 (Retina) | 2880×1672 | 60 fps (vsync), p50 16.7 ms, p95 16.8 ms |
| headless Chromium, SwiftShader (no GPU) | dpr 2 | 15 fps, p50 66.7 ms: **software rendering, reported for contrast only** |

GPU: `ANGLE (Apple, ANGLE Metal Renderer: Apple M5 Pro)`. Raw numbers: `demo/v2/perf/perf.json`, `demo/v2/perf-dpr1/perf.json`.

## Edit notes

- Terminal segment: eight consecutive Textual screenshots (SVG export of the real widget tree, converted
  to PNG with Chromium), 0.5 s each, scaled to 1440 px wide and padded to 900. Textual adds a window
  frame to its exports; it is not part of the product.
- Web segment: straight trim of the Playwright recording (`raw/web-walkthrough.webm`) from 1.2 s, H.264,
  no retiming. A 3-second caption bar marks the transition; it fades out.
- 5-second cut: straight trim from `select − 0.4 s`, caption bar for the whole cut.
- No audio. Offsets: `demo/v2/out/cuts.txt`; checksums: `demo/v2/out/SHA256SUMS.txt`.

## Reproduce

```sh
python scripts/serve_daemon.py start --mode replay --port 8791
# terminal frames while the replay runs (seek/play as in scripts, or by hand in the TUI)
.venv/bin/python scripts/tui_shot.py --size 180x50 --out demo/v2/tui --wait 4.5 --frames 9 --every 0.5
cd web && node e2e/svg2png.mjs ../demo/v2/tui/*.svg
node e2e/record-demo-v2.mjs                      # web recording -> demo/v2/raw
PERF=1 DPR=2 OUT=../demo/v2/perf node e2e/record-demo-v2.mjs
cd .. && .venv/bin/python demo/v2/build.py       # mp4s, frames, checksums
```

To record by hand: run the terminal (`uv run stampede terminal --api-url http://127.0.0.1:8791`) in a
120×36 or larger window and the web page (`?layout=presentation`) at 1440×900; press `space`
in either to play/pause the shared clock, click a line or a ticker row for the flight, `E` for rows,
`Esc` back.

## Error and edge states (frames)

- `demo/v2/errors/web-provider-error.png`: live server with a rejected provider key: inverted red block
  `! PROVIDER ERROR · rpc error -32600: Must be authenticated!`, `LAST BLOCK ? UTC`, nothing drawn as live.
- `demo/v2/errors/tui-provider-error-00.png`: same server in the terminal: `[LIVE · PAUSED (provider
  error)]`, inverted red alert, empty feed with `NO OBSERVED SEQUENCES`, no substituted numbers.
