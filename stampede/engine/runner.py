"""The engine process: feed -> normalize -> state -> incremental radar -> alerts -> SSE bus, with a batching writer.

Threads (all daemon, started by `Engine.start()`):
- `engine-feed`     asyncio loop with the two websocket connections, block assembly and gap backfill (feed.Feed)
- `engine-proc`     the only thread that mutates the state: `apply_block` per released block, publishes events
- `engine-writer`   persists trades / sequences / blocks / logs / lifecycle / alerts to SQLite in one transaction every 250 ms
- `engine-resolve`  off the hot path: symbols of new tokens, curves seen before their launch, reserve snapshots, and the
                    lifecycle catch-up between the seeded registry and the chain head (Alchemy / public RPC, never on
                    the block path)
- `engine-refresh`  every 30 s: external context caches for the ranked coins, the full radar snapshot event, and the
                    reconciliation of in-memory rows with the SQL radar (docs/ENGINE-PERF.md)

`Engine.status` has the keys of `api.live.LiveTail.status`, so `/api/status` and the session label work unchanged;
`Engine.perf()` feeds `/api/perf`.
"""
from __future__ import annotations

import json
import os
import queue
import resource
import subprocess
import threading
import time
import traceback
from collections import Counter
from pathlib import Path
from typing import Any

from .. import chain
from ..env import env, redact
from ..store import Store
from .bus import Bus, Histogram
from .feed import ENDPOINTS, Block, BlockAssembler, Feed, PonsBloom, make_backfill_fn, parse_log
from .state import (
    ALERT_INSERT,
    ALERT_SYMBOL,
    ALERT_UPDATE,
    CURVE_INSERT,
    GRAD_IGNORE_INSERT,
    GRAD_POOL_INSERT,
    INFRA_INSERT,
    LAUNCH_INSERT,
    LOG_INSERT,
    POOL_INSERT,
    RULES,
    SEQ_INSERT,
    TOKEN_INSERT,
    TRADE_INSERT_ID,
    WALLET_UPSERT,
    EngineState,
    WriteBatch,
)

WRITE_INTERVAL_S = 0.25
FULL_SNAPSHOT_S = 30.0
SESSION_EVERY_S = 5.0


