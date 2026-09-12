# Backfilling PONS v2 history into research stores

Two paths write the same rows (`trades`, `blocks`, `wallets`, lifecycle `logs` → `curves` / `tokens` / `pools` /
`launches` / `graduations`, cursor `meta.backfill_cursor`) into a research SQLite store:

| | `stampede backfill` (HyperSync) | `stampede backfill-rpc` (public JSON-RPC) |
|---|---|---|
| source | Envio HyperSync, `HYPERSYNC_TOKEN` (free tier) | keyless RPC endpoints of Robinhood Chain, no key |
| logs per transaction | server-side join (`JoinAll`): every log of a matched tx | two `eth_getLogs` per chunk: swap-class topics, then Transfers of the traded tokens |
| block timestamps | joined blocks, exact | `blockTimestamp` in every log (recent go-ethereum), exact; header batch + `Interp` only as fallback |
| `txs` (from / to) | filled | empty (not in getLogs; `/api` fetches on demand, `verify` uses Alchemy receipts) |
| throughput (2026-09-12) | 55-90 blocks/s per token after the burst (~3 requests/min) | 400-600 blocks/s combined across the free endpoints (see below) |
| resume | `meta.backfill_cursor` | same key: either path resumes the other |

Code: `stampede/research/backfill.py`, `stampede/research/backfill_rpc.py`; tests `tests/test_backfill.py`,
`tests/test_backfill_rpc.py` (fake JSON-RPC transport, no network).

## Why the RPC path exists

The 14-day range (blocks 48,872,434 → 60,968,434, ~12.1M blocks, ~30M trades) was being fetched over HyperSync in three
segments. The free tier throttles a worker to ~3 requests/min after a burst; measured 55-92 blocks/s → ~3 days for the range.
Robinhood Chain has several keyless RPC endpoints (Arbitrum Nitro nodes) that serve `eth_getLogs` over thousands of blocks
per call. They are metered per IP, so the interesting number is not latency but *blocks per allowed request*.

## Endpoint measurements (2026-09-12, 1,000-block probe at 50,600,000 + sustained runs)

| endpoint | 1k curve logs | max span | rate limit / behaviour | `blockTimestamp` | verdict |
|---|---|---|---|---|---|
| `rpc-robinhood.blockmachine.io` | 1,510 logs, 0.5 s | 10,000 (`-32602 max_blocks=10000`); answers above 50 MiB refused (`-32003 response too large`) | **300 CU/min per IP** (`429`, body carries `limit/remaining/reset/retry_after_ms`, `Retry-After`); eth_getLogs ≈ 20-25 CU → 12-15 calls/min; 10k blocks of swap-class logs in 1.2-3.9 s when allowed | yes | primary |
| `rpc.ordofi.network` | 1,510 logs, 3.2 s | 5,000 (10k → `-32005 the network is busy`) | no explicit limit; 4-11 s per 5k-block query, `busy` / `log query timed out` under load (≈ half the calls at 3 concurrent) | yes | secondary |
| `rpc.mainnet.chain.robinhood.com` (official) | 1,510 logs, 1.4 s | 10,000-log cap per answer → 750-1,500 blocks in busy periods; 300 addresses per Transfer query | `429 Too Many Requests` in bursts below ~3-4 s pacing; `log query timed out` for 800 addresses × 10k blocks | **always `0x0`** | opt-in: in a pool its small bites multiplied a chunk's request count (a 10k-block transfer phase became 60+ calls) and stalled the cursor for 7 min; alone it is a slow fallback |
| `rpc.nodeflare.app/robinhood/public` | 1,510 logs, 0.1 s | ≥ 30,000 (173k logs / 148 MB in 13.9 s) | keyless: 1 request / 10 s per IP **and 17 "heavy" calls (eth_getLogs) per day**, then `403 method_not_supported` | yes | opt-in; the best endpoint measured *with a free key* (2M CU/month, sign-up) |
| `robinhood.api.pocket.network` | 1,510 logs, 2.6 s | – | `-31001 relay error` / `historical state is not available` on 9 of 10 historical calls; batch headers work | yes | opt-in, not used |
| `lb.routeme.sh/rpc/evm/4663` | – | – | `-32029 public rate limit exceeded — sign up`; getLogs only 52 blocks from tip | – | dropped |
| `robinhood.rpc.blxrbdn.com` | – | – | `403 Forbidden` for eth_getLogs (headers work) | – | dropped |
| `robinhood.rpc.hypersync.xyz` (Envio HyperRPC) | – | – | `401 token malformed` without token; `403 Your token does not have access to this product` with `HYPERSYNC_TOKEN` (bearer or path) | – | dropped |
| Alchemy (`ALCHEMY_KEY`) | – | 10 blocks per getLogs on this plan | – | – | header fallback only (`Rpc.get_blocks`, batches of 50) |

