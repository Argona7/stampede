# Trader intelligence: who earns on PONS after fees, and does it persist?

Generated 2026-09-12 10:03 UTC by `stampede traders` from `/tmp/bf-test.sqlite`.

## Setup

- Data: 15,663 trades by 4,365 non-infrastructure wallets on 888 coins, 2026-09-12 08:23–2026-09-12 08:33 UTC (0.2 h). Walk-forward split at 2026-09-12 08:28 UTC (first half: 2,529 wallets, second half: 2,766).
- Fees: `strict`. Rows without `fee_raw` on the curve: 0 (skipped); rows without a quote amount (multi-hop / two-sided transactions): 5,568 counted as trades but not priced. Graduated-pool (v4) rows: fees sit inside the net amounts (the hook takes them in the unspecified currency), so they are not listed separately.
- USD: fx_rates loaded (3332 hourly points); USD columns are filled where every leg has a rate. PnL is stated in quote units first; the ETH columns cover positions quoted in native ETH/WETH (1,081 wallets); other quote assets are kept per asset in `pnl_by_quote`.
- Wallets tagged: 3 bots (> 60 trades/h over their active span, > 500 trades, or ≥ 3 blocks with buys of several coins), 103 deployer-linked (deployer of a coin they traded, or bought without snipe tax while later buyers still paid it: 48), 141 snipers (≥ 30% of entries under 3 s after launch or with snipe tax paid).
- Positions: 991 closed (wallet, coin) episodes; a position closes when ≤ 1% of its peak inventory is left (the dust is written off). Sells without matching lots (tokens received by transfer, or bought before the range) are ignored, never counted as profit.
- Compute: token pass 0.1 s, wallet pass 0.2 s, total 1 s.

## Definitions

- **Cost of a lot** = quote paid + 1% fee (incl. snipe tax) + creator tax. **Proceeds** = net quote received. **Realized PnL** = FIFO proceeds − matched cost. **Unrealized** = open inventory × last minute-median price − remaining cost. **ROI** = (realized + unrealized) / total cost of buys; `roi_realized` = realized / cost of the lots actually sold.
- **Win rate** = closed positions with PnL > 0 / closed positions. **Median hold** = median(last sell − first buy) over closed positions. **Trades/hour** over the wallet's active span (≥ 1 h).
- **Buyer rank** = n-th distinct buyer of the coin since its launch (known only when the launch is inside the range). **Sniper** = entry < 3 s after launch or snipe tax paid. **Exit quality** = exit price / max minute-median price in the following 15 min (1 = sold the top), proceeds-weighted per position. **Rug avoidance** = share of closed positions after which the coin fell ≥ 80% within 60 min. **Consistency** = share of active ISO weeks with positive realized PnL.
- **Quality** (0–1, 0.5 = no evidence) = shrink × (0.35 × Laplace win rate + 0.25 × clip((roi_realized + 1)/2) + 0.2 × exit quality + 0.2 × rug avoidance) + (1 − shrink) × 0.5, shrink = n / (n + 5) over n closed positions. Bots and deployer-linked wallets keep their quality but are excluded from the `smart` preset and from the walk-forward top lists.

## Population

- 4,365 wallets with ≥ 1 trade; 635 with ≥ 1 closed position; of those 188 (29.6%) ended the range with a positive ETH PnL (realized + unrealized).
- ETH PnL across wallets with a closed position: median -0.0006 ETH, mean -0.0039, sum -2.47; fees paid 1.69 ETH.
- Median win rate 0.0%, median exit quality n/a, median rug-avoidance share n/a, median hold 34s.

## Leaderboards (full range)

### Top by ETH PnL (realized + unrealized), every wallet

