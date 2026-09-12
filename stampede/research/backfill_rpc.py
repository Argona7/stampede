"""`stampede backfill-rpc`: the rows of `stampede backfill`, fetched from free public JSON-RPC endpoints instead of HyperSync.

Why a second path: the free HyperSync tier throttles a worker to ~3 requests/min after a burst (~55-90 blocks/s), so the
14-day range takes days on one token. Robinhood Chain has several keyless RPC endpoints (Nitro nodes) that answer
`eth_getLogs` over 5-30k-block spans and - because they run a recent go-ethereum - include `blockTimestamp` in every log,
so trade blocks get exact timestamps without header calls. Measurements per endpoint live in docs/BACKFILL.md.

Per chunk of blocks (default 10,000) the path does two `eth_getLogs`:

1. swap-class topics with no address filter: CurveBuy / CurveSell / SnipeTaxCharged / CurveBuyRefunded / HookFeeCollected /
   v4 Swap. Client-side it keeps HookFeeCollected of the PONS hook and Swap of the PoolManager whose poolId is a PONS pool
   of the store (`pools`), exactly what the HyperSync query selected server-side.
2. Transfer logs with `address` = the launched tokens traded in the chunk (curve -> token via `curves`, pool -> token via
   `pools`), then only the transfers whose transaction has a swap event. `trades_from_tx` reads nothing else.

Both are normalized with the same `trades_from_tx` + `Context` as the live and HyperSync paths and written with the same
`TRADE_INSERT`, so the segment stores merge unchanged. Differences to the HyperSync rows: `txs` stays empty (from/to are
not in getLogs; the API fetches them on demand) and a trade block whose logs lack `blockTimestamp` (never seen on the
endpoints measured) gets a header batch for every 5th block and `Interp` for the rest (`ts_exact` 0).

Endpoints form a pool with per-endpoint pacing, a penalty box on 429 / transport errors (Retry-After honoured, exponential
otherwise) and a per-endpoint span that halves on "range too large" / "response too large" / "timed out" answers. Chunks
are fetched by a few worker threads in block order; the store is written by the main thread, cursor `meta.backfill_cursor`
per chunk (same key as the HyperSync path, so either path resumes the other).
"""
from __future__ import annotations

import json
import sys
import threading
import time
from collections import Counter, defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

import requests

from .. import chain
from ..env import redact
from ..normalize import TRADE_INSERT, Context, Interp, build_context, trades_from_tx
from ..store import Store
from .backfill import BLOCKS_PER_SECOND, LIFECYCLE_LOOKBACK_DAYS, apply_lifecycle, group_by_tx, seed_infra

SWAP_CLASS_TOPICS = [chain.T_CURVE_BUY, chain.T_CURVE_SELL, chain.T_SNIPE_TAX, chain.T_CURVE_REFUND, chain.T_HOOK_FEE, chain.T_V4_SWAP]
LIFECYCLE_TOPICS = list(dict.fromkeys(chain.LIFECYCLE_TOPICS + [chain.T_SNIPE_TAX_EXEMPTED]))
FACTORY_HOOK_TOPICS = {chain.T_TOKEN_LAUNCHED, chain.T_POOL_REGISTERED, chain.T_LAUNCH_SWEPT, chain.T_POOL_GRADUATED}
MIN_SPAN = 250  # an endpoint whose span shrank below this is answering nothing useful; it is not split further
MIN_ADDRESSES = 50
HEADER_BATCH = 50
ANCHOR_EVERY = 5  # header fallback: fetch every 5th missing trade block, interpolate the rest (ts_exact 0)
MAX_ATTEMPTS = 60  # per sub-range, across endpoints; every attempt waits for the pool, so this is minutes, not a hot loop
SPAN_GROW_AFTER = 25  # clean answers in a row before a shrunken span is doubled again
GETLOGS_CU = 20.0  # compute units one eth_getLogs costs on a CU-metered endpoint (blockmachine: 12-15 calls exhaust 300; a 429 drains the bucket)
SOURCE = "rpc"

# Keyless endpoints measured on 2026-09-12 (docs/BACKFILL.md): pacing = seconds between requests, span = blocks per
# eth_getLogs the endpoint accepts, inflight = concurrent requests it tolerates, addresses = Transfer address list size.
KNOWN_ENDPOINTS: dict[str, dict[str, Any]] = {
    # 300 CU/min per IP (429 carries limit/remaining/reset/retry_after_ms); getLogs ~25 CU; responses above ~50 MB are refused
    "blockmachine": {"url": "https://rpc-robinhood.blockmachine.io", "pace": 0.5, "span": 10_000, "inflight": 2, "addresses": 1500, "cu": 300},
    # 4-11 s per 5k-block query, "-32005 the network is busy" under load, 10k spans refused
    "ordofi": {"url": "https://rpc.ordofi.network", "pace": 5.0, "span": 5_000, "inflight": 1, "addresses": 1500},
    # opt-in: 429 bursts below ~4 s pacing, 10k-log cap per answer (750 blocks in busy periods), 300 addresses per
    # Transfer query, blockTimestamp always 0x0. In a pool it multiplies the request count of a chunk (a 10k-block
    # transfer phase became 60+ calls) and starves the cursor; alone it is a slow but working fallback
    "official": {"url": chain.PUBLIC_RPC, "pace": 4.0, "span": 1_500, "inflight": 1, "addresses": 300},
    # opt-in: keyless tier allows 17 "heavy" calls (eth_getLogs) per day and 1 request per 10 s; with a free key it is
    # the best endpoint measured (30k-block spans, 148 MB answers, blockTimestamp present)
    "nodeflare": {"url": "https://rpc.nodeflare.app/robinhood/public", "pace": 10.2, "span": 30_000, "inflight": 1, "addresses": 2500},
    # opt-in: relay errors on most historical queries ("historical state is not available")
    "pocket": {"url": "https://robinhood.api.pocket.network", "pace": 1.0, "span": 1_000, "inflight": 1, "addresses": 300},
}
DEFAULT_ENDPOINTS = ["blockmachine", "ordofi"]


