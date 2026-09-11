# Demo v2: terminal + spatial scene

Files live in `demo/v2/`. Everything shown is the running product reading one replay session of the
recorded PONS v2 sample; no event, count or name was staged.

## What is on screen

| Time in `stampede-v2-walkthrough.mp4` | Surface | What happens |
|---|---|---|
| 0.0–4.0 s | terminal (`stampede terminal`) | Red block wordmark, status line `CHAIN · [REPLAY 20×] · CLOCK · RANGE · WINDOW · SESSION`. The visible history (6,705 real sequences) **streams into the feed** with a `STREAMING n / total` counter and progress bar while the activity sparkline builds; then the shared clock runs and rows arrive (`+N new`, red marks), selected pair on the right, hot rotations below |
| 4.0–8.0 s | web, presentation layout | **Boot**: the same history streams through the canvas tape on the right (`STREAMING n / total OBSERVED SEQUENCES`, real rows: time · wallet · SOLD A → BOUGHT B · grade) while the 3D network **builds up in the order the sequences were first observed** and the camera dollies in from far; then OVERVIEW with a slow bounded drift, `SHOWING N OF M EDGES` in the strip |
| 4.6 s → | web | Replay playing at 20×: pulses run along edges that just received a confirmed sequence (`+N sequences`); the tape pushes the same rows in |
| ~11.3 s | web | The pair with the most distinct wallets in the visible 30 min is selected. FOLLOW: camera drops to A (white ring, `A · SOLD`), travels along the red route to B (red, `B · BOUGHT`) |
| ~14.5 s | web | EVIDENCE: camera settles; caption `54 WALLETS / SOLD Piecoin → BOUGHT TruffleHog / within 30 min · observed sequences · REPLAY 10× · HH:MM:SS UTC` |
| ~18 s | web | `E`: evidence rows slide in (wallet, sold/bought amounts, UTC block time, block, tx links); the pair reframes to the left |
| ~20 s | web | Replay paused from the shared session; `Esc` returns to the overview |

The 5-second cut `cut-5s-pair-count-evidence.mp4` is the selection → flight → caption moment alone.

Nothing in the intro is decorative: every flying row is an observed sequence from the API, the build-up
order is `first_ts` of each edge, and the counters are the real totals of the visible 30 minutes.

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

- Terminal segment: twenty consecutive Textual screenshots at 5 fps (SVG export of the real widget tree,
  converted to PNG with Chromium), starting at the first frame where the history stream is visible,
  scaled to 1440 px wide and padded to 900. Textual adds a window frame to its exports; it is not part of
  the product.
- Web segment: straight trim of the Playwright recording (`raw/web-walkthrough.webm`) from 0.2 s (the
  boot is the intro), H.264, no retiming. A 3-second caption bar marks the transition; it fades out.
- 5-second cut: straight trim from `select − 0.4 s`, caption bar for the whole cut.
- No audio. Offsets: `demo/v2/out/cuts.txt`; checksums: `demo/v2/out/SHA256SUMS.txt`.

## Reproduce

```sh
python scripts/serve_daemon.py start --mode replay --port 8791
# terminal frames while the replay runs (seek/play as in scripts, or by hand in the TUI)
.venv/bin/python scripts/tui_shot.py --size 180x50 --out demo/v2/tui --wait 0.6 --frames 33 --every 0.2
cd web && node e2e/svg2png.mjs ../demo/v2/tui/*.svg
node e2e/record-demo-v2.mjs                      # web recording -> demo/v2/raw
PERF=1 DPR=2 OUT=../demo/v2/perf node e2e/record-demo-v2.mjs
cd .. && .venv/bin/python demo/v2/build.py       # mp4s, frames, checksums
```

To record by hand: run the terminal (`uv run stampede terminal --api-url http://127.0.0.1:8791`) in a
120×36 or larger window and the web page (`?layout=presentation`) at 1440×900; press `space`
in either to play/pause the shared clock, click a line or a ticker row for the flight, `E` for rows,
`T` to hide/show the tape, `Esc` back.

## Live check (2026-09-11, real chain, not the sample)

`scripts/verify_live_sequences.py` against a server started with `--mode live` (port 8793): after ~4
minutes the tail was at block 60,152,073 with head lag 2.7 s, 66 ticks, 207 direct/clean sequences
observed in the previous 30 minutes of real time. The newest six were re-verified independently through
raw RPC: both transactions exist, the wallet sent the sold token in the sell tx and received the bought
token in the buy tx (ERC-20 Transfer logs), the sell block precedes the buy block, and block header
times match the recorded times (exact or within the ≈ interpolation). Result: 6/6. Two of them have
`tx.from != wallet` (router/relayer transactions): the Transfer-based attribution is what makes those
visible at all. Raw output: `demo/v3/live/verify.json`; terminal frames on live data:
`demo/v3/live/tui-live-*.png` (`[LIVE] · LAST BLOCK 09:30:58 UTC · DATA AGE 3s`).

Live top rotations at that moment (last 30 min): `BEARLY·daed → SQUIDMIND·1c82` 6 wallets,
`AAPL·d4ec → FIRST·1d80` 5, `FLYYIELD → SQUIDMIND·1c82` 4, `TADPOLEBRAIN → LIQ·7a80` 4. Tickers with a
suffix are coins whose symbol is reused by other coins (502 such symbols in the store, e.g. RBNHD ×28):
the suffix is the last four hex digits of the address, so `A → B` is never two different coins with one
name.

## Error and edge states (frames)

- `demo/v2/errors/web-provider-error.png`: live server with a rejected provider key: inverted red block
  `! PROVIDER ERROR · rpc error -32600: Must be authenticated!`, `LAST BLOCK ? UTC`, nothing drawn as live.
- `demo/v2/errors/tui-provider-error-00.png`: same server in the terminal: `[LIVE · PAUSED (provider
  error)]`, inverted red alert, empty feed with `NO OBSERVED SEQUENCES`, no substituted numbers.
