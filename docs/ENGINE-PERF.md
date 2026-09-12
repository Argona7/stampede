# Realtime engine: 60-minute live measurement (2026-09-12)

Target (programme goal, stage 6): a live engine on the free websocket feed of Robinhood Chain with block -> alert
latency p50 < 300 ms and p95 < 800 ms over a 60-minute live run, zero unfilled block gaps, events over SSE, every
alert journaled with outcomes.

**Result (run 2, the code as committed): p50 99.8 ms, p90 141.8 ms, p95 168.9 ms, p99 339 ms, max 1,019 ms from the
`newHeads` frame to the last event of the block published; 35,906 consecutive live blocks, 0 gaps, 0 unfilled, 0
reconnects; 252,611 events (70 / s at the client); 64 alerts journaled, every one that reached +30 min has its outcome.**
The first attempt (run 1) met the latency and gap numbers too but exposed a failure of the free endpoint that the
engine did not detect at the time (the log subscriptions went silent while heads kept flowing); the fix - a
subscription watchdog - and the fix for a rarer loss path found by a recall check are in the committed code and were
in place for run 2. Both runs are reported.

## Setup

- Machine: Apple M5 Pro, macOS 26.5.1, Python 3.13.14, `websockets` 17.1. The same machine was running the owner's
  replay server (:8791), two other agents' replay servers (:8798, :8799), a 14-day HyperSync backfill into
  `data/research-14d.sqlite` (CPU + disk), and Cursor.
