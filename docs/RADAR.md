# RADAR: coins wallets are rotating into right now

The radar ranks coins by **observed rotation inflow**: distinct wallets that sold another coin and then
bought this one within the pairing window (direct/clean grade only). It is a ranking aid over observed
order of trades, not a prediction and not a recommendation.

## Score (0–100), every component shown next to the total

| part | max | meaning |
|---|---|---|
| inflow | 40 | log-scaled distinct wallets rotating in during the last 10 minutes (150 wallets = 40) |
| acceleration | 25 | log-scaled ratio of the last 10 minutes to the average 10-minute rate of the previous 20 minutes |
| breadth | 15 | log-scaled number of distinct source coins in the last 10 minutes |
| wallet quality | 20 | mean walk-forward runner rate of the inflow wallets (`wallet_scores`, from `stampede.research.backtest`); 10 when unknown |
| age bonus | +12 / +6 | coin younger than 30 min / 2 h at the clock (from `TokenLaunched`, else first indexed trade) |
| curve bonus | +6 | still on the bonding curve (not graduated) |
| attention penalty | up to −60 | log-scaled X mentions of the cashtag/contract in the last hour (0 when not fetched) |

Thresholds come from `docs/RESEARCH-RUNNERS.md`: inflow ≥ 8 wallets within 10 minutes, acceleration ≥ 4 and
age < 15 min are the buckets with the highest measured lift; inflow 40+ is *lower* lift (the move already
happened). The score is recomputed on every request from indexed data, as of the shared clock.

## Presets

- **Under radar**: on the curve, younger than 4 h, X mentions ≤ 3 in the last hour (or unknown), bots hidden.
- **Graduating**: curve at ≥ 60% of its graduation threshold, sorted by progress.
- **Smart rotators**: inflow wallets with mean quality ≥ 0.55, bots hidden.
- **Clean launch**: launch intel known and clean — at most 2 tax-exempt bundle wallets, dev bought ≤ 5% of supply, not a launch farm (coins without launch intel do not pass; n/a is not 0).
- **All**: every coin with inflow in the visible range.

Filters: minimum wallets in range, coin age, stage (curve / graduated), X 1h ≤ N, sort, hide bots.

## Context modules (each labelled with its source and fetch time)

| module | source | when | cache |
|---|---|---|---|
| PONS lifecycle | `TokenLaunched`, `CurveCompleted`, `PoolRegistered` logs (indexed) or one on-demand `eth_getLogs` | always | store tables `launches`, `graduations` |
| Curve progress | net quote in / launch threshold from indexed curve trades | always, as of the clock | computed |
| Declared socials | strings in the launch transaction calldata (X handle, Telegram, website, description) | coin drawer refresh, live top-10 | 7 days |
| Market now | GeckoTerminal `networks/robinhood/tokens/{token}/pools` (price, FDV, liquidity, volume, change) | live mode, or drawer refresh | 60 s |
| Holders / dev | full ERC-20 Transfer history of the coin via public RPC: holders, top-10 share, dev holding and sold share, launch-block buyers | live top-10, or drawer refresh | 5–10 min |
| X mentions | twitterapi.io advanced search: `"$SYM" OR <contract> OR (SYM (robinhood OR pons OR memecoin))`, last 24 h, up to 60 tweets counted | live top-30, or drawer refresh | 5 min |
| Wallet scores | `stampede.research.backtest --write-scores` on a research store, imported with `stampede wallet-scores --from` | import step | until re-imported |
| Launch quality | `stampede launch-intel --db <store>` from indexed trades and lifecycle logs: dev buy share, tax-exempt bundle (proxy, exact when `SnipeTaxExempted` logs are indexed), creator tax, deployer record over 30 days, launch-farm flag, snipe-tax zero time (`docs/RESEARCH-LAUNCHES.md`). Shown as the `LAUNCH` column group (dev % · bndl · farm at 1440 px; tax and deployer rate when wider) and the drawer's *Launch* section; not scored yet | batch step; the drawer refresh reads the exact exemption list from the launch receipt | until recomputed |
| Verdict | `signals.verdict` (stage 4/5): ENTER / WAIT / AVOID with p(≥ 2× within 30 min), p(−50%), EV per trade, the exit plan (TP ladder, trail, stop, time exit, exit-now triggers) and the risk-engine size (fractional Kelly under hard caps, volatility scaling, daily stop; `signals/risk-config.json`). p comes from the walk-forward model pickle `data/models/edge-<date>.pkl` when present, else from the calibrated rule cells in `signals/edge-config.json` (both written by `stampede edge`, measured in `docs/RESEARCH-EDGE.md`). Shown as the `verdict` column (ENTER in red; the short address yields at 1440 px) and the drawer's *Verdict* section; one line on the TUI coin card | computed with every radar row from the row's own features; < 2 ms with the model | per clock |

Replay mode keeps to as-of-clock on-chain numbers; external "now" data is fetched only in live mode
(`serve --context live`, default) or with `--context always`. Every external number carries its fetch time.
Daily call budgets are counted in `api_budget` and shown in `/api/radar`.

## Alerts

Rule `under_radar_top5`: a coin enters the radar top 5 with score ≥ 60, inflow ≥ 8 and X mentions ≤ 3 (or
unknown). One alert per coin per 30 min of clock time. Outcomes are filled in 30 and 60 clock-minutes later
from indexed trades (median price of the last 5 trades) and graduation events. `/api/alerts` returns the
journal and the running track record; `--notify` posts a macOS notification.

Rule `edge_enter` (stage 4): the verdict says ENTER — p(≥ 2× in 30 min) at or above the threshold that gave 5
alerts/hour on the train folds of `docs/RESEARCH-EDGE.md`, EV > 0, the risk engine allows a size — at most 5 per
clock hour, ranked by p, one per coin per 30 min. The journal row's `detail` carries p, the size and the exit-plan
lines; outcomes are filled by the same +30/+60 loop as the other rule.

## Honesty

- Inflow is observed order of trades by the same address. It is not money flow, not shared ownership, not
  insider knowledge.
- "Runner" outcomes are price paths on the PONS curve with fees ignored; nobody earned them.
- Duplicate tickers are disambiguated with the address suffix (`MARIO·43cb`) because 502 tickers in the
  store belong to several coins.
- X counts are what a search found, not reach. Bare tickers that are ordinary words attract unrelated tweets.
