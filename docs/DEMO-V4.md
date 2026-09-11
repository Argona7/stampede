# Demo v4: RADAR → coin → FLOW → evidence → alerts

Files: `demo/v4/out/stampede-v4-radar.mp4` (26.9 s), `demo/v4/out/cut-5s-radar-to-flow.mp4` (5 s), frames
`demo/v4/out/frame-*.png`, raw `demo/v4/raw/web-walkthrough.webm` + `marks.json`, terminal frames `demo/v4/tui/`.
Same replay session on both surfaces (`marks.json.session_id`), replay 20×, clock from the start of the sample's
last 7 minutes.

| Time | Surface | What happens |
|---|---|---|
| 0–4 s | terminal | Feed of observed sequences, then `Tab` → RADAR screen: coins ranked by rotation inflow (score, inflow 10 m, acceleration, sources, age, stage, price change, X), `Enter` opens the coin card |
| 4–8 s | web RADAR | The board: score with its parts, coin with age and curve progress, inflow 10 m with acceleration arrow, 30-min sparkline, top source coins with wallet counts, price 5 m / 1 h, X mentions (n/a in replay), holders; presets Under radar / Graduating / Smart rotators / All; alert journal on the right |
| 5 s → | web RADAR | Replay running at 20×: inflow counters move, rows flash red when a coin gains wallets |
| ~13 s | coin drawer | Top coin: on-chain as-of numbers, launched time and deployer, price changes, rotation from/to, holders, X attention, declared socials, market now (when fetched) |
| ~16 s | FLOW | Ego network: sources on the left, the coin in the centre, destinations on the right; ribbon width = distinct wallets; labels never overlap |
| ~20 s | MAP | Clicking a ribbon opens the pair in the 3D scene with the caption `N WALLETS / SOLD A → BOUGHT B` and the evidence rows |
| ~24 s | RADAR | Back to the board: the alert journal with 30-minute outcomes |

## What the alerts say (honestly)

The journal in the recording shows the v2 rule (`inflow 8–40 & age < 1 h & not already +100% in 10 min &
score ≥ 60`, X mentions ≤ 3 or unknown). Outcomes are the price change at +30 min on indexed trades. In the
replay of the recorded hour most outcomes are negative even when the coin doubled in between: the backtest
(`docs/RESEARCH-RUNNERS.md`) measures a 20% chance of a ≥2× move within 30 min for this rule (3.8× the base
rate) and a median price at +30 min of about −36%. Catching the move and holding for 30 minutes are
different bets; the tool shows both numbers.

## Reproduce

```sh
python scripts/serve_daemon.py start --mode replay --port 8791
uv run stampede wallet-scores --from data/research-12h.sqlite      # wallet quality (optional)
# terminal frames (feed streaming, then Tab -> radar), replay playing at 20x
.venv/bin/python scripts/session.py reset && .venv/bin/python scripts/session.py play
.venv/bin/python scripts/tui_shot.py --size 180x50 --out demo/v4/tui --wait 1.4 --frames 11 --every 0.2 --prefix a-feed
.venv/bin/python scripts/tui_shot.py --size 180x50 --out demo/v4/tui --wait 4.5 --keys tab --key-wait 1.5 --frames 7 --every 0.25 --prefix b-radar
cd web && node e2e/svg2png.mjs ../demo/v4/tui/*.svg
# let the replay run ahead so the alert journal has outcomes, then record
node e2e/record-demo-v4.mjs && cd .. && .venv/bin/python demo/v4/build.py
```

By hand: open `http://127.0.0.1:8791/?view=radar`, press `space` to play, click a row (drawer), `Flow`
button, click a ribbon (map evidence), `1` back to the radar. In the terminal: `Tab`, `↑/↓`, `Enter`,
`r` to refresh a coin's context (live mode fetches X / market / holders).