| # | wallet | trades | coins | closed | win rate | PnL ETH (real + unreal) | ROI | median hold | exit q | rug avoid | sniper | quality | tags |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `0xf42029ad…212a` | 8 | 2 | 2 | 100% | +0.6497 (+0.6497 + +0.0000) | 208% | 14s | n/a | n/a | 100% | 0.561 | snipe_exempt, sniper |
| 2 | `0xeecfd8e4…e2d6` | 3 | 1 | 0 | n/a | +0.4490 (+0.0000 + +0.4490) | 42% | n/a | n/a | n/a | 0% | 0.500 | — |
| 3 | `0xf4bee223…d725` | 8 | 2 | 2 | 100% | +0.3955 (+0.3955 + +0.0000) | 144% | 13s | n/a | n/a | 100% | 0.561 | snipe_exempt, sniper |
| 4 | `0xa3e2f78d…44d3` | 8 | 2 | 2 | 50% | +0.2984 (+0.2984 + +0.0000) | 94% | 14s | n/a | n/a | 100% | 0.533 | snipe_exempt, sniper |
| 5 | `0x9bef45e5…c731` | 3 | 2 | 0 | n/a | +0.1512 (+0.0000 + +0.1512) | 88% | n/a | n/a | n/a | 0% | 0.500 | — |
| 6 | `0x4eae6ec4…e9db` | 5 | 2 | 2 | 100% | +0.1469 (+0.1469 + +0.0000) | 106% | 14s | n/a | n/a | 50% | 0.561 | snipe_exempt, sniper |
| 7 | `0xe7ffde40…8023` | 4 | 2 | 2 | 100% | +0.1285 (+0.1285 + +0.0000) | 77% | 12s | n/a | n/a | 100% | 0.552 | snipe_exempt, sniper |
| 8 | `0x24abcde6…b3e3` | 2 | 1 | 1 | 100% | +0.1146 (+0.1146 + +0.0000) | 121% | 16s | n/a | n/a | 0% | 0.531 | — |
| 9 | `0xcb4d28c2…35c9` | 6 | 3 | 0 | n/a | +0.1079 (+0.0043 + +0.1037) | 26% | n/a | n/a | n/a | 0% | 0.500 | — |
| 10 | `0xdb1c052c…5add` | 2 | 1 | 0 | n/a | +0.0995 (+0.0000 + +0.0995) | 98% | n/a | n/a | n/a | 0% | 0.500 | — |
| 11 | `0x40e9049a…0c39` | 1 | 1 | 0 | n/a | +0.0982 (+0.0000 + +0.0982) | 47% | n/a | n/a | n/a | 0% | 0.500 | — |
| 12 | `0x6f149c0d…9e71` | 4 | 2 | 0 | n/a | +0.0958 (+0.0000 + +0.0958) | 59% | n/a | n/a | n/a | 0% | 0.500 | — |
| 13 | `0xf9d33b7d…47dd` | 2 | 2 | 0 | n/a | +0.0944 (+0.0000 + +0.0944) | 50% | n/a | n/a | n/a | 0% | 0.500 | — |
| 14 | `0x79cd295f…0972` | 1 | 1 | 0 | n/a | +0.0892 (+0.0000 + +0.0892) | 87% | n/a | n/a | n/a | 0% | 0.500 | — |
| 15 | `0x77a97779…13cb` | 4 | 1 | 0 | n/a | +0.0869 (+0.0000 + +0.0869) | 71% | n/a | n/a | n/a | 0% | 0.500 | — |
| 16 | `0x86fed60e…e379` | 13 | 2 | 0 | n/a | +0.0865 (+0.0000 + +0.0865) | 65% | n/a | n/a | n/a | 0% | 0.500 | — |
| 17 | `0x111b8304…0911` | 4 | 2 | 2 | 100% | +0.0859 (+0.0859 + +0.0000) | 50% | 22s | n/a | n/a | 100% | 0.543 | snipe_exempt, sniper |
| 18 | `0x093c4ad7…52c6` | 2 | 1 | 0 | n/a | +0.0848 (+0.0000 + +0.0848) | 54% | n/a | n/a | n/a | 0% | 0.500 | — |
| 19 | `0x80246101…08c3` | 2 | 1 | 0 | n/a | +0.0794 (+0.0000 + +0.0794) | 65% | n/a | n/a | n/a | 0% | 0.500 | — |
| 20 | `0x4dbf197e…dd39` | 7 | 3 | 0 | n/a | +0.0768 (+0.0000 + +0.0768) | 68% | n/a | n/a | n/a | 0% | 0.500 | launcher |

### Top by quality · ≥ 5 trades · no bots, no deployer-linked

