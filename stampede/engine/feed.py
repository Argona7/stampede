"""Realtime feed: free `eth_subscribe` websockets of Robinhood Chain assembled into complete blocks.

Two connections to the same endpoint (measured 2026-09-12, docs/ENGINE-PERF.md): a *fast* one carrying `newHeads`
and the PONS-specific log subscriptions (curve events by topic, v4 `Swap` on the PoolManager, factory and hook events;
~7 frames per block, delivered within a few ms of the head) and a *bulk* one carrying every ERC-20 `Transfer` of the
chain (~30 frames per block, trailing the head by 30-300 ms because the node writes one frame per log). A block is
released in strict order when its header is in, the fast side has moved past it (or the header's bloom filter rules
out PONS activity) and - only if the block has a PONS swap - the transfer stream has moved past it; otherwise after a
grace (fast side 250 ms) / cap (transfers 1 s). Transactions whose transfers were still incomplete at the cap are
deferred and re-emitted as a late fragment when their transfers complete, so a trade is never attributed from a
partial transfer set. Gaps in the head sequence, blocks whose header never came and the blocks around a reconnect
are backfilled over HTTP (Alchemy 10-block `eth_getLogs`) with the same topics; the pending queue holds later blocks
until the gap is filled (or a timeout marks it unfilled).
"""
from __future__ import annotations

import asyncio
import json
import queue
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from eth_hash.auto import keccak

from .. import chain
from ..env import redact
from .bus import Histogram

ENDPOINTS = ("wss://robinhood-rpc.publicnode.com", "wss://robinhood.api.pocket.network")
GRACE_S = 0.25  # fast side: release without the (sparse) PONS logs boundary after this
BULK_CAP_S = 1.0  # transfer side: never hold a block longer than this; incomplete swap txs are deferred
HEADLESS_S = 1.5  # logs for a block whose header never arrived -> fetch the block over HTTP
GAP_FILL_TIMEOUT_S = 60.0
LATE_SETTLE_S = 0.1  # late logs for an already released block are grouped for this long
LATE_DEADLINE_S = 5.0  # a deferred transaction still without its transfers after this is fetched over HTTP instead
MAX_CATCHUP_BLOCKS = 600  # ~1 min of chain (60 Alchemy calls); a longer pause is recorded as skipped, not backfilled at start
BACKFILL_CHUNK = 50  # blocks per backfill round trip: the oldest blocks of a gap are released first
MAX_FRAME = 16 * 1024 * 1024

CURVE_TOPICS = [chain.T_CURVE_BUY, chain.T_CURVE_SELL, chain.T_SNIPE_TAX, chain.T_CURVE_REFUND, chain.T_CURVE_COMPLETED]
FACTORY_TOPICS = [chain.T_TOKEN_LAUNCHED, chain.T_LAUNCH_SWEPT, chain.T_POOL_GRADUATED]
HOOK_TOPICS = [chain.T_POOL_REGISTERED, chain.T_HOOK_FEE]
FAST_SUBSCRIPTIONS: dict[str, list[Any]] = {
    "heads": ["newHeads"],
    "curve": ["logs", {"topics": [CURVE_TOPICS]}],
    "swap": ["logs", {"address": chain.V4_POOL_MANAGER, "topics": [[chain.T_V4_SWAP]]}],
    "factory": ["logs", {"address": chain.PONS_V2_FACTORY, "topics": [FACTORY_TOPICS]}],
    "hook": ["logs", {"address": chain.PONS_V2_HOOK, "topics": [HOOK_TOPICS]}],
}
BULK_SUBSCRIPTIONS: dict[str, list[Any]] = {"transfer": ["logs", {"topics": [[chain.T_TRANSFER]]}]}
BACKFILL_TOPICS = [CURVE_TOPICS + [chain.T_V4_SWAP] + FACTORY_TOPICS + HOOK_TOPICS + [chain.T_TRANSFER]]
SWAP_KINDS = ("curve_buy", "curve_sell", "v4_swap")


