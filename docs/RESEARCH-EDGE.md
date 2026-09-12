# Is there an edge? Walk-forward runner model, alert precision and a simulated exit policy

Generated 2026-09-12 12:06 UTC by `stampede edge` from `data/research-12h.sqlite` (coin_minutes run 1789214724).

## Targets (the programme goal, measured here)

| target | bar | measured (test folds) | met |
|---|---|---|---|
| precision of the alerts (≥ 2× within 30 min) | ≥ 30% | 42.1% | yes |
| lift over the coin-minute base rate | ≥ 5× | 7.8× | yes |
| alert precedes the peak (median minutes, hits) | ≥ 2 min | 14 | yes |
| policy expectancy per 0.02 ETH trade (test) | > 0 with CI lower bound > 0 | +0.0024 ETH, CI [-0.0030, +0.0086] | no |
| catch rate of coins that did ≥ 3× within 60 min | reported | 3.6% (3 of 84) | — |

## Setup

- Data: 64,899 coin-minutes with a trade, 2026-09-11 05:44–2026-09-11 10:49 UTC (5.1 h). Labels use the per-minute median trade price in quote units; a coin-minute counts when its 30-min horizon is inside the range (58,368 rows).
- Folds: 14 × 22 min — the store is shorter than the requested 7 + 7 days, so a fold is the range divided by 14; on the 14-day store a fold is one day. Train folds 0–6, test folds 7–13. Every fold ≥ 1 is scored by a model trained on earlier folds only (expanding window); the alert threshold and the exit policy are chosen on the train folds and applied unchanged to the test folds.
- Model: `HistGradientBoostingClassifier` (200 iterations, 15 leaves, lr 0.06) on 46 features (missing values native), trained on curve-stage rows of every quote asset. Target: max gain ≥ 100% within 30 min or graduation within 30 min; secondary models for ≥ 50% and for a −50% drawdown.
- Candidates for alerts: curve stage, ETH-quoted, reconstructed reserves, age 60 s – 4 h, rotation inflow ≥ 1 wallet in 10 min (1,188 test rows). Alert rule: p ≥ 0.455 (the threshold that gave 5.0 alerts/hour on the train folds), one alert per coin per 30 min.
- Base rates on the test folds: all coin-minutes 5.39%, curve-stage 7.99%, candidates 14.90%. Lift is against the first.
- Fees in the simulation: 1% curve fee, creator tax per coin (fitted from the first trades, else 100 bps — estimated, this store has no fee columns), snipe tax when the entry is < 3 s after launch, price impact of 0.02 ETH by the constant-product fill on the reconstructed reserves, 2 s entry latency. Exits at graduation are approximated by the last curve state.

## Alerts on the test folds

| rule | alerts | coins | /h | ≥ 2× in 30 min | ≥ +50% | lift | lead (min) | lead ≥ 2 min | catch 3×/60 | median max gain | median drawdown |
|---|---|---|---|---|---|---|---|---|---|---|---|
| model_causal | 19 | 19 | 7.5 | 42.1% | 47.4% | 7.8× | 14 | 100.0% | 3.6% | 47.5% | -22.6% |
| model_topk | 15 | 15 | 5.9 | 26.7% | 40.0% | 4.9× | 12 | 100.0% | 2.4% | 29.8% | -36.8% |
| rules_calibrated | 18 | 17 | 7.1 | 33.3% | 33.3% | 6.2× | 21 | 100.0% | 3.6% | 19.2% | -25.1% |
| radar_score | 11 | 11 | 4.3 | 18.2% | 27.3% | 3.4× | 15 | 100.0% | 1.2% | -3.8% | -58.7% |
| under_radar | 42 | 42 | 16.5 | 21.4% | 35.7% | 4.0× | 8 | 100.0% | 3.6% | 0.0% | -36.1% |

`model_causal` is the deployable rule (threshold fixed on train). `model_topk` picks the k best rows of each clock hour with hindsight inside the hour and is an upper bound, not a rule. `rules_calibrated` is the no-model fallback the verdict uses (cell table below). `radar_score` thresholds the stage-1 score the same way. `under_radar` is the live alert rule.

### Precision at other alert rates (model, causal thresholds from the train folds)