- Command (port 8798 was taken by another agent's replay server, so :8808):
  `python scripts/bg.py engine-8808 -- .venv/bin/python -m stampede serve --mode live --feed wss --port 8808 --db data/live-engine.sqlite`
  on a store freshly seeded with `python -m stampede.engine.seed --into data/live-engine.sqlite --from data/research-14d.sqlite --from data/stampede.sqlite`
  (414,364 curves, 7,189 pools, 414,355 launches, 3,603 wallet scores, 61 quotes, 4,718 tokens with symbols).
- Client: a Python script consuming `GET /api/stream` for 3,600 s (urllib, one connection, `Last-Event-ID` on
  reconnect), timing every frame on arrival; `GET /api/perf` snapshotted every 5 min and at the end.
- Endpoint: `wss://robinhood-rpc.publicnode.com` for both connections (fast: heads + PONS logs; bulk: all transfers);
  `wss://robinhood.api.pocket.network` configured as failover (accepted the same six subscriptions in the probes; at
  11:52 UTC its heads trailed the chain by 3.4 s median, publicnode's by 0.46 s). Alchemy HTTP only for gap fills,
  `eth_call`s (symbols, unknown curves, reserve snapshots) and the lifecycle catch-up headers; the public RPC for the
  wide catch-up `eth_getLogs` at start.
- Run 2: engine `started_at` 11:45:22 UTC (process start 11:43:56; registry load 0.7 s, lifecycle catch-up of
  111,545 blocks since the seed 84.8 s on the paced public RPC), snapshot at 12:45:55 UTC (uptime 3,633 s), commit
  `51e4602`. Run 1: 10:37:04 -> 11:37:52 UTC (uptime 3,649 s), commit `4e03ffe`.

## Run 2 (final code)

### Latency (ms, engine side, live blocks; the histogram holds every sample of the hour)

| stage | n | p50 | p90 | p95 | p99 | max | mean |
|---|---|---|---|---|---|---|---|
| head received → block complete | 35906 | 96.7 | 137.6 | 163.7 | 329.8 | 1018.4 | 100.6 |
| block complete → trades normalized | 35906 | 0.3 | 0.8 | 1.3 | 5.3 | 401.5 | 0.6 |
| trades → rings, sequences, radar rows | 35906 | 1.1 | 6.1 | 9.7 | 19.2 | 445.2 | 2.6 |
| **head received → last event published** | 35906 | **99.8** | 141.8 | **168.9** | 339.0 | 1019.4 | 104.1 |
| release → processor pick-up | 36245 | 0.1 | 0.4 | 0.7 | 4.4 | 399.4 | 0.4 |
| apply_block (all stages) | 36245 | 1.4 | 6.7 | 10.3 | 20.0 | 445.4 | 2.9 |
| late fragments: release → applied | 339 | 0.6 | 4.0 | 5.7 | 11.8 | 17.7 | 1.4 |

The budget is the block assembly: the PONS logs of block N are only known complete when the next head (or a later
log) arrives, ~100 ms later; the transfer stream (30 frames per block, one frame per log on the node side) trails the
head by 16-104 ms (p50-p95 measured in the probes). Normalization, rings, sequences and radar rows take 1-10 ms.
The max (1,019 ms) is the 1-s transfer cap on a block whose transfers were still streaming.

### Latency seen by the SSE client (ms)

| measure | n | p50 | p95 | p99 | max |
|---|---|---|---|---|---|
| head received (engine) → `block` event parsed by the client | 35593 | 100.2 | 171.4 | 344.8 | 1020.0 |
| `ts_emit` → parsed by the client (all events) | 250517 | 0.5 | 1.8 | 16.4 | - |
| block.timestamp (1-s granularity) → client, seconds | 35593 | 0.6 | 1.1 | - | - |

Alerts are evaluated inside the same `apply_block`, so an `alert` event leaves the process at the block's
`block_to_emit` time (the `block` event of a block is published first, the `alert` last, 0.5 ms apart at the client).

### Block coverage

- First block 61,080,728, last 61,116,633 (head at the snapshot 61,116,634): 35,906 consecutive block numbers, all
  released live; 339 late fragments (logs that arrived after their block was released, applied 0.6 ms median later);
  **gaps found 0, unfilled 0**; `skipped_at_start` none.
- Release paths: bloom skip 1,048 (header bloom rules out PONS activity: released at the head), fast grace 1,349
  (no later head within 250 ms - the chain paused), transfer cap 76 with 187 transactions deferred and re-emitted
  complete, late logs 7,465, late transfers parked 4,994 / rejoined to a late swap 220, duplicate logs 0, removed
  (reorg) logs 0.
- SSE client: 35,593 `block` events from 61,080,914 to 61,116,506 with **no block-number gap in the stream**, 0
  reconnects, 0 replay gaps.

### Recall and correctness checks (read-only against the run's store)

- Recall, busy window 61,095,500-61,095,999 (500 blocks, 12:10 UTC, 11.8k trades / 5 min): every subscribed log
  fetched over HTTP (the backfill path), normalized with the store's registry, compared with the engine's trades:
  **2,685 / 2,685 identical, 0 missing, 0 extra**.
- Recall, window 61,110,000-61,110,499 (500 blocks, 12:35 UTC): **1,064 / 1,064, 0 missing, 0 extra**.
- Receipts: 300 random trade transactions of the run, receipts from Alchemy, normalized with the same function:
  **300 / 300 identical** (token, wallet, side, amounts, venue, fee, tax).
- Radar reconciliation (last 30-s cycle, memory rows vs `radar.compute_rows` on the store): 381 memory rows vs 380
  SQL rows, 380 in both, 4 coins with a different `inflow_10m` (the store was up to 26 s behind the state at that
  moment, see the writer below), top-4 identical, 5th different.

### Feed

- fast `wss://robinhood-rpc.publicnode.com`: 1 connect, 0 reconnects, 0 failovers, 0 stalls, 330,021 frames
  (346.8 MB), 294,114 logs, 35,907 heads, ping RTT 143 ms at the end.
- bulk (transfers), same endpoint: 1 connect, 0 reconnects, 1,242,369 frames (934.3 MB), ping RTT 102 ms.
- Head gap: p50 99.5 ms, p90 142, p95 164, p99 252, max 1,728 ms.
- No rate limiting on the websocket side. Alchemy rate-limits the parallel 10-block `eth_getLogs` under load (22-75
  of ~150 calls per 500-block recall check answered 429 and retried; the gap fills in run 1 completed in 0.4-1.7 s);
  the public RPC needs the 3-s pacing of `rpc.py` (the 111k-block lifecycle catch-up took 84.8 s in 23 calls).

### Events, state, store, process

- 252,611 events: block 36,245, trade 114,520, sequence 69,225, radar_delta 31,812, alert 91 (61 fired + 30
  outcomes), session 718; 69.6 events/s at the client over the hour (84 events/s in the last minute).
- State at the end: 114,520 trades, 69,225 sequences, 2,331 launches seen live (+ the 415k seeded), 47 pool
  registrations, 1,456 curve reserves (706 started mid-life and were snapshotted from chain), 25,353 wallets with a
  trade in the last 2 h, 2,395 coins with a trade in the last hour, 381 radar rows (231 with >= 2 wallets).
  Normalizer notes: `non_universe_pool` 94,800 (v4 swaps of the Robinhood DEX pools on the same PoolManager, dropped),
  `no_net_wallet_change` 2,809, `swap_without_transfer` 507 (graduated tokens whose transfer is a non-standard event
  or a PoolManager claim settlement - the same 0.4 % as the batch path), `unresolved_curve` 24 (resolved by the
  resolver within 2-4 s; 11 curves resolved by `eth_call`), ambiguous 365.
- Writer: 17,468 flushes, 1,199,978 rows (856,616 raw logs of trade transactions, 114,379 trades, 110,268 wallet
  upserts, 69,068 sequences, 35,879 blocks, 61 alerts + 30 outcome updates, registry rows), 0 errors, 0 dropped
  batches, 59 passive checkpoints; flush p50 2.3 ms, p95 44 ms, **p99 4.4 s, max 26.6 s, max queue lag 26.5 s**: the
  API's context worker holds the SQLite write lock across X API calls (`context/xmentions.py` executes the budget
  `INSERT` after each page and commits only after the last one), so the engine's batches wait behind it. Nothing is
  lost (the writer retries until written) but `/api/radar` and `/api/alerts` can lag the stream by up to half a minute
  during those holds. Store at the end: 114,868 trades, 69,541 sequences, 35,974 blocks, 860,063 logs, 100 % of
  trades with an exact block timestamp.
- Process: 7.9 % of one core on average (288 s CPU in 3,633 s), RSS 348 MB at the end (peak 636 MB while loading the
  415k-curve registry), 10 threads.

### Alert journal (mode='live')

64 alerts, 61 fired by the engine and 3 by the context worker's own evaluation (it keeps running; one of its three,
#8, is a repeat of #6 within 30 min because the engine's row was still waiting behind the worker's own write lock when
the worker checked the journal). Every alert older than 30 min has `outcome_30m` (30 of 64 at the snapshot; the rest
were not due yet); no alert was 60 min old at the snapshot, so `outcome_60m` settles after this report (the engine
kept running). Outcome = median price of the last 5 trades at +30 min vs at the alert, in percent; four of the 30 were
positive (+1073 %, +128 %, +11 %, +11 %), the median was -48 %: the rule fires at the first burst of rotation inflow
into a coin a few minutes old, and most of those coins fade - the journal is the record the rule needs, not a
recommendation.

