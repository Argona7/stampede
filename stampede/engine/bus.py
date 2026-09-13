"""Event bus for the SSE stream and the latency histograms of the engine.

The bus keeps a bounded ring (last 5,000 events) so a client can resume with `Last-Event-ID`, and fans every new
event out to the asyncio queues of the connected SSE generators (thread-safe: the engine publishes from its own
threads, uvicorn consumes on its loop). Each event is serialized once at publish time.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass
from typing import Any

EVENT_TYPES = ("block", "trade", "sequence", "radar_delta", "alert", "verdict", "position", "session")
RING_SIZE = 5000


@dataclass(frozen=True)
class Event:
    id: int
    type: str
    ts_emit: float
    block: int | None
    data: dict[str, Any]
    sse: str  # the wire frame, built once

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "type": self.type, "ts_emit": self.ts_emit, "block": self.block, "data": self.data}


def sse_frame(id_: int | None, type_: str, body: dict[str, Any]) -> str:
    head = f"id: {id_}\n" if id_ is not None else ""
    return f"{head}event: {type_}\ndata: {json.dumps(body, separators=(',', ':'), default=str)}\n\n"


class Bus:
    def __init__(self, ring_size: int = RING_SIZE):
        self.ring: deque[Event] = deque(maxlen=ring_size)
        self._id = 0
        self._lock = threading.Lock()
        self._subs: dict[int, tuple[asyncio.AbstractEventLoop, asyncio.Queue, set[str] | None]] = {}
        self._sub_id = 0
        self.counts: Counter = Counter()
        self._rate: deque[tuple[float, int]] = deque()  # (second, events in that second)
        self.created = time.time()

    # ---- publishing (any thread) ----
    def publish(self, type_: str, block: int | None, data: dict[str, Any], ts_emit: float | None = None) -> Event:
        ts = ts_emit if ts_emit is not None else time.time()
        with self._lock:
            self._id += 1
            ev = Event(self._id, type_, ts, block, data, sse_frame(self._id, type_, {"id": self._id, "type": type_, "ts_emit": ts, "block": block, "data": data}))
            self.ring.append(ev)
            self.counts[type_] += 1
            sec = int(ts)
            if self._rate and self._rate[-1][0] == sec:
                self._rate[-1] = (sec, self._rate[-1][1] + 1)
            else:
                self._rate.append((sec, 1))
                while len(self._rate) > 120:
                    self._rate.popleft()
            subs = list(self._subs.values())
        for loop, q, types in subs:
            if types is not None and type_ not in types:
                continue
            try:
                loop.call_soon_threadsafe(q.put_nowait, ev)
            except RuntimeError:
                pass  # loop closed: the subscriber is being torn down
        return ev

    def publish_many(self, events: list[tuple[str, int | None, dict[str, Any]]], ts_emit: float | None = None) -> list[Event]:
        return [self.publish(t, b, d, ts_emit) for t, b, d in events]

    # ---- reading ----
    @property
    def last_id(self) -> int:
        return self._id

    def replay(self, since_id: int, types: set[str] | None = None) -> tuple[list[Event], bool]:
        """Events with id > since_id still in the ring; `gap` is True when older events were already evicted."""
        with self._lock:
            items = list(self.ring)
            last = self._id
        if since_id > last:
            return [], True  # an id from a previous server process (the counter restarts at 1): nothing to replay, the client must resync
        if not items:
            return [], since_id < last
        gap = since_id < items[0].id - 1
        out = [e for e in items if e.id > since_id and (types is None or e.type in types)]
        return out, gap

    def subscribe(self, loop: asyncio.AbstractEventLoop, q: asyncio.Queue, types: set[str] | None = None) -> int:
        with self._lock:
            self._sub_id += 1
            self._subs[self._sub_id] = (loop, q, types)
            return self._sub_id

    def unsubscribe(self, sub_id: int) -> None:
        with self._lock:
            self._subs.pop(sub_id, None)

    def _per_s(self, window_s: int) -> float:
        now = int(time.time())
        return round(sum(c for s, c in self._rate if s > now - window_s) / window_s, 2)

    def events_per_s(self, window_s: int = 60) -> float:
        with self._lock:
            return self._per_s(window_s)

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {"last_id": self._id, "ring": len(self.ring), "ring_size": self.ring.maxlen, "subscribers": len(self._subs), "by_type": dict(self.counts), "per_s_60s": self._per_s(60), "per_s_10s": self._per_s(10)}


class Histogram:
    """Bounded reservoir (last `maxlen` samples) with percentiles on demand; values in milliseconds."""

    def __init__(self, maxlen: int = 50000):
        self.values: deque[float] = deque(maxlen=maxlen)
        self.n = 0
        self.max = 0.0
        self.sum = 0.0
        self._lock = threading.Lock()

    def add(self, v: float) -> None:
        with self._lock:
            self.values.append(v)
            self.n += 1
            self.sum += v
            if v > self.max:
                self.max = v

    def summary(self) -> dict[str, Any]:
        with self._lock:
            vals = sorted(self.values)
            n_all, mx, sm = self.n, self.max, self.sum
        if not vals:
            return {"n": 0, "p50": None, "p90": None, "p95": None, "p99": None, "max": None, "mean": None}

        def pct(q: float) -> float:
            return round(vals[min(len(vals) - 1, int(len(vals) * q))], 1)

        return {"n": n_all, "p50": pct(0.5), "p90": pct(0.9), "p95": pct(0.95), "p99": pct(0.99), "max": round(mx, 1), "mean": round(sm / n_all, 1), "window_n": len(vals)}