| # | wallet | trades | coins | closed | win rate | PnL ETH (real + unreal) | ROI | median hold | exit q | rug avoid | sniper | quality | tags |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `0x3e7c18b0…000b` | 7 | 4 | 3 | 100% | +0.0125 (+0.0125 + +0.0000) | 116% | 12s | n/a | n/a | 100% | 0.586 | sniper |
| 2 | `0xb5e55edf…0890` | 8 | 4 | 4 | 100% | +0.0063 (+0.0063 + +0.0000) | 58% | 7s | n/a | n/a | 100% | 0.584 | sniper |
| 3 | `0xf3ce52b1…32b3` | 7 | 4 | 3 | 100% | +0.0058 (+0.0057 + +0.0000) | 53% | 6s | n/a | n/a | 100% | 0.573 | sniper |
| 4 | `0xbcd5f0f5…7a8a` | 8 | 2 | 4 | 100% | +0.0134 (+0.0134 + +0.0000) | 33% | 2s | n/a | n/a | 0% | 0.570 | — |
| 5 | `0xf5704526…f8c7` | 6 | 3 | 3 | 100% | +0.0020 (+0.0020 + +0.0000) | 32% | 5s | n/a | n/a | 100% | 0.554 | sniper |
| 6 | `0xec383f8d…1431` | 5 | 1 | 2 | 100% | +0.0207 (+0.0222 + -0.0015) | 30% | 2m05s | n/a | n/a | 0% | 0.542 | launcher |
| 7 | `0x8f6f0cb3…eac3` | 15 | 4 | 7 | 71% | +0.0032 (+0.0032 + +0.0000) | 9% | 4s | n/a | n/a | 29% | 0.541 | — |
| 8 | `0xddc77870…6bf6` | 5 | 1 | 2 | 100% | +0.0173 (+0.0165 + +0.0008) | 31% | 1m46s | n/a | n/a | 0% | 0.538 | — |
| 9 | `0x4eb43afc…df70` | 5 | 1 | 2 | 100% | +0.0158 (+0.0158 + +0.0000) | 22% | 3m37s | n/a | n/a | 0% | 0.533 | — |
| 10 | `0xc70e3eab…b662` | 5 | 2 | 2 | 100% | +0.0209 (+0.0209 + +0.0000) | 15% | 8s | n/a | n/a | 0% | 0.530 | — |
| 11 | `0x84377ec6…a61a` | 5 | 1 | 2 | 100% | +0.0076 (+0.0068 + +0.0009) | 10% | 2m44s | n/a | n/a | 0% | 0.529 | — |
| 12 | `0x97fa9d5d…fad2` | 5 | 1 | 2 | 100% | +0.0044 (+0.0044 + +0.0000) | 8% | 2m53s | n/a | n/a | 0% | 0.528 | — |
| 13 | `0xc2606da7…b35f` | 5 | 3 | 2 | 100% | +0.0032 (+0.0032 + +0.0000) | 4% | 29s | n/a | n/a | 0% | 0.526 | — |
| 14 | `0x9dd01531…e2ef` | 9 | 5 | 4 | 75% | -0.0002 (-0.0002 + +0.0000) | -3% | 17s | n/a | n/a | 0% | 0.524 | — |
| 15 | `0x53afaa82…41e5` | 6 | 3 | 3 | 67% | +0.0194 (+0.0194 + +0.0000) | 24% | 1m48s | n/a | n/a | 100% | 0.524 | sniper |
| 16 | `0x7e21d492…c2d2` | 6 | 1 | 3 | 67% | +0.0012 (+0.0012 + +0.0000) | 16% | 5s | n/a | n/a | 0% | 0.520 | — |
| 17 | `0x46d540bb…5df9` | 6 | 1 | 3 | 67% | +0.0010 (+0.0010 + +0.0000) | 13% | 5s | n/a | n/a | 0% | 0.519 | — |
| 18 | `0x9c1288c4…659b` | 10 | 5 | 1 | 100% | +0.0042 (+0.0042 + +0.0000) | 41% | 6m39s | n/a | n/a | 0% | 0.518 | — |
| 19 | `0x3bfbecf7…ca28` | 9 | 1 | 1 | 100% | +0.0146 (+0.0146 + +0.0000) | 40% | 4m53s | n/a | n/a | 0% | 0.518 | — |
| 20 | `0x48145f26…954f` | 6 | 1 | 1 | 100% | +0.0285 (+0.0285 + +0.0000) | 40% | 4m02s | n/a | n/a | 0% | 0.518 | — |

### Snipers by ETH PnL

