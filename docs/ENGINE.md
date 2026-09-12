# Realtime engine (stage 6)

`stampede serve --mode live --feed wss` runs the engine inside the API process: the free websocket feed of Robinhood
Chain -> complete blocks -> the same normalizer as every other path -> in-memory state (registry, curve reserves,
per-wallet rings, rolling per-coin windows) -> incremental sequences and radar rows -> the alert rule -> Server-Sent
Events at `GET /api/stream`. A writer thread persists everything to SQLite in one transaction every 250 ms, so the
existing endpoints (`/api/radar`, `/api/events`, `/api/alerts`, `/api/coin/...`) keep working on the same store.
Measured numbers: `docs/ENGINE-PERF.md`. Code: `stampede/engine/` (`feed`, `state`, `runner`, `bus`, `seed`) and
`stampede/api/stream.py`. `--feed alchemy` keeps the previous 2-second polling tail (`api/live.py`).

## Running it

```sh
uv sync                                                        # websockets is a declared dependency
python -m stampede.engine.seed --into data/live-engine.sqlite \
    --from data/research-14d.sqlite --from data/stampede.sqlite   # optional: registry of curves / pools / launches, quotes, wallet scores
uv run stampede serve --mode live --feed wss --port 8798 --db data/live-engine.sqlite
curl -N http://127.0.0.1:8798/api/stream                       # every event
curl -N "http://127.0.0.1:8798/api/stream?types=alert,radar_delta"
curl -s http://127.0.0.1:8798/api/perf | python -m json.tool
```

Environment: `ALCHEMY_KEY` (gap backfill over HTTP, `eth_call` for symbols / unknown curves / reserve snapshots; the
engine runs without it but cannot fill gaps), `PUBLIC_RPC` (the lifecycle catch-up at start), `STAMPEDE_ENGINE_LOGS=0`
to skip persisting raw logs of trade transactions.

Start-up: the registry is loaded from the store (~1.5 s for 415k curves), launches and pool registrations between the
seeded registry and the chain head are fetched once (public RPC, ~40 s for an hour of chain; the API listens after
that), then the feed connects. A store without a seed works too: every curve is then resolved on first sight with two
`eth_call`s and every reserve starts from a chain snapshot.

## The feed (`engine/feed.py`)

Two websocket connections to the same endpoint, `wss://robinhood-rpc.publicnode.com` first, `wss://robinhood.api.pocket.network`
as failover (both accept the six subscriptions; measured 2026-09-12):

| connection | subscriptions | frames / block |
|---|---|---|
| fast | `newHeads`; `logs` CurveBuy / CurveSell / SnipeTaxCharged / CurveBuyRefunded / CurveCompleted by topic (no address); v4 `Swap` on the PoolManager; TokenLaunched / LaunchSwept / PoolGraduated on the factory; PoolRegistered / HookFeeCollected on the hook | ~7 |
| bulk | `logs` ERC-20 `Transfer`, no address filter (the attribution needs every transfer of a trade transaction) | ~30, trailing the head by 30-300 ms |

Why two: the node writes one frame per log, so on a shared connection the PONS events queue behind the transfer
firehose; on the fast connection they land within a few ms of the head.

Block assembly (`BlockAssembler`, pure, tested with synthetic frames) releases blocks in strict order when

1. the header is in (block timestamp, hash, logs bloom),
2. the fast side has moved past the block (a head or a PONS log of a later block) - or the header's bloom cannot contain
   any subscribed topic / PONS pool id (`bloom_skip`, no wait at all) - otherwise after a 250 ms grace (`fast_grace`),
