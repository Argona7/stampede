# Trader intelligence: who earns on PONS after fees, and does it persist?

Generated 2026-09-12 10:25 UTC by `stampede traders` from `data/research-12h.sqlite`.

> Dry run on the 5-hour research store (data/research-12h.sqlite, normalized before the fee columns existed: the 1% base fee is applied by rule, creator and snipe tax are unknown, so PnL is slightly optimistic and sniper flags rest on launch times alone). The 14-day store with real fee columns replaces this report when its backfill is READY; the smoke store with real fees (10 min, 15.6k trades) reproduces the same pipeline: 4,365 wallets, 991 closed positions, 3 bots, 103 deployer-linked, 141 snipers.

## Setup

- Data: 444,537 trades by 68,330 non-infrastructure wallets on 9,644 coins, 2026-09-11 05:44–2026-09-11 10:49 UTC (5.1 h). Walk-forward split at 2026-09-11 08:16 UTC (first half: 44,454 wallets, second half: 38,109).
- Fees: `estimate`. Rows without `fee_raw` on the curve: 189,692 (base fee estimated at 1%, creator tax unknown); rows without a quote amount (multi-hop / two-sided transactions): 9,708 counted as trades but not priced. Graduated-pool (v4) rows: fees sit inside the net amounts (the hook takes them in the unspecified currency), so they are not listed separately.
- USD: no fx_rates in this store: USD columns are n/a (run `stampede fx --db ...`). PnL is stated in quote units first; the ETH columns cover positions quoted in native ETH/WETH (41,579 wallets); other quote assets are kept per asset in `pnl_by_quote`.
- Wallets tagged: 160 bots (> 60 trades/h over their active span, > 500 trades, or ≥ 3 blocks with buys of several coins), 1,560 deployer-linked (deployer of a coin they traded, or bought without snipe tax while later buyers still paid it: 0), 2,401 snipers (≥ 30% of entries under 3 s after launch or with snipe tax paid).
- Positions: 119,939 closed (wallet, coin) episodes; a position closes when ≤ 1% of its peak inventory is left (the dust is written off). Sells without matching lots (tokens received by transfer, or bought before the range) are ignored, never counted as profit.
- Compute: token pass 2.1 s, wallet pass 7.2 s, total 13 s.

## Definitions

- **Cost of a lot** = quote paid + 1% fee (incl. snipe tax) + creator tax. **Proceeds** = net quote received. **Realized PnL** = FIFO proceeds − matched cost. **Unrealized** = open inventory × last minute-median price − remaining cost. **ROI** = (realized + unrealized) / total cost of buys; `roi_realized` = realized / cost of the lots actually sold.
- **Win rate** = closed positions with PnL > 0 / closed positions. **Median hold** = median(last sell − first buy) over closed positions. **Trades/hour** over the wallet's active span (≥ 1 h).
- **Buyer rank** = n-th distinct buyer of the coin since its launch (known only when the launch is inside the range). **Sniper** = entry < 3 s after launch or snipe tax paid. **Exit quality** = exit price / max minute-median price in the following 15 min (1 = sold the top), proceeds-weighted per position. **Rug avoidance** = share of closed positions after which the coin fell ≥ 80% within 60 min. **Consistency** = share of active ISO weeks with positive realized PnL.
- **Quality** (0–1, 0.5 = no evidence) = shrink × (0.35 × Laplace win rate + 0.25 × clip((roi_realized + 1)/2) + 0.2 × exit quality + 0.2 × rug avoidance) + (1 − shrink) × 0.5, shrink = n / (n + 5) over n closed positions. Bots and deployer-linked wallets keep their quality but are excluded from the `smart` preset and from the walk-forward top lists.

## Population

- 68,330 wallets with ≥ 1 trade; 29,791 traded ETH-quoted coins and closed ≥ 1 position; of those 9,414 (31.6%) ended the range with a positive ETH PnL (realized + unrealized). Wallets quoted only in other assets are counted in the leaderboards by their per-asset sums, not here.
- ETH PnL across wallets with a closed position: median -0.0019 ETH, mean -0.0006, sum -19.18; fees paid 38.32 ETH.
- Median win rate 0.0%, median exit quality 0.88, median rug-avoidance share 0.0%, median hold 6m02s.