| alerts/h wanted | threshold p | test alerts | test /h | ≥ 2× | lift | lead (min) | catch 3× |
|---|---|---|---|---|---|---|---|
| 1 | 0.770 | 1 | 0.4 | 0.0% | — | — | 0.0% |
| 2 | 0.740 | 2 | 0.8 | 50.0% | 9.3× | 22 | 1.2% |
| 3 | 0.532 | 13 | 5.1 | 30.8% | 5.7× | 21 | 2.4% |
| 5 | 0.455 | 19 | 7.5 | 42.1% | 7.8× | 14 | 3.6% |
| 8 | 0.305 | 40 | 15.7 | 42.5% | 7.9× | 8 | 9.5% |
| 12 | 0.199 | 64 | 25.1 | 32.8% | 6.1× | 10 | 10.7% |

### Calibration on the test candidates (deciles of p)

| p range | rows | mean p | realized ≥ 2× |
|---|---|---|---|
| 0.001–0.005 | 119 | 0.3% | 0.0% |
| 0.005–0.011 | 119 | 0.8% | 2.5% |
| 0.011–0.021 | 119 | 1.6% | 4.2% |
| 0.021–0.036 | 118 | 2.7% | 10.2% |
| 0.036–0.058 | 119 | 4.6% | 18.5% |
| 0.058–0.090 | 119 | 7.4% | 18.5% |
| 0.090–0.136 | 118 | 11.1% | 16.1% |
| 0.136–0.204 | 119 | 16.8% | 26.1% |
| 0.204–0.315 | 119 | 25.5% | 26.9% |
| 0.315–0.799 | 119 | 44.4% | 26.1% |

### Feature importance (permutation, average precision, test candidates)

| feature | Δ average precision |
|---|---|
| sell_ratio_10m | +0.0195 |
| since_snipe_zero_s | +0.0088 |
| progress_5m | +0.0064 |
| deployer_prior_launches_30d | +0.0057 |
| trades_1h | +0.0054 |
| out_in_10m | +0.0054 |
| quote_in_10m | +0.0051 |
| range_5m | +0.0050 |
| mom_5m | +0.0041 |
| buyers_10m | +0.0037 |
| buys_10m | +0.0030 |
| holders | +0.0018 |
| sellers_10m | +0.0016 |
| rot_share | +0.0012 |
| inflow_10m | +0.0009 |

## Exit policy

Grid of 360 policies simulated on the 11 train-fold alerts (TP ladder × trailing stop × stop loss × time exit × inflow-dies trigger × smart-wallets-exit trigger); chosen by expectancy with ≥ 5 simulated trades. Then applied unchanged to the test alerts.

Chosen: `tp=100%x50% trail=0.25 sl=0.3 time=45m inflow_dies=on smart_exit=off`.

| policy (train alerts) | trades | expectancy ETH | hit rate | max DD ETH |
|---|---|---|---|---|
| tp=100%x50% trail=0.25 sl=0.3 time=45m inflow_dies=on smart_exit=off | 11 | +0.0072 | 54.5% | -0.017 |
| tp=100%x50% trail=0.25 sl=0.3 time=45m inflow_dies=on smart_exit=on | 11 | +0.0072 | 54.5% | -0.017 |
| tp=100%x50% trail=0.25 sl=0.4 time=45m inflow_dies=on smart_exit=off | 11 | +0.0069 | 54.5% | -0.019 |
| tp=100%x50% trail=0.25 sl=0.4 time=45m inflow_dies=on smart_exit=on | 11 | +0.0069 | 54.5% | -0.019 |
| tp=100%x50% trail=0.35 sl=0.3 time=45m inflow_dies=on smart_exit=off | 11 | +0.0068 | 54.5% | -0.017 |
| tp=100%x50% trail=0.35 sl=0.3 time=45m inflow_dies=on smart_exit=on | 11 | +0.0068 | 54.5% | -0.017 |
| tp=none trail=0.35 sl=0.3 time=45m inflow_dies=on smart_exit=off | 11 | +0.0068 | 63.6% | -0.009 |
| tp=none trail=0.35 sl=0.3 time=45m inflow_dies=on smart_exit=on | 11 | +0.0068 | 63.6% | -0.009 |

### Test folds (0.02 ETH per trade, sequential equity curve, bootstrap by coin × 1000)