class Writer(threading.Thread):
    """Batches every write of the engine into one SQLite transaction per interval (WAL, busy timeout)."""

    def __init__(self, db_path: Path, interval: float = WRITE_INTERVAL_S):
        super().__init__(daemon=True, name="engine-writer")
        self.db_path = db_path
        self.interval = interval
        self.q: queue.Queue[WriteBatch] = queue.Queue()
        self._stop = threading.Event()
        self.hist = Histogram()
        self.stats: dict[str, Any] = {"flushes": 0, "rows": 0, "errors": 0, "last_error": None, "last_flush_ms": None, "pending_batches": 0, "lag_s": 0.0, "max_lag_s": 0.0, "dropped_batches": 0, "by_table": Counter()}

    def enqueue(self, wb: WriteBatch) -> None:
        if wb.rows():
            self.q.put(wb)

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        import sqlite3

        store = Store(self.db_path)
        # other writers in the API process hold the lock for seconds at a time (the context worker keeps a write open
        # across X API calls); a batch is never dropped for that - it waits, and the lag is reported
        store.db.execute("PRAGMA busy_timeout=30000")
        last_ckpt = time.time()
        while not (self._stop.is_set() and self.q.empty()):
            try:
                first = self.q.get(timeout=self.interval)
            except queue.Empty:
                self.stats["lag_s"] = 0.0
                continue
            if time.time() - last_ckpt > 60:
                # the API's long readers (radar SQL, /api/status) starve the automatic checkpoint; a passive one now and
                # then keeps the WAL short so a later flush does not stall on a giant checkpoint
                last_ckpt = time.time()
                try:
                    store.db.execute("PRAGMA wal_checkpoint(PASSIVE)")
                    self.stats["checkpoints"] = self.stats.get("checkpoints", 0) + 1
                except Exception:  # noqa: BLE001
                    pass
            batch = WriteBatch()
            batch.extend(first)
            while True:
                try:
                    batch.extend(self.q.get_nowait())
                except queue.Empty:
                    break
            self.stats["pending_batches"] = self.q.qsize()
            self.stats["lag_s"] = round(time.time() - first.created, 2)
            self.stats["max_lag_s"] = max(self.stats.get("max_lag_s", 0.0), self.stats["lag_s"])
            t0 = time.perf_counter()
            attempt = 0
            while True:
                try:
                    self.flush(store, batch)
                    break
                except Exception as e:  # noqa: BLE001
                    self.stats["errors"] += 1
                    self.stats["last_error"] = f"{type(e).__name__}: {redact(str(e))[:160]}"
                    try:
                        store.db.rollback()
                    except Exception:  # noqa: BLE001
                        pass
                    locked = isinstance(e, sqlite3.OperationalError) and ("locked" in str(e) or "busy" in str(e))
                    attempt += 1
                    if not locked and attempt >= 3:
                        self.stats["dropped_batches"] = self.stats.get("dropped_batches", 0) + 1
                        break  # a real error (schema, disk): do not loop forever on the same rows
                    if self._stop.is_set() and attempt >= 10:
                        break
                    time.sleep(min(2.0, 0.1 * (2**min(attempt, 5))))
            ms = (time.perf_counter() - t0) * 1000
            self.hist.add(ms)
            self.stats["flushes"] += 1
            self.stats["rows"] += batch.rows()
            self.stats["last_flush_ms"] = round(ms, 1)
        store.close()

    def flush(self, store: Store, b: WriteBatch) -> None:
        db = store.db
        bt = self.stats["by_table"]
        if b.blocks:
            store.upsert_blocks(b.blocks)
            bt["blocks"] += len(b.blocks)
        for sql, rows, name in (
            (CURVE_INSERT, b.curves, "curves"),
            (TOKEN_INSERT, b.tokens, "tokens"),
            ("UPDATE tokens SET symbol=?, name=? WHERE address=?", b.token_meta, "token_meta"),
            (POOL_INSERT, b.pools, "pools"),
            (INFRA_INSERT, b.infra, "infra"),
            (LAUNCH_INSERT, b.launches, "launches"),
            (GRAD_POOL_INSERT, b.graduations_pool, "graduations"),
            (GRAD_IGNORE_INSERT, b.graduations_ignore, "graduations"),
            (LOG_INSERT, b.logs, "logs"),
            (TRADE_INSERT_ID, b.trades, "trades"),
            (WALLET_UPSERT, list(b.wallets.items()), "wallets"),
            (SEQ_INSERT, b.sequences, "sequences"),
            (ALERT_INSERT, b.alerts, "alerts"),
            (ALERT_UPDATE, b.alert_updates, "alert_updates"),
            (ALERT_SYMBOL, b.alert_symbols, "alert_symbols"),
        ):
            if rows:
                db.executemany(sql, rows)
                bt[name] += len(rows)
        for k, v in b.meta.items():
            db.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (k, json.dumps(v)))
        db.commit()