## Leaderboards (full range)

### Top by ETH PnL (realized + unrealized), every wallet

| # | wallet | trades | coins | closed | win rate | PnL ETH (real + unreal) | ROI | median hold | exit q | rug avoid | sniper | quality | tags |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `0x352823e4…df02` | 4 | 2 | 0 | n/a | +21.1009 (+0.0000 + +21.1009) | 588% | n/a | n/a | n/a | 0% | 0.500 | — |
| 2 | `0x91d0e6c7…1292` | 22 | 10 | 3 | 0% | +11.3922 (-0.0191 + +11.4113) | 1025% | 8s | 0.71 | 0% | 100% | 0.437 | sniper, fees_partly_estimated |
| 3 | `0x4f967828…d136` | 27 | 12 | 4 | 25% | +8.6634 (+0.0424 + +8.6210) | 858% | 12s | 0.68 | 0% | 100% | 0.449 | sniper, fees_partly_estimated |
| 4 | `0xa7279881…9772` | 8 | 3 | 2 | 50% | +8.1822 (+0.0250 + +8.1571) | 2556% | 2m55s | 1.00 | 0% | 0% | 0.504 | fees_estimated |
| 5 | `0x1f1b1f7b…e9f3` | 9 | 1 | 0 | n/a | +8.0844 (+0.0000 + +8.0844) | 482% | n/a | n/a | n/a | 0% | 0.500 | — |
| 6 | `0xc9045ddf…7e65` | 28 | 12 | 12 | 42% | +7.7641 (-0.0686 + +7.8327) | 643% | 48s | 0.78 | 0% | 0% | 0.446 | fees_estimated |
| 7 | `0xf696076f…c8b6` | 2 | 1 | 0 | n/a | +6.8245 (+0.0658 + +6.7586) | 4505% | n/a | n/a | n/a | 0% | 0.500 | fees_estimated |
| 8 | `0xa9da311d…ec17` | 8 | 4 | 4 | 75% | +5.2411 (+5.2411 + +0.0000) | 94% | 36s | 1.00 | 0% | 0% | 0.578 | fees_partly_estimated |
| 9 | `0xd7160549…0fa5` | 1 | 1 | 0 | n/a | +5.0927 (+0.0000 + +5.0927) | 504% | n/a | n/a | n/a | 100% | 0.500 | deployer, sniper, fees_estimated |
| 10 | `0xfb4a77b5…fbcc` | 7 | 1 | 0 | n/a | +5.0652 (+0.0000 + +5.0652) | 336% | n/a | n/a | n/a | 0% | 0.500 | — |
| 11 | `0x74e958c4…8099` | 14 | 2 | 1 | 100% | +4.9029 (+0.6019 + +4.3010) | 788% | 50s | 1.00 | 0% | 0% | 0.531 | fees_partly_estimated |
| 12 | `0xfa30cbed…3353` | 3 | 2 | 1 | 0% | +4.2062 (+0.0000 + +4.2062) | 4624% | 1m34s | 1.00 | 0% | 50% | 0.490 | sniper, fees_estimated |
| 13 | `0xd3b81988…b58f` | 2 | 1 | 0 | n/a | +3.7139 (+0.0000 + +3.7139) | 3435% | n/a | n/a | n/a | 0% | 0.500 | — |
| 14 | `0x7187e8b7…9c4d` | 5 | 1 | 0 | n/a | +3.6830 (+0.0000 + +3.6830) | 365% | n/a | n/a | n/a | 0% | 0.500 | — |
| 15 | `0xb086f7d8…7eac` | 4325 | 433 | 2081 | 48% | +3.6716 (+3.6030 + +0.0686) | 4% | 59s | 0.83 | 22% | 0% | 0.506 | bot, contract, fees_partly_estimated |
| 16 | `0xb0b09244…2215` | 3 | 2 | 0 | n/a | +3.6610 (+0.0042 + +3.6567) | 67% | n/a | n/a | n/a | 0% | 0.500 | — |
| 17 | `0xae10c92a…7c20` | 1 | 1 | 0 | n/a | +3.6319 (+0.0000 + +3.6319) | 1211% | n/a | n/a | n/a | 0% | 0.500 | fees_estimated |
| 18 | `0x047adcc0…b7c3` | 29 | 1 | 1 | 0% | +3.4827 (+0.8431 + +2.6397) | 158% | 25s | 0.17 | 0% | 0% | 0.476 | fees_partly_estimated |
| 19 | `0x99e8f44f…4775` | 7 | 1 | 0 | n/a | +3.4583 (+0.0000 + +3.4583) | 159% | n/a | n/a | n/a | 0% | 0.500 | — |
| 20 | `0x365a446a…b289` | 7 | 4 | 0 | n/a | +3.4020 (+0.0000 + +3.4020) | 309% | n/a | n/a | n/a | 0% | 0.500 | — |