3. only if the block has a PONS swap: the transfer stream has moved past the block (transfers arrive in log-index order,
   so a transfer of a later block proves the block's transfers are complete) - otherwise after a 1 s cap (`bulk_cap`).
   At the cap, swap transactions whose transfers may still be streaming are **deferred**: they are left out of the
   block and re-emitted as a `late` fragment once the transfer stream passes the block (or fetched over HTTP after 5 s).
   A trade is never attributed from a partial transfer set.

Gaps: a head sequence with holes, a block whose header never arrives, and the blocks around a (re)connect of either
connection are requested from Alchemy (`Rpc.get_logs_parallel`, the same topics, 10-block sub-ranges, 50-block chunks
oldest first) plus headers; later blocks wait in the queue until the gap is filled (60 s timeout -> the block is
released as `unfilled` and counted; its logs, if they arrive later, are applied as a fragment). On start, a stored
`engine_cursor` up to 600 blocks behind the head is backfilled the same way; further behind it is recorded as
`skipped_at_start`. Reconnect: exponential backoff 0.25 -> 15 s, endpoints alternate on consecutive failures, a
connection stable for 30 s resets the counter; a connection silent for 5 s is dropped (blocks arrive every 100 ms).

Every released block carries `t_first_seen`, `t_head_received`, `t_logs_complete`, the number, timestamp and flags.

## The state (`engine/state.py`)

- Registry: `curve -> {token, pair_token}`, `pool_id -> {token, quote}`, universe, infrastructure - the `normalize.Context`
  itself, mutated in place from TokenLaunched / PoolRegistered (no `eth_call`); the resolver thread adds curves seen
  before their launch (two `eth_call`s) and symbols of new tokens.
- Curve reserves from CurveBuy / CurveSell: buy `quote += quoteIn - fee - tax`, `tokens -= tokensOut`; sell
  `quote -= quoteOut + fee + tax`, `tokens += tokensIn`; spot price `(phantom + quote) / tokens`, progress
  `quote / threshold`. Validated on 5,202 real curve trades of the research store: every buy and sell whose event
  amounts are known reproduces exactly (`tokensOut = net * T / (Q + net)`). A curve first seen mid-life is snapshotted
  from `trackedQuote() / trackedTokens() / phantomQuote() / graduationThreshold()` at a block, and events at or before
  that block are skipped; non-ETH launch configs (USDG pairs have their own phantom) get their phantom from the snapshot.
- Per-wallet ring of trades (`deque`, 2 h) -> `rotation.sequences_for_wallet` on the ring slice inside the window for
  every wallet that bought in the block; only sequences ending in this block's buys are new (a new sell has no later
  buy yet; grades never change because time only moves forward). Verified equal to `rotate()` on the same trades.
- Rolling per-coin windows: main-grade sequences (span, 30 min) -> distinct inflow wallets in 10 min, previous rate,
  acceleration, breadth of source coins, minute histogram; trades (1 h) -> the same `radar.stats_from_rows` numbers as
  the SQL radar (median price of the last 5 trades, 5 m / 10 m / 1 h change, volume, buyers). Rows use `radar.score()`
  unchanged, including the stage-2 `smart_inflow` part (top-decile traders from `traders_api.smart_set`, refreshed
  every 30 s) and the stage-3 `launch` object (`launch_intel.intel_for`, same cadence; `None` for coins without a row);
  every 5 s of chain time all rows are recomputed and expired ones removed; every 30 s the memory rows are reconciled
  against `radar.compute_rows` on the store (`/api/perf` -> `radar_reconcile`: rows in both, inflow mismatches, top-5
  of each).
- Trade ids are assigned by the engine (`INSERT OR IGNORE INTO trades(id, ...)`), so `sequences.sell_trade/buy_trade`
  and the `trade` events agree with the database before the writer has flushed.
- Alerts: the `under_radar_top5` rule of `api/context_worker.py` (same parameters: rank <= 5, score >= 60, inflow
  8-40 in 10 min, age <= 1 h, not already +100 % in 10 min, <= 3 X mentions when known, 30 min per coin) evaluated
  after every block; rows go to `alerts` with `mode='live'`; outcomes at +30 / +60 min of chain time from the in-memory
  price (same estimator as `curve_stats`), persisted with `outcome_checked_ts`, plus `graduated_after`.

`apply_block(block) -> (events, write_batch, timings)` is one function per block with per-stage wall-clock stamps.

## Threads (`engine/runner.py`)

`engine-feed` (asyncio: sockets, assembly, gap worker), `engine-proc` (the only writer of the state; publishes to the
bus), `engine-writer` (250 ms batches: trades, sequences, blocks, logs of trade transactions, launches, graduations,
curves, pools, tokens, wallets, alerts, `meta.engine_cursor`), `engine-resolve` (every 2 s: unknown curves, token
symbols, reserve snapshots), `engine-refresh` (every 30 s: cached X / market / holders context for the ranked coins,
SQL reconciliation). `Engine.status` has the keys of `LiveTail.status`, so `/api/status` and the session label are
unchanged; `Engine.perf()` is `/api/perf`.