Sustained getLogs rates measured with one thread per endpoint (swap-class topics, 50,700,000+): blockmachine 3,841 blocks/s
until the CU quota (12 answers), ordofi 674 blocks/s (5k spans, 2 `busy` in 12), official 430 blocks/s (2k spans, 3 s pacing,
1 `429` in 10), nodeflare 94 blocks/s keyless (1 per 10 s), pocket 15 blocks/s.

Per 10,000 blocks the chain carries (block 50.8M, quiet period → block 52.9M, busy period): 56k → 125k swap-class logs
(v4 `Swap` 52%, of which ~half are non-PONS pools, `HookFeeCollected` 26%, curve events 21%), 48 → 110 MB of JSON, and
65k → 130k Transfer logs of the 800 → 1,500 launched tokens traded in the chunk (41 → 80 MB). Responses are gzip on the wire
(~6×); the link (Tailscale exit node) does 13 MB/s, so bandwidth is not the limit - **allowed requests per minute × blocks
per answer** is. Server-side poolId filtering (topic1 list of the 7,156 PONS pools) is refused: `-32602 exceed max topics`.

## How `backfill-rpc` works

```
stampede backfill-rpc --db <store> --from-block A --to-block B [--endpoints blockmachine ordofi] [--chunk 10000] [--workers 3]
```