| # | clock (UTC) | coin | score | inflow 10m | rank | age | price | +30 min |
|---|---|---|---|---|---|---|---|---|
| 1 | 11:47:51 | SWAPLY·6261 `0x340d8c16` | 69.8 | 8 | 1 | 65 s | 2.11e-09 | -15.3 % |
| 2 | 11:48:29 | TURNER·7977 `0xa3bbcf20` | 66.1 | 8 | 2 | 68 s | 4.11e-07 | -26.3 % |
| 3 | 11:50:38 | PCAT·7073 `0x8890b1a1` | 66.8 | 8 | 2 | 161 s | 4.13e-09 | -17.8 % |
| 4 | 11:52:36 | ? `0x4d637090` | 82.6 | 17 | 2 | 0 s | 4.05e-09 | -58.6 % |
| 5 | 11:54:10 | KRONUS `0x4a4caceb` (worker) | 73.0 | 14 | 4 | 338 s | 0.000692 | -7.4 % |
| 6 | 11:55:40 | SWAPLY·5612 `0x1875950e` | 68.6 | 8 | 5 | 62 s | 3.73e-09 | -54.8 % |
| 7 | 11:55:55 | DIHFIH `0xfe3bfd6b` | 63.8 | 9 | 5 | 1088 s | 1.18e-05 | -69.7 % |
| 8 | 11:57:57 | SWAPLY·5612 `0x1875950e` (worker, repeat) | 86.7 | 20 | 1 | 199 s | 4.32e-09 | -60.9 % |
| 9 | 11:59:38 | OPENGAP·800f `0x260cc28a` | 61.0 | 8 | 5 | 35 s | 2.18e-09 | -24.4 % |
| 10 | 11:59:53 | CatTAO `0x6ff14702` | 68.6 | 11 | 3 | 14 s | 3.79e-09 | -55.8 % |
| 11 | 12:01:49 | Titty `0xc085718b` | 64.2 | 8 | 4 | 75 s | 4.44e-08 | -8.2 % |
| 12 | 12:02:30 | HES `0x9f33f679` | 73.0 | 12 | 3 | 73 s | 2.59e-09 | -36.6 % |
| 13 | 12:02:58 | Swaply·f0b5 `0xfb2fe7af` | 63.3 | 9 | 4 | 18 s | 5.89e-09 | -66.4 % |
| 14 | 12:03:08 | COW·9e35 `0x1d4ff3de` | 67.2 | 10 | 5 | 90 s | 2.11e-08 | -78.3 % |
| 15 | 12:03:28 | PURRPS `0x3885a333` | 62.9 | 8 | 5 | 25 s | 2.6e-09 | +11.4 % |
| 16 | 12:03:58 | FoMo·d427 `0x7e4fe1ec` | 64.9 | 9 | 5 | 37 s | 1.46e-05 | +1073.4 % |
| 17 | 12:05:11 | INUWIFINUWIFINU `0x2bcecc02` | 63.3 | 9 | 5 | 4 s | 3.48e-09 | -51.6 % |
| 18 | 12:06:13 | MAPLE·9ca1 `0x52501ac0` | 69.0 | 13 | 4 | 76 s | 5.75e-09 | -70.9 % |
| 19 | 12:06:39 | BITTY·675b `0x0f23410f` | 67.5 | 11 | 5 | 23 s | 1.69e-10 | -54.6 % |
| 20 | 12:07:30 | HODLER `0x3b772d95` | 64.8 | 8 | 5 | 41 s | 2.51e-10 | -68.8 % |
| 21 | 12:08:12 | HODLER·ebf7 `0x31208da4` | 72.0 | 10 | 3 | 22 s | 1.3e-10 | -44.3 % |
| 22 | 12:09:20 | HODLER·3676 `0x961ef57a` | 61.0 | 8 | 5 | 20 s | 4.18e-09 | -59.3 % |
| 23 | 12:10:49 | JodlHODLer·b987 `0x5c6cb001` | 66.1 | 8 | 3 | 35 s | 8.82e-11 | +11.1 % |
| 24 | 12:13:02 | FoMo·e23a `0x43c523cc` | 66.8 | 9 | 3 | 41 s | 8.05e-06 | -34.3 % |
| 25 | 12:13:32 | FoMo·2fa2 `0x132427e2` | 62.9 | 8 | 4 | 20 s | 3.33e-09 | -48.8 % |
| 26 | 12:13:36 | CLT `0x636498d0` | 64.2 | 8 | 4 | 118 s | 3.88e-09 | -18.0 % |
| 27 | 12:14:16 | FoMo·3859 `0x111f598f` | 64.2 | 8 | 3 | 18 s | 3.59e-09 | -51.0 % |
| 28 | 12:14:33 | PSTOCK `0x6fdab0c3` | 66.8 | 8 | 3 | 36 s | 1.43e-08 | +128.3 % |
| 29 | 12:14:51 | FOMO·05aa `0xd892f3f9` | 62.5 | 11 | 5 | 20 s | 3.19e-09 | -47.0 % |
| 30 | 12:15:15 | Presidente·2a7c `0xdb68b444` | 63.3 | 9 | 5 | 60 s | 1.99e-10 | -61.9 % |
| 31-64 | 12:16:11 - 12:44:45 | 34 more (MACROFLY, RUFUS, TREZOR, AWallet, UDOG (worker), TIT, NIKKI, RCAT, ...) | 60.5-78.6 | 8-15 | 1-5 | 0 s - 40 min | | not due at the snapshot |