The context worker of the API process keeps running in live mode (it fetches the external context the radar and the
alert rule use). Its own alert evaluation stays enabled as a fallback; both sides check the 30-minute-per-coin rule in
the database, so a coin is not journaled twice.

## Event contract: `GET /api/stream`

`text/event-stream`. Every frame:

```
id: 1234
event: trade
data: {"id":1234,"type":"trade","ts_emit":1789208380.123,"block":61033700,"data":{...}}
```

- `id` is monotonic per server process (one counter for all types). `ts_emit` is the server wall clock at publish
  (float seconds). `block` is the block the event belongs to (`null` for session events before the first block).
- Reconnect with the `Last-Event-ID` header (browsers do it themselves) or `?last_event_id=N`: every event with a
  larger id still in the ring (last 5,000 events, ~90 s at the measured 55 events/s) is replayed before live events.
  If the ring no longer reaches back that far, the hello carries `replay_gap: true` and the client should refetch
  `/api/radar` (and `/api/alerts`) before consuming deltas.
- The first frame of every connection is a `session` hello **without an `id:` line** (it does not move the client's
  Last-Event-ID); `data.hello = true`, `data.last_event_id` is the current counter, `data.replay_from` what was asked.
- `?types=a,b` filters server-side (replay and live). `?limit=N` closes after N events (scripts, tests). A comment line
  `: keepalive` is sent after 15 s of silence.
- Event types and their `data`:

| type | when | data |
|---|---|---|
| `block` | every processed block or fragment | `number, ts, hash, source (live / backfill / late / unfilled), flags, logs, swap_txs, trades, sequences, launches, graduations, deferred_txs, t_head_received, t_logs_complete, latency_ms {head_to_logs, logs_to_trades, trades_to_radar, apply}, ts_estimated` |
| `trade` | one per normalized trade | `id, tx, block, ts, token, symbol, wallet, side, token_amount, quote_amount (raw integers as strings), quote_token, quote_symbol, venue, fee, tax, snipe, flags, price_quote (quote per token, decimals applied), bot` |
| `sequence` | one per new sequence (all grades) | `wallet, window_s, block, sell_token, buy_token, sell_symbol, buy_symbol, sell_trade, buy_trade, sell_ts, buy_ts, gap_s, grade, candidates` |
| `radar_delta` | after a block that touched rows (`reason: block`), every 5 s of chain time when rows aged (`tick`), every 30 s a full snapshot (`full: true, reason: snapshot`) | `full, reason, clock, window_s, span_s, rows[]` (the `/api/radar` row shape plus `rank, price_spot, reserve_quote, block, updated_ts`), `removed[]` (tokens whose inflow left the span), `top[]` (tokens in rank order, first 50) |
| `alert` | when the rule fires (`kind: fired`) and when an outcome settles (`kind: outcome`) | fired: `key (token:clock_ts), created_ts, clock_ts, mode, token, symbol, rule, score, inflow, mentions_1h, price, detail {rank, sources, accel, breadth, age_s, stage, mentions_known, engine, block, progress}`; outcome: `key, token, symbol, clock_ts, price, outcome_30m and/or outcome_60m (percent), graduated_after` |
| `verdict` | reserved for stage 4 (not emitted yet) | `token, ...` |
| `session` | hello at connect (no id), then every 5 s | `mode, label, clock_ts (timestamp of the last processed block = the live clock), head_block, last_block, head_lag_s, paused, feed {endpoint, connected, reconnects, failovers, last_head_age_s, pending_blocks}, blocks_processed, events_per_s, engine, window_s, span_s, server_time`; the hello additionally carries the shared session fields of `/api/session` (`id, rev, ...`) and `last_event_id, replay_from, replay_gap, types, hello` |