1. **Lifecycle pass** (only for blocks after the store's `backfill_lifecycle_cursor`, 30-day lookback like the HyperSync path):
   `eth_getLogs` topics = TokenLaunched / CurveCompleted / PoolRegistered / LaunchSwept / PoolGraduated / SnipeTaxExempted,
   kept with the same address rules as the HyperSync query, written to `logs` (source `rpc`) and parsed into `curves` /
   `tokens` / `pools` by the shared `apply_lifecycle`; then `sync_lifecycle` fills `launches` / `graduations`.
2. **Trades pass**, per chunk of 10,000 blocks (default; endpoints with a smaller span split it):
   - `eth_getLogs` with topics `[CurveBuy, CurveSell, SnipeTaxCharged, CurveBuyRefunded, HookFeeCollected, Swap]` and no
     address filter. Client-side: keep HookFeeCollected of the PONS hook and Swap of `V4_POOL_MANAGER` whose poolId is in
     the store's `pools` - exactly the HyperSync selection.
   - Tokens traded in the chunk: curve → token via `curves`, pool → token via `pools`. One `eth_getLogs` with `address =
     [tokens…]`, topic Transfer, same range (address list sliced per endpoint capacity, halved on `timed out`), then only
     the transfers whose tx has a swap event.
   - `group_by_tx` → `normalize.trades_from_tx` with `build_context(store)` → `Trade.row(ts, exact)` → `TRADE_INSERT`;
     `blocks` (exact timestamps of every matched log), `wallets` counts (upsert per chunk), `meta.backfill_cursor = to + 1`,
     one commit per chunk. Chunks are fetched by `--workers` threads and written in block order by the main thread.
   - Timestamps come from `blockTimestamp` of any log in the block (`ts_exact` 1). A trade block without one (the official
     endpoint sends `0x0`) gets `eth_getBlockByNumber` for every 5th such block (all when < 500) - Alchemy `Rpc.get_blocks`
     when `ALCHEMY_KEY` is set, else a batch through the pool - and `normalize.Interp` for the rest (`ts_exact` 0); with no
     anchors at all the time is NULL, never a fake value.
3. **Endpoint pool** (`EndpointPool`): per-endpoint pacing, at most `inflight` concurrent requests, penalty box on failures
   (`Retry-After` / `retry_after_ms` honoured, exponential otherwise, 15 min for 401/403), per-endpoint span that halves on
   `range` answers (`block range exceeds`, `exceeds limit of 10000`, `response too large`, `timed out`) and doubles back after
   25 clean answers, round-robin among ready endpoints, and priority for the oldest chunk so the cursor never starves.
   `SharedPacer` (files under `/tmp/stampede-rpc-pacer`, `flock`) spaces requests **across processes** on the same IP and
   holds blockmachine's CU token bucket, so three segment workers do not burn a quota on 429s.
4. `runs` gets the per-endpoint summary (ok / 429 / range / errors, MB, median latency, blocks/s while busy).

## Equivalence check against the HyperSync rows

Scratch store `data/research-eq-rpc.sqlite` = the universe tables of the HyperSync store `data/research-14d.sqlite`
(`curves`, `tokens`, `pools`, `infra`, `launches`, `graduations`, `quotes`, `logs`; `backfill_lifecycle_cursor` set), then the
RPC path over a window worker A had already fetched, with `--no-seed-infra` so the normalizer sees the same (empty) `infra`
as the HyperSync run did:

```
.venv/bin/python -m stampede.cli backfill-rpc --db data/research-eq-rpc.sqlite --from-block 50600000 --to-block 50601999 --chunk 2000 --workers 2 --no-seed-infra
.venv/bin/python -m stampede.cli backfill-rpc --db data/research-eq-rpc.sqlite --from-block 50600000 --to-block 50651999 --chunk 10000 --workers 6 --no-seed-infra
```

Comparison (all 17 `trades` columns except `id`, keyed by `(tx_hash, token, wallet)`; the HyperSync rows located by their
`id` range, ids grow with block):

| window | HyperSync rows | RPC rows | identical rows | wallets | fee_raw / tax_raw / snipe_raw sums | quote sum | venues | `ts_exact` |
|---|---|---|---|---|---|---|---|---|
| 50,600,000-50,601,999 (2,000 blocks) | 4,713 | 4,713 | **4,713** (0 only-A, 0 only-B, 0 columns differing) | 1,649 = 1,649 | equal (36.20e24 / 12.09e24 / 6.24e15) | equal (72.28 ETH) | curve 1,139, v4 3,574 both | 1.0 both |
| 50,600,000-50,651,999 (52,000 blocks) | 114,636 | 114,636 | **114,636** | 16,369 = 16,369 | equal (666.27e24 / 314.75e24 / 588.5e15) | equal (4,879.9 ETH) | curve 25,803, v4 88,833 both | 1.0 both |

Exact block timestamps: identical for all 1,444 / 35,453 blocks both stores hold; the RPC store additionally holds the
timestamps of transfer-only blocks. `txs` is the one table that differs (empty on the RPC side). The 52k-block run took 21.4 s
(2,338 blocks/s, blockmachine still inside its CU burst).

The first attempt of this check found a real defect: the official endpoint returns `blockTimestamp: 0x0`, which had become
`ts = 0` with `ts_exact = 1`; `rpc_log_dict` now treats 0 as unknown and the fallback fetches headers.

## A defect in the existing research stores, and what was done about it

`build_context` reads `infra` from the store to exclude infrastructure from wallet attribution. The live ingest seeds it
(`Ingest.seed_infra`); `stampede backfill` never did. In the HyperSync store worker A had filled, **5,519,580 of 14.6M trades
carried the v4 PoolManager as the wallet** (plus 221k the hook), and every v4 trade was a `two_sided_tx` pair with
`quote_amount = 0` - the v4 venue (≈ 3/4 of all trades) had no quote amounts. The partial B / C stores had the same rows.

Done (2026-09-12):

- both paths now call `seed_infra(store)` (`chain.KNOWN_INFRA` → `infra`) before `build_context`; `backfill-rpc --no-seed-infra`
  reproduces the old rows for equivalence checks (`tests/test_backfill_rpc.py::test_without_infra_seed_the_pool_manager_becomes_a_wallet`
  pins both behaviours);
- the partial HyperSync rows of B (1,546,496 trades, 566,000 PoolManager rows) and C (413,104 / 152,553) were dropped and both
  cursors reset to the segment start; the lifecycle tables were kept;
- A's range is re-fetched by the RPC path into `data/research-14d-a.sqlite` (lifecycle tables copied from the HyperSync
  store); the HyperSync worker A was stopped at block 52,304,500 (its rows are superseded) and the finisher keeps its store as
  `data/research-14d-hypersync.sqlite` instead of deleting it.

## The 14-day run (2026-09-12)

Segments and workers (all detached with `scripts/bg.py`, logs append):