class Engine:
    """Drop-in for `LiveTail` inside the API process: `.status`, `.start()`, `.stop()`, plus `.perf()` and the bus."""

    def __init__(self, db_path: Path | str, window_s: int = 1800, span_s: int = 1800, bus: Bus | None = None, endpoints=ENDPOINTS, persist_logs: bool = True, rpc_factory=None):
        self.db_path = Path(db_path)
        self.window_s, self.span_s = window_s, span_s
        self.bus = bus or Bus()
        self.endpoints = list(endpoints)
        self.persist_logs = persist_logs
        self.rpc_factory = rpc_factory
        self.status: dict[str, Any] = {
            "engine": "wss",
            "running": False,
            "paused": False,
            "ticks": 0,
            "last_error": None,
            "head_block": None,
            "head_lag_s": None,
            "last_block": None,
            "last_ts": None,
            "tick_ms": None,
            "new_trades_total": 0,
            "new_sequences_total": 0,
            "start_block": None,
            "start_ts": None,
            "gaps": [],
            "feed": {},
        }
        self.started_at: float | None = None
        self.q: queue.Queue[Block] = queue.Queue()
        self.hist = {k: Histogram() for k in ("head_to_logs_complete", "logs_to_trades", "trades_to_radar", "block_to_emit", "apply_block", "queue_wait", "block_to_emit_backfill", "fragment_apply")}
        self.counts: Counter = Counter()
        self.state: EngineState | None = None
        self.feed: Feed | None = None
        self.writer: Writer | None = None
        self.assembler: BlockAssembler | None = None
        self.rpc = None
        self.reconcile: dict[str, Any] = {}
        self.resolver_stats: Counter = Counter()
        self._stop = threading.Event()
        self._cpu_last: tuple[float, float] | None = None
        self._threads: list[threading.Thread] = []

    # ---- lifecycle ----
    def start(self) -> None:
        store = Store(self.db_path)
        t0 = time.time()
        self.state = EngineState(store, window_s=self.window_s, span_s=self.span_s, rules=RULES, persist_logs=self.persist_logs)
        cursor = store.get_meta("engine_cursor")
        store.close()
        self.status["state_load_s"] = round(time.time() - t0, 2)
        try:
            self.rpc = self.rpc_factory() if self.rpc_factory else self._default_rpc()
        except Exception as e:  # noqa: BLE001
            self.rpc = None
            self.status["last_error"] = f"rpc unavailable: {redact(str(e))[:120]}"
        backfill = make_backfill_fn(self.rpc) if (self.rpc is not None and getattr(self.rpc, "alchemy_url", None)) else None
        self.assembler = BlockAssembler(pons_pool=self.state.is_pons_pool, bloom=PonsBloom(self.state.ctx.pool_token))
        self.feed = Feed(self.q, self.assembler, backfill, endpoints=self.endpoints, start_from=(int(cursor) + 1) if cursor else None)
        self.writer = Writer(self.db_path)
        self.writer.start()
        if self.rpc is not None:
            # launches / pool registrations between the seeded registry and the head, before the first block is processed:
            # a curve that launched in that window would otherwise trade as `unresolved_curve` until the resolver catches it
            t1 = time.time()
            try:
                self._lifecycle_catchup(self.rpc)
            except Exception as e:  # noqa: BLE001
                self.resolver_stats["catchup_errors"] += 1
                self.status["resolver_error"] = f"catch-up: {redact(str(e))[:160]}"
            self.status["catchup_s"] = round(time.time() - t1, 1)
        self.started_at = time.time()
        self.status["running"] = True
        self.status["backfill_available"] = backfill is not None
        for target, name in ((self._proc_loop, "engine-proc"), (self._resolver_loop, "engine-resolve"), (self._refresh_loop, "engine-refresh")):
            t = threading.Thread(target=target, name=name, daemon=True)
            t.start()
            self._threads.append(t)
        self.feed.start()

    def stop(self) -> None:
        self._stop.set()
        if self.feed:
            self.feed.stop()
        if self.writer:
            self.writer.stop()
        self.status["running"] = False

    @staticmethod
    def _default_rpc():
        from ..rpc import Rpc

        rpc = Rpc()
        return rpc if rpc.alchemy_url else None

    # ---- processor ----
    def _proc_loop(self) -> None:
        assert self.state is not None and self.writer is not None
        st, bus = self.state, self.bus
        store = Store(self.db_path)  # occasional reads only (alerts written by the context worker)
        last_session = 0.0
        last_full = time.time()
        last_alert_reload = time.time()
        while not self._stop.is_set():
            try:
                block = self.q.get(timeout=0.5)
            except queue.Empty:
                if time.time() - last_session >= SESSION_EVERY_S:
                    self._publish_session()
                    last_session = time.time()
                continue
            try:
                t_got = time.time()
                events, wb, tm = st.apply_block(block)
                t_emit = None
                for typ, data in events:
                    bus.publish(typ, block.number, data)
                t_emit = time.time()
                self.writer.enqueue(wb)
                self._account(block, tm, t_got, t_emit, events)
                if time.time() - last_full >= FULL_SNAPSHOT_S:
                    last_full = time.time()
                    bus.publish("radar_delta", block.number, {"full": True, "reason": "snapshot", "clock": st.clock, "window_s": st.window_s, "span_s": st.span_s, "rows": st.ranked[:50], "removed": [], "top": [r["address"] for r in st.ranked[:50]]})
                if time.time() - last_alert_reload >= 30:
                    last_alert_reload = time.time()
                    st.load_alerts(store)
                if time.time() - last_session >= SESSION_EVERY_S:
                    self._publish_session()
                    last_session = time.time()
                self.status["paused"] = False
                self.status["last_error"] = None
            except Exception as e:  # noqa: BLE001
                self.status["paused"] = True
                self.status["last_error"] = f"{type(e).__name__}: {redact(str(e))[:200]}"
                self.counts["apply_errors"] += 1
                traceback.print_exc()
        store.close()

    def _account(self, block: Block, tm: dict[str, float], t_got: float, t_emit: float, events: list) -> None:
        st = self.state
        assert st is not None
        self.counts["blocks"] += 1
        self.counts[f"blocks_{block.source}"] += 1
        for f in block.flags:
            self.counts[f"flag_{f}"] += 1
        n_tr = sum(1 for e in events if e[0] == "trade")
        n_seq = sum(1 for e in events if e[0] == "sequence")
        self.status["ticks"] += 1
        self.status["new_trades_total"] += n_tr
        self.status["new_sequences_total"] += n_seq
        self.status["tick_ms"] = round((tm["t_end"] - tm["t_start"]) * 1000, 1)
        self.hist["apply_block"].add((tm["t_end"] - tm["t_start"]) * 1000)
        self.hist["queue_wait"].add((t_got - block.t_logs_complete) * 1000)
        if block.is_fragment:
            self.hist["fragment_apply"].add((t_emit - block.t_logs_complete) * 1000)
            return
        if block.ts is not None:
            self.status["last_ts"] = block.ts
            self.status["head_lag_s"] = round(t_emit - block.ts, 2)
        self.status["last_block"] = block.number
        self.status["head_block"] = self.assembler.last_head if self.assembler else block.number
        if self.status["start_block"] is None:
            self.status["start_block"] = block.number
            self.status["start_ts"] = block.ts
        if block.source == "live" and block.t_head_received is not None:
            self.hist["head_to_logs_complete"].add((block.t_logs_complete - block.t_head_received) * 1000)
            self.hist["logs_to_trades"].add((tm["t_trades"] - block.t_logs_complete) * 1000)
            self.hist["trades_to_radar"].add((tm["t_radar"] - tm["t_trades"]) * 1000)
            self.hist["block_to_emit"].add((t_emit - block.t_head_received) * 1000)
        else:
            self.hist["block_to_emit_backfill"].add((t_emit - block.t_first_seen) * 1000)
        if block.source == "unfilled":
            self.status["gaps"].append([block.number, block.number, "not indexed: gap backfill failed (see /api/perf)"])
            del self.status["gaps"][:-50]

    def _publish_session(self) -> None:
        st = self.state
        feed = self.feed.snapshot() if self.feed else {}
        conns = feed.get("connections", {})
        self.status["feed"] = {
            "endpoint": (conns.get("fast") or {}).get("endpoint"),
            "connected": all(c.get("connected") for c in conns.values()) if conns else False,
            "reconnects": feed.get("reconnects"),
            "failovers": feed.get("failovers"),
            "last_head_age_s": feed.get("last_head_age_s"),
            "pending_blocks": feed.get("pending_blocks"),
        }
        self.status["head_block"] = feed.get("last_head") or self.status["head_block"]
        self.bus.publish("session", self.status.get("last_block"), {
            "mode": "live",
            "label": "LIVE · PAUSED (engine error)" if self.status["paused"] else ("LIVE" if self.status["feed"]["connected"] else "LIVE · RECONNECTING"),
            "clock_ts": self.status["last_ts"],
            "head_block": self.status["head_block"],
            "last_block": self.status["last_block"],
            "head_lag_s": self.status["head_lag_s"],
            "paused": self.status["paused"],
            "feed": self.status["feed"],
            "blocks_processed": self.counts["blocks"],
            "events_per_s": self.bus.events_per_s(10),
            "writer": {"lag_s": self.writer.stats.get("lag_s"), "pending_batches": self.writer.stats.get("pending_batches"), "errors": self.writer.stats.get("errors")} if self.writer else None,
            "engine": "wss",
            "window_s": st.window_s if st else self.window_s,
            "span_s": st.span_s if st else self.span_s,
            "server_time": int(time.time()),
        })

    # ---- resolver: symbols, unknown curves, reserve snapshots, lifecycle catch-up (never on the block path) ----
    def _resolver_loop(self) -> None:
        st = self.state
        assert st is not None and self.writer is not None
        if self.rpc is None:
            self.resolver_stats["disabled_no_rpc"] = 1
            return
        rpc = self.rpc
        while not self._stop.is_set():
            try:
                self._resolve_curves(rpc)
                self._resolve_meta(rpc)
                self._snapshot_reserves(rpc)
            except Exception as e:  # noqa: BLE001
                self.resolver_stats["errors"] += 1
                self.status["resolver_error"] = f"{type(e).__name__}: {redact(str(e))[:160]}"
            self._stop.wait(2.0)

    def _resolve_curves(self, rpc) -> None:
        st = self.state
        todo = sorted(st.unresolved_curves)[:24]
        if not todo:
            return
        res = rpc.eth_call_batch([(c, chain.S_TOKEN) for c in todo] + [(c, chain.S_PAIR_TOKEN) for c in todo], size=12)
        toks, pairs = res[: len(todo)], res[len(todo) :]
        wb = WriteBatch()
        for c, t, p in zip(todo, toks, pairs):
            if not t or len(t) < 66:
                st.unresolved_curves.discard(c)  # not a PONS curve (or dead): stop asking
                self.resolver_stats["curves_failed"] += 1
                continue
            token = chain.addr_from_word(t, 0)
            pair = chain.addr_from_word(p, 0) if p and len(p) >= 66 else chain.NATIVE
            wb.curves.append(st.add_curve(c, token, pair, "eth_call token()/pairToken()"))
            wb.infra.append((c, "pons_curve", "engine"))
            if token not in st.known_tokens:
                st.known_tokens.add(token)
                wb.tokens.append((token, None, None, "curve", c, None))
            st.tokens_needing_meta.add(token)
            self.resolver_stats["curves_resolved"] += 1
        self.writer.enqueue(wb)

    def _resolve_meta(self, rpc) -> None:
        st = self.state
        todo = sorted(st.tokens_needing_meta)[:24]
        if not todo:
            return
        syms = rpc.eth_call_batch([(t, chain.S_SYMBOL) for t in todo], size=12)
        names = rpc.eth_call_batch([(t, chain.S_NAME) for t in todo], size=12)
        wb = WriteBatch()
        for t, s, n in zip(todo, syms, names):
            if s is None:
                st.tokens_needing_meta.discard(t)
                self.resolver_stats["meta_failed"] += 1
                continue
            sym, name = chain.dec_string(s)[:32] or "?", chain.dec_string(n or "")[:64] or ""
            st.set_label(t, sym, name)
            wb.token_meta.append((sym, name, t))
            if t in st.alert_recent:  # alerted seconds after launch, before the symbol was known: fix the journal row
                label = st.label(t)["symbol"]
                wb.alert_symbols.append((label, t))
                for p in st.alert_pending:
                    if p.token == t and p.symbol == "?":
                        p.symbol = label
            self.resolver_stats["meta_resolved"] += 1
        self.writer.enqueue(wb)

    def _snapshot_reserves(self, rpc) -> None:
        st = self.state
        todo = [c for c, r in list(st.reserves.items()) if r.needs_snapshot][:12]
        if not todo:
            return
        head = int(rpc.call("eth_blockNumber", [], prefer="alchemy"), 16)
        sels = [chain.selector("trackedQuote()"), chain.selector("trackedTokens()"), chain.selector("phantomQuote()"), chain.selector("graduationThreshold()")]
        res = rpc.eth_call_batch([(c, s) for c in todo for s in sels], block=hex(head), size=12)
        for i, c in enumerate(todo):
            q, t, ph, thr = res[4 * i : 4 * i + 4]
            rs = st.reserves.get(c)
            if rs is None:
                continue
            if not (q and t and len(q) >= 66 and len(t) >= 66):
                rs.complete = True  # not a v2 curve we can read; stop asking (price falls back to trades)
                rs.phantom = rs.phantom or chain.CURVE_PHANTOM_QUOTE_WEI
                self.resolver_stats["snapshots_failed"] += 1
                continue
            rs.snapshot(chain.u256(q, 0), chain.u256(t, 0), head, chain.u256(ph, 0) if ph and len(ph) >= 66 else None, chain.u256(thr, 0) if thr and len(thr) >= 66 else None)
            self.resolver_stats["snapshots"] += 1

    def _lifecycle_catchup(self, rpc) -> None:
        """Launches and pool registrations between the seeded registry and the chain head (public RPC, wide ranges)."""
        st = self.state
        since = max([la["block"] for la in list(st.launches.values()) if la.get("block")] or [0])
        if not since:
            return
        head = rpc.block_number(prefer="alchemy")
        if head - since > 400000:  # > 11 h behind: the seed is stale, lazy resolution covers what trades
            self.resolver_stats["catchup_skipped_blocks"] = head - since
            return
        n = 0
        for lo in range(since + 1, head + 1, 5000):
            hi = min(lo + 4999, head)
            logs = rpc.get_logs(lo, hi, address=[chain.PONS_V2_FACTORY, chain.PONS_V2_HOOK], topics=[[chain.T_TOKEN_LAUNCHED, chain.T_POOL_REGISTERED]])
            hdr_nums = sorted({int(l["blockNumber"], 16) for l in logs})
            hdrs = rpc.get_blocks(hdr_nums[:400]) if hdr_nums else {}
            wb = WriteBatch()
            for raw in sorted(logs, key=lambda l: (int(l["blockNumber"], 16), int(l["logIndex"], 16))):
                l = parse_log(raw)
                if l is None:
                    continue
                h = hdrs.get(l["block"])
                ts = int(h["timestamp"], 16) if h else None
                if l["kind"] == "token_launched":
                    n += st._on_launch(l, l["block"], ts, l["tx_hash"], wb, live=False)
                elif l["kind"] == "pool_registered":
                    n += st._on_pool(l, l["block"], ts, l["tx_hash"], wb)
            self.writer.enqueue(wb)
            self.resolver_stats["catchup_events"] = n
            self.resolver_stats["catchup_to_block"] = hi
        self.resolver_stats["catchup_from_block"] = since + 1

    # ---- refresh: context caches + reconciliation with the SQL radar ----
    def _refresh_loop(self) -> None:
        from ..api import radar as radar_mod
        from ..api.context_worker import load_context

        st = self.state
        assert st is not None
        store = Store(self.db_path)
        while not self._stop.is_set():
            self._stop.wait(FULL_SNAPSHOT_S)
            if self._stop.is_set() or not st.clock:
                break
            try:
                toks = [r["address"] for r in st.ranked[:200]] or list(st.rows)[:200]
                if toks:
                    st.mentions = load_context(store, "mentions", toks)
                    st.context["market"] = load_context(store, "market", toks)
                    st.context["holders"] = load_context(store, "holders", toks)
                st.load_radar_inputs(store, toks)
                clock = st.clock
                t0 = time.perf_counter()
                sql_rows = {r["address"]: r for r in radar_mod.compute_rows(store, st.window_s, clock, st.span_s, True, {"mentions": st.mentions})}
                mem_rows = dict(list(st.rows.items()))
                both = set(sql_rows) & set(mem_rows)
                diff_inflow = sum(1 for a in both if sql_rows[a]["inflow_10m"] != mem_rows[a]["inflow_10m"])
                self.reconcile = {
                    "at": int(time.time()),
                    "clock": clock,
                    "sql_ms": round((time.perf_counter() - t0) * 1000),
                    "sql_rows": len(sql_rows),
                    "memory_rows": len(mem_rows),
                    "in_both": len(both),
                    "only_sql": len(set(sql_rows) - both),
                    "only_memory": len(set(mem_rows) - both),
                    "inflow_10m_mismatch": diff_inflow,
                    "top5_sql": [sql_rows[a]["address"] for a in sorted(sql_rows, key=lambda a: -sql_rows[a]["score"]) if sql_rows[a]["wallets_range"] >= 2][:5],
                    "top5_memory": [r["address"] for r in st.ranked[:5]],
                }
            except Exception as e:  # noqa: BLE001
                self.reconcile = {"error": f"{type(e).__name__}: {redact(str(e))[:160]}", "at": int(time.time())}
        store.close()

    # ---- perf ----
    def _process(self) -> dict[str, Any]:
        ru = resource.getrusage(resource.RUSAGE_SELF)
        cpu = ru.ru_utime + ru.ru_stime
        now = time.time()
        pct = None
        if self._cpu_last is not None and now > self._cpu_last[0]:
            pct = round((cpu - self._cpu_last[1]) / (now - self._cpu_last[0]) * 100, 1)
        self._cpu_last = (now, cpu)
        rss_mb = None
        try:
            out = subprocess.run(["ps", "-o", "rss=", "-p", str(os.getpid())], capture_output=True, text=True, timeout=2).stdout.strip()
            rss_mb = round(int(out) / 1024, 1) if out else None
        except Exception:  # noqa: BLE001
            pass
        return {"pid": os.getpid(), "cpu_percent_since_last_call": pct, "cpu_percent_avg": round(cpu / max(1e-6, now - (self.started_at or now)) * 100, 1) if self.started_at else None, "cpu_time_s": round(cpu, 1), "rss_mb": rss_mb, "maxrss_mb": round(ru.ru_maxrss / (1024 * 1024), 1), "threads": threading.active_count()}

    def perf(self) -> dict[str, Any]:
        st = self.state
        feed = self.feed.snapshot() if self.feed else {}
        asm = feed.get("assembler", {})
        return {
            "engine": "wss",
            "started_at": self.started_at,
            "uptime_s": round(time.time() - self.started_at) if self.started_at else None,
            "blocks": {
                "first": self.status.get("start_block"),
                "last": self.status.get("last_block"),
                "head": feed.get("last_head"),
                "processed": self.counts["blocks"],
                "live": self.counts["blocks_live"],
                "backfilled": self.counts["blocks_backfill"],
                "fragments": self.counts["blocks_late"],
                "unfilled": self.counts["blocks_unfilled"],
                "gaps_found": asm.get("gaps", 0),
                "gap_blocks": asm.get("gap_blocks", 0),
                "gaps_backfilled_blocks": feed.get("gap_fills", {}).get("blocks_filled", 0),
                "gaps_unfilled_blocks": asm.get("gap_unfilled_blocks", 0),
                "late_logs": asm.get("late_logs", 0),
                "deferred_txs": asm.get("deferred_txs", 0),
                "fast_grace": asm.get("fast_grace", 0),
                "bulk_cap": asm.get("bulk_cap", 0),
                "bloom_skip": asm.get("bloom_skip", 0),
                "skipped_at_start": feed.get("skipped_at_start"),
            },
            "latency_ms": {k: h.summary() for k, h in self.hist.items()},
            "feed": feed,
            "events": self.bus.stats(),
            "writer": {**{k: v for k, v in self.writer.stats.items() if k != "by_table"}, "by_table": dict(self.writer.stats["by_table"]), "flush_ms": self.writer.hist.summary()} if self.writer else None,
            "process": self._process(),
            "state": st.summary() if st else None,
            "resolver": dict(self.resolver_stats),
            "radar_reconcile": self.reconcile,
            "counts": dict(self.counts),
            "rules": RULES,
            "status": {k: v for k, v in self.status.items() if k != "feed"},
        }


def live_source(db_path: Path | str, app_state: dict[str, Any], feed: str = "wss"):
    """What `serve --mode live` starts: the websocket engine (default) or the polling `LiveTail` (`--feed alchemy`)."""
    if feed == "wss":
        try:
            import websockets  # noqa: F401
        except ImportError:
            print("websockets is not installed: falling back to --feed alchemy (uv add websockets)", flush=True)
            feed = "alchemy"
    if feed == "wss":
        bus = app_state.setdefault("bus", Bus())
        return Engine(db_path, window_s=app_state["window_s"], bus=bus, persist_logs=env("STAMPEDE_ENGINE_LOGS", "1") != "0")
    from ..api.live import LiveTail

    return LiveTail(db_path, window_s=app_state["window_s"])