# ---- frames -> dicts ---------------------------------------------------------------------------------------------
def accept_log(kind: str, address: str) -> bool:
    """The subscription filters, applied uniformly to live and backfilled logs."""
    if kind in ("curve_buy", "curve_sell", "snipe_tax", "curve_refund", "curve_completed", "transfer"):
        return True
    if kind == "v4_swap":
        return address == chain.V4_POOL_MANAGER
    if kind in ("token_launched", "launch_swept", "pool_graduated"):
        return address == chain.PONS_V2_FACTORY
    if kind in ("pool_registered", "hook_fee"):
        return address == chain.PONS_V2_HOOK
    return False


def parse_log(r: dict[str, Any]) -> dict[str, Any] | None:
    """JSON-RPC log object -> the dict `normalize.trades_from_tx` expects (plus block / tx / tx_index / removed)."""
    tp = r.get("topics") or []
    t0 = tp[0].lower() if tp else None
    kind = chain.KIND_BY_TOPIC.get(t0 or "", "other")
    address = (r.get("address") or "").lower()
    if not accept_log(kind, address):
        return None
    return {
        "log_index": int(r["logIndex"], 16),
        "address": address,
        "topic0": t0,
        "topic1": tp[1].lower() if len(tp) > 1 else None,
        "topic2": tp[2].lower() if len(tp) > 2 else None,
        "topic3": tp[3].lower() if len(tp) > 3 else None,
        "data": r.get("data") or "0x",
        "kind": kind,
        "block": int(r["blockNumber"], 16),
        "tx_hash": (r.get("transactionHash") or "").lower(),
        "tx_index": int(r.get("transactionIndex") or "0x0", 16),
        "removed": bool(r.get("removed")),
    }


# ---- bloom filter of the header -------------------------------------------------------------------------------------
def bloom_mask(item: bytes) -> int:
    """The three bits Ethereum sets in the 2048-bit logs bloom for `item` (an address or a topic)."""
    h = keccak(item)
    m = 0
    for i in (0, 2, 4):
        m |= 1 << (((h[i] << 8) | h[i + 1]) & 0x7FF)
    return m


def _hex_bytes(h: str) -> bytes:
    return bytes.fromhex(h[2:] if h.startswith("0x") else h)


class PonsBloom:
    """Answers 'can this header's bloom contain any log the engine subscribes to?' (no false negatives)."""

    def __init__(self, pool_ids: Iterable[str] = ()):
        self.curve_masks = [bloom_mask(_hex_bytes(t)) for t in CURVE_TOPICS]
        self.factory = bloom_mask(_hex_bytes(chain.PONS_V2_FACTORY))
        self.factory_masks = [bloom_mask(_hex_bytes(t)) for t in FACTORY_TOPICS]
        self.hook = bloom_mask(_hex_bytes(chain.PONS_V2_HOOK))
        self.hook_masks = [bloom_mask(_hex_bytes(t)) for t in HOOK_TOPICS]
        self.pm = bloom_mask(_hex_bytes(chain.V4_POOL_MANAGER))
        self.swap = bloom_mask(_hex_bytes(chain.T_V4_SWAP))
        self.pool_masks: dict[str, int] = {}
        for p in pool_ids:
            self.add_pool(p)

    def add_pool(self, pool_id: str) -> None:
        if pool_id and pool_id not in self.pool_masks:
            try:
                self.pool_masks[pool_id] = bloom_mask(_hex_bytes(pool_id))
            except ValueError:
                pass

    def expect_fast(self, bloom_hex: str | None) -> bool:
        if not bloom_hex:
            return True
        try:
            b = int(bloom_hex, 16)
        except ValueError:
            return True
        if b == 0:
            return False
        if any((b & m) == m for m in self.curve_masks):
            return True
        if (b & self.factory) == self.factory and any((b & m) == m for m in self.factory_masks):
            return True
        if (b & self.hook) == self.hook and any((b & m) == m for m in self.hook_masks):
            return True
        if (b & self.pm) == self.pm and (b & self.swap) == self.swap:
            if not self.pool_masks:
                return True
            for m in self.pool_masks.values():
                if (b & m) == m:
                    return True
        return False


# ---- block assembly -------------------------------------------------------------------------------------------------
@dataclass
class Block:
    number: int
    ts: int | None
    hash: str | None
    logs: list[dict[str, Any]]  # accepted logs sorted by log_index (a fragment carries only its own logs)
    t_first_seen: float
    t_head_received: float | None
    t_logs_complete: float
    source: str  # live | backfill | late | unfilled
    flags: set[str] = field(default_factory=set)
    n_fast: int = 0
    n_bulk: int = 0
    deferred_txs: int = 0

    @property
    def is_fragment(self) -> bool:
        return self.source == "late"


