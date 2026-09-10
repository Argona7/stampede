# Data sources for STAMPEDE (measured)

Measured at 2026-09-10 18:18:51 UTC by `stampede probe`. Re-run it to refresh; numbers below are not copied from provider marketing.

## Public RPC `rpc.mainnet.chain.robinhood.com`

- chain id 4663; head block 59612039, head lag 3.5 s, 58 txs in the head block; 1.7 blocks/s.
- `eth_blockNumber` latency ms: {'min': 204, 'median': 205, 'max': 261}.
- `eth_getLogs`, 300 blocks, topics CurveBuy|CurveSell|Swap: 2606 logs in 3284 ms, 2181 distinct txs, 54 emitting contracts; by kind {'v4_swap': 1905, 'curve_sell': 293, 'curve_buy': 408}.
- v4 `Swap` logs emitted by PoolManager `0x8366a39c…`: 1905; by other addresses (other v4 forks): 0.
- `eth_getLogs`, same 300 blocks, topic Transfer: 12859 logs in 3473 ms, of which 10137 belong to swap transactions.
- `eth_getLogs` over 1000 blocks: ok, 7584 logs in 2694 ms.
- `eth_getLogs` over 3000 blocks: failed: `rpc error -32000: logs matched by query exceeds limit of 10000`.
- Historical state 5000 blocks back (`eth_getBalance`): ok.

## Alchemy `robinhood-mainnet`

- `eth_blockNumber` latency ms: {'min': 111, 'median': 116, 'max': 656}.
- `eth_getLogs` 10 blocks: ok (98 logs, 288 ms); 40 blocks: rejected: `Under the Free tier plan, you can make eth_getLogs requests with up to a 10 block range. Based on your parameters, this block range should work: [0x38d9c07, 0x3`.
- `eth_getBlockReceipts`: supported (62 receipts, 126 ms).
- Batch of 50 block headers: 50 returned in 602 ms (used for exact block timestamps).
- Single receipt lookup: 111 ms, 6 logs.

## Envio HyperSync `robinhood.hypersync.xyz`

- `/height` = 59612233 in 622 ms; token configured: False.
- `/query`: hypersync: 401 (API token missing or invalid). Ingest uses the RPC path until a free token is added to `.env` as `HYPERSYNC_TOKEN`.

## Blockscout `robinhoodchain.blockscout.com`

- `/api/v2/stats` from a script: HTTP 403, content-type `text/html; charset=UTF-8`; JSON: False. Cloudflare challenge page ("Just a moment...") returned to a script
- Used only for human-readable links (`/tx/<hash>`, `/address/<addr>`) in reports and the UI. The PRO API (`api.blockscout.com/4663/...`, free key) is the scripted alternative if decoded explorer data is ever needed.

## GeckoTerminal (cross-check and coverage list only)

- Network `robinhood` lists 40 DEX ids; PONS ids: ['pons-dot-family', 'pons-v2', 'pons-v2-dex'].
- Full list: `uniswap-v2-robinhood`, `uniswap-v3-robinhood`, `uniswap-v4-robinhood`, `pancakeswap-v3-robinhood`, `pancakeswap-v2-robinhood`, `bankr-robinhood`, `virtuals-robinhood`, `robinswap`, `hoodit`, `pons-dot-family`, `clanker-robinhood`, `easya-kickstart-robinhood`, `swaphood-finance-v2`, `swaphood-finance-v3`, `mint-club-robinhood`, `sushiswap-v2-robinhood`, `sushiswap-v3-robinhood`, `curve-robinhood`, `up-v3`, `sectorone-v2-2-robinhood`, `sectorone-v2-0-robinhood`, `parityswap`, `pons-v2`, `pons-v2-dex`, `ekubo-v3-robinhood`, `uniswap-pools-trade`, `ramses-v3-robinhood`, `ramses-dlmm-robinhood`, `ramses-legacy-robinhood`, `giga-v3`, `giga-v2`, `alandale`, `alandale-cl`, `orvex-v2`, `orvex-v4`, `synthra-robinhood`, `rubicon-robinhood`, `rubicon-clmm-robinhood`, `o1-launchpad-robinhood`, `brownfi-v3-robinhood`.
- Not a data source for edges: pool trades there are capped to the last few hundred per pool and are not attributable beyond `tx_from_address`. The list above is what STAMPEDE does *not* cover unless a venue is explicitly added.

## What this means for the ingest

- Ingest path with an Alchemy key: `eth_getLogs` in 10-block sub-ranges, one topic filter for CurveBuy|CurveSell|Swap|Transfer, 4 parallel workers (measured ~170 blocks/s, i.e. one hour of chain in ~6 minutes, 0 failed ranges). The public RPC 429s after a few quick calls and caps a query at 10 000 logs, so it is used only when no key is configured (300-block chunks, halved on error). Alchemy also serves block-header batches (timestamps), `eth_call` batches (registry) and receipt cross-checks.
- Wallet attribution cannot rely on event topics (router addresses appear there); it uses ERC-20 `Transfer` logs inside the same transaction. See `docs/ALGORITHM.md`.
- Block timestamps: exact for anchor blocks fetched in batches, interpolated in between; each trade stores `ts_exact`. Examples in `docs/EXAMPLES.md` use exact timestamps.
- HyperSync, when a token is present, replaces the RPC scan and adds `tx.from`/`tx.to` and exact timestamps for every row. The coverage report names the path actually used.