def _hex_int(v: Any) -> int:
    if v is None:
        return 0
    if isinstance(v, int):
        return v
    s = str(v)
    return int(s, 16) if s.startswith("0x") else int(s)


# ---- transport --------------------------------------------------------------------------------------------------------


class RpcFail(Exception):
    """One failed request, classified so the pool knows what to do: rate_limited (wait retry_after), range (split the
    query), forbidden (bench the endpoint for long), transport / rpc_error (short penalty, try another endpoint)."""

    def __init__(self, kind: str, message: str, retry_after: float | None = None):
        super().__init__(f"{kind}: {redact(message)[:160]}")
        self.kind = kind
        self.retry_after = retry_after


RANGE_MARKERS = ("block range", "exceeds", "limit of", "too large", "timed out", "timeout", "max results", "too many results", "query returned more", "response size")
RATE_MARKERS = ("rate limit", "rate_limit", "too many requests", "rate-limit")
TRANSIENT_MARKERS = ("busy", "try again", "temporarily", "not available", "internal error", "relay")


def classify_error(status: int, headers: dict[str, str], body: Any) -> RpcFail | None:
    """HTTP status + parsed JSON body -> RpcFail, or None when the body is a usable JSON-RPC result."""
    lower_headers = {k.lower(): v for k, v in (headers or {}).items()}
    retry_after = None
    if lower_headers.get("retry-after"):
        try:
            retry_after = float(lower_headers["retry-after"])
        except ValueError:
            retry_after = None
    err = body.get("error") if isinstance(body, dict) else None
    if err is not None:
        code = err.get("code") if isinstance(err, dict) else None
        msg = str(err.get("message", err) if isinstance(err, dict) else err)
        data = err.get("data") if isinstance(err, dict) else None
        low = msg.lower()
        if isinstance(data, dict) and data.get("retry_after_ms"):
            retry_after = float(data["retry_after_ms"]) / 1000.0
        if status == 429 or code in (429, -32029) or any(m in low for m in RATE_MARKERS):
            return RpcFail("rate_limited", msg, retry_after)
        if (isinstance(data, dict) and "max_blocks" in data) or any(m in low for m in RANGE_MARKERS):
            return RpcFail("range", msg)
        if any(m in low for m in TRANSIENT_MARKERS):
            return RpcFail("transport", msg)
        return RpcFail("rpc_error", msg)
    if status == 429:
        return RpcFail("rate_limited", "HTTP 429", retry_after)
    if status in (401, 403):
        return RpcFail("forbidden", f"HTTP {status}")
    if status >= 400:
        return RpcFail("transport", f"HTTP {status}")
    if isinstance(body, dict) and "result" in body:
        return None
    if isinstance(body, list):
        return None
    return RpcFail("transport", "no result in response")


def http_transport(url: str, payload: Any, timeout: float) -> tuple[int, dict[str, str], Any]:
    """POST one JSON-RPC payload. Returns (status, headers, parsed json or None). Thread-local sessions."""
    sess = getattr(_local, "sess", None)
    if sess is None:
        sess = requests.Session()
        sess.headers.update({"content-type": "application/json", "user-agent": "stampede/0.1 backfill-rpc"})
        _local.sess = sess
    r = sess.post(url, data=json.dumps(payload), timeout=timeout)
    try:
        body = r.json()
    except ValueError:  # Cloudflare challenge / HTML error page
        body = None
    return r.status_code, dict(r.headers), body


_local = threading.local()


# ---- endpoint pool ----------------------------------------------------------------------------------------------------


class SharedPacer:
    """Cross-process pacing per endpoint: the keyless endpoints meter the caller's IP, so three segment workers on one
    machine share every quota. A small JSON file per host under `directory` holds the next allowed request time and a
    compute-unit token bucket; `fcntl.flock` serializes access. Absent or unwritable directory -> no shared pacing."""

    def __init__(self, directory: str | Path | None = "/tmp/stampede-rpc-pacer", clock=time.time, sleep=time.sleep):
        self.dir = Path(directory) if directory else None
        self.clock = clock
        self.sleep = sleep
        if self.dir is not None:
            try:
                self.dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                self.dir = None

    def _path(self, ep: "Endpoint") -> Path:
        host = ep.url.split("//", 1)[-1].split("/", 1)[0].replace(":", "_")
        return self.dir / f"{host}.json"  # type: ignore[operator]

    def _update(self, ep: "Endpoint", fn) -> float:
        """Run fn(state, now) -> wait_seconds under the file lock; the state dict is written back."""
        import fcntl

        p = self._path(ep)
        with open(p, "a+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.seek(0)
                raw = f.read()
                state = json.loads(raw) if raw.strip() else {}
                wait = fn(state, self.clock())
                f.seek(0)
                f.truncate()
                f.write(json.dumps(state))
                f.flush()
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
        return wait

    def wait_turn(self, ep: "Endpoint", cost: float = 0.0) -> None:
        """Block until the endpoint's shared pace allows one more request (and the CU bucket holds `cost`)."""
        if self.dir is None:
            return
        while True:

            def step(state: dict, now: float) -> float:
                next_at = float(state.get("next_at", 0.0))
                tokens = None
                if ep.cu_per_minute:
                    t_prev = float(state.get("t", now))
                    tokens = min(float(ep.cu_per_minute), float(state.get("tokens", ep.cu_per_minute)) + (now - t_prev) * ep.cu_per_minute / 60.0)
                    state["tokens"], state["t"] = tokens, now
                wait = max(0.0, next_at - now)
                if tokens is not None and tokens < cost:
                    wait = max(wait, (cost - tokens) * 60.0 / ep.cu_per_minute)
                if wait <= 0:
                    state["next_at"] = now + ep.cur_pace
                    if tokens is not None:
                        state["tokens"] = tokens - cost
                return wait

            try:
                wait = self._update(ep, step)
            except OSError:
                return
            if wait <= 0:
                return
            self.sleep(min(wait, 5.0))

    def penalize(self, ep: "Endpoint", seconds: float, drain: bool = False) -> None:
        """A 429 told one process to wait: every process on this IP waits (and the CU bucket is drained on a quota 429)."""
        if self.dir is None:
            return

        def step(state: dict, now: float) -> float:
            state["next_at"] = max(float(state.get("next_at", 0.0)), now + seconds)
            if drain and ep.cu_per_minute:
                state["tokens"], state["t"] = 0.0, now + seconds
            return 0.0

        try:
            self._update(ep, step)
        except OSError:
            return


@dataclass
class Endpoint:
    name: str
    url: str
    pace: float = 1.0  # base seconds between two requests
    span: int = 10_000  # blocks per eth_getLogs
    max_inflight: int = 1
    max_addresses: int = 1000
    timeout: float = 120.0
    cu_per_minute: int = 0  # compute-unit quota per minute (0 = none known); getLogs is charged GETLOGS_CU from it
    # state (guarded by the pool lock)
    cur_pace: float = 0.0
    max_span: int = 0  # the configured span; `span` shrinks on rejects and grows back after SPAN_GROW_AFTER clean answers
    next_at: float = 0.0
    inflight: int = 0
    strikes: int = 0
    streak: int = 0
    last_error: str = ""
    stats: Counter = field(default_factory=Counter)
    latencies: list[float] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.cur_pace = self.pace
        self.max_span = self.span

    def summary(self) -> dict[str, Any]:
        lat = sorted(self.latencies)
        secs = self.stats["seconds"]
        return {
            "ok": self.stats["ok"],
            "rate_limited": self.stats["rate_limited"],
            "range": self.stats["range"],
            "errors": self.stats["errors"],
            "blocks": self.stats["blocks"],
            "logs": self.stats["logs"],
            "mb": round(self.stats["bytes"] / 1e6, 1),
            "median_s": round(lat[len(lat) // 2], 2) if lat else None,
            "blocks_per_s_while_busy": round(self.stats["blocks"] / secs) if secs else 0,
            "span": self.span,
            "pace": round(self.cur_pace, 1),
            "last_error": self.last_error,
        }


def make_endpoints(names_or_urls: Iterable[str] | None) -> list[Endpoint]:
    out = []
    for item in names_or_urls or DEFAULT_ENDPOINTS:
        if item in KNOWN_ENDPOINTS:
            k = KNOWN_ENDPOINTS[item]
            out.append(Endpoint(item, k["url"], k["pace"], k["span"], k["inflight"], k["addresses"], cu_per_minute=k.get("cu", 0)))
        else:
            host = item.split("//", 1)[-1].split("/", 1)[0]
            out.append(Endpoint(host, item))
    return out


class EndpointPool:
    """Hands out endpoints under pacing + penalty box; the transport is injectable (tests pass a fake)."""

    def __init__(self, endpoints: list[Endpoint], transport: Callable[..., tuple[int, dict, Any]] = http_transport, clock=time.time, sleep=time.sleep, progress=print, pacer: SharedPacer | None = None):
        if not endpoints:
            raise ValueError("endpoint pool needs at least one endpoint")
        self.eps = list(endpoints)
        self.transport = transport
        self.clock = clock
        self.sleep = sleep
        self.progress = progress
        self.pacer = pacer if pacer is not None else SharedPacer(None)
        self._cv = threading.Condition()
        self._rr = -1
        self._waiters: list[int] = []

    # -- scheduling --
    def acquire(self, priority: int = 0) -> Endpoint:
        """Block until some endpoint may take a request: the one ready earliest (round-robin among equally ready ones).

        `priority` orders the waiting threads (lower first): the fetcher of the oldest chunk gets the next free slot, so
        the in-order writer (and the cursor) never starve behind newer chunks when endpoints are scarce."""
        with self._cv:
            self._waiters.append(priority)
            try:
                while True:
                    now = self.clock()
                    if priority > min(self._waiters):
                        self._cv.wait(timeout=5.0)  # someone older is first in line; they notify when they took a slot
                        continue
                    free = [e for e in self.eps if e.inflight < e.max_inflight]
                    if free:
                        ready = [e for e in free if e.next_at <= now]
                        if ready:
                            # round-robin among the ready ones so a fast endpoint does not starve the others of work
                            self._rr += 1
                            ep = ready[self._rr % len(ready)]
                            ep.inflight += 1
                            ep.next_at = now + ep.cur_pace
                            self._cv.notify_all()
                            return ep
                        wait = min(e.next_at for e in free) - now
                    else:
                        wait = 0.05
                    self._cv.wait(timeout=max(0.01, min(wait, 5.0)))
            finally:
                self._waiters.remove(priority)

    def report(self, ep: Endpoint, ok: bool, dt: float, fail: RpcFail | None = None, blocks: int = 0, logs: int = 0, nbytes: int = 0) -> None:
        with self._cv:
            ep.inflight = max(0, ep.inflight - 1)
            ep.latencies.append(dt)
            if len(ep.latencies) > 500:
                del ep.latencies[:250]
            now = self.clock()
            if ok:
                ep.strikes = 0
                ep.stats["ok"] += 1
                ep.stats["blocks"] += blocks
                ep.stats["logs"] += logs
                ep.stats["bytes"] += nbytes
                ep.stats["seconds"] += dt
                ep.cur_pace = max(ep.pace, ep.cur_pace * 0.9)  # decay back to the base pacing after a 429 episode
                ep.streak += 1
                if ep.span < ep.max_span and ep.streak >= SPAN_GROW_AFTER:  # a quiet period: try wider spans again
                    ep.span = min(ep.max_span, ep.span * 2)
                    ep.streak = 0
            else:
                kind = fail.kind if fail else "transport"
                ep.streak = 0
                ep.last_error = str(fail)[:100] if fail else "transport"
                if kind == "rate_limited":
                    ep.stats["rate_limited"] += 1
                    ep.strikes += 1
                    # the server's Retry-After is the truth (other processes on this IP share the quota); without one, back off
                    penalty = fail.retry_after if (fail and fail.retry_after) else min(120.0, max(2.0, ep.cur_pace * 2 ** ep.strikes))
                    ep.cur_pace = min(4.0 * max(ep.pace, 1.0), max(ep.pace, ep.cur_pace * 1.25))  # gently slower, at most 4x the base
                    self.pacer.penalize(ep, penalty, drain=bool(ep.cu_per_minute))
                elif kind == "range":
                    ep.stats["range"] += 1
                    penalty = 0.0  # a split follows; the endpoint itself is fine
                elif kind == "forbidden":
                    ep.stats["errors"] += 1
                    penalty = 900.0
                else:
                    ep.stats["errors"] += 1
                    ep.strikes += 1
                    penalty = min(120.0, 2.0 * 2 ** min(ep.strikes, 6))
                if penalty:
                    ep.next_at = max(ep.next_at, now + penalty)
            self._cv.notify_all()

    # -- requests --
    def post(self, ep: Endpoint, payload: Any, expect_list: bool = False) -> tuple[Any, int]:
        """One request on `ep`; returns (result, bytes). Raises RpcFail (already reported to the pool)."""
        method = payload[0]["method"] if isinstance(payload, list) else payload.get("method")
        self.pacer.wait_turn(ep, GETLOGS_CU if method == "eth_getLogs" else (len(payload) if isinstance(payload, list) else 1.0))
        t0 = self.clock()
        try:
            status, headers, body = self.transport(ep.url, payload, ep.timeout)
        except Exception as e:  # noqa: BLE001 - connection errors, timeouts
            fail = RpcFail("transport", f"{type(e).__name__}: {e}")
            self.report(ep, False, self.clock() - t0, fail)
            raise fail from None
        dt = self.clock() - t0
        fail = classify_error(status, headers, body)
        if fail is None and expect_list and not isinstance(body, list):
            fail = RpcFail("transport", "batch answered with a single object")
        if fail is not None:
            self.report(ep, False, dt, fail)
            raise fail
        nbytes = len(json.dumps(body)) if body is not None else 0  # measured after parsing; close enough for the report
        return (body if expect_list else body["result"]), nbytes

    def get_logs(self, fr: int, to: int, flt: dict[str, Any], stats: Counter | None = None, priority: int = 0) -> list[dict]:
        """eth_getLogs over [fr, to] inclusive, split across endpoints by their spans; adaptive on range errors."""
        out: list[dict] = []
        cur = fr
        attempts = 0
        while cur <= to:
            ep = self.acquire(priority)
            hi = min(to, cur + ep.span - 1)
            payload = {"jsonrpc": "2.0", "id": 1, "method": "eth_getLogs", "params": [{"fromBlock": hex(cur), "toBlock": hex(hi), **flt}]}
            t0 = self.clock()
            try:
                res, nbytes = self.post(ep, payload)
            except RpcFail as e:
                attempts += 1
                if e.kind == "range":
                    addrs = flt.get("address")
                    if isinstance(addrs, list) and len(addrs) > MIN_ADDRESSES:
                        raise  # a long address list is the likelier cause: the caller halves it; the span stays
                    span = hi - cur + 1
                    if span > MIN_SPAN:
                        with self._cv:
                            ep.span = max(MIN_SPAN, min(ep.span, span // 2))
                        if stats is not None:
                            stats["span_shrinks"] += 1
                if attempts >= MAX_ATTEMPTS:
                    raise RuntimeError(f"getLogs {cur:,}-{hi:,} failed after {attempts} attempts (last: {e})") from e
                if stats is not None:
                    stats[f"retry_{e.kind}"] += 1
                continue
            if not isinstance(res, list):
                self.report(ep, False, self.clock() - t0, RpcFail("transport", "result is not a list"))
                attempts += 1
                continue
            self.report(ep, True, self.clock() - t0, blocks=hi - cur + 1, logs=len(res), nbytes=nbytes)
            out.extend(res)
            cur = hi + 1
        return out

    def get_logs_by_address(self, fr: int, to: int, addresses: list[str], topics: list, stats: Counter | None = None, priority: int = 0) -> list[dict]:
        """Transfer logs of many token contracts: the address list is sliced per endpoint capacity and halved on rejects."""
        out: list[dict] = []
        pending = deque([list(addresses)])
        while pending:
            addrs = pending.popleft()
            cap = min(e.max_addresses for e in self.eps)
            if len(addrs) > cap:
                pending.appendleft(addrs[cap:])
                addrs = addrs[:cap]
            try:
                out.extend(self.get_logs(fr, to, {"address": addrs, "topics": topics}, stats, priority))
            except RpcFail as e:
                if e.kind == "range" and len(addrs) > MIN_ADDRESSES:
                    half = len(addrs) // 2
                    with self._cv:
                        for ep in self.eps:
                            ep.max_addresses = max(MIN_ADDRESSES, min(ep.max_addresses, half))
                    if stats is not None:
                        stats["address_splits"] += 1
                    pending.appendleft(addrs[half:])
                    pending.appendleft(addrs[:half])
                    continue
                raise
        return out

    def get_headers(self, numbers: list[int], stats: Counter | None = None, priority: int = 0) -> dict[int, int]:
        """Block number -> timestamp via batched eth_getBlockByNumber (only the fallback when logs carry no timestamp)."""
        out: dict[int, int] = {}
        for i in range(0, len(numbers), HEADER_BATCH):
            chunk = numbers[i : i + HEADER_BATCH]
            body = [{"jsonrpc": "2.0", "id": j, "method": "eth_getBlockByNumber", "params": [hex(n), False]} for j, n in enumerate(chunk)]
            for attempt in range(8):
                ep = self.acquire(priority)
                t0 = self.clock()
                try:
                    res, nbytes = self.post(ep, body, expect_list=True)
                except RpcFail:
                    if stats is not None:
                        stats["retry_headers"] += 1
                    continue
                self.report(ep, True, self.clock() - t0, nbytes=nbytes)
                for item in res:
                    if isinstance(item, dict) and isinstance(item.get("result"), dict) and item["result"].get("timestamp") is not None:
                        out[_hex_int(item["result"]["number"])] = _hex_int(item["result"]["timestamp"])
                break
        return out

    def summary(self) -> dict[str, Any]:
        return {e.name: e.summary() for e in self.eps}


# ---- planning / normalizing -------------------------------------------------------------------------------------------


def plan_chunks(fr: int, to: int, size: int) -> list[tuple[int, int]]:
    """[fr, to] inclusive -> consecutive inclusive chunks of `size` blocks (the last one shorter)."""
    if size <= 0:
        raise ValueError("chunk size must be positive")
    out = []
    b = fr
    while b <= to:
        e = min(b + size - 1, to)
        out.append((b, e))
        b = e + 1
    return out


def rpc_log_dict(l: dict[str, Any]) -> dict[str, Any]:
    """JSON-RPC log -> the dict `trades_from_tx` expects, plus block / tx / ts (blockTimestamp when the node sends it)."""
    tp = l.get("topics") or []
    t = [(tp[i].lower() if i < len(tp) and isinstance(tp[i], str) else None) for i in range(4)]
    bts = l.get("blockTimestamp")
    ts = _hex_int(bts) if bts not in (None, "", "0x") else 0
    return {
        "log_index": _hex_int(l.get("logIndex")),
        "address": (l.get("address") or "").lower(),
        "topic0": t[0],
        "topic1": t[1],
        "topic2": t[2],
        "topic3": t[3],
        "data": l.get("data") or "0x",
        "kind": chain.KIND_BY_TOPIC.get(t[0] or "", "other"),
        "block": _hex_int(l.get("blockNumber")),
        "tx_hash": (l.get("transactionHash") or "").lower(),
        "ts": ts or None,  # the official endpoint fills blockTimestamp with 0x0: unknown, not 1970
    }


def select_swap_class(logs: list[dict[str, Any]], ctx: Context, stats: Counter) -> tuple[list[dict[str, Any]], set[str], set[str]]:
    """Keep what the HyperSync query selected server-side; return (kept logs, traded tokens, swap tx hashes)."""
    kept: list[dict[str, Any]] = []
    tokens: set[str] = set()
    swap_txs: set[str] = set()
    for l in logs:
        k = l["kind"]
        if k == "v4_swap":
            if l["address"] != chain.V4_POOL_MANAGER:
                stats["v4_other_manager_dropped"] += 1
                continue
            p = ctx.pool_token.get(l["topic1"] or "")
            if p is None:
                stats["v4_non_universe_dropped"] += 1
                continue
            if p.get("token"):
                tokens.add(p["token"])
            swap_txs.add(l["tx_hash"])
        elif k == "hook_fee":
            if l["address"] != chain.PONS_V2_HOOK:
                stats["hook_fee_other_dropped"] += 1
                continue
        elif k in ("curve_buy", "curve_sell"):
            c = ctx.curves.get(l["address"])
            if c:
                tokens.add(c["token"])
            swap_txs.add(l["tx_hash"])
        elif k not in ("snipe_tax", "curve_refund"):
            continue
        kept.append(l)
    return kept, tokens, swap_txs


def transfers_of_swaps(transfers: Iterable[dict[str, Any]], swap_txs: set[str]) -> list[dict[str, Any]]:
    """Only transfers inside a transaction that has a swap event: the rest are plain token movements, never trades."""
    return [l for l in transfers if l["kind"] == "transfer" and l["tx_hash"] in swap_txs]


def resolve_timestamps(trade_blocks: set[int], known: dict[int, int], fetch_headers: Callable[[list[int]], dict[int, int]] | None, stats: Counter) -> dict[int, tuple[int | None, int]]:
    """block -> (ts, exact). Exact from the logs' blockTimestamp; missing blocks get headers for every ANCHOR_EVERY-th one
    (all of them when few) and linear interpolation between anchors for the rest (`ts_exact` 0)."""
    out: dict[int, tuple[int | None, int]] = {b: (known[b], 1) for b in trade_blocks if b in known}
    missing = sorted(b for b in trade_blocks if b not in known)
    if not missing:
        return out
    anchors = dict(known)
    if fetch_headers is not None:
        wanted = missing if len(missing) <= 2 * ANCHOR_EVERY * HEADER_BATCH else sorted(set(missing[::ANCHOR_EVERY]) | {missing[0], missing[-1]})
        try:
            got = fetch_headers(wanted)
        except Exception as e:  # noqa: BLE001 - the fallback of the fallback is interpolation
            stats["header_fetch_failed"] += 1
            got = {}
        stats["headers_fetched"] += len(got)
        anchors.update(got)
        for b in missing:
            if b in got:
                out[b] = (got[b], 1)
    interp = Interp(anchors)
    for b in missing:
        if b in out:
            continue
        ts, exact = interp(b)
        out[b] = (ts, exact)
        stats["ts_interpolated" if ts is not None else "ts_unknown"] += 1
    return out


@dataclass
class ChunkResult:
    fr: int
    to: int
    rows: list[tuple]
    blocks: dict[int, int]  # exact timestamps seen (any matched log)
    wallets: Counter
    stats: Counter
    seconds: float


class RpcBackfill:
    def __init__(self, store: Store, pool: EndpointPool, progress=print, header_fetcher: Callable[[list[int]], dict[int, int]] | None = None):
        self.store = store
        self.pool = pool
        self.progress = progress
        self.header_fetcher = header_fetcher if header_fetcher is not None else (lambda nums: pool.get_headers(nums))

    # -- lifecycle pass --
    def lifecycle(self, fr: int, to: int, chunk: int = 10_000) -> Counter:
        t0 = time.time()
        total: Counter = Counter()
        for a, b in plan_chunks(fr, to, chunk):
            raw = [rpc_log_dict(l) for l in self.pool.get_logs(a, b, {"topics": [LIFECYCLE_TOPICS]}, total, priority=a)]
            logs = [l for l in raw if (l["topic0"] in FACTORY_HOOK_TOPICS and l["address"] in (chain.PONS_V2_FACTORY, chain.PONS_V2_HOOK)) or l["topic0"] in (chain.T_CURVE_COMPLETED, chain.T_SNIPE_TAX_EXEMPTED)]
            self.store.upsert_blocks((l["block"], l["ts"], 1) for l in logs if l["ts"] is not None)
            total += apply_lifecycle(self.store, logs, SOURCE)
            total["logs"] += len(logs)
            self.store.set_meta("backfill_lifecycle_cursor", b + 1)
        total["seconds"] = round(time.time() - t0, 1)
        self.progress(f"lifecycle (rpc): {dict(total)}")
        return total

    # -- trades pass --
    def fetch_chunk(self, fr: int, to: int, ctx: Context) -> ChunkResult:
        t0 = time.time()
        st: Counter = Counter()
        raw = [rpc_log_dict(l) for l in self.pool.get_logs(fr, to, {"topics": [SWAP_CLASS_TOPICS]}, st, priority=fr)]
        st["logs_swap_class"] += len(raw)
        kept, tokens, swap_txs = select_swap_class(raw, ctx, st)
        known_ts = {l["block"]: l["ts"] for l in raw if l["ts"] is not None}
        transfers: list[dict[str, Any]] = []
        if tokens:
            traw = [rpc_log_dict(l) for l in self.pool.get_logs_by_address(fr, to, sorted(tokens), [[chain.T_TRANSFER]], st, priority=fr)]
            st["logs_transfer_seen"] += len(traw)
            known_ts.update({l["block"]: l["ts"] for l in traw if l["ts"] is not None})
            transfers = transfers_of_swaps(traw, swap_txs)
        st["logs_transfer_kept"] += len(transfers)
        st["tokens_in_chunk"] += len(tokens)
        by_tx = group_by_tx(kept + transfers)
        trade_blocks = {lst[0]["block"] for lst in by_tx.values() if any(l["kind"] in ("curve_buy", "curve_sell", "v4_swap") for l in lst)}
        ts_of = resolve_timestamps(trade_blocks, known_ts, self.header_fetcher, st)
        rows: list[tuple] = []
        wallets: Counter = Counter()
        for tx_hash, lst in by_tx.items():
            if not any(l["kind"] in ("curve_buy", "curve_sell", "v4_swap") for l in lst):
                st["txs_without_swap"] += 1
                continue
            block = lst[0]["block"]
            trades, notes = trades_from_tx(tx_hash, block, lst, ctx)
            for _, n, _ in notes:
                st[f"note_{n}"] += 1
            ts, exact = ts_of.get(block, (None, 0))
            if ts is None:
                st["trades_unknown_time"] += len(trades)
            for t in trades:
                rows.append(t.row(ts, exact))
                wallets[t.wallet] += 1
                st[f"trades_{t.side}"] += 1
            st["txs_with_trades"] += 1 if trades else 0
        st["txs"] += len(by_tx)
        st["trades"] += len(rows)
        return ChunkResult(fr, to, rows, known_ts, wallets, st, time.time() - t0)

    def write_chunk(self, res: ChunkResult) -> None:
        db = self.store.db
        if res.rows:
            db.executemany(TRADE_INSERT, res.rows)
        if res.blocks:
            self.store.upsert_blocks((n, ts, 1) for n, ts in res.blocks.items())
        if res.wallets:
            db.executemany("INSERT INTO wallets(address,is_contract,trades) VALUES(?,NULL,?) ON CONFLICT(address) DO UPDATE SET trades=wallets.trades+excluded.trades", list(res.wallets.items()))
        self.store.set_meta("backfill_cursor", res.to + 1)  # commits rows + blocks + wallets + cursor together

    def trades(self, fr: int, to: int, ctx: Context | None = None, chunk: int = 10_000, workers: int = 6, report_every: int = 5, heartbeat_s: float = 120.0) -> Counter:
        ctx = ctx or build_context(self.store)
        chunks = plan_chunks(fr, to, chunk)
        t0 = time.time()
        total: Counter = Counter()
        window: deque = deque()
        n_done = 0
        recent: deque = deque()  # (time, next_block) for the rolling rate

        def submit(ex, a, b):
            f = ex.submit(self.fetch_chunk, a, b, ctx)
            f.chunk_fr, f.chunk_to = a, b  # type: ignore[attr-defined]
            return f

        with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
            it = iter(chunks)
            for _ in range(max(1, workers) + 2):
                nxt = next(it, None)
                if nxt is None:
                    break
                window.append(submit(ex, nxt[0], nxt[1]))
            while window:
                fut = window[0]
                while True:
                    try:
                        res: ChunkResult = fut.result(timeout=heartbeat_s)
                        break
                    except TimeoutError:  # nothing finished for a while: say what the endpoints are doing
                        eps = " ".join(f"{e.name}:{e.stats['ok']}ok/{e.stats['rate_limited']}rl/{e.stats['range']}range/{e.stats['errors']}err/pace {e.cur_pace:.0f}s/span {e.span}" + (f" (last: {e.last_error})" if e.last_error else "") for e in self.pool.eps)
                        self.progress(f"waiting on chunk {window[0].chunk_fr:,}-{window[0].chunk_to:,} ({len(window)} in flight); {eps}")
                window.popleft()
                nxt = next(it, None)
                if nxt is not None:
                    window.append(submit(ex, nxt[0], nxt[1]))
                self.write_chunk(res)
                total += res.stats
                total["chunks"] += 1
                n_done += 1
                now = time.time()
                recent.append((now, res.to + 1))
                while len(recent) > 1 and now - recent[0][0] > 120:
                    recent.popleft()
                if n_done % report_every == 0 or res.to >= to:
                    el = now - t0
                    done = res.to + 1 - fr
                    rate = done / el if el else 0
                    rr = (recent[-1][1] - recent[0][1]) / (recent[-1][0] - recent[0][0]) if len(recent) > 1 and recent[-1][0] - recent[0][0] >= 30 else rate
                    eps = " ".join(f"{e.name}:{e.stats['ok']}ok/{e.stats['rate_limited']}rl/{e.stats['errors']}err/{e.span}" for e in self.pool.eps)
                    self.progress(f"trades (rpc): block {res.to + 1:,} ({done / max(1, to + 1 - fr) * 100:.1f}%), {total['trades']:,} trades, {rate:,.0f} blocks/s avg, {rr:,.0f} blocks/s last 2 min, eta {((to - res.to) / rr / 60) if rr else 0:.0f} min; {eps}")
        total["seconds"] = round(time.time() - t0, 1)
        self.progress(f"trades pass done (rpc): {dict(total)}")
        return total


def run(store: Store, fr: int, to: int, pool: EndpointPool, chunk: int = 10_000, workers: int = 6, resume: bool = True, progress=print, header_fetcher=None, infra: bool = True) -> dict[str, Any]:
    bf = RpcBackfill(store, pool, progress=progress, header_fetcher=header_fetcher)
    out: dict[str, Any] = {"from_block": fr, "to_block": to, "chunk": chunk, "workers": workers}
    if infra:
        out["infra_seeded"] = seed_infra(store)
    lc = store.get_meta("backfill_lifecycle_cursor")
    lifecycle_from = max(0, fr - int(LIFECYCLE_LOOKBACK_DAYS * 86400 * BLOCKS_PER_SECOND))
    if not (resume and lc and int(lc) > to):
        start = max(lifecycle_from, int(lc)) if (resume and lc) else lifecycle_from
        out["lifecycle_from_block"] = start
        out["lifecycle"] = dict(bf.lifecycle(start, to, chunk))
        from ..context.pons import sync_lifecycle

        out["sync_lifecycle"] = sync_lifecycle(store)
    else:
        progress("lifecycle pass already complete (resume)")
    cur = store.get_meta("backfill_cursor")
    start = max(fr, int(cur)) if (resume and cur) else fr
    if start <= to:
        out["trades"] = dict(bf.trades(start, to, chunk=chunk, workers=workers))
    else:
        progress("trades pass already complete (resume)")
    store.set_meta("sample_from_block", fr)
    store.set_meta("sample_to_block", to)
    if not store.get_meta("sample_label"):
        store.set_meta("sample_label", f"PONS v2 backfill (rpc), blocks {fr}-{to}")
    out["endpoints"] = pool.summary()
    return out


def main_backfill_rpc(args) -> int:
    store = Store(Path(args.db))
    store.db.execute("PRAGMA synchronous=OFF")  # resumable via the cursor, so fsync per commit buys nothing
    store.db.execute("PRAGMA cache_size=-262144")
    pool = EndpointPool(make_endpoints(args.endpoints), progress=lambda m: print(m, flush=True), pacer=SharedPacer(args.pacer_dir or None))
    header_fetcher = None
    if not args.no_alchemy_headers:
        try:
            from ..rpc import Rpc

            rpc = Rpc()
            if rpc.alchemy_url:
                header_fetcher = lambda nums: {n: _hex_int(b["timestamp"]) for n, b in rpc.get_blocks(nums).items()}  # noqa: E731
        except Exception:  # noqa: BLE001 - no key: the pool's batch headers are the fallback
            header_fetcher = None
    fr, to = int(args.from_block), int(args.to_block)
    label = args.label or f"PONS v2 backfill (rpc), blocks {fr}-{to}"
    if not store.get_meta("sample_label") or args.label:
        store.set_meta("sample_label", label)
    run_id = store.start_run("backfill-rpc", {"from_block": fr, "to_block": to, "chunk": args.chunk, "workers": args.workers, "endpoints": [e.name for e in pool.eps], "resume": not args.no_resume})
    print(f"backfill-rpc {fr:,} → {to:,} ({to - fr + 1:,} blocks) into {store.path} via {', '.join(e.name for e in pool.eps)}; chunk {args.chunk:,}, {args.workers} workers", flush=True)
    res = run(store, fr, to, pool, chunk=args.chunk, workers=args.workers, resume=not args.no_resume, progress=lambda m: print(m, flush=True), header_fetcher=header_fetcher, infra=not args.no_seed_infra)
    store.finish_run(run_id, res)
    print(json.dumps(res, indent=1, default=str))
    return 0