| # | wallet | trades | coins | closed | win rate | PnL ETH (real + unreal) | ROI | median hold | exit q | rug avoid | sniper | quality | tags |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `0xf42029ad…212a` | 8 | 2 | 2 | 100% | +0.6497 (+0.6497 + +0.0000) | 208% | 14s | n/a | n/a | 100% | 0.561 | snipe_exempt, sniper |
| 2 | `0xf4bee223…d725` | 8 | 2 | 2 | 100% | +0.3955 (+0.3955 + +0.0000) | 144% | 13s | n/a | n/a | 100% | 0.561 | snipe_exempt, sniper |
| 3 | `0xa3e2f78d…44d3` | 8 | 2 | 2 | 50% | +0.2984 (+0.2984 + +0.0000) | 94% | 14s | n/a | n/a | 100% | 0.533 | snipe_exempt, sniper |
| 4 | `0x4eae6ec4…e9db` | 5 | 2 | 2 | 100% | +0.1469 (+0.1469 + +0.0000) | 106% | 14s | n/a | n/a | 50% | 0.561 | snipe_exempt, sniper |
| 5 | `0xe7ffde40…8023` | 4 | 2 | 2 | 100% | +0.1285 (+0.1285 + +0.0000) | 77% | 12s | n/a | n/a | 100% | 0.552 | snipe_exempt, sniper |
| 6 | `0x111b8304…0911` | 4 | 2 | 2 | 100% | +0.0859 (+0.0859 + +0.0000) | 50% | 22s | n/a | n/a | 100% | 0.543 | snipe_exempt, sniper |
| 7 | `0xc6097380…cbf4` | 1 | 1 | 0 | n/a | +0.0516 (+0.0000 + +0.0516) | 84% | n/a | n/a | n/a | 100% | 0.500 | deployer, snipe_exempt, sniper |
| 8 | `0x79495dc9…1ece` | 1 | 1 | 0 | n/a | +0.0409 (+0.0000 + +0.0409) | 47% | n/a | n/a | n/a | 100% | 0.500 | sniper |
| 9 | `0xa23248f4…a2d4` | 1 | 1 | 0 | n/a | +0.0388 (+0.0000 + +0.0388) | 77% | n/a | n/a | n/a | 100% | 0.500 | deployer, sniper |
| 10 | `0x307ee99b…74e2` | 2 | 1 | 1 | 100% | +0.0312 (+0.0312 + +0.0000) | 38% | 44s | n/a | n/a | 100% | 0.518 | deployer, sniper |
| 11 | `0x4f497a00…23e0` | 2 | 1 | 1 | 100% | +0.0311 (+0.0311 + +0.0000) | 60% | 3m12s | n/a | n/a | 100% | 0.522 | deployer, snipe_exempt, sniper |
| 12 | `0xff4bc257…a8c3` | 7 | 2 | 2 | 50% | +0.0290 (+0.0290 + +0.0000) | 10% | 35s | n/a | n/a | 100% | 0.503 | sniper |
| 13 | `0xc9b303e7…b681` | 2 | 1 | 0 | n/a | +0.0262 (+0.0039 + +0.0223) | 28% | n/a | n/a | n/a | 100% | 0.500 | sniper |
| 14 | `0xa653ca14…765f` | 4 | 2 | 1 | 100% | +0.0202 (+0.0054 + +0.0148) | 52% | 3m19s | n/a | n/a | 50% | 0.516 | snipe_exempt, sniper |
| 15 | `0xfec7796f…641e` | 4 | 2 | 2 | 50% | +0.0199 (+0.0199 + +0.0000) | 11% | 22s | n/a | n/a | 100% | 0.504 | snipe_exempt, sniper |
| 16 | `0x53afaa82…41e5` | 6 | 3 | 3 | 67% | +0.0194 (+0.0194 + +0.0000) | 24% | 1m48s | n/a | n/a | 100% | 0.524 | sniper |
| 17 | `0x8bf2bbea…1bc6` | 5 | 2 | 2 | 50% | +0.0191 (+0.0191 + +0.0000) | 15% | 20s | n/a | n/a | 50% | 0.505 | sniper |
| 18 | `0x59b39e11…e6cf` | 6 | 2 | 2 | 100% | +0.0167 (+0.0199 + -0.0032) | 21% | 2m57s | n/a | n/a | 33% | 0.540 | snipe_exempt, sniper |
| 19 | `0x57775491…e99a` | 4 | 2 | 2 | 50% | +0.0149 (+0.0149 + +0.0000) | 13% | 15s | n/a | n/a | 50% | 0.504 | snipe_exempt, sniper |
| 20 | `0x637fa1cc…36a0` | 2 | 1 | 1 | 100% | +0.0144 (+0.0144 + +0.0000) | 16% | 1s | n/a | n/a | 100% | 0.513 | sniper |

### Bots by ETH PnL

| # | wallet | trades | coins | closed | win rate | PnL ETH (real + unreal) | ROI | median hold | exit q | rug avoid | sniper | quality | tags |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `0xaa769c28…9889` | 201 | 201 | 0 | n/a | +0.0000 (+0.0000 + +0.0000) | n/a | n/a | n/a | n/a | n/a | 0.500 | bot |
| 2 | `0xd91abf0e…fb56` | 61 | 60 | 0 | n/a | +0.0000 (+0.0000 + +0.0000) | n/a | n/a | n/a | n/a | 0% | 0.500 | bot |
| 3 | `0xb086f7d8…7eac` | 126 | 57 | 20 | 15% | -0.0036 (-0.0123 + +0.0087) | -1% | 32s | n/a | n/a | 0% | 0.407 | bot |

