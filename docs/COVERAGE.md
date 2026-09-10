# Coverage report

Generated 2026-09-10 18:18:26 UTC. Everything below describes **one explicitly bounded sample**, not the whole chain.

## Sample

- Label: **PONS v2 sample, 60 min**
- Chain: Robinhood Chain (id 4663). Blocks 59546948–59582600 (35653 blocks).
- Time: 2026-09-10 16:29:57 UTC → 2026-09-10 17:29:57 UTC (60 min), from exact block headers.
- Ingest path: `alchemy_10_block_parallel`; chunks 119, splits 0, failed chunks 0; block ranges lost: 0.
- RPC accounting: 17830 calls, 929 rate-limited responses (all retried), 1207.8 MB received; per host {'alchemy': {'calls': 5382, 'rate_limited': 926, 'errors': 0}, 'public': {'calls': 262, 'rate_limited': 3, 'errors': 0}}.
- Block timestamps: 725 exact anchor headers, largest gap between anchors 50 blocks (≈5 s at ~10 blocks/s); other blocks are linearly interpolated and marked `ts_exact=0`. Trades cited in `EXAMPLES.md` carry exact timestamps.

## Universe: what counts as a coin here

- Nodes are **PONS v2 launchpad tokens only**: 2083 seen on bonding curves in the window (curve → `token()` via eth_call) and 1234 more seen only in graduated Uniswap v4 pools, accepted only when the pool id recomputes from `(quote, token, fee 0, tickSpacing 200, V2MemeHook)` and the PONS v2 factory knows the token.
- v4 pools seen: 3894; resolved as PONS v2: 1255; **not resolved: 2639 pools with 113686 Swap logs** — other tokens/hooks (PONS token itself, PONIE, stock pairs, other launchpads). They are not in the map.
- Venues not covered at all (GeckoTerminal lists 40 DEX ids on this chain): `uniswap-v2-robinhood`, `uniswap-v3-robinhood`, `uniswap-v4-robinhood`, `pancakeswap-v3-robinhood`, `pancakeswap-v2-robinhood`, `bankr-robinhood`, `virtuals-robinhood`, `robinswap`, `hoodit`, `clanker-robinhood`, `easya-kickstart-robinhood`, `swaphood-finance-v2`, `swaphood-finance-v3`, `mint-club-robinhood`, `sushiswap-v2-robinhood`, `sushiswap-v3-robinhood`, `curve-robinhood`, `up-v3`, `sectorone-v2-2-robinhood`, `sectorone-v2-0-robinhood`, `parityswap`, `ekubo-v3-robinhood`, `uniswap-pools-trade`, `ramses-v3-robinhood`, `ramses-dlmm-robinhood`, `ramses-legacy-robinhood`, `giga-v3`, `giga-v2`, `alandale`, `alandale-cl`, `orvex-v2`, `orvex-v4`, `synthra-robinhood`, `rubicon-robinhood`, `rubicon-clmm-robinhood`, `o1-launchpad-robinhood`, `brownfi-v3-robinhood`.

## Raw data

- Swap-class logs kept: 263425 (CurveBuy/CurveSell from any curve, v4 `Swap` only from PoolManager `0x8366…0951`; 0 Swap logs from other addresses dropped).
- Transactions with swap-class logs: 205516. ERC-20 Transfer logs: 1511373 seen in the window, 1148983 kept (those inside swap transactions).

## Normalization (logs → trades)

- 205516 transactions examined → 143526 produced trades → **146329 trades** (75936 buys, 70393 sells) by 33743 wallets in 3315 tokens; by venue {'curve': 63050, 'v4': 83279}.
- Attribution: `transfer_net` for every trade (net ERC-20 balance change inside the transaction). Pass-through addresses learned (net zero in ≥3 txs): 144. 334 transactions contain a sell and a buy by the same wallet (direct swaps).
- Swap events that did **not** become trades, by reason:
  - `non_universe_pool`: 113686 — v4 Swap in a pool that is not a resolved PONS v2 pool (excluded venue)
  - `no_net_wallet_change`: 2814 — all token movement netted to zero or touched only infrastructure
  - `swap_without_transfer`: 390 — swap event present but no ERC-20 Transfer of that token in the receipt
  - `ambiguous_recipients`: 265 — several wallets received the token and none held ≥90% — no buyer asserted
  - `ambiguous_senders`: 117 — several wallets sent the token and none ≥90% — no seller asserted