| part | blocks | path | command |
|---|---|---|---|
| A′ | 48,872,434-52,900,000 | rpc | `python scripts/bg.py backfill-A-rpc -- .venv/bin/python -m stampede.cli backfill-rpc --db data/research-14d-a.sqlite --from-block 48872434 --to-block 52900000 --workers 3 --label "part A (rpc)"` → `data/backfill-A-rpc.log` |
| B | 52,900,001-56,930,000 | rpc | `python scripts/bg.py backfill-B -- … backfill-rpc --db data/research-14d-b.sqlite --from-block 52900001 --to-block 56930000 --workers 3 --label "part B"` → `data/backfill-B.log` |
| C | 56,930,001-60,000,000 | rpc | `python scripts/bg.py backfill-C -- … backfill-rpc --db data/research-14d-c.sqlite --from-block 56930001 --to-block 60000000 --workers 3 --label "part C"` → `data/backfill-C.log` |
| C2 | 60,000,001-60,968,434 | HyperSync | `python scripts/bg.py backfill-C2 -- … backfill --db data/research-14d-c2.sqlite --from-block 60000001 --to-block 60968434 --label "part C2 (hypersync)"` → `data/backfill-C2.log` (the HyperSync quota is separate from the RPC quotas, so it adds ~60-150 blocks/s) |

Before them: `pkill -f hs-sequential.sh` (the sequential orchestrator; its HyperSync worker for B, started when A died at
block 52,070,689, was stopped too).

Finisher `/tmp/hs-finish-rpc.sh` (`python scripts/bg.py backfill-finish -- /bin/sh /tmp/hs-finish-rpc.sh`, log
`data/backfill-14d.log`): every 60 s checks each part's `meta.backfill_cursor` and restarts a worker that is not running
(resume; gives up after 20 restarts); when all four cursors are past their targets it stops any HyperSync worker on
`data/research-14d.sqlite`, checkpoints and renames the HyperSync store to `data/research-14d-hypersync.sqlite`, installs
`data/research-14d-a.sqlite` as `data/research-14d.sqlite`, merges B, C, C2 into it (the merge of `/tmp/hs-sequential.sh`
plus `infra`; wallets are verified against `trades` and recomputed if they drifted), sets `sample_from_block` /
`sample_to_block` / `backfill_cursor`, then the streaming rotate 300 / 1800, `stampede fx --days 15`, and writes `READY`.

Observed combined throughput of the three RPC workers (16:50-16:55Z, blockmachine + ordofi): **~400 blocks/s**
(100k blocks in 253 s; blockmachine ≈ 10-12 answers/min at 2.5-5k blocks each, ordofi ≈ 5-6/min at 2.5-5k with a third of
its calls `busy`), plus HyperSync C2 at 120-150 blocks/s in its burst (55-90 after): **~500-550 blocks/s combined**, 5-6× the
single HyperSync worker. A single RPC worker alone bursts to 2,300 blocks/s until the blockmachine minute quota is spent;
the shared pacer removed the 429 churn between the three processes. Blocks in the busy segments (52.9M+) carry twice the
logs of the quiet ones, so the same request budget buys half the blocks there. Remaining at 16:55Z: A′ 3.7M, B 3.9M, C
3.0M blocks over RPC (~7-8 h at 400 blocks/s combined), C2 0.85M over HyperSync (~2 h). Each restart of a worker loses its
in-flight chunks (3-5 min of fetching), so the finisher restarts a worker only when it is not running.

What would raise the ceiling: a free nodeflare key (30k-block answers, 1 request/10 s → ~1,500 blocks/s on its own); a
second machine with another IP (the quotas are per IP); a second HyperSync token.

## Operating notes

- Resume: rerun the same command; both paths continue from `meta.backfill_cursor`. `--no-resume` starts over (rows are
  `INSERT OR IGNORE`, so nothing duplicates).
- Cross-process pacing state lives in `/tmp/stampede-rpc-pacer/<host>.json`; delete the directory to reset it.
- To add an endpoint: `--endpoints blockmachine ordofi official https://other/rpc` (unknown URLs get the generic defaults
  and adapt). A free nodeflare key (`https://<key>@…` is not supported; use its keyed URL) would be the fastest lane measured.
- Never print `HYPERSYNC_TOKEN` / `ALCHEMY_KEY`; `env.redact` strips them from errors.