## Walk-forward: first half → second half

Stats are built on 2026-09-12 08:23–2026-09-12 08:28 and the same wallets are measured again on 2026-09-12 08:28–2026-09-12 08:33 UTC (positions opened in the second half only; open lots marked at the end of each half). Wallets with ≥ 5 first-half trades that traded again in the second half: **79**; eligible for the top lists (not bot, not deployer-linked): 77.

| rank correlation (Spearman) | ρ |
|---|---|
| pnl h1 vs pnl h2 | 0.323 |
| quality h1 vs pnl h2 | 0.391 |
| quality h1 vs quality h2 | 0.282 |
| roi h1 vs roi h2 | 0.322 |

| second-half ETH PnL of… | wallets | mean | median | bootstrap 95% CI of the mean (1000 resamples) | positive | sum |
|---|---|---|---|---|---|---|
| top-50 by first-half quality | 50 | -0.0007 | +0.0000 | [-0.0020, +0.0004] | 8% | -0.035 |
| top-50 by first-half PnL | 50 | -0.0004 | +0.0000 | [-0.0016, +0.0005] | 8% | -0.020 |
| bottom-50 by first-half quality | 0 | n/a | n/a | n/a | n/a | n/a |
| all eligible wallets | 77 | -0.0028 | +0.0000 | [-0.0049, -0.0011] | 12% | -0.219 |

## Copy-test: follow the top-K wallets in the second half

Rule: when a top-K wallet (by first-half quality) buys an ETH-quoted coin, buy 0.02 ETH at the curve state right after its trade (constant product, reserves = 1.68 ETH virtual + net quote in / 1e9 supply − net tokens out, 1% fee + the coin's median creator tax, our own impact included on entry and exit); sell when it sells, or after 30 min. Graduated pools: observed price with a flat 1% + tax. One copy position per wallet-coin episode; 15 leader buys, 14 priced.

| K | copy trades | hit rate | mean return | median return | 95% CI of the mean | total PnL (ETH) | exit on leader's sell | mean ret · leader sold | mean ret · 30 min | curve method | median impact |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 10 | 8 | 50% | 6.1% | -11.5% | [-29.3%, 48.5%] | +0.0097 | 8 | 6.1% | n/a | 7 / 8 | 2.14% |
| 25 | 9 | 44% | 5.0% | -3.3% | [-25.9%, 42.0%] | +0.0091 | 8 | 6.1% | -3.3% | 8 / 9 | 2.45% |
| 50 | 14 | 50% | 5.7% | -0.9% | [-15.4%, 29.7%] | +0.0161 | 11 | 6.8% | 1.7% | 12 / 14 | 2.04% |

## Reading this honestly

- Copy-test verdict: K=10: earns 6.1% per trade over 8 trades (50% hit rate); K=25: earns 5.0% per trade over 9 trades (44% hit rate); K=50: earns 5.7% per trade over 14 trades (50% hit rate). A copier pays the 1% fee and the creator tax twice and buys after the leader moved the curve; the leader's own numbers do not include that.
- Persistence: Spearman ρ of first-half vs second-half PnL is 0.323; of first-half quality vs second-half PnL 0.391. The top-50 by quality made -0.0007 ETH each on average in the second half (95% CI [-0.0020, +0.0004]); a CI that includes 0 means the forward edge is not established by this sample.
- Ranks, sniper flags and rug flags are only as good as the launch time: `launches.ts` is derived from exact block timestamps when the launch block is inside the store; coins launched before the range have unknown buyer ranks (shown as n/a, never 0).
- Unrealized PnL marks open bags at the last minute-median trade price; illiquid coins can rarely be sold at that mark. A wallet's ETH PnL ignores its positions quoted in other assets (kept in `pnl_by_quote`).
- Same address, observed trades. Nothing here says who controls a wallet or why it traded; deployer links are on-chain facts (launch events and snipe-tax exemption), not accusations.

## Exact commands

```
uv run stampede traders --db /tmp/bf-test.sqlite --fees strict --min-trades 5 --top 50 --out docs/RESEARCH-TRADERS.md
# then: GET /api/traders?preset=smart · GET /api/wallet/<address> · web TRADERS view (key 4) · TUI screen 3
```

Written: wallet_stats 9,660 rows (full / first half / second half), wallet_positions for wallets with ≥ 2 trades, wallet_scores bot flags for 3 wallets (`score` untouched).