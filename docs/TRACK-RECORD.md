# Live paper run: track record

Generated 2026-09-12 18:48:06 UTC by `stampede track-record` from `data/live-engine.sqlite`, counting from the engine start at 2026-09-12 16:05:06 UTC.

**Every position below is simulated.** The engine's `edge_enter` verdicts open paper positions filled by the curve arithmetic at the *next* block's observed reserves (1 % fee, the coin's creator tax, snipe tax inside the 3-s window, price impact of our own size); exits follow the plan of docs/RESEARCH-EDGE.md every block. No order was sent, no wallet was funded, nobody earned or lost these amounts. Alert outcomes are price changes measured on indexed trades. MEV, failed transactions and gas are not modelled.

## Run (engine `/api/perf`)

| item | value |
|---|---|
| engine started | 2026-09-12 17:24:26 UTC |
| uptime at the snapshot | 1 h 23 min (5,020 s) |
| blocks processed | 50,507 (49,445 live), 61279694 → 61329522 |
| gaps found / gap blocks / backfilled / unfilled | 2 / 384 / 384 / 0 |
| skipped at start | none |
| head → last event published, ms (n 49445) | p50 118.2 · p95 262.0 · p99 1003.9 · max 38831.2 |
| feed | wss://robinhood-rpc.publicnode.com · reconnects 0 · failovers 0 · stalls 0 · returns to preferred 0 |
| events | 380,244 total · 60.98 / s in the last minute |
| writer | max lag 1.24 s · errors 0 |
| process | RSS 414.1 MB · CPU 61.5 % of one core · ledger errors 0 · notifications 0 |

Full `/api/perf` snapshot: `docs/TRACK-RECORD-perf.json`.

## Alerts fired and their outcomes

Outcome = median price of the last 5 trades at +30 / +60 min of chain time vs the price at the alert, in percent; `due` counts the alerts old enough to have one.

| rule | fired | coins | due +30 | with +30 | up +30 | ≥ 2× +30 | median +30 | mean +30 | due +60 | with +60 | up +60 | median +60 | graduated after |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `edge_enter` | 13 | 13 | 11 | 10 | 6 | 1 | +3.3 % | +2.0 % | 8 | 7 | 1 | -36.8 % | 2 |
| `under_radar_top5` | 139 | 134 | 118 | 100 | 18 | 7 | -53.8 % | -10.6 % | 96 | 75 | 11 | -62.4 % | 15 |

### `edge_enter` alerts (13 shown, newest first)

| clock (UTC) | coin | p(2×/30m) | size ETH | age | price | +30 min | +60 min |
|---|---|---|---|---|---|---|---|
| 09-12 18:45:39 | RIDER `0xf896dad5` | 50 % | 0.0050 | 3 min | 5.1e-09 | pending | pending |
| 09-12 18:35:03 | ROUTR `0x6e83f4f5` | 49 % | 0.0050 | 12 min | 6.03e-09 | pending | pending |
| 09-12 18:09:44 | HITS `0xb743dea7` | 46 % | 0.0077 | 19 min | 5.26e-09 | +3.3 % | pending |
| 09-12 17:56:46 | NFTSTR `0xa99c60ba` | 48 % | 0.0094 | 9 min | 5.49e-09 | -21.0 % | pending |
| 09-12 17:48:57 | YKICIS `0x757c02e2` | 46 % | 0.0200 | 22 min | 5.76e-09 | +9.9 % | pending |
| 09-12 17:43:38 | DGAI·bbd0 `0x7bb410dc` | 46 % | 0.0050 | 1 min | 4.14e-09 | +130.8 % | -70.0 % |
| 09-12 17:34:47 | RULEPAD·d1a5 `0xf95c96b6` | 47 % | 0.0083 | 11 min | 3.84e-09 | -51.6 % | -54.1 % |
| 09-12 17:09:19 | STOCKPILOT `0xff2adc83` | 50 % | 0.0071 | 10 min | 5.69e-09 | +32.7 % | +13.8 % |
| 09-12 16:21:58 | RNCAT `0x5fb515ee` | 47 % | 0.0200 | 50 min | 4.05e-09 | -16.7 % | -36.8 % |
| 09-12 16:21:30 | TIDE·84d5 `0x83534d1d` | 46 % | 0.0155 | 21 min | 3.29e-09 | +7.1 % | -2.2 % |
| 09-12 16:18:50 | HABITS·650c `0x85bd531b` | 51 % | 0.0188 | 102 min | 3.84e-09 | +1.9 % | -3.9 % |
| 09-12 16:12:47 | JUNKPAD·1ab7 `0x75d959da` | 47 % | 0.0050 | 2 min | 5.99e-09 | no price | no price |
| 09-12 16:09:19 | Why `0x4252d8fd` | 49 % | 0.0050 | 58 s | 6.64e-09 | -76.0 % | -76.0 % |

## Paper ledger (simulated)

Size cap 0.02 ETH per position, at most 3 open, daily stop −5 % of the bankroll; plan `tp=100%x50% trail=0.25 sl=0.3 time=45m inflow_dies=on smart_exit=off`; fill at the first block after the decision block; USD via `fx_rates` available.

| statistic | value |
|---|---|
| closed trades (coins) | 13 (13) |
| open positions | 0 · unrealized +0.0000 ETH · exposure 0.0000 ETH |
| hit rate | 23.1 % (3 of 13) |
| expectancy per trade | -0.0024 ETH (-18.6 % of the stake) |
| expectancy per trade, USD | $-6.07 (13 of 13 with an hourly rate) |
| 95 % CI of the expectancy | [-0.0050, -0.0003] ETH — bootstrap of the mean pnl per trade, resampling coins with replacement, 1000 draws, seed 7 |
| total | -0.0312 ETH · $-78.97 |
| profit factor | 0.15 |
| max drawdown (sequential equity) | -0.0312 ETH |
| average win / loss | +0.0019 / -0.0037 ETH |
| median hold | 3 min 34 s |
| fees + taxes paid (simulated) | 0.0094 ETH |
| exits | stop 5, inflow_dies 4, trigger: deployer / tax-exempt wallet selling (10 min) 1, trigger: sell pressure 1, trail 1, trigger: insider selling 1 |
| entries skipped by the risk engine | 0 |

### Per hour (UTC hour the position was opened)

| hour | trades | wins | pnl ETH |
|---|---|---|---|
| 2026-09-12 16 | 5 | 1 | -0.0196 |
| 2026-09-12 17 | 5 | 1 | -0.0101 |
| 2026-09-12 18 | 3 | 1 | -0.0015 |

### Closed positions (13, newest first)

| opened (UTC) | coin | size ETH | entry px | exit | hold | pnl ETH | pnl USD | peak | fees ETH |
|---|---|---|---|---|---|---|---|---|---|
| 09-12 18:45:39 | RIDER `0xf896dad5` | 0.0050 | 5.48e-09 | stop | 1 min 18 s | -0.0016 | $-3.96 | +1 % | 0.00021 |
| 09-12 18:35:03 | ROUTR `0x6e83f4f5` | 0.0050 | 6.05e-09 | trigger: insider selling | 1 min 14 s | +0.0001 | $+0.36 | +8 % | 0.00022 |
| 09-12 18:09:44 | HITS `0xb743dea7` | 0.0077 | 5.39e-09 | inflow_dies | 8 min 05 s | -0.0000 | $-0.08 | +0 % | 0.00023 |
| 09-12 17:48:57 | YKICIS `0x757c02e2` | 0.0200 | 6.06e-09 | stop | 17 min 28 s | -0.0086 | $-21.91 | +47 % | 0.00095 |
| 09-12 17:56:46 | NFTSTR `0xa99c60ba` | 0.0094 | 5.9e-09 | stop | 3 min 34 s | -0.0028 | $-7.21 | +6 % | 0.00049 |
| 09-12 17:43:38 | DGAI·bbd0 `0x7bb410dc` | 0.0050 | 4.47e-09 | trail | 1 min 42 s | +0.0045 | $+11.40 | +139 % | 0.00060 |
| 09-12 17:34:47 | RULEPAD·d1a5 `0xf95c96b6` | 0.0083 | 3.76e-09 | stop | 1 min 10 s | -0.0025 | $-6.35 | +0 % | 0.00028 |
| 09-12 17:09:19 | STOCKPILOT `0xff2adc83` | 0.0071 | 5.91e-09 | inflow_dies | 14 min 59 s | -0.0007 | $-1.66 | +35 % | 0.00038 |
| 09-12 16:21:30 | TIDE·84d5 `0x83534d1d` | 0.0200 | 3.59e-09 | inflow_dies | 16 min 40 s | +0.0009 | $+2.34 | +5 % | 0.00167 |
| 09-12 16:21:58 | RNCAT `0x5fb515ee` | 0.0200 | 4.21e-09 | inflow_dies | 10 min 20 s | -0.0019 | $-4.71 | +20 % | 0.00038 |
| 09-12 16:18:51 | HABITS·650c `0x85bd531b` | 0.0200 | 4.12e-09 | trigger: sell pressure | 12 min 52 s | -0.0010 | $-2.47 | +5 % | 0.00159 |
| 09-12 16:12:47 | JUNKPAD·1ab7 `0x75d959da` | 0.0200 | 5.63e-09 | trigger: deployer / tax-exempt wallet selling (10 min) | 0 min 18 s | -0.0050 | $-12.60 | +0 % | 0.00070 |
| 09-12 16:09:19 | Why `0x4252d8fd` | 0.0200 | 6.8e-09 | stop | 0 min 14 s | -0.0127 | $-32.13 | +27 % | 0.00167 |

## Run notes

Kept by hand in `docs/TRACK-RECORD-notes.md` and included by `stampede track-record` on every regeneration.

- **2026-09-12 16:05:06 UTC** — first start of the 72-hour paper run (`serve --mode live --feed wss --port 8821 --db data/live-engine.sqlite`, `--notify` off; :8811/:8812 were taken by another agent's preview servers). The store already held the 60-minute measurement run of docs/ENGINE-PERF.md (235 `under_radar_top5` alerts, 7 `edge_enter` from the context worker's SQL rule); this report counts from 16:05:06.
- **16:36 UTC** — restart 1: the process used ~175 % CPU; libomp's spin-wait after every `predict_proba` of the verdict model kept four threads busy. Fix `OMP_WAIT_POLICY=PASSIVE` (stampede/signals/verdict.py). The old process did not exit on SIGTERM and both engines ran on the store for ~50 s: the sequences of blocks 61,251,496-61,252,100 may reference trade ids the other process assigned. Restart rule since then: TERM, verify, KILL, then start.
- **16:57 UTC** — restart 2: paper entries were sized at the 0.02 cap instead of the alert's vol-scaled size (the verdict features were not available to the ledger at the alert); the five positions opened before this (Why, JUNKPAD, HABITS, RNCAT, TIDE) show size 0.0200 next to alert sizes of 0.0050-0.0188. `sync_lifecycle` moved before the engine start (the first writer batches used to wait ~30 s behind it).
- **17:24 UTC** — restart 3 (the run of the `/api/perf` snapshot above): write-lock hygiene - GeckoTerminal commits right after the budget bump, the context worker commits after every coin; the writer's max lag fell from 36 s to 1.2 s. `/api/track-record` covers the whole journal by default.
- **19:42 local (16:42 UTC), inside restart 1's window** — the transfer stream fell 52 blocks behind the heads while this machine ran at load average 11 (Playwright + pytest of two agents + a HyperSync backfill): stall detector → failover → stall → back to publicnode; 283 gap requests, 740 blocks refetched over Alchemy, 10 blocks released unfilled (deferred transactions past their deadline). Not repeated after 17:24 (2 gaps, 0 unfilled, 0 reconnects at the snapshot).
- The 13 closed paper trades so far lose money (expectancy −0.0024 ETH per trade, CI upper bound below zero): 5 stops, 4 inflow-dies exits, 1 trailing-stop winner (DGAI·bbd0, +139 % peak). Eleven of thirteen entries were coins younger than 25 minutes (RNCAT 50 min and HABITS 102 min the exceptions); the research alerts (docs/RESEARCH-EDGE.md) had a median age of several minutes too, so this is the policy being measured, not a bug in the ledger. 72 hours decide.

## Reading this honestly

- The paper ledger buys the coin nobody else was buying at that block: the observed trades are replayed unchanged and our fill moves only our own price. A real order would also pay gas and could fail or be front-run.
- p(2×/30 min) is the out-of-sample rate of docs/RESEARCH-EDGE.md; with a handful of trades the CI above is wide by construction, and the bootstrap resamples coins, not trades, because one coin can alert more than once.
- Outcomes need +30 / +60 min of chain time after the alert; `pending` means not due yet, `no price` means the coin had no trade to price it with.

## Regenerate

```sh
uv run stampede track-record --db data/live-engine.sqlite --out docs/TRACK-RECORD.md --api http://127.0.0.1:8821 --since 1789229106   # --api: the running engine's /api/perf; omit when it is down
uv run stampede fx --db data/live-engine.sqlite --days 4   # optional: hourly ETH/USD so the USD columns are known
```