### Top by quality · ≥ 5 trades · no bots, no deployer-linked

| # | wallet | trades | coins | closed | win rate | PnL ETH (real + unreal) | ROI | median hold | exit q | rug avoid | sniper | quality | tags |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `0x3034c12e…ea8a` | 102 | 3 | 51 | 94% | +0.0719 (+0.0719 + +0.0000) | 4% | 1s | 0.76 | 98% | 0% | 0.774 | — |
| 2 | `0x5bdddd1a…433e` | 212 | 5 | 106 | 87% | +0.0996 (+0.0996 + +0.0000) | 3% | 2s | 0.81 | 97% | 0% | 0.774 | — |
| 3 | `0xed1282c4…2a01` | 27 | 7 | 7 | 100% | +0.7501 (+0.7501 + +0.0000) | 129% | 1m26s | 1.00 | 50% | 86% | 0.711 | sniper, fees_estimated |
| 4 | `0xebaad699…5686` | 216 | 13 | 108 | 84% | +0.2772 (+0.2772 + +0.0000) | 3% | 9s | 0.77 | 71% | 0% | 0.709 | — |
| 5 | `0x9be4f198…d374` | 44 | 2 | 22 | 95% | +0.0381 (+0.0381 + +0.0000) | 5% | 1s | 0.99 | n/a | 0% | 0.704 | — |
| 6 | `0x73cdee08…eb2f` | 16 | 1 | 8 | 100% | +0.0351 (+0.0351 + +0.0000) | 16% | 1m29s | 0.80 | 100% | 0% | 0.698 | — |
| 7 | `0x0f9c0261…ac8f` | 13 | 3 | 6 | 100% | +0.0462 (+0.0462 + +0.0000) | 15% | 44s | 0.95 | 100% | 0% | 0.685 | fees_estimated |
| 8 | `0xdf16c64e…217e` | 32 | 2 | 16 | 94% | +0.0307 (+0.0307 + +0.0000) | 5% | 1s | 0.99 | n/a | 0% | 0.684 | — |
| 9 | `0x3c1edbde…1cf8` | 26 | 4 | 8 | 100% | +0.0299 (+0.0299 + +0.0000) | 12% | 1m08s | 0.90 | 80% | 0% | 0.682 | — |
| 10 | `0x89ebf21e…3ca9` | 8 | 4 | 4 | 100% | +0.0036 (+0.0036 + +0.0000) | 61% | 3m34s | 0.96 | 100% | 0% | 0.671 | — |
| 11 | `0x321a8237…8d73` | 210 | 15 | 105 | 82% | +0.2555 (+0.2555 + +0.0000) | 4% | 19s | 0.76 | 55% | 0% | 0.669 | — |
| 12 | `0x1aca18b4…af40` | 13 | 3 | 6 | 83% | +0.0496 (+0.0496 + +0.0000) | 17% | 1m07s | 0.97 | 100% | 0% | 0.666 | fees_estimated |
| 13 | `0xedf65f7b…a568` | 50 | 17 | 25 | 68% | +0.4870 (+0.4870 + +0.0000) | 46% | 7s | 0.99 | 43% | 36% | 0.666 | sniper, fees_estimated |
| 14 | `0x5caeecc1…d6ed` | 35 | 12 | 17 | 100% | +0.0754 (+0.0754 + +0.0000) | 17% | 32s | 0.70 | 44% | 0% | 0.659 | fees_partly_estimated |
| 15 | `0xc60cbcd9…f8bd` | 138 | 29 | 65 | 71% | +0.0065 (+0.0076 + -0.0010) | 3% | 21s | 0.81 | 65% | 0% | 0.655 | contract |
| 16 | `0xad48fc7b…6e96` | 26 | 14 | 12 | 83% | +0.1242 (+0.0979 + +0.0262) | 43% | 4m14s | 0.68 | 60% | 0% | 0.652 | fees_partly_estimated |
| 17 | `0xd9a0219e…52f6` | 24 | 1 | 12 | 92% | +0.0105 (+0.0105 + +0.0000) | 4% | 2s | 0.91 | n/a | 0% | 0.650 | — |
| 18 | `0xca8527b3…f453` | 40 | 15 | 13 | 77% | +0.0617 (+0.0627 + -0.0010) | 62% | 3m57s | 0.88 | 25% | 0% | 0.649 | fees_partly_estimated |
| 19 | `0xf4931f57…0db3` | 83 | 24 | 20 | 75% | +1.2906 (+1.1648 + +0.1258) | 101% | 6m33s | 0.85 | 6% | 0% | 0.648 | fees_partly_estimated |
| 20 | `0xf83464fd…2007` | 77 | 23 | 20 | 80% | +1.0550 (+0.9457 + +0.1093) | 90% | 6m14s | 0.84 | 6% | 0% | 0.647 | fees_partly_estimated |