- Uniswap v4 `Swap` sign convention observed: on buys the token amount was positive in 41001 and negative in 661 cases; on sells negative in 40920, positive in 281. Side is always taken from Transfer direction, never from the sign.
- Contract wallets: of the 400 most active wallets, 46 have code (bots); they account for 5628 trades and are kept but marked.
- Exact timestamps on trades: 2.1% (anchor blocks); the rest interpolated (see above).

## Verification against receipts

- Random sample of 200 trade transactions (seed 7) fetched as receipts from Alchemy: 200/200 txs have every receipt log we index; 200/200 txs reproduce the same trades from the receipt.
- Attributed wallet vs `tx.from`: 164/202 attributed wallets equal tx.from; 8 wallets are the contract the transaction called (bot contracts holding the tokens), 30 are third addresses (tokens delivered to an address other than the sender).
- Transaction targets in the sample: PONS router 51, Uniswap UniversalRouter 31, PONS curve (direct call) 30, other contract 0xccc88a9d 21, other contract 0xef161b8b 14, other contract 0x9689992f 9, other contract 0x6e2a35a7 6, other contract 0x00000000 5, other contract 0x4337084d 5, other contract 0xe9f89a76 3, other contract 0xb086f7d8 3, other contract 0x81da6bcd 2. Routers appear as `tx.to` and in event topics, never as wallets.

## Rotation sequences and edges

A sequence is `sell A → buy B` by one wallet inside the window. Grades: `direct` (same transaction), `clean` (A the only token sold in the window before the buy and nothing else bought in between), `ambiguous` (everything else). Edge main weight = distinct wallets with a direct or clean sequence.

- **Window 5 min**: 41167 sequences (direct 331, clean 10268, ambiguous 30568); 16271 edges, 3707 with main weight ≥1, 533 with ≥3 wallets; 55886 buys had no prior sell by the same wallet in the window (fresh entries, no edge).
- **Window 30 min**: 87497 sequences (direct 331, clean 11833, ambiguous 75333); 25914 edges, 3714 with main weight ≥1, 560 with ≥3 wallets; 42079 buys had no prior sell by the same wallet in the window (fresh entries, no edge).
- **Window 2 h**: 93785 sequences (direct 331, clean 11623, ambiguous 81831); 27147 edges, 3637 with main weight ≥1, 535 with ≥3 wallets; 40983 buys had no prior sell by the same wallet in the window (fresh entries, no edge).

Top edges, 30 min window (main weight = distinct wallets):

- TruffleHog (0x1a4e…e085) → LUNAR (0x6cdb…55b0): **124 wallets** (direct 0, clean 124; ambiguous 1; 131 sequences)
- EDEN (0x9420…c9b6) → AUTON (0x5e1e…5eab): **66 wallets** (direct 0, clean 66; ambiguous 89; 231 sequences)
- Piecoin (0x6c36…1a01) → TruffleHog (0x4ad5…7b08): **54 wallets** (direct 0, clean 54; ambiguous 0; 883 sequences)
- EVERYTHING (0xabda…b22f) → MARIO (0xb89c…6819): **43 wallets** (direct 1, clean 42; ambiguous 20; 429 sequences)
- LIQ (0xdf3e…0785) → MOTIF (0x9fc7…f8dc): **38 wallets** (direct 0, clean 38; ambiguous 0; 38 sequences)
- PUCHATO (0x7710…3c5a) → STEPPER (0xdefd…8efb): **37 wallets** (direct 0, clean 37; ambiguous 0; 37 sequences)
- BUILDING (0xda31…8d03) → BUILDING (0x9bb8…8dcc): **35 wallets** (direct 0, clean 35; ambiguous 100; 235 sequences)
- GME2 (0x5384…5807) → $1 (0x4dab…3664): **35 wallets** (direct 0, clean 35; ambiguous 37; 102 sequences)
- MARIO (0x7a49…420c) → MARIO (0xb89c…6819): **30 wallets** (direct 0, clean 30; ambiguous 13; 161 sequences)
- AUTON (0x5e1e…5eab) → egregore (0x8a6a…c0f4): **29 wallets** (direct 0, clean 29; ambiguous 137; 336 sequences)

## Limits that stay true regardless of the numbers

- An edge is an observed order of trades by one address. It does not prove that the proceeds of the sale funded the purchase, that several addresses belong to one person, coordination, insider knowledge, or any future price move.
- Symbols are not unique on this chain (copycat launches share names); every label carries the address.
- Bots and contract wallets trade in the same pools; their trades are marked, not removed.
- One hour of one launchpad. Extending the window or adding venues changes the picture; the sample label travels with every artifact.
