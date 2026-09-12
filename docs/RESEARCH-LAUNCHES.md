# Launch quality and runners: what the launch itself says about a PONS coin

Generated 2026-09-12 10:13 UTC by `stampede launch-intel` from `/tmp/bf-test.sqlite`.

## Setup

- Data: 15,663 trades over 0.2 h (2026-09-12 08:23–08:33 UTC, blocks 60,961,233–60,967,133, measured 9.85 blocks/s); 400,750 launches in the store (lifecycle logs reach 30 days back so deployer history is complete for the trade range); 7,096 graduations.
- Observed launches: 65 launched inside the trade range with a resolvable launch time; 59 of them had at least one trade within the horizon. Launches from before the range have deployer history only; their dev buy / bundle / snipe columns stay NULL (unknown, not 0).
- Entry: the first trade at ≥ 3 s after the launch, i.e. the first moment a buy is not snipe-taxed. 55 observed launches had such a trade; 28 of them also have the full 5-min horizon inside the data and form the sample below.
- Outcome 'runner': the minute-median price reaches ≥ 2× the entry price within 5 min of the launch, or the coin graduates within that window. Base rate over the sample: **3.57%** of 28 coins.
- Features are per launch, one row per coin, all knowable at launch time except the bundle proxy (needs the 3-second window to close) — nothing looks past the entry.

## Definitions