Amounts are raw integer strings (18 decimals for tokens; the quote's decimals are in `quotes`); prices are floats in
quote units per token. Symbols are disambiguated like everywhere else (`MARIO·9b72` when several coins share a
ticker); `?` means the symbol is not resolved yet (the resolver fills it within seconds; `trade` and `radar_delta`
events after that carry the name). In modes other than live the endpoint exists but only carries what the API process
publishes (today: nothing besides the hello and keepalives).

For a web client: `new EventSource('/api/stream')`, handle `radar_delta` (replace rows by `address`, drop `removed`,
order by `top` or by `rank`), append `trade` / `sequence` to the tape, show `alert`, and use `session` for the LIVE
label and the lag. For the TUI: the same over `httpx`'s streaming response, one thread, `Last-Event-ID` on reconnect.

## `GET /api/perf`

```
engine, started_at, uptime_s,
blocks {first, last, head, processed, live, backfilled, fragments, unfilled, gaps_found, gap_blocks,
        gaps_backfilled_blocks, gaps_unfilled_blocks, late_logs, deferred_txs, fast_grace, bulk_cap, bloom_skip, skipped_at_start},
latency_ms {head_to_logs_complete, logs_to_trades, trades_to_radar, block_to_emit, apply_block, queue_wait,
            block_to_emit_backfill, fragment_apply}      # each: n, p50, p90, p95, p99, max, mean (ms, last 50k samples)
feed {connections {fast, bulk: endpoint, connected, connects, reconnects, failovers, errors, frames, mb, logs, heads,
      last_frame_age_s, ping_rtt_ms, subscriptions, last_error}, reconnects, failovers, first_head, last_head,
      last_head_age_s, head_gap_ms, pending_blocks, late_buckets, assembler {...counters}, gaps[], gap_fills, backfill_ms, errors[]},
events {last_id, ring, subscribers, by_type, per_s_60s, per_s_10s},
writer {flushes, rows, errors, last_error, last_flush_ms, pending_batches, by_table, flush_ms},
process {pid, cpu_percent_since_last_call, cpu_percent_avg, cpu_time_s, rss_mb, maxrss_mb, threads},
state {curves, pools, launches, reserves, reserves_incomplete, wallets_tracked, coins_tracked, radar_rows, ranked,
       alerts_fired, alert_outcomes, unresolved_curves, notes, note_samples, clock, next_trade_id, ...},
resolver {...}, radar_reconcile {...}, counts {...}, rules {...}, status {...}
```

Latency definitions: `head_to_logs_complete` = block released - `newHeads` frame received; `logs_to_trades` =
normalization done - released; `trades_to_radar` = rings, sequences and radar rows done - normalization done;
`block_to_emit` = last event of the block published - `newHeads` frame received (the number in the programme goal);
`queue_wait` = time between release and the processor picking the block up; `apply_block` = the whole state update.
Only `live` blocks enter the four main histograms; backfilled blocks are measured from the first frame that mentioned
them (`block_to_emit_backfill`) and fragments separately (`fragment_apply`).

## Tests

`tests/test_engine.py`: assembly with out-of-order logs, the 250 ms grace and the bloom skip; deferral of transactions
with incomplete transfers and their late fragment; gap detection, the backfill call (topics, grouping, fork filter) and
the in-order release; bloom filter without false negatives; reserve reconstruction against `chain.CURVE_*` (a 0.01 ETH
buy at t = 0 gives `tokensOut = net * T / (Q + net)`, sells, snapshots, non-ETH configs); incremental sequences equal
`rotate()` on the same synthetic trades (direct / clean / ambiguous) and the radar rows they produce; launch and pool
events in the registry and the reserves; the SSE ring with `Last-Event-ID`, type filter and gap reporting through the
FastAPI TestClient; `/api/perf` shape. Gate: `env -u NO_COLOR TEXTUAL_COLOR_SYSTEM=truecolor uv run pytest -q`.

## Known limits

- `/api/status` loads the whole `blocks` table (`queries.sample`); the engine adds ~36k rows per hour, so that
  endpoint slows on multi-day runs (an index-only bounds query would fix it; not touched in this pass).
- `swap_without_transfer` notes (about 0.2 % of swap transactions) are v4 swaps where the graduated token emits a
  non-standard transfer event or the PoolManager settles through claims: no attribution, same as the batch path.
- The Robinhood DEX's own v4 pools share the PoolManager: their `Swap` frames are received and dropped
  (`non_universe_pool`); the bloom short-cut cannot tell them apart in busy blocks, so such blocks wait for the fast
  boundary (~100 ms).
- One process, one engine: two `serve --mode live --feed wss` on the same store would both assign trade ids.