`?` is a coin alerted 0 s after its launch, before the resolver fetched its symbol; the code after this run patches the
journal row when the symbol arrives (commit after `51e4602`).

## Run 1 (10:37-11:37 UTC, commit `4e03ffe`) and what it found

Numbers over the hour: block -> emit p50 102.5 ms, p90 137.3, p95 160.8, p99 259.9, max 3,627 ms (n 36,034);
36,042 block numbers, 36,034 live + 8 backfilled, 0 unfilled; 5 gaps / 71 blocks, all backfilled; events 119,651
(32.8 / s at the client); 30 alerts, 30 outcomes at +30 min; CPU 5.5 %, RSS 132 MB. The same latency and coverage
picture - but the store shows the last trade at 11:06:57 UTC while blocks kept coming until the end. Timeline:

- 11:03:36 the transfer connection went silent for 5 s -> detected, reconnected in 50 ms, the 67 blocks around the
  reconnect (61,055,937-61,056,003) refetched over Alchemy in 1.66 s; four blocks whose deferred transactions passed
  the 5-s deadline were fetched individually (0.4-0.8 s each). Every gap filled, 0 blocks missing.
- ~11:07 the fast connection stopped delivering its four log subscriptions while `newHeads` continued on the same
  socket (a fresh connection to the same endpoint at 11:45 received curve / swap / hook logs normally, so this is a
  server-side subscription death, not a network cut). Heads kept the release logic going, the blooms kept saying
  "maybe PONS activity", no log ever came, transfers of released blocks were dropped as stragglers (87,518), the trade
  stream stopped. The 5-s silence check could not see it (frames kept arriving), the process reported no error.
