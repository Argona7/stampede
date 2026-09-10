# Algorithm

STAMPEDE turns raw Robinhood Chain logs into a directed graph of *observed* wallet rotations between
PONS v2 memecoins. Every step is reproducible from the SQLite store; every derived row points back to
the transaction hash and log index it came from.

## 1. Universe

- A **coin** (node) is a token launched by the PONS v2 factory `0x7ed598bc…`.
- Seen on a bonding curve: every contract that emitted `CurveBuy`/`CurveSell` in the window is asked
  `token()` and `pairToken()`; the token joins the universe.
- Seen only in a graduated Uniswap v4 pool: the pool id from the `Swap` log is accepted only if it
  equals `keccak(abi.encode(quote, token, fee 0, tickSpacing 200, V2MemeHook))` for a token that the
  factory's `getLaunchedToken` knows. Pools that fail this reconstruction are **not** in the map and are
  counted in the coverage report.
- Quote currencies (ETH, USDG, tokenized stocks such as NVDA/SPY/GME) are never nodes; a hop through a
  quote inside one transaction is not a rotation.

## 2. Swap-class events

- `CurveBuy(buyer, recipient, quoteIn, tokensOut, fee, tax)` and
  `CurveSell(seller, recipient, tokensIn, quoteOut, fee, tax)` from any PONS v2 curve.
- Uniswap v4 `Swap(id, sender, amount0, amount1, …)` from the PoolManager `0x8366a39c…` only.
- The `buyer`/`seller`/`sender` fields are **not** used for attribution: on this chain they name the
  router (PONS router, UniversalRouter, aggregators, bot contracts) in a large share of transactions.

## 3. Attribution: who traded

For each transaction and each universe token that has a swap-class event in it:

1. Sum the ERC-20 `Transfer` logs of that token inside the transaction into a net balance change per
   address.
2. Drop infrastructure: curves, PoolManager, hook, routers, Permit2, WETH, the token contract, the zero
   address, and every address whose net change is zero while its gross movement is not (pass-through).
3. The **buyer** is the single remaining address with a positive change. If several remain, the one
   holding at least 90 % of the positive total is accepted (flag `minor_buy_recipients:n`); otherwise no
   buyer is asserted (`ambiguous_recipients`). Sellers symmetrically.
4. A transaction with a swap event but no `Transfer` of that token (`swap_without_transfer`), or where
   everything nets to zero (`no_net_wallet_change`), produces no trade. A `Transfer` without a swap
   event is a transfer, never a trade.
5. Amounts: the token amount is the wallet's net change; the quote amount is read from the swap events
   (`quoteIn`/`quoteOut`, or the quote side of the v4 delta) and is left unattributed when two different
   wallets traded the same token in one transaction (`two_sided_tx`).

Every trade stores `attribution = transfer_net`, the swap log indexes it rests on, and its flags.
A trade is one row per (transaction, token, wallet).

## 4. Sequences and grades

Window `W` (default 30 min; 5 min and 2 h are computed too). For one wallet, sorted by
(timestamp, block, id):

- For each **buy** of `B` at `t2`, take every token `A ≠ B` the wallet **sold** in `[t2 − W, t2]`
  (before the buy in transaction order); the sell used is the most recent sell of `A`.
- `direct`: the sell and the buy sit in the same transaction and `A` is the only token sold in it.
- `clean`: `A` is the only token sold in the window and no other universe token was bought between the
  sell and this buy.
- `ambiguous`: anything else (several tokens sold; something else bought in between; several tokens
  sold in the same transaction). Stored with the list of candidates, capped at the five most recent sold
  tokens per buy.
- Selling and re-buying the same token is not a rotation.

## 5. Edges and weights

Edge `A → B` aggregates sequences. Its **main weight is the number of distinct wallets with at least
one direct or clean sequence**. Ambiguous wallets are counted separately and shown as such. One wallet
with three sequences on the same pair counts once. No amount-based or money-flow weight is invented.

## 6. Timestamps

Block headers are fetched exactly every 50 blocks and at the window bounds; blocks in between are
linearly interpolated (≈0.1 s per block, worst case a few seconds off) and marked `ts_exact = 0`. Every
trade cited in `EXAMPLES.md` and every evidence view in the terminal can be upgraded to an exact
timestamp by fetching its block header.

## 7. What is deliberately not done

- No inference of common ownership across addresses, no clustering, no "smart money" score.
- No claim that the sale funded the purchase. The gap between sell and buy is reported as a number.
- No filtering of bots beyond marking contract wallets; the map shows what the chain shows.
- No venues outside PONS v2 curves and their graduated v4 pools in this sample; the coverage report
  lists what is missing.
