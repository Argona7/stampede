# Demo: how it was recorded, what it shows, what was edited

All files live in `demo/`. Nothing in the data was staged: the scenario is the last four minutes of the
recorded sample, which happen to contain the largest observed wave (TruffleHog → LUNAR, 124 distinct
wallets by the end of the window).

## Mode and labelling

- The recording runs the server in **REPLAY** mode (`python scripts/serve_daemon.py start --mode replay`).
  The amber `REPLAY` badge, the corner label `REPLAY · recorded PONS v2 sample, 60 min`, the replay clock
  in the header and the sentence "Recorded data played back, not the current market" are part of the
  interface, not of the edit.
- Replay speed 10x. Five seconds of clip are fifty seconds of chain time. **Clip duration says nothing
  about detection latency.** Measured live-mode head lag is 2–5 s (see `docs/COVERAGE.md` and
  `/api/status` in live mode); it is a different number from anything visible in these clips.
- Observation window of the sample: 2026-09-10 16:29:57 → 17:29:57 UTC, PONS v2 curves and their
  graduated Uniswap v4 pools only.

## Three key states (screenshots in `demo/out/`)

1. `state-3-new-events.png`: the map changes when new confirmed sequences arrive. Edges that just
   received a `sell A → buy B` pulse amber once; the ticker prepends the rows. Replay clock 17:27:57 UTC.
2. `state-1-edge.png`: an edge is selected (TruffleHog → LUNAR, 35 wallets at 17:28:37 UTC, all clean,
   1 ambiguous not counted). The camera focuses on the pair; everything else dims.
3. `state-2-evidence.png`: the sequences behind the edge. Each row: wallet, what it sold and bought, exact
   UTC block time, block number, transaction hash linking to `robinhoodchain.blockscout.com/tx/…`.

Also captured: `state-0-map.png` (map before playback) and `state-4-token.png` (coin view: buyers,
sellers, first-time vs repeat buyers, inbound and outbound edges).

## Files

| File | What it is |
|---|---|
| `demo/raw/walkthrough.webm` | Untouched Playwright recording, 1440×900, ~44 s |
| `demo/raw/marks.json` | Timeline marks written by the script (offsets in seconds) |
| `demo/raw/state-*.png` | Untouched screenshots taken by the script |
| `demo/out/walkthrough-1440x900.mp4` | Same recording re-encoded to H.264, no cuts |
| `demo/out/cut-5s-trufflehog-to-lunar.mp4` | 5 s: edge selected from the ticker, camera focus, wallet count and rows |
| `demo/out/cut-5s-new-events.mp4` | 5 s: replay running, new sequences pulsing on the map |
| `demo/out/caption-*.png` | Caption bars overlaid on the cuts (rendered with Pillow, Menlo) |
| `demo/out/cuts.txt` | Exact source offsets of both cuts |
| `demo/out/SHA256SUMS.txt` | Checksums of the raw recording and the three mp4 files |

## Edit notes

- Cuts are straight trims of the raw recording (`ffmpeg -ss … -t 5`), re-encoded to H.264, no speed
  change, no retiming, no frame removed inside a cut.
- The only added element is a 64 px caption bar at the bottom of each cut. Its text states the replay
  speed, the UTC time shown, what the highlighted element means, and that the caption was added in post.
- No audio.

## Reproduce

```sh
python scripts/serve_daemon.py start --mode replay --port 8791
cd web && npm install && node e2e/record-demo.mjs        # writes demo/raw/*
cd ../demo && cat out/cuts.txt                           # offsets used for the cuts
```

The script (`web/e2e/record-demo.mjs`) moves the replay clock to 330 s before the end of the sample,
plays at 10x, pauses, selects `TruffleHog → LUNAR` from the ticker, scrolls the rows, then opens the LUNAR
coin view. Edit the offsets in `cuts.txt` if a re-recording shifts the timeline.

## Record it yourself

1. `python scripts/serve_daemon.py start --mode replay` (or `--mode live` for the chain head).
2. Open `http://127.0.0.1:8791/` in a 1440×900 window and start any screen recorder.
3. Replay: press `10x`, then `Play`; wait for amber pulses; press `Pause`; click a ticker row.
4. Live: nothing to press; the ticker and the map follow the chain with the data age shown top right.

## Comprehension check

An independent model instance with no context saw `state-1-edge.png` and was asked what the highlighted
line means, whether the screen claims the sale paid for the purchase, and whether it claims the wallets
belong to one person. Its reading: the line is the set of wallets that sold TruffleHog and then bought
LUNAR within 30 minutes, thickness = wallets; the screen explicitly denies both stronger claims
("Not proof that the sale paid for the purchase", "not proof that different addresses share an owner").

Two labels were changed after its remarks: "Evidence: 39 of 39 sequences" became "Observed sequences: 39.
A wallet appears once per sequence, so this can exceed the wallet count" (35 wallets vs 39 rows had looked
inconsistent), and the venue jargon "on curve" became "on the PONS bonding curve" / "in the Uniswap v4
pool". It also noted that the tagline "Watch wallets move between coins" nudges a reader toward picturing
money flow; the tagline is the product's chosen phrase and is left for the owner to decide.