### Snipers by ETH PnL

| # | wallet | trades | coins | closed | win rate | PnL ETH (real + unreal) | ROI | median hold | exit q | rug avoid | sniper | quality | tags |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `0x91d0e6c7…1292` | 22 | 10 | 3 | 0% | +11.3922 (-0.0191 + +11.4113) | 1025% | 8s | 0.71 | 0% | 100% | 0.437 | sniper, fees_partly_estimated |
| 2 | `0x4f967828…d136` | 27 | 12 | 4 | 25% | +8.6634 (+0.0424 + +8.6210) | 858% | 12s | 0.68 | 0% | 100% | 0.449 | sniper, fees_partly_estimated |
| 3 | `0xd7160549…0fa5` | 1 | 1 | 0 | n/a | +5.0927 (+0.0000 + +5.0927) | 504% | n/a | n/a | n/a | 100% | 0.500 | deployer, sniper, fees_estimated |
| 4 | `0xfa30cbed…3353` | 3 | 2 | 1 | 0% | +4.2062 (+0.0000 + +4.2062) | 4624% | 1m34s | 1.00 | 0% | 50% | 0.490 | sniper, fees_estimated |
| 5 | `0x76ab301f…0c2e` | 5 | 3 | 2 | 0% | +3.0978 (-0.0134 + +3.1112) | 1813% | 53s | 0.99 | 50% | 33% | 0.498 | sniper, fees_estimated |
| 6 | `0xf0321b0e…c350` | 14 | 7 | 7 | 100% | +1.9662 (+1.9662 + +0.0000) | 139% | 17s | 0.76 | 0% | 100% | 0.624 | deployer, sniper, fees_estimated |
| 7 | `0xe499794d…8a87` | 40 | 20 | 20 | 90% | +1.9596 (+1.9596 + +0.0000) | 30% | 13s | 0.82 | 0% | 100% | 0.603 | deployer, sniper, fees_estimated |
| 8 | `0x9e141375…c69e` | 6 | 2 | 1 | 0% | +1.3090 (+0.1339 + +1.1751) | 1432% | 1m33s | 1.00 | 0% | 50% | 0.511 | sniper, fees_estimated |
| 9 | `0xa432f6cc…1bcb` | 2 | 1 | 1 | 100% | +1.2720 (+1.2720 + +0.0000) | 125% | 10m51s | 1.00 | 0% | 100% | 0.531 | deployer, sniper, fees_estimated |
| 10 | `0x32cfdee3…96d9` | 22 | 11 | 11 | 91% | +1.1569 (+1.1569 + +0.0000) | 35% | 14s | 0.63 | 0% | 100% | 0.562 | deployer, sniper, fees_estimated |
| 11 | `0x33754f0e…f90f` | 1 | 1 | 0 | n/a | +1.0993 (+0.0000 + +1.0993) | 218% | n/a | n/a | n/a | 100% | 0.500 | deployer, sniper, fees_estimated |
| 12 | `0x96164204…eba8` | 34 | 10 | 10 | 60% | +1.0481 (+1.0481 + +0.0000) | 73% | 20s | 0.89 | 12% | 40% | 0.582 | sniper, fees_estimated |
| 13 | `0x89fb00e7…2e0d` | 27 | 13 | 13 | 92% | +1.0144 (+1.0144 + +0.0000) | 26% | 16s | 0.92 | 0% | 100% | 0.605 | deployer, sniper, fees_estimated |
| 14 | `0x09234d44…5415` | 2 | 1 | 1 | 100% | +0.9201 (+0.9201 + +0.0000) | 234% | 15m28s | 1.00 | 0% | 100% | 0.531 | deployer, sniper, fees_estimated |
| 15 | `0x36df3662…62d8` | 70 | 31 | 31 | 35% | +0.8730 (+0.8730 + +0.0000) | 23% | 16s | 0.87 | 0% | 100% | 0.462 | deployer, sniper, fees_estimated |
| 16 | `0x4ee6d50c…cafd` | 14 | 7 | 7 | 100% | +0.8465 (+0.8465 + +0.0000) | 40% | 19s | 0.90 | n/a | 100% | 0.655 | deployer, sniper, fees_estimated |
| 17 | `0xed1282c4…2a01` | 27 | 7 | 7 | 100% | +0.7501 (+0.7501 + +0.0000) | 129% | 1m26s | 1.00 | 50% | 86% | 0.711 | sniper, fees_estimated |
| 18 | `0xff779577…aada` | 3 | 1 | 1 | 100% | +0.7268 (+0.7268 + +0.0000) | 360% | 56s | 0.39 | n/a | 100% | 0.527 | deployer, sniper, fees_estimated |
| 19 | `0x412361ca…4096` | 1 | 1 | 0 | n/a | +0.6523 (+0.0000 + +0.6523) | 431% | n/a | n/a | n/a | 100% | 0.500 | deployer, sniper, fees_estimated |
| 20 | `0x50ac6b79…39c2` | 74 | 34 | 34 | 21% | +0.5854 (+0.5854 + +0.0000) | 14% | 18s | 0.86 | 0% | 100% | 0.406 | deployer, sniper, fees_estimated |

