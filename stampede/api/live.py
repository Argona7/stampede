"""Live tail: extend the sample block by block from the chain head and keep sequences current.

Each tick fetches the new blocks (Alchemy 10-block parallel path), resolves new curves/pools, stores
exact headers for every new block, normalizes the new transactions and recomputes the sequences of the
wallets that traded. Status carries the measured head lag and the last error; on error the tail marks
itself paused so the UI can stop pretending to be live.
"""
from __future__ import annotations

import json
import threading
import time
import traceback
from collections import defaultdict
from typing import Any

from .. import chain
from ..env import redact
from ..hypersync import HyperSync
from ..ingest import Ingest
from ..normalize import Interp, build_context, trades_from_tx
from ..rotation import T, sequences_for_wallet
from ..rpc import Rpc
from ..store import Store

SAFETY_BLOCKS = 3
MAX_CATCHUP_BLOCKS = 6000  # ~10 minutes of chain; beyond that we skip and record a gap


class LiveTail(threading.Thread):
    def __init__(self, db_path, window_s: int = 1800, interval: float = 2.0):
        super().__init__(daemon=True, name="live-tail")
        self.db_path = db_path
        self.window_s = window_s
        self.interval = interval
        self.status: dict[str, Any] = {
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
            "gaps": [],  # [from_block, to_block, reason] ranges deliberately skipped (server was down, head ran away)
        }
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        store = Store(self.db_path)
        rpc = Rpc()
        ing = Ingest(store, rpc, HyperSync(), workers=6)
        ing.seed_infra()
        self.status["running"] = True
        cursor: int | None = None
        while not self._stop.is_set():
            t0 = time.time()
            try:
                if cursor is None:
                    # (re)initialise: a failing provider must show up as paused, never as a dead thread
                    head0 = rpc.block_number(prefer="alchemy")
                    saved = store.get_meta("live_cursor")
                    if saved is None or head0 - saved > MAX_CATCHUP_BLOCKS:
                        if saved is not None:
                            self.status["gaps"].append([saved + 1, head0 - 60, "not indexed: the live tail was stopped for too long to catch up"])
                        saved = head0 - 60
                    start_hdr = rpc.get_block(saved + 1)
                    self.status["start_block"] = saved + 1
                    self.status["start_ts"] = int(start_hdr["timestamp"], 16) if start_hdr else None
                    store.set_meta("live_start_block", saved + 1)
                    cursor = saved
                head = rpc.block_number(prefer="alchemy")
                target = head - SAFETY_BLOCKS
                self.status["head_block"] = head
                if target - cursor > MAX_CATCHUP_BLOCKS:
                    # the head ran away (long pause): skip honestly instead of chasing for minutes
                    self.status["gaps"].append([cursor + 1, target - 300, "not indexed: fell behind the head"])
                    cursor = target - 300
                if target > cursor:
                    fr, to = cursor + 1, min(target, cursor + 300)
                    failures_before = ing.stats["chunk_failures"]
                    ing.fetch_range(fr, to)
                    if ing.stats["chunk_failures"] > failures_before:
                        lost = ing.stats["blocks_missing"][-1]
                        # do not advance: the range is retried next tick and the UI shows the pause
                        raise RuntimeError(f"log fetch failed for blocks {lost[0]}-{lost[1]}: {lost[2]}")
                    ing.resolve_curves(fr, to)
                    ing.resolve_pools(fr, to)
                    ing.resolve_token_meta()
                    ing.resolve_quotes()
                    # exact headers every 5th block + the last one (CU budget); the rest interpolate within <0.5 s
                    hdrs = rpc.get_blocks(sorted(set(range(fr, to + 1, 5)) | {to}))
                    store.upsert_blocks((n, int(b["timestamp"], 16), 1) for n, b in hdrs.items())
                    store.commit()
                    n_tr, n_seq = self._normalize_range(store, fr, to)
                    cursor = to
                    store.set_meta("live_cursor", cursor)
                    last = hdrs.get(to)
                    if last:
                        self.status["last_ts"] = int(last["timestamp"], 16)
                    self.status["last_block"] = cursor
                    self.status["new_trades_total"] += n_tr
                    self.status["new_sequences_total"] += n_seq
                if self.status.get("last_ts"):
                    self.status["head_lag_s"] = round(time.time() - self.status["last_ts"], 1)
                self.status["paused"] = False
                self.status["last_error"] = None
            except Exception as e:  # noqa: BLE001
                self.status["paused"] = True
                self.status["last_error"] = redact(str(e))[:200]
                traceback.print_exc()
            self.status["ticks"] += 1
            self.status["tick_ms"] = round((time.time() - t0) * 1000)
            self._stop.wait(max(0.2, self.interval - (time.time() - t0)))
        self.status["running"] = False

    def _normalize_range(self, store: Store, fr: int, to: int) -> tuple[int, int]:
        ctx = build_context(store)
        blocks = {n: ts for n, (ts, ex) in store.blocks().items() if ex == 1 and fr - 60 <= n <= to + 1}
        interp = Interp(blocks)
        cur = store.db.execute("SELECT tx_hash, log_index, block, address, topic0, topic1, topic2, topic3, data, kind FROM logs WHERE block BETWEEN ? AND ? ORDER BY block, tx_hash, log_index", (fr, to))
        groups: dict[str, tuple[int, list[dict]]] = {}
        for tx_hash, li, block, addr, t0, t1, t2, t3, data, kind in cur:
            g = groups.setdefault(tx_hash, (block, []))
            g[1].append({"log_index": li, "address": addr, "topic0": t0, "topic1": t1, "topic2": t2, "topic3": t3, "data": data, "kind": kind})
        rows = []
        wallets: set[str] = set()
        for tx_hash, (block, logs) in groups.items():
            trades, _notes = trades_from_tx(tx_hash, block, logs, ctx)
            if not trades:
                continue
            ts, exact = interp(block)
            for t in trades:
                wallets.add(t.wallet)
                rows.append((t.tx_hash, t.block, ts, exact, t.token, t.wallet, t.side, str(t.token_amount), t.quote_token, str(t.quote_amount), t.venue, json.dumps(t.swap_logs), "transfer_net", json.dumps(t.flags)))
        if rows:
            store.db.executemany("INSERT OR IGNORE INTO trades(tx_hash,block,ts,ts_exact,token,wallet,side,token_amount,quote_token,quote_amount,venue,swap_logs,attribution,flags) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            store.db.executemany("INSERT INTO wallets(address,is_contract,trades) VALUES(?,NULL,1) ON CONFLICT(address) DO UPDATE SET trades=trades+1", [(r[5],) for r in rows])
        # Incremental pairing: only buys that arrived in this range can create new sequences (a new sell has no
        # later buy yet), and the grade of an older sequence never changes because time only moves forward.
        n_seq = 0
        new_buy_ids: dict[str, set[int]] = defaultdict(set)
        min_ts: dict[str, int] = {}
        for tx_hash, block, ts, _e, token, wallet, side, *_rest in rows:
            if side != "buy":
                continue
            r = store.db.execute("SELECT id FROM trades WHERE tx_hash=? AND token=? AND wallet=?", (tx_hash, token, wallet)).fetchone()
            if r:
                new_buy_ids[wallet].add(r[0])
                min_ts[wallet] = min(min_ts.get(wallet, ts), ts)
        for w, ids in new_buy_ids.items():
            lo = min_ts[w] - self.window_s - 5
            trades = [T(r[0], r[1], r[2], r[3], r[4], r[5]) for r in store.db.execute("SELECT id, tx_hash, block, ts, token, side FROM trades WHERE wallet=? AND ts>=?", (w, lo))]
            seqs = [s for s in sequences_for_wallet(trades, self.window_s) if s["buy_trade"] in ids]
            store.db.executemany(
                "INSERT OR IGNORE INTO sequences(window_s,wallet,sell_token,buy_token,sell_trade,buy_trade,sell_ts,buy_ts,gap_s,grade,candidates) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                [(self.window_s, w, s["sell_token"], s["buy_token"], s["sell_trade"], s["buy_trade"], s["sell_ts"], s["buy_ts"], s["gap_s"], s["grade"], s["candidates"]) for s in seqs],
            )
            n_seq += len(seqs)
        store.commit()
        return len(rows), n_seq