- Fix (`51e4602`): the assembler tracks, per released block whose bloom expects PONS logs, whether any arrived; 60
  such blocks in a row without one (or the transfer stream 50 blocks behind the heads) is a stall -> the connection is
  dropped, the next attempt goes to the other endpoint, and the whole quiet window (from the first empty block) is
  refetched over HTTP so the trades of those blocks are recovered as late fragments. A connection is never judged in
  its first 30 s. Unit test with synthetic frames; run 2 saw no stall in 60 min (0 triggers, health 60/60 at the end).
- A recall check on a 500-block window inside a transfer burst (10:50 UTC, the transfer stream 1-2 s behind the heads
  for a minute) found 3 of 1,370 trades missing and 0 wrong: a PONS log delivered after the next head reached the state
  as a fragment without the transaction's transfers, which had arrived on time in the released block. Fix
  (`3b7f719`): the state keeps every log of the last 300 blocks and normalizes a fragment together with what already
  arrived for its transaction; late transfers of a transaction without a swap yet are parked by the assembler and
  rejoined when the swap log comes. In run 2 that path saved 220 transfers, and both recall windows are 100 %.
- The writer initially dropped a batch after three failed attempts on `database is locked` (23 lock errors in 9 min
  because of the context worker's write holds, 11 of 13 alerts persisted). Fixed before run 2 (`4e03ffe`, in place for
  run 1's second half): retry until written, 30 s busy timeout, lag reported.

## Endpoint behaviour observed

- publicnode: six subscriptions per connection accepted; 100 ms head cadence (p50 99.5-100.3 ms, p99 ~250 ms,
  max 1.1-1.7 s pauses); PONS logs within a few ms of the head on a dedicated connection; transfers trail by 16-104 ms
  (p50-p95), more in bursts; no rate limiting; one 5-s silence of the transfer stream and one silent death of the log
  subscriptions in ~2.7 connection-hours. Ping RTT 88-143 ms.
- pocket: accepts the same subscriptions; in the 20-s probes its heads were 0.6 s (12:20 UTC on 2026-09-11 probes) to
  3.4 s (11:52 UTC today) behind the chain; kept as failover only.
- Alchemy HTTP: 10-block `eth_getLogs` with 4 workers is throttled (429 on 15-50 % of calls under load), retried
  transparently; a 67-block gap filled in 1.7 s, a single block in 0.4-0.8 s. `eth_call` batches for symbols /
  curves / reserve snapshots: 2,817 calls over the hour without an error.
- Public RPC (`rpc.mainnet.chain.robinhood.com`): wide `eth_getLogs` works at one call per 3 s (the paced client);
  used only for the lifecycle catch-up at start (43-85 s for 1-2 hours of chain).

## Open issues

1. The context worker's write lock (`context/xmentions.py`: `bump()` after each X page, commit after the last) stalls
   every other writer for seconds; it caused the writer lag (max 26-46 s) and one duplicate alert. One-line fix on the
   owner's side: commit right after `bump`, or skip the worker's `_alerts` when the wss engine runs.
2. `/api/status` loads the whole `blocks` table (`queries.sample`); +36k rows per hour of engine.
3. The 415k-curve registry costs 636 MB peak / ~100 MB resident; a store seeded with the last few days only would be
   lighter. A curve outside the seed is resolved on first sight anyway.
4. After a stall the feed stays on the failover endpoint until that connection drops; pocket's heads trail the chain
   by seconds, so a "return to the preferred endpoint after N stable minutes" policy is worth adding before relying
   on failover for latency.
5. Gap fills hold the strict-order queue: blocks behind a 67-block fill waited ~4 s (run 1, `block_to_emit_backfill`).