### Bots by ETH PnL

| # | wallet | trades | coins | closed | win rate | PnL ETH (real + unreal) | ROI | median hold | exit q | rug avoid | sniper | quality | tags |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `0xb086f7d8…7eac` | 4325 | 433 | 2081 | 48% | +3.6716 (+3.6030 + +0.0686) | 4% | 59s | 0.83 | 22% | 0% | 0.506 | bot, contract, fees_partly_estimated |
| 2 | `0xc87b51c7…6a66` | 933 | 92 | 128 | 64% | +2.7032 (+3.0355 + -0.3323) | 7% | 28m03s | 0.86 | 4% | 0% | 0.537 | bot |
| 3 | `0x66c5f288…5ce7` | 599 | 88 | 154 | 47% | +0.8598 (+0.9309 + -0.0711) | 5% | 7m40s | 0.82 | 13% | 0% | 0.486 | bot, contract, fees_partly_estimated |
| 4 | `0xbc3baa7b…9a9f` | 718 | 1 | 11 | 64% | +0.8526 (+0.8508 + +0.0017) | 19% | 2m40s | 0.86 | 36% | 0% | 0.575 | bot, contract |
| 5 | `0xd01d7d0f…2257` | 439 | 42 | 215 | 80% | +0.5538 (+0.6456 + -0.0918) | 2% | 50s | 0.75 | 27% | 0% | 0.608 | bot |
| 6 | `0x38765994…a683` | 253 | 23 | 34 | 26% | +0.5436 (+0.5256 + +0.0180) | 24% | 1m56s | 0.82 | 9% | 0% | 0.444 | bot, fees_partly_estimated |
| 7 | `0x1749561f…310a` | 679 | 2 | 339 | 84% | +0.4620 (+0.4620 + +0.0000) | 1% | 1s | 0.72 | 100% | 0% | 0.761 | bot |
| 8 | `0xdd3b39aa…92c6` | 366 | 3 | 183 | 89% | +0.3690 (+0.3690 + +0.0000) | 2% | 1s | 0.67 | 99% | 0% | 0.761 | bot |
| 9 | `0x5c87ecc3…65ec` | 610 | 23 | 303 | 60% | +0.3261 (+0.3234 + +0.0027) | 1% | 14s | 0.78 | 73% | 0% | 0.637 | bot, fees_partly_estimated |
| 10 | `0x2cb2a174…a1b1` | 1529 | 33 | 63 | 60% | +0.2764 (+0.2580 + +0.0184) | 2% | 17m33s | 0.85 | 7% | 0% | 0.519 | bot, contract |
| 11 | `0x50f6d9d3…ed80` | 663 | 33 | 245 | 54% | +0.2746 (+0.2676 + +0.0071) | 1% | 36s | 0.81 | 10% | 0% | 0.497 | bot, contract |
| 12 | `0x2a4d34cd…21d7` | 822 | 71 | 408 | 57% | +0.2522 (+0.2513 + +0.0009) | 5% | 50s | 0.81 | 8% | 0% | 0.510 | bot, fees_partly_estimated |
| 13 | `0x555f23f8…b6a3` | 350 | 3 | 175 | 90% | +0.2398 (+0.2398 + +0.0000) | 3% | 1s | 0.68 | 100% | 0% | 0.772 | bot |
| 14 | `0xf8022562…5c7d` | 172 | 3 | 86 | 87% | +0.2298 (+0.2298 + +0.0000) | 5% | 1s | 0.76 | 100% | 0% | 0.769 | bot |
| 15 | `0x4127a392…180e` | 544 | 63 | 228 | 60% | +0.2054 (+0.2040 + +0.0014) | 9% | 7s | 0.83 | 30% | 0% | 0.568 | bot, fees_partly_estimated |
| 16 | `0x6e15979e…faab` | 327 | 101 | 138 | 42% | +0.1828 (+0.1828 + +0.0000) | 5% | 2m01s | 0.81 | 0% | 4% | 0.443 | bot, contract, fees_estimated |
| 17 | `0xe184d817…ec2f` | 80 | 3 | 40 | 82% | +0.1145 (+0.1145 + +0.0000) | 8% | 7s | 0.75 | 0% | 0% | 0.560 | bot |
| 18 | `0x8f6f0cb3…eac3` | 610 | 234 | 305 | 53% | +0.0959 (+0.0959 + +0.0000) | 6% | 14s | 0.80 | 5% | 13% | 0.490 | bot, contract, fees_estimated |
| 19 | `0x3c2bca0a…eb99` | 168 | 58 | 72 | 38% | +0.0931 (+0.0931 + +0.0000) | 9% | 14s | 0.86 | 0% | 12% | 0.445 | bot, fees_estimated |
| 20 | `0x88f5a943…4a77` | 72 | 7 | 6 | 17% | +0.0899 (+0.2153 + -0.1253) | 2% | 6m15s | 0.81 | 17% | 0% | 0.454 | bot |