| policy on the test alerts | trades | expectancy per trade | 95% CI (mean) | hit rate | total ETH | max DD ETH | exits |
|---|---|---|---|---|---|---|---|
| chosen (model alerts) | 19 | +0.0024 (+12.2%) | [-0.0030, +0.0086] | 42.1% | +0.046 | -0.043 | stop 7, inflow_dies 5, trail 3, time 2, graduation 2 |
| hold_30 (model alerts) | 18 | -0.0031 (-15.4%) | [-0.0077, +0.0028] | 27.8% | -0.056 | -0.090 | time 15, graduation 3 |
| tp80_half_trail25_sl30_t30 (model alerts) | 19 | +0.0013 (+6.3%) | [-0.0035, +0.0065] | 36.8% | +0.024 | -0.043 | stop 7, time 6, trail 4, graduation 2 |
| sl30_t30 (model alerts) | 19 | -0.0034 (-16.8%) | [-0.0057, -0.0008] | 21.1% | -0.064 | -0.064 | stop 9, time 8, graduation 2 |
| chosen (under_radar alerts) | 40 | +0.0006 (+3.2%) | [-0.0019, +0.0037] | 22.5% | +0.026 | -0.036 | stop 14, time 12, inflow_dies 7, trail 6, graduation 1 |

## Alerts, one row per alert (test folds)

| UTC | coin | p | inflow | age | max gain 30 | drawdown 30 | peak (min) | ≥ 2× | sim pnl ETH (exit) |
|---|---|---|---|---|---|---|---|---|---|
| 08:17 | 0xdc40…1fb7 | 0.644 | 1 | 32 min | 179.6% | -4.3% | 19 | 1 | +0.0163 (trail) |
| 08:20 | 0xb74c…abf6 | 0.625 | 2 | 1 min | 3.3% | -9.8% | 1 | 0 | -0.0112 (stop) |
| 08:27 | 0x1e56…d09d | 0.539 | 3 | 3 min | 287.9% | -61.6% | 6 | 1 | +0.0302 (trail) |
| 08:37 | 0xcd99…57ef | 0.535 | 2 | 113 min | -4.5% | -18.3% | 3 | 0 | -0.0023 (inflow_dies) |
| 08:39 | 0x5cc4…995c | 0.458 | 21 | 16 min | 47.5% | -88.1% | 3 | 1 | +0.0016 (graduation) |
| 08:40 | 0x1277…53fc | 0.455 | 1 | 5 min | 362.2% | -53.4% | 8 | 1 | +0.0326 (inflow_dies) |
| 08:41 | 0x01e6…58c8 | 0.496 | 1 | 2 min | 155.6% | 5.4% | 2 | 1 | +0.0182 (trail) |
| 09:01 | 0x05fd…9925 | 0.484 | 2 | 11 min | 61.9% | -8.5% | 3 | 0 | +0.0032 (inflow_dies) |
| 09:06 | 0x0d4b…0684 | 0.628 | 4 | 1 min | -43.8% | -43.8% | 1 | 0 | -0.0088 (stop) |
| 09:15 | 0x3a3a…2e49 | 0.601 | 2 | 143 min | 45.7% | -60.6% | 5 | 0 | -0.0071 (stop) |
| 09:22 | 0x29a9…7a80 | 0.536 | 19 | 202 min | 231.2% | -18.3% | 23 | 1 | +0.0063 (graduation) |
| 09:35 | 0x679b…de7e | 0.528 | 2 | 1 min | -22.3% | -28.2% | 1 | 0 | -0.0057 (time) |
| 09:40 | 0xe30f…67bc | 0.505 | 5 | 1 min | 17.7% | -28.1% | 2 | 0 | -0.0060 (inflow_dies) |
| 09:51 | 0x8db8…ddea | 0.464 | 3 | 2 min | 37.1% | -56.2% | 1 | 0 | -0.0070 (stop) |
| 09:53 | 0x7275…8b16 | 0.501 | 1 | 3 min | 33.5% | 33.5% | 1 | 0 | -0.0017 (time) |
| 09:54 | 0x5eb0…8915 | 0.555 | 1 | 185 min | 124.0% | 17.7% | 29 | 1 | +0.0063 (inflow_dies) |
| 10:02 | 0x9d3b…c0f4 | 0.473 | 1 | 6 min | 42.2% | 13.0% | 1 | 0 | -0.0066 (stop) |
| 10:06 | 0x67cd…9516 | 0.478 | 7 | 43 min | 599.1% | -22.6% | 22 | 1 | -0.0061 (stop) |
| 10:20 | 0xf0f7…0d49 | 0.515 | 9 | 1 min | 73.5% | -48.0% | 6 | 0 | -0.0061 (stop) |

## One row per coin (first alert; 19 coins, 8 reached 2× within 30 min = 42.1%)