- `dev_buy_share`: tokens bought in the launch transaction (`launchAndBuy`) or by the deployer within 5 s, over the 1e9 supply. `dev_buy_quote` is what was paid, in pair-token units — NULL when the store does not know the pair token's decimals (`quotes`; ETH always does, `--resolve-quotes` fills the rest with two `eth_call`s per pair token, not per launch).
- `creator_tax_bps`: creator tax read from the first curve trade with a readable ratio (tax / quoteIn on buys, tax / (quoteOut + fee + tax) on sells), rounded to 5 bps; 0 when the first curve trades carry no tax.
- `bundle_n` / `bundle_share`: distinct wallets other than the deployer that bought inside the 3-second snipe window and paid **no** snipe tax — the behavioural proxy for the declared exemption list (`SnipeTaxExempted`, up to 32 wallets). `exempt_declared_n` is the exact count from indexed `SnipeTaxExempted` logs when a store has them (research stores built before 2026-09-12 do not; the ingest paths now keep them).
- `taxed_snipers_3s`: distinct wallets that bought inside the window and paid the snipe tax (`SnipeTaxCharged`, part of `CurveBuy.fee`); `snipe_tax_paid_quote` is the sum they paid. `first_buyers_5s`: distinct buyers in the first 5 s, deployer included.
- `deployer_prior_launches_30d` / `deployer_prior_graduations_30d` / `deployer_graduation_rate`: launches by the same deployer in the 30 days before this one (block distance at the measured block rate, 25,530,550 blocks), how many of those had graduated before this launch, and the ratio (NULL without prior launches). 'Deployer' is the `TokenLaunched.deployer` topic — the caller of the factory, which can be a contract (e.g. Multicall3).
- `launch_farm`: 1 when the deployer's first launch is < 24 h old **and** at least 3 launches within ±30 min (this one included) share the fingerprint (pair token, `creator_tax_bps`, exact dev-buy quote amount) **and** all come from < 24 h-old deployers. `farm_group_n` is the size of that group. Launches without a dev buy have no fingerprint and are never flagged by this rule; a deployer first seen before the 30-day lifecycle window looks 'young' on its first launch inside it (known limitation).
- `socials_present`: 1 / 0 from the socials parsed out of the launch calldata (`launch_meta`, filled by the coin drawer's Refresh context or `--socials N`); NULL = not parsed (never fetched in the batch for every launch).
- `snipe_tax_zero_ts`: launch timestamp + the curve's snipe window (3 s on every curve launched so far; the batch path does not read per-curve values). The tax is `9900 >> (elapsed × 14 // 3)` bps of quote-in: 9800 (capped at 10000 − fee − creator tax − 100) at 0 s, 618 at 1 s, 19 at 2 s, 0 from 3 s. `elapsed` uses `block.timestamp`, which has 1-second granularity, so the zero-tax time is a lower bound: the first block whose timestamp is ≥ launch + 3 s.

## What the observed launches look like

| measure | value |
|---|---|
| observed launches | 65 |
| with any trade within the horizon | 59 (90.8%) |
| with a dev buy | 49 (75.4%) · median share 2.51% · p90 4.42% |
| with an untaxed bundle in the snipe window (proxy) | 9 (13.8%) |
| with taxed snipers in the window | 35 (53.8%) |
| creator tax readable | 59 · most common: 100 bps ×15, 0 bps ×15, 200 bps ×14, 300 bps ×7, 400 bps ×4, 250 bps ×2 |
| deployer first seen < 24 h before the launch | 56 (86.2%) · median prior launches in 30 d: 0 · p90 8 |
| launch farm (rule above) | 14 (21.5%) of 45 fingerprinted |
| socials parsed / declared | 65 / 53 |
| exact exemption logs available | 0 launches |

## Results: runner rate by launch feature (one row per coin)

### By dev buy (share of supply bought in the launch tx or by the deployer within 5 s)

| bucket | coins | runner rate | lift vs base | median max gain | median gain at +H | graduated |
|---|---|---|---|---|---|---|
| no dev buy | 5 | 0.0% | 0.00× | 0.0% | -2.5% | 0 |
| 0–1% | 5 | 0.0% | 0.00× | -1.0% | -1.3% | 0 |
| 1–3% | 11 | 9.1% | 2.55× | 2.7% | -8.0% | 0 |
| 3–5% | 6 | 0.0% | 0.00× | 8.9% | -9.6% | 0 |
| 5–10% | 1 | 0.0% | 0.00× | -19.8% | -35.9% | 0 |
| > 10% | 0 | — | — | — | — | — |

### By untaxed bundle size in the snipe window (proxy for declared exemptions)

| bucket | coins | runner rate | lift vs base | median max gain | median gain at +H | graduated |
|---|---|---|---|---|---|---|
| unknown (store without snipe-tax columns) | 0 | — | — | — | — | — |
| 0 | 21 | 0.0% | 0.00× | 0.0% | -8.0% | 0 |
| 1–2 | 2 | 0.0% | 0.00× | 42.3% | 42.3% | 0 |
| 3–5 | 3 | 33.3% | 9.33× | 40.2% | -23.9% | 0 |
| 6+ | 2 | 0.0% | 0.00× | 8.9% | -32.0% | 0 |

### By creator tax

| bucket | coins | runner rate | lift vs base | median max gain | median gain at +H | graduated |
|---|---|---|---|---|---|---|
| unknown (no curve trade with a readable ratio) | 0 | — | — | — | — | — |
| 0 bps | 7 | 0.0% | 0.00× | -2.3% | -3.1% | 0 |
| 5–100 bps | 9 | 0.0% | 0.00× | 0.0% | -3.7% | 0 |
| 105–300 bps | 9 | 11.1% | 3.11× | 0.0% | -8.1% | 0 |
| 305–600 bps | 3 | 0.0% | 0.00× | 10.8% | -9.6% | 0 |
| 605–1000 bps | 0 | — | — | — | — | — |

### By the deployer's graduation rate over the previous 30 days

| bucket | coins | runner rate | lift vs base | median max gain | median gain at +H | graduated |
|---|---|---|---|---|---|---|
| no prior launch in 30 d | 23 | 0.0% | 0.00× | 0.0% | -9.3% | 0 |
| prior launches, none graduated | 4 | 25.0% | 7.00× | -2.3% | -2.5% | 0 |
| 0–10% graduated | 1 | 0.0% | 0.00× | -19.8% | -35.9% | 0 |
| 10–30% | 0 | — | — | — | — | — |
| > 30% | 0 | — | — | — | — | — |

### By the deployer's prior launches in 30 days

| bucket | coins | runner rate | lift vs base | median max gain | median gain at +H | graduated |
|---|---|---|---|---|---|---|
| 0 | 23 | 0.0% | 0.00× | 0.0% | -9.3% | 0 |
| 1–5 | 2 | 50.0% | 14.00× | 131.8% | 131.8% | 0 |
| 6–50 | 0 | — | — | — | — | — |
| 51+ | 3 | 0.0% | 0.00× | -2.3% | -2.5% | 0 |

### By launch farm flag

| bucket | coins | runner rate | lift vs base | median max gain | median gain at +H | graduated |
|---|---|---|---|---|---|---|
| launch farm: yes | 6 | 0.0% | 0.00× | 3.0% | -8.1% | 0 |
| launch farm: no | 22 | 4.5% | 1.27× | 0.0% | -3.7% | 0 |

### By declared socials

| bucket | coins | runner rate | lift vs base | median max gain | median gain at +H | graduated |
|---|---|---|---|---|---|---|
| socials declared: yes | 23 | 4.3% | 1.22× | 0.0% | -8.1% | 0 |
| socials declared: no | 5 | 0.0% | 0.00× | 14.6% | -2.5% | 0 |
| unknown (launch calldata not parsed) | 0 | — | — | — | — | — |

### By taxed snipers in the window (someone paid to be first)

| bucket | coins | runner rate | lift vs base | median max gain | median gain at +H | graduated |
|---|---|---|---|---|---|---|
| unknown | 0 | — | — | — | — | — |
| 0 taxed snipers | 12 | 0.0% | 0.00× | 0.0% | -9.6% | 0 |
| 1–2 | 12 | 8.3% | 2.33× | 0.0% | -3.1% | 0 |
| 3+ | 4 | 0.0% | 0.00× | 52.1% | -19.8% | 0 |

### Combined rules (the `clean_launch` radar preset)

| bucket | coins | runner rate | lift vs base | median max gain | median gain at +H | graduated |
|---|---|---|---|---|---|---|
| clean launch (bundle ≤ 2, dev buy ≤ 5%, not a farm) | 19 | 0.0% | 0.00× | 0.0% | -3.7% | 0 |
| … & deployer graduated before (rate > 0) | 0 | — | — | — | — | — |
| … & creator tax ≤ 300 bps | 16 | 0.0% | 0.00× | 0.0% | -3.1% | 0 |
| bundle ≥ 3 or dev buy > 5% or farm | 9 | 11.1% | 3.11× | -2.2% | -19.8% | 0 |
| bundle unknown (store without snipe-tax columns) | 0 | — | — | — | — | — |

## Entry at t = 3 s versus t = 0 s: what the snipe tax costs and what waiting costs

| elapsed since launch | tax by formula (bps of quote-in) | taxed buys observed | median tax paid (bps) | total paid, ETH-quoted launches |
|---|---|---|---|---|
| 0 s | 9800 | 3 | 9700 | 0.0613 ETH |
| 1 s | 618 | 77 | 618 | 0.1243 ETH |
| 2 s | 19 | 48 | 19 | 0.0024 ETH |
| ≥ 3 s | 0 | — | — | — |

- Formula: `9900 >> (elapsed × 14 // 3)`, capped at `10000 − 100 (curve fee) − creator tax − 100`; the buy at 0 s keeps ~1% of its quote. Observed medians below the formula at a given second are two-sided or multi-event rows and the 1-second timestamp rounding, not a different rate.
- Waiting for the tax to reach zero: the first untaxed trade comes a median 5 s after the launch (p90 26 s) at a median **1.04×** the launch-block price (p25 0.99×, p75 1.12×; 46 launches with both prices). Paying 99% tax at 0 s means receiving ~1% of the tokens the same quote buys 3 s later — every observed ratio is far below 100×, so the untaxed entry is the cheaper one.

## Reading this honestly

- One row per coin; buckets with a handful of coins are noise. Lifts are versus the base rate of *observed launches with an untaxed entry*, which is not the base rate of the rotation backtest (RESEARCH-RUNNERS.md measures coin-minutes with inflow).
- 'Runner' is a price path on the curve; nobody earned it. Fees (1%), the creator tax and the price impact of the entry itself are ignored, and the entry is the first trade after the window, whoever made it.
- The bundle is a behavioural proxy: an untaxed buyer inside the window is either on the declared exemption list, the creator fee recipient, or a wallet whose buy landed in a block stamped ≥ 3 s after a launch stamped earlier than it happened. Exact counts need the `SnipeTaxExempted` logs, which the ingest paths keep from now on.
- Deployer history counts launches by the same `deployer` address; farms that rotate wallets show up as young deployers instead. The 30-day window is measured in blocks at the store's block rate (9.85/s).
- The sample is 0.2 hours of one chain on one launchpad (28 coins with a complete horizon). Treat these as first measurements; the numbers are recomputed whenever this command runs on a new range.

## Exact commands

```sh
.venv/bin/python -m stampede launch-intel --db /tmp/bf-test.sqlite --horizon 300 --gain 1.0 --out docs/RESEARCH-LAUNCHES.md
```

Compute time 5 s: deployer pass 4.2 s over 400,750 launches, trades pass 0.1 s over 65 observed launches (1,901 trade rows read), farm pass 0.0 s. No RPC call in the batch path.

Extrapolation at this run's rate (19,010 trade rows/s, 1.54 ms per observed launch, one indexed range query each): a 14-day store with 40,000,000 trades of which ~60% fall in the first hour after their launch, and ~180,000 observed launches, needs about 21 min for the rows plus 4.6 min of per-launch overhead; the deployer pass is linear in launches (4.2 s here). Short stores overstate the per-row cost (fixed costs dominate).