## Walk-forward: first half → second half

Stats are built on 2026-09-11 05:44–2026-09-11 08:16 and the same wallets are measured again on 2026-09-11 08:16–2026-09-11 10:49 UTC (positions opened in the second half only; open lots marked at the end of each half). Wallets with ≥ 5 first-half trades that traded again in the second half: **5,938**; eligible for the top lists (not bot, not deployer-linked): 5,827.

| rank correlation (Spearman) | ρ |
|---|---|
| pnl h1 vs pnl h2 | 0.156 |
| quality h1 vs pnl h2 | 0.133 |
| quality h1 vs quality h2 | 0.249 |
| roi h1 vs roi h2 | 0.119 |

| second-half ETH PnL of… | wallets | mean | median | bootstrap 95% CI of the mean (1000 resamples) | positive | sum |
|---|---|---|---|---|---|---|
| top-50 by first-half quality | 50 | +0.0101 | -0.0002 | [-0.0209, +0.0529] | 34% | +0.507 |
| top-50 by first-half PnL | 50 | +0.0334 | +0.0000 | [-0.0039, +0.0748] | 46% | +1.670 |
| bottom-50 by first-half quality | 50 | -0.0341 | -0.0070 | [-0.0524, -0.0165] | 16% | -1.704 |
| all eligible wallets | 5827 | +0.0022 | +0.0000 | [-0.0036, +0.0090] | 26% | +12.830 |