| UTC | coin | p | inflow | max gain 30 | max gain 60 | ≥ 2× | sim pnl ETH |
|---|---|---|---|---|---|---|---|
| 08:17 | 0xdc40…1fb7 | 0.644 | 1 | 179.6% | 179.6% | 1 | +0.0163 |
| 08:20 | 0xb74c…abf6 | 0.625 | 2 | 3.3% | 3.3% | 0 | -0.0112 |
| 08:27 | 0x1e56…d09d | 0.539 | 3 | 287.9% | 287.9% | 1 | +0.0302 |
| 08:37 | 0xcd99…57ef | 0.535 | 2 | -4.5% | -4.5% | 0 | -0.0023 |
| 08:39 | 0x5cc4…995c | 0.458 | 21 | 47.5% | 47.5% | 1 | +0.0016 |
| 08:40 | 0x1277…53fc | 0.455 | 1 | 362.2% | 362.2% | 1 | +0.0326 |
| 08:41 | 0x01e6…58c8 | 0.496 | 1 | 155.6% | 155.6% | 1 | +0.0182 |
| 09:01 | 0x05fd…9925 | 0.484 | 2 | 61.9% | 61.9% | 0 | +0.0032 |
| 09:06 | 0x0d4b…0684 | 0.628 | 4 | -43.8% | -43.8% | 0 | -0.0088 |
| 09:15 | 0x3a3a…2e49 | 0.601 | 2 | 45.7% | 45.7% | 0 | -0.0071 |
| 09:22 | 0x29a9…7a80 | 0.536 | 19 | 231.2% | 231.2% | 1 | +0.0063 |
| 09:35 | 0x679b…de7e | 0.528 | 2 | -22.3% | -22.3% | 0 | -0.0057 |
| 09:40 | 0xe30f…67bc | 0.505 | 5 | 17.7% | 17.7% | 0 | -0.0060 |
| 09:51 | 0x8db8…ddea | 0.464 | 3 | 37.1% | — | 0 | -0.0070 |
| 09:53 | 0x7275…8b16 | 0.501 | 1 | 33.5% | — | 0 | -0.0017 |
| 09:54 | 0x5eb0…8915 | 0.555 | 1 | 124.0% | — | 1 | +0.0063 |
| 10:02 | 0x9d3b…c0f4 | 0.473 | 1 | 42.2% | — | 0 | -0.0066 |
| 10:06 | 0x67cd…9516 | 0.478 | 7 | 599.1% | — | 1 | -0.0061 |
| 10:20 | 0xf0f7…0d49 | 0.515 | 9 | 73.5% | — | 0 | -0.0061 |

## Reading this honestly

- The range is 5.1 hours, so the folds are 22-minute slices, not days; the model for early folds saw minutes of data. Treat every number here as a pipeline check; the 14-day store is the measurement.
- Alerts on the test folds: 19 on 19 coins. With a base rate of 5.39% and 19 alerts the 95% binomial interval of the precision is roughly ±22.2%; the bootstrap CI of the policy is wide for the same reason.
- Signal minutes are autocorrelated (a coin stays a candidate while its inflow lasts); the per-coin table is the fairer count and the bootstrap resamples coins, not alerts.
- The simulation buys the coin nobody else was buying at that second: the observed trades are replayed unchanged, our 0.02 ETH moves only our own fills. Sells at graduation are approximated by the last curve state; snipe-window entries pay the snipe tax; MEV, failed transactions and gas are not modelled.
- Wallet features are walk-forward: rotation quality uses outcomes known at the decision minute; stage-2 smart-wallet features come from the first half of the range and are unknown (missing) before it.
- 'Runner' is a price path; nobody earned these numbers. If the edge is not in the table, it is not there — the numbers are recomputed whenever this command runs.

## Commands

```
stampede edge-features --db data/research-12h.sqlite
stampede edge --db data/research-12h.sqlite --train-days 7 --test-days 7 --out docs/RESEARCH-EDGE.md
# 14-day store, once data/backfill-14d.log ends with READY:
stampede fx --db data/research-14d.sqlite --days 16
stampede launch-intel --db data/research-14d.sqlite --horizon 3600 --resolve-quotes --out docs/RESEARCH-LAUNCHES.md
stampede traders --db data/research-14d.sqlite --fees strict --out docs/RESEARCH-TRADERS.md
stampede edge-features --db data/research-14d.sqlite
stampede edge --db data/research-14d.sqlite --train-days 7 --test-days 7 --out docs/RESEARCH-EDGE.md
```

Artefacts: model `data/models/edge-20260912.pkl` (gitignored), config `stampede/signals/edge-config.json`. Compute time 17 s (features 7.1 s).