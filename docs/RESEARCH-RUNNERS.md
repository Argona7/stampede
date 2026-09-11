# Do rotations precede runners? Backtest on indexed PONS trades

Generated 2026-09-11 12:45 UTC by `stampede.research.backtest` from `data/research-12h.sqlite`.

## Setup

- Data: 444,537 trades, 9,596 coins with a price series, 38,059 direct/clean sequences (pairing window 30 min), range 2026-09-11 05:44–10:49 UTC, 44 graduations recorded.
- Signal minute: a coin had ≥ 1 rotation inflow wallet in the previous 10 minutes; features use only data up to that minute. 10,146 signal minutes on 1,517 coins (bots excluded: 42 wallets with > 60 trades/hour or > 500 trades).
- Outcome: median trade price reaches ≥ 2× the signal-minute price within 30 min, or the coin graduates within 30 min. Signals whose horizon runs past the end of the data are dropped.
- Base rate: the same outcome at every coin-minute with at least one trade: **5.30%** of 57,400 coin-minutes (median max gain 1.2%).

## Results

### By rotation inflow (distinct wallets in the last 10 min)

| bucket | signals | coins | runner rate | lift vs base | median max gain | median gain at +H | graduated |
|---|---|---|---|---|---|---|---|
| 1–2 | 3697 | 1256 | 5.9% | 1.12× | 2.9% | -1.1% | 44 |
| 3–4 | 1371 | 417 | 12.8% | 2.42× | 11.0% | -6.9% | 49 |
| 5–7 | 1151 | 289 | 13.8% | 2.61× | 16.0% | -13.2% | 54 |
| 8–11 | 980 | 217 | 14.9% | 2.81× | 17.0% | -15.2% | 60 |
| 12–19 | 1050 | 198 | 17.7% | 3.34× | 16.2% | -23.9% | 85 |
| 20–39 | 968 | 138 | 21.6% | 4.07× | 22.3% | -31.0% | 96 |
| 40+ | 929 | 72 | 15.9% | 3.01× | 23.2% | -38.0% | 15 |

### By acceleration (inflow last 10 min vs the 20 min before)

| bucket | signals | coins | runner rate | lift vs base | median max gain | median gain at +H | graduated |
|---|---|---|---|---|---|---|---|
| < 1 (slowing) | 2135 | 199 | 11.4% | 2.15× | 17.2% | -10.2% | 66 |
| 1–2 | 3088 | 1126 | 8.6% | 1.62× | 4.4% | -1.7% | 85 |
| 2–4 | 2223 | 661 | 11.2% | 2.11× | 8.7% | -5.4% | 77 |
| 4+ (new burst) | 2700 | 553 | 18.0% | 3.40× | 14.0% | -26.5% | 175 |

### By coin age at the signal

| bucket | signals | coins | runner rate | lift vs base | median max gain | median gain at +H | graduated |
|---|---|---|---|---|---|---|---|
| < 15 min | 2838 | 1127 | 15.6% | 2.95× | 5.5% | -22.9% | 121 |
| 15–60 min | 2394 | 396 | 17.8% | 3.35× | 16.5% | -9.8% | 189 |
| 1–4 h | 4372 | 503 | 8.2% | 1.54× | 9.0% | -2.2% | 89 |
| 4 h+ | 542 | 166 | 3.1% | 0.59× | 7.2% | -3.7% | 4 |

### By price momentum already in the last 10 min

| bucket | signals | coins | runner rate | lift vs base | median max gain | median gain at +H | graduated |
|---|---|---|---|---|---|---|---|
| already +100% in 10 min | 522 | 124 | 28.2% | 5.31× | 22.5% | -31.4% | 94 |
| +20..100% | 1563 | 261 | 17.3% | 3.27× | 17.0% | -14.6% | 98 |
| flat -20..+20% | 4488 | 641 | 7.0% | 1.32× | 6.9% | -1.9% | 107 |
| falling | 1169 | 196 | 11.6% | 2.20× | 23.0% | -7.0% | 18 |
| no price 10 min ago | 2404 | 1085 | 15.6% | 2.94× | 3.5% | -22.1% | 86 |

### By breadth (number of source coins)

| bucket | signals | coins | runner rate | lift vs base | median max gain | median gain at +H | graduated |
|---|---|---|---|---|---|---|---|
| 1 source | 2885 | 1274 | 5.9% | 1.11× | 2.2% | -1.1% | 26 |
| 2–3 | 2274 | 663 | 11.7% | 2.21× | 9.0% | -7.3% | 67 |
| 4–7 | 1938 | 325 | 13.3% | 2.50× | 14.9% | -12.6% | 104 |
| 8+ | 3049 | 155 | 18.1% | 3.41× | 20.4% | -22.8% | 206 |

### By walk-forward wallet quality (mean past runner rate of the inflow wallets)

| bucket | signals | coins | runner rate | lift vs base | median max gain | median gain at +H | graduated |
|---|---|---|---|---|---|---|---|
| unknown | 3950 | 1087 | 9.9% | 1.86× | 5.2% | -3.2% | 137 |
| < 0.35 | 3559 | 658 | 11.5% | 2.16× | 10.5% | -9.4% | 108 |
| 0.35–0.5 | 1850 | 195 | 15.6% | 2.95× | 17.9% | -21.9% | 109 |
| 0.5–0.65 | 505 | 153 | 23.4% | 4.41× | 22.0% | -15.5% | 42 |
| 0.65+ | 282 | 134 | 13.5% | 2.54× | 13.4% | -7.6% | 7 |

### Combined rules

| bucket | signals | coins | runner rate | lift vs base | median max gain | median gain at +H | graduated |
|---|---|---|---|---|---|---|---|
| inflow ≥ 8 | 3927 | 313 | 17.5% | 3.31× | 18.9% | -25.1% | 256 |
| inflow ≥ 8 & age < 1 h & not already +100% in 10 min (under radar) | 2163 | 290 | 20.2% | 3.82× | 20.1% | -36.5% | 156 |
| … & wallet quality ≥ 0.5 | 238 | 51 | 31.9% | 6.03× | 45.9% | -31.5% | 21 |
| inflow ≥ 8 & accel ≥ 4 (fresh burst) | 1727 | 310 | 20.6% | 3.89× | 17.7% | -39.6% | 144 |
| inflow ≥ 20 | 1897 | 151 | 18.8% | 3.55× | 22.6% | -33.6% | 111 |

### First time a coin crosses inflow ≥ 8 (one row per coin)

| bucket | signals | coins | runner rate | lift vs base | median max gain | median gain at +H | graduated |
|---|---|---|---|---|---|---|---|
| all coins | 313 | 313 | 23.6% | 4.46× | 19.5% | -38.7% | 25 |
| age < 1 h | 291 | 291 | 23.7% | 4.47× | 20.1% | -39.8% | 20 |
| age < 1 h and not already +100% | 282 | 282 | 22.7% | 4.28× | 20.1% | -39.6% | 16 |

## What the radar score takes from this

- Inflow: lift rises through 20–39 wallets and falls at 40+, so the score plateaus at 20–40 and eases off above.
- A fresh burst (acceleration ≥ 4) and breadth (many source coins) both carry lift; a slowing inflow (< 1) carries less.
- Age: 15–60 min is the strongest bucket, under 15 min next; coins older than 1 h fall towards the base rate, older than 4 h below it.
- Momentum: a coin already up 100% in the last 10 minutes is *more* likely to double again within the horizon (continuation), but its median price at +H is what the 'gain at +H' column shows: entering late and holding is a different bet from catching the move. The score keeps momentum neutral and shows it as a column.
- Wallet quality: inflow from wallets whose earlier rotations preceded runners (0.5–0.65 bucket) roughly doubles the rate versus unknown wallets; the top bucket is small and noisier.

## Reading this honestly

- Signal minutes are autocorrelated (a coin with inflow for 20 minutes contributes 20 rows). The per-coin table is the fairer count.
- 'Runner' is a price path on the PONS curve, with 1% fees and creator taxes ignored; nobody earned these numbers, and many of these coins go to zero afterwards (see the FDV of yesterday's TruffleHog: $3k).
- Rotation inflow is observed order of trades by the same address. It says nothing about who those addresses are or why they moved.
- The sample is 5.1 hours of one chain on one launchpad. Treat lifts as a first measurement, not a law; the numbers are recomputed whenever this script runs on a new range.

Wallet scores written: 3,559 wallets with ≥ 3 known rotation outcomes or flagged as bots. Compute time 2 s.