@dataclass
class PendingBlock:
    number: int
    t_first: float
    source: str = "live"
    t_head: float | None = None
    ts: int | None = None
    hash: str | None = None
    bloom: str | None = None
    expect_fast: bool | None = None
    logs: dict[tuple[str, int], dict[str, Any]] = field(default_factory=dict)
    n_fast: int = 0
    n_bulk: int = 0
    pons_swap: bool = False
    swap_txs: dict[str, int] = field(default_factory=dict)  # tx -> tx_index
    bulk_max_txi: int = -1
    filled: bool = False
    headless_requested: bool = False
    flags: set[str] = field(default_factory=set)
    deferred_txs: int = 0


@dataclass
class _Late:
    """Logs for a block that was already released: deferred swap transactions, stragglers, or a backfill in flight."""

    number: int
    ts: int | None
    hash: str | None
    t_created: float
    t_last: float
    logs: dict[tuple[str, int], dict[str, Any]] = field(default_factory=dict)
    deferred: bool = False
    wait_backfill: bool = False
    filled: bool = False
    txs: set[str] = field(default_factory=set)


class BlockAssembler:
    """Pure block assembly (no network): feed it heads and logs with arrival times, poll for released blocks."""

    def __init__(
        self,
        pons_pool: Callable[[str], bool] | None = None,
        bloom: PonsBloom | None = None,
        grace_s: float = GRACE_S,
        bulk_cap_s: float = BULK_CAP_S,
        headless_s: float = HEADLESS_S,
        gap_timeout_s: float = GAP_FILL_TIMEOUT_S,
        late_settle_s: float = LATE_SETTLE_S,
        late_deadline_s: float = LATE_DEADLINE_S,
    ):
        self.pons_pool = pons_pool or (lambda pid: False)
        self.bloom = bloom
        self.grace_s, self.bulk_cap_s, self.headless_s = grace_s, bulk_cap_s, headless_s
        self.gap_timeout_s, self.late_settle_s, self.late_deadline_s = gap_timeout_s, late_settle_s, late_deadline_s
        self.pending: dict[int, PendingBlock] = {}
        self.late: dict[int, _Late] = {}
        self.fast_max = -1
        self.bulk_max = -1
        self.last_head: int | None = None
        self.next_release: int | None = None
        self.pools_seen: set[str] = set()
        self.released_swap_txs: dict[int, set[str]] = {}  # last released blocks -> their swap transactions (late-transfer filter)
        self.orphans: dict[int, dict[str, dict[tuple[str, int], dict[str, Any]]]] = {}  # late transfers of transactions without a swap yet
        self.gap_requests: list[tuple[int, int]] = []
        self.gaps: list[dict[str, Any]] = []
        self.stats: Counter = Counter()
        self._reset: dict[str, bool] = {}

    # ---- input ----
    def seed_start(self, start_block: int) -> None:
        """Release from `start_block` on: the first head registers [start_block, head-1] as a gap to backfill."""
        self.last_head = start_block - 1
        self.next_release = start_block

    def on_connected(self, role: str) -> None:
        """A connection was (re)established: the blocks between the last one seen on it and the first new one are suspect
        (for the transfer side also at the very first connect, which lands a few hundred ms after the fast side)."""
        if role == "bulk" or self.last_head is not None:
            self._reset[role] = True

    def _check_reset(self, role: str, n: int, t: float) -> None:
        if not self._reset.get(role):
            return
        self._reset[role] = False
        if role == "bulk":
            lo = self.bulk_max if self.bulk_max >= 0 else (self.next_release if self.next_release is not None else (min(self.pending) if self.pending else n))
            hi = n - 1
        else:
            lo = hi = self.last_head if self.last_head is not None else -1  # fast: missing heads are handled by on_head; only the partial block is suspect
        if lo is None or lo < 0:
            return
        if lo <= hi and hi - lo < 20000:
            self._register_gap(lo, hi, f"{role} (re)connect", t)

    def on_head(self, hdr: dict[str, Any], t: float) -> None:
        n = int(hdr["number"], 16)
        if self.last_head is not None and n <= self.last_head:
            self.stats["head_dup"] += 1
            return
        self._check_reset("fast", n, t)
        if self.last_head is not None and n > self.last_head + 1:
            self._register_gap(self.last_head + 1, n - 1, "missing heads", t)
        self.last_head = n
        if n > self.fast_max:
            self.fast_max = n
        pb = self.pending.get(n)
        if pb is None:
            pb = self.pending[n] = PendingBlock(n, t)
        pb.t_head = t
        pb.ts = int(hdr["timestamp"], 16)
        pb.hash = hdr.get("hash")
        pb.bloom = hdr.get("logsBloom")
        if self.next_release is None:
            self.next_release = n
            for k in [k for k in self.pending if k < n]:  # tails of blocks that predate the first head
                self.stats["pre_start_logs"] += len(self.pending.pop(k).logs)

    def on_log(self, log: dict[str, Any], conn: str, t: float) -> None:
        n = log["block"]
        if log.get("removed"):
            self.stats["removed_logs"] += 1
            return
        self._check_reset(conn, n, t)
        if log["kind"] == "pool_registered" and log.get("topic1"):
            self.pools_seen.add(log["topic1"])
            if self.bloom:
                self.bloom.add_pool(log["topic1"])
        if conn == "bulk" and n > self.bulk_max:
            self.bulk_max = n
        if self.next_release is not None and n < self.next_release:
            tx = log["tx_hash"]
            if conn == "bulk" and tx not in self.released_swap_txs.get(n, ()) and n not in self.late:
                # a straggling transfer of a transaction without a known PONS swap: parked, in case its swap log is late too
                self.stats["late_transfers_parked"] += 1
                self.orphans.setdefault(n, {}).setdefault(tx, {})[(tx, log["log_index"])] = log
                for k in [k for k in self.orphans if k < n - 300]:
                    del self.orphans[k]
                return
            self.stats["late_logs"] += 1
            lb = self._late(n, None, None, t)
            lb.logs.setdefault((tx, log["log_index"]), log)
            parked = self.orphans.get(n, {}).pop(tx, None)
            if parked:
                lb.logs.update(parked)
                self.stats["late_transfers_rejoined"] += len(parked)
            lb.t_last = t
            return
        pb = self.pending.get(n)
        if pb is None:
            pb = self.pending[n] = PendingBlock(n, t)
        key = (log["tx_hash"], log["log_index"])
        if key in pb.logs:
            self.stats["dup_logs"] += 1
            return
        pb.logs[key] = log
        if conn == "bulk":
            pb.n_bulk += 1
            if log["tx_index"] > pb.bulk_max_txi:
                pb.bulk_max_txi = log["tx_index"]
        else:
            pb.n_fast += 1
            if n > self.fast_max:
                self.fast_max = n
            k = log["kind"]
            if k in ("curve_buy", "curve_sell") or (k == "v4_swap" and ((log.get("topic1") or "") in self.pools_seen or self.pons_pool(log.get("topic1") or ""))):
                pb.pons_swap = True
                pb.swap_txs[log["tx_hash"]] = log["tx_index"]

    def on_backfill(self, number: int, ts: int | None, hash_: str | None, logs: list[dict[str, Any]], t: float) -> None:
        if self.next_release is not None and number < self.next_release:
            # released meanwhile: deliver as a late fragment rather than losing the trades
            lb = self._late(number, ts, hash_, t)
            for log in logs:
                lb.logs.setdefault((log["tx_hash"], log["log_index"]), log)
            lb.ts = ts if ts is not None else lb.ts
            lb.t_last = t
            lb.filled = True
            self.stats["backfill_late"] += 1
            return
        pb = self.pending.get(number)
        if pb is None:
            pb = self.pending[number] = PendingBlock(number, t, source="gap")
        pb.source = "gap"
        pb.ts = ts if ts is not None else pb.ts
        pb.hash = hash_ or pb.hash
        if pb.t_head is None:
            pb.t_head = t
        for log in logs:
            pb.logs.setdefault((log["tx_hash"], log["log_index"]), log)
        pb.filled = True

    def take_gap_requests(self) -> list[tuple[int, int]]:
        out, self.gap_requests = self.gap_requests, []
        return out

    # ---- output ----
    def poll(self, now: float) -> list[Block]:
        out: list[Block] = []
        while self.next_release is not None:
            n = self.next_release
            pb = self.pending.get(n)
            if pb is None:
                break
            if pb.source == "gap":
                if pb.filled:
                    out.append(self._release(pb, now, "backfill"))
                    continue
                if now - pb.t_first >= self.gap_timeout_s:
                    pb.flags.add("unfilled")
                    self.stats["gap_unfilled_blocks"] += 1
                    out.append(self._release(pb, now, "unfilled"))
                    continue
                break
            if pb.t_head is None:
                if now - pb.t_first >= self.headless_s and not pb.headless_requested:
                    pb.headless_requested = True
                    self._register_gap(n, n, "header never arrived", now)
                break
            if pb.expect_fast is None:
                pb.expect_fast = self.bloom.expect_fast(pb.bloom) if (self.bloom is not None and pb.bloom) else True
            age = now - pb.t_head
            fast_done = self.fast_max > n or (pb.n_fast == 0 and not pb.expect_fast)
            if not fast_done:
                if age < self.grace_s:
                    break
                pb.flags.add("fast_grace")
                self.stats["fast_grace"] += 1
            bulk_done = (not pb.pons_swap) or self.bulk_max > n
            if not bulk_done:
                if age < self.bulk_cap_s:
                    break
                pb.flags.add("bulk_cap")
                self.stats["bulk_cap"] += 1
                self._defer_incomplete(pb, now)
            if pb.n_fast == 0 and not pb.expect_fast:
                pb.flags.add("bloom_skip")
                self.stats["bloom_skip"] += 1
            out.append(self._release(pb, now, "live"))
        out.extend(self._poll_late(now))
        return out

    # ---- internals ----
    def _late(self, n: int, ts: int | None, hash_: str | None, t: float) -> _Late:
        lb = self.late.get(n)
        if lb is None:
            lb = self.late[n] = _Late(n, ts, hash_, t, t)
        return lb

    def _register_gap(self, lo: int, hi: int, reason: str, t: float) -> None:
        for n in range(lo, hi + 1):
            if self.next_release is not None and n < self.next_release:
                lb = self._late(n, None, None, t)
                lb.wait_backfill, lb.deferred, lb.t_created = True, False, t
                continue
            pb = self.pending.get(n)
            if pb is None:
                pb = self.pending[n] = PendingBlock(n, t, source="gap")
            pb.source = "gap"
        self.gap_requests.append((lo, hi))
        self.gaps.append({"from": lo, "to": hi, "blocks": hi - lo + 1, "reason": reason, "detected_at": t})
        self.stats["gaps"] += 1
        self.stats["gap_blocks"] += hi - lo + 1

    def _defer_incomplete(self, pb: PendingBlock, now: float) -> None:
        """Swap transactions whose transfers may still be streaming stay behind until the transfer side passes the block."""
        if self.bulk_max < pb.number:
            incomplete = set(pb.swap_txs)
        else:
            incomplete = {tx for tx, txi in pb.swap_txs.items() if txi >= pb.bulk_max_txi}
        if not incomplete:
            return
        lb = self._late(pb.number, pb.ts, pb.hash, now)
        lb.deferred = True
        lb.txs |= incomplete
        for key in [k for k in pb.logs if k[0] in incomplete]:
            lb.logs[key] = pb.logs.pop(key)
        pb.deferred_txs = len(incomplete)
        self.stats["deferred_txs"] += len(incomplete)

    def _release(self, pb: PendingBlock, now: float, source: str) -> Block:
        self.pending.pop(pb.number, None)
        self.next_release = pb.number + 1
        self.stats["released"] += 1
        self.stats[f"released_{source}"] += 1
        self.released_swap_txs[pb.number] = set(pb.swap_txs) | {l["tx_hash"] for l in pb.logs.values() if l["kind"] in SWAP_KINDS}
        for k in [k for k in self.released_swap_txs if k < pb.number - 200]:
            del self.released_swap_txs[k]
        logs = sorted(pb.logs.values(), key=lambda l: l["log_index"])
        return Block(pb.number, pb.ts, pb.hash, logs, pb.t_first, pb.t_head, now, source, set(pb.flags), pb.n_fast, pb.n_bulk, pb.deferred_txs)

    def _poll_late(self, now: float) -> list[Block]:
        out: list[Block] = []
        for n in sorted(self.late):
            lb = self.late[n]
            if lb.wait_backfill:
                ready = lb.filled or now - lb.t_created >= self.gap_timeout_s
                flags = {"backfill"} | (set() if lb.filled else {"unfilled"})
            elif lb.deferred:
                ready = self.bulk_max > n
                flags = {"deferred"}
                if not ready and now - lb.t_created >= self.late_deadline_s:
                    # the transfer stream never passed this block: fetch it over HTTP rather than attribute from a partial set
                    self.stats["deferred_to_backfill"] += 1
                    self._register_gap(n, n, "deferred transactions past deadline", now)
                    continue
            else:
                ready = now - lb.t_last >= self.late_settle_s
                flags = {"late"}
            if not ready:
                continue
            del self.late[n]
            if not lb.logs:
                continue
            self.stats["fragments"] += 1
            logs = sorted(lb.logs.values(), key=lambda l: l["log_index"])
            out.append(Block(n, lb.ts, lb.hash, logs, lb.t_created, None, now, "late", flags, 0, 0, 0))
        return out


# ---- the websocket client -------------------------------------------------------------------------------------------
@dataclass
class ConnMetrics:
    endpoint: str | None = None
    connected: bool = False
    connects: int = 0
    failovers: int = 0
    errors: int = 0
    frames: int = 0
    bytes: int = 0
    logs: int = 0
    heads: int = 0
    last_frame_t: float | None = None
    last_error: str | None = None
    connected_since: float | None = None
    subscriptions: dict[str, str] = field(default_factory=dict)
    rtt_ms: float | None = None

    def snapshot(self, now: float) -> dict[str, Any]:
        return {
            "endpoint": self.endpoint,
            "connected": self.connected,
            "connects": self.connects,
            "reconnects": max(0, self.connects - 1),
            "failovers": self.failovers,
            "errors": self.errors,
            "frames": self.frames,
            "mb": round(self.bytes / 1e6, 2),
            "logs": self.logs,
            "heads": self.heads,
            "last_frame_age_s": round(now - self.last_frame_t, 2) if self.last_frame_t else None,
            "connected_for_s": round(now - self.connected_since) if (self.connected and self.connected_since) else None,
            "last_error": self.last_error,
            "subscriptions": dict(self.subscriptions),
            "ping_rtt_ms": self.rtt_ms,
        }


BackfillFn = Callable[[int, int], dict[int, tuple[int | None, str | None, list[dict[str, Any]]]]]


class Feed:
    """Runs the two websocket connections and the assembler on its own asyncio loop (thread `engine-feed`);
    released blocks go to `out` (a thread-safe queue) for the processor."""

    def __init__(
        self,
        out: queue.Queue,
        assembler: BlockAssembler,
        backfill_fn: BackfillFn | None = None,
        endpoints: Iterable[str] = ENDPOINTS,
        start_from: int | None = None,
        max_catchup: int = MAX_CATCHUP_BLOCKS,
        stable_after_s: float = 30.0,
    ):
        self.out = out
        self.asm = assembler
        self.backfill_fn = backfill_fn
        self.endpoints = list(endpoints)
        self.start_from = start_from
        self.max_catchup = max_catchup
        self.stable_after_s = stable_after_s
        self.metrics = {"fast": ConnMetrics(), "bulk": ConnMetrics()}
        self.head_gap_ms = Histogram()
        self.backfill_ms = Histogram()
        self.first_head: int | None = None
        self.skipped_at_start: list[int] | None = None
        self.gap_fills: Counter = Counter()
        self._last_head_t: float | None = None
        self._stop = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._gap_q: asyncio.Queue | None = None
        self.errors: list[str] = []

    # ---- lifecycle ----
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="engine-feed", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        try:
            asyncio.run(self._main())
        except Exception as e:  # noqa: BLE001
            self.errors.append(f"feed loop died: {redact(str(e))[:200]}")

    async def _main(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._gap_q = asyncio.Queue()
        tasks = [
            asyncio.create_task(self._conn("fast", FAST_SUBSCRIPTIONS)),
            asyncio.create_task(self._conn("bulk", BULK_SUBSCRIPTIONS)),
            asyncio.create_task(self._ticker()),
            asyncio.create_task(self._gap_worker()),
        ]
        while not self._stop.is_set():
            await asyncio.sleep(0.2)
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    # ---- connections ----
    async def _conn(self, role: str, subs: dict[str, list[Any]]) -> None:
        from websockets.asyncio.client import connect

        m = self.metrics[role]
        fails = 0
        while not self._stop.is_set():
            url = self.endpoints[fails % len(self.endpoints)]
            if m.endpoint and url != m.endpoint:
                m.failovers += 1
            m.endpoint = url
            t_open = time.time()
            try:
                async with connect(url, max_size=MAX_FRAME, ping_interval=15, ping_timeout=15, open_timeout=10, close_timeout=2, max_queue=4096) as ws:
                    names = await self._subscribe(ws, subs)
                    m.subscriptions = {v: k for k, v in names.items()}
                    m.connected = True
                    m.connects += 1
                    m.connected_since = time.time()
                    self.asm.on_connected(role)
                    await self._recv(ws, role, names, m)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - transport: reconnect with backoff, alternate endpoints
                m.errors += 1
                m.last_error = f"{type(e).__name__}: {redact(str(e))[:140]}"
                self.errors.append(f"{time.strftime('%H:%M:%S')} {role} {url}: {m.last_error}")
                del self.errors[:-20]
            m.connected = False
            m.subscriptions = {}
            if self._stop.is_set():
                break
            fails = 0 if time.time() - t_open >= self.stable_after_s else fails + 1
            await asyncio.sleep(min(15.0, 0.25 * (2**fails)) if fails else 0.05)

    async def _subscribe(self, ws, subs: dict[str, list[Any]]) -> dict[str, str]:
        ids = {}
        for i, (name, params) in enumerate(subs.items(), 1):
            await ws.send(json.dumps({"jsonrpc": "2.0", "id": i, "method": "eth_subscribe", "params": params}))
            ids[i] = name
        names: dict[str, str] = {}
        deadline = time.time() + 10
        while ids and time.time() < deadline:
            raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, deadline - time.time()))
            msg = json.loads(raw)
            if "id" in msg and msg["id"] in ids:
                name = ids.pop(msg["id"])
                if "result" not in msg:
                    raise RuntimeError(f"eth_subscribe {name} rejected: {msg.get('error')}")
                names[msg["result"]] = name
        if ids:
            raise RuntimeError(f"eth_subscribe timed out for {sorted(ids.values())}")
        return names

    async def _recv(self, ws, role: str, names: dict[str, str], m: ConnMetrics) -> None:
        silent_s = 5.0
        while not self._stop.is_set():
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=silent_s)
            except asyncio.TimeoutError:
                raise RuntimeError(f"no frames for {silent_s:g} s (stale connection)") from None
            t = time.time()
            m.frames += 1
            m.bytes += len(raw)
            m.last_frame_t = t
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if msg.get("method") != "eth_subscription":
                continue
            p = msg.get("params") or {}
            r = p.get("result")
            name = names.get(p.get("subscription"))
            if not isinstance(r, dict) or name is None:
                continue
            if name == "heads":
                m.heads += 1
                self._on_head(r, t)
            else:
                log = parse_log(r)
                if log is not None:
                    m.logs += 1
                    self.asm.on_log(log, role, t)
            lat = getattr(ws, "latency", None)
            if lat:
                m.rtt_ms = round(lat * 1000, 1)
            self._drain(t)

    def _on_head(self, r: dict[str, Any], t: float) -> None:
        if self.first_head is None:
            n = int(r["number"], 16)
            self.first_head = n
            if self.start_from is not None and self.start_from < n:
                if n - self.start_from <= self.max_catchup:
                    self.asm.seed_start(self.start_from)
                else:
                    self.skipped_at_start = [self.start_from, n - 1]
        if self._last_head_t is not None:
            self.head_gap_ms.add((t - self._last_head_t) * 1000)
        self._last_head_t = t
        self.asm.on_head(r, t)

    # ---- release + gaps ----
    def _drain(self, now: float) -> None:
        for b in self.asm.poll(now):
            self.out.put(b)
        for lo, hi in self.asm.take_gap_requests():
            if self._gap_q is not None:
                self._gap_q.put_nowait((lo, hi))

    async def _ticker(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(0.02)
            self._drain(time.time())

    async def _gap_worker(self) -> None:
        assert self._gap_q is not None
        loop = asyncio.get_running_loop()
        while not self._stop.is_set():
            lo, hi = await self._gap_q.get()
            self.gap_fills["requests"] += 1
            if self.backfill_fn is None:
                self.gap_fills["no_backfill_fn"] += 1
                continue
            t0 = time.time()
            filled = 0
            for c_lo in range(lo, hi + 1, BACKFILL_CHUNK):  # oldest chunk first: the strict-order queue moves as soon as it lands
                c_hi = min(c_lo + BACKFILL_CHUNK - 1, hi)
                got: dict[int, tuple[int | None, str | None, list[dict[str, Any]]]] = {}
                for attempt in range(3):
                    try:
                        got = await loop.run_in_executor(None, self.backfill_fn, c_lo, c_hi)
                    except Exception as e:  # noqa: BLE001
                        self.gap_fills["errors"] += 1
                        self.errors.append(f"{time.strftime('%H:%M:%S')} backfill {c_lo}-{c_hi}: {redact(str(e))[:160]}")
                        del self.errors[:-20]
                        got = {}
                    if len(got) >= c_hi - c_lo + 1:
                        break
                    await asyncio.sleep(1.0 * (attempt + 1))
                t = time.time()
                for n in sorted(got):
                    ts, h, logs = got[n]
                    self.asm.on_backfill(n, ts, h, logs, t)
                filled += len(got)
                self.gap_fills["blocks_filled"] += len(got)
                self.gap_fills["blocks_missing"] += max(0, (c_hi - c_lo + 1) - len(got))
                self._drain(t)
                if self._stop.is_set():
                    break
            t = time.time()
            self.backfill_ms.add((t - t0) * 1000)
            for g in self.asm.gaps:
                if g["from"] == lo and g["to"] == hi and "filled_blocks" not in g:
                    g["filled_blocks"] = filled
                    g["fill_ms"] = round((t - t0) * 1000)

    # ---- status ----
    def snapshot(self) -> dict[str, Any]:
        now = time.time()
        return {
            "connections": {role: m.snapshot(now) for role, m in self.metrics.items()},
            "reconnects": sum(max(0, m.connects - 1) for m in self.metrics.values()),
            "failovers": sum(m.failovers for m in self.metrics.values()),
            "first_head": self.first_head,
            "last_head": self.asm.last_head,
            "last_head_age_s": round(now - self._last_head_t, 2) if self._last_head_t else None,
            "head_gap_ms": self.head_gap_ms.summary(),
            "pending_blocks": len(self.asm.pending),
            "late_buckets": len(self.asm.late),
            "assembler": dict(self.asm.stats),
            "gaps": self.asm.gaps[-20:],
            "gap_fills": dict(self.gap_fills),
            "backfill_ms": self.backfill_ms.summary(),
            "skipped_at_start": self.skipped_at_start,
            "errors": self.errors[-8:],
        }


def make_backfill_fn(rpc) -> BackfillFn:
    """Alchemy HTTP path for gaps: headers + `eth_getLogs` (10-block sub-ranges in parallel) with the feed's topics."""

    def backfill(lo: int, hi: int) -> dict[int, tuple[int | None, str | None, list[dict[str, Any]]]]:
        hdrs = rpc.get_blocks(list(range(lo, hi + 1)))
        logs, failed = rpc.get_logs_parallel(lo, hi, topics=BACKFILL_TOPICS, workers=4)
        still: list[tuple[int, int, str]] = []
        for f_lo, f_hi, err in failed:
            try:
                logs.extend(rpc.get_logs(f_lo, f_hi, topics=BACKFILL_TOPICS))
            except Exception:  # noqa: BLE001
                still.append((f_lo, f_hi, err))
        bad = {n for f_lo, f_hi, _ in still for n in range(f_lo, f_hi + 1)}
        by_block: dict[int, list[dict[str, Any]]] = {}
        for raw in logs:
            l = parse_log(raw)
            if l is not None:
                by_block.setdefault(l["block"], []).append(l)
        out: dict[int, tuple[int | None, str | None, list[dict[str, Any]]]] = {}
        for n in range(lo, hi + 1):
            if n in bad or n not in hdrs:
                continue
            h = hdrs[n]
            out[n] = (int(h["timestamp"], 16), h.get("hash"), by_block.get(n, []))
        return out

    return backfill