## Copy-test: follow the top-K wallets in the second half

Rule: when a top-K wallet (by first-half quality) buys an ETH-quoted coin, buy 0.02 ETH at the curve state right after its trade (constant product, reserves = 1.68 ETH virtual + net quote in / 1e9 supply − net tokens out, 1% fee + the coin's median creator tax, our own impact included on entry and exit); sell when it sells, or after 30 min. Graduated pools: observed price with a flat 1% + tax. One copy position per wallet-coin episode; 166 leader buys, 164 priced.

| K | copy trades | hit rate | mean return | median return | 95% CI of the mean | total PnL (ETH) | exit on leader's sell | mean ret · leader sold | mean ret · 30 min | curve method | median impact |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 10 | 49 | 43% | -11.8% | -4.1% | [-22.0%, -2.2%] | -0.1153 | 37 | -3.1% | -38.6% | 16 / 49 | 1.74% |
| 25 | 103 | 49% | 0.2% | -0.8% | [-8.4%, 9.7%] | +0.0040 | 89 | 5.7% | -34.9% | 52 / 103 | 1.81% |
| 50 | 164 | 41% | -4.6% | -4.8% | [-11.8%, 3.2%] | -0.1505 | 145 | -3.8% | -10.3% | 87 / 164 | 1.70% |

## Reading this honestly

- Copy-test verdict: K=10: loses -11.8% per trade over 49 trades (43% hit rate); K=25: earns 0.2% per trade over 103 trades (49% hit rate); K=50: loses -4.6% per trade over 164 trades (41% hit rate). A copier pays the 1% fee and the creator tax twice and buys after the leader moved the curve; the leader's own numbers do not include that.
- Persistence: Spearman ρ of first-half vs second-half PnL is 0.156; of first-half quality vs second-half PnL 0.133. The top-50 by quality made +0.0101 ETH each on average in the second half (95% CI [-0.0209, +0.0529]); a CI that includes 0 means the forward edge is not established by this sample.
- Ranks, sniper flags and rug flags are only as good as the launch time: `launches.ts` is derived from exact block timestamps when the launch block is inside the store; coins launched before the range have unknown buyer ranks (shown as n/a, never 0).
- Unrealized PnL marks open bags at the last minute-median trade price; illiquid coins can rarely be sold at that mark. A wallet's ETH PnL ignores its positions quoted in other assets (kept in `pnl_by_quote`).
- Same address, observed trades. Nothing here says who controls a wallet or why it traded; deployer links are on-chain facts (launch events and snipe-tax exemption), not accusations.

## Exact commands

```
uv run stampede traders --db data/research-12h.sqlite --fees estimate --min-trades 5 --top 50 --out docs/RESEARCH-TRADERS.md
# 14-day research store (real fee columns), once data/backfill-14d.log says READY:
uv run stampede fx --db data/research-14d.sqlite --days 16   # hourly ETH rates for the USD columns
uv run stampede traders --db data/research-14d.sqlite --fees strict --out docs/RESEARCH-TRADERS.md
# stores normalized before the fee columns existed (NULL fee_raw): --fees estimate
# then: GET /api/traders?preset=smart · GET /api/wallet/<address> · web TRADERS view (key 4) · TUI screen 3
```

Written: wallet_stats 150,893 rows (full / first half / second half), wallet_positions for wallets with ≥ 2 trades, wallet_scores bot flags for 160 wallets (`score` untouched).