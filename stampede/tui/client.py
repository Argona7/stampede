"""Small HTTP client for the STAMPEDE API. Injected into the TUI so tests can replace it.

`open_stream()` adds the live side: one daemon thread reading `GET /api/stream` (Server-Sent Events, docs/ENGINE.md)
with `Last-Event-ID` on every reconnect, so a live TUI gets alerts, paper positions, radar deltas and the engine's lag as
they are published instead of waiting for the next poll. A server without an engine (fixture, replay, `--feed alchemy`)
answers the hello with another engine name: the reader reports `unavailable` and stops, and the TUI keeps polling.
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any, Callable

import requests


class ApiError(Exception):
    pass


class StreamReader(threading.Thread):
    """SSE reader thread. `on_event(frame)` gets the parsed JSON frame (`id, type, ts_emit, block, data`); `on_status(state,
    detail)` gets `connecting` / `live` / `reconnecting` / `unavailable` / `off`. Both are called from this thread."""

    def __init__(self, base_url: str, on_event: Callable[[dict[str, Any]], None], on_status: Callable[[str, str | None], None], types: list[str] | None = None, session: requests.Session | None = None, backoff_max_s: float = 15.0):
        super().__init__(daemon=True, name="tui-stream")
        self.base = base_url.rstrip("/")
        self.on_event = on_event
        self.on_status = on_status
        self.types = types
        self._s = session or requests.Session()
        self._stop = threading.Event()
        self.last_id: int | None = None
        self.last_lag_s: float | None = None
        self.frames = 0
        self.reconnects = 0
        self.backoff_max_s = backoff_max_s
        self._resp: requests.Response | None = None

    def stop(self) -> None:
        self._stop.set()
        try:
            if self._resp is not None:
                self._resp.close()
        except Exception:  # noqa: BLE001
            pass

    def _url(self) -> str:
        q = []
        if self.types:
            q.append("types=" + ",".join(self.types))
        if self.last_id is not None:
            q.append(f"last_event_id={self.last_id}")
        return self.base + "/api/stream" + ("?" + "&".join(q) if q else "")

    def run(self) -> None:
        fails = 0
        while not self._stop.is_set():
            self.on_status("connecting" if not self.reconnects else "reconnecting", None)
            try:
                with self._s.get(self._url(), stream=True, timeout=(6, 40), headers={"accept": "text/event-stream"}) as r:
                    self._resp = r
                    if r.status_code != 200:
                        raise ApiError(f"HTTP {r.status_code} /api/stream")
                    hello_seen = False
                    frame: dict[str, str] = {}
                    for raw in r.iter_lines(decode_unicode=True):
                        if self._stop.is_set():
                            return
                        if raw is None:
                            continue
                        line = raw if isinstance(raw, str) else raw.decode("utf-8", "replace")
                        if line == "":
                            if "data" in frame:
                                ev = json.loads(frame["data"])
                                if ev.get("id") is not None:
                                    self.last_id = int(ev["id"])
                                if isinstance(ev.get("ts_emit"), (int, float)):
                                    self.last_lag_s = time.time() - float(ev["ts_emit"])
                                self.frames += 1
                                d = ev.get("data") or {}
                                if ev.get("type") == "session" and d.get("hello"):
                                    hello_seen = True
                                    if d.get("engine") != "wss":
                                        self.on_status("unavailable", f"engine {d.get('engine') or 'none'}")
                                        return
                                    fails = 0
                                    self.on_status("live", None)
                                self.on_event(ev)
                            frame = {}
                            continue
                        if line.startswith(":"):
                            continue  # keepalive comment
                        k, _, v = line.partition(":")
                        frame[k] = v[1:] if v.startswith(" ") else v
                    if not hello_seen:
                        raise ApiError("stream ended before the hello")
            except Exception as e:  # noqa: BLE001 - transport: back off and resume from the last id
                if self._stop.is_set():
                    return
                fails += 1
                self.reconnects += 1
                self.on_status("reconnecting", f"{type(e).__name__}: {str(e)[:80]}")
                self._stop.wait(min(self.backoff_max_s, 0.5 * (2 ** min(fails, 5))))
        self.on_status("off", None)


class ApiClient:
    def __init__(self, base_url: str, timeout: float = 6.0):
        self.base = base_url.rstrip("/")
        self.timeout = timeout
        self._s = requests.Session()
        self._s.headers.update({"user-agent": "stampede-tui/0.3"})

    def _reason(self, e: requests.RequestException) -> str:
        """One readable line for the alert bar instead of urllib3's nested exception text."""
        host = self.base.split("://", 1)[-1]
        if isinstance(e, requests.ConnectionError):
            return f"connection refused at {host} (is the server running?)"
        if isinstance(e, requests.Timeout):
            return f"no answer from {host} within {self.timeout:g}s"
        return f"{type(e).__name__}: {str(e)[:80]}"

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        try:
            r = self._s.get(self.base + path, params={k: v for k, v in (params or {}).items() if v is not None}, timeout=self.timeout)
        except requests.RequestException as e:  # noqa: PERF203
            raise ApiError(self._reason(e)) from e
        if r.status_code >= 400:
            raise ApiError(f"HTTP {r.status_code} {path}")
        return r.json()

    def _post(self, path: str, body: dict[str, Any]) -> Any:
        try:
            r = self._s.post(self.base + path, json=body, timeout=self.timeout)
        except requests.RequestException as e:
            raise ApiError(self._reason(e)) from e
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail")
            except Exception:  # noqa: BLE001
                detail = r.text[:120]
            raise ApiError(f"HTTP {r.status_code}: {detail}")
        return r.json()

    def status(self) -> dict[str, Any]:
        return self._get("/api/status")

    def session(self) -> dict[str, Any]:
        return self._get("/api/session")

    def control(self, action: str, **kw: Any) -> dict[str, Any]:
        return self._post("/api/session", {"action": action, **kw})

    def events(self, window_s: int, until: int | None, after: str | None, limit: int = 500, backfill_s: int = 1800) -> dict[str, Any]:
        return self._get("/api/events", {"window": f"{window_s}s", "until": until, "after": after, "limit": limit, "backfill_s": backfill_s})

    def graph(self, window_s: int, from_ts: int | None, to_ts: int | None, min_wallets: int = 1, limit: int = 12) -> dict[str, Any]:
        return self._get("/api/graph", {"window": f"{window_s}s", "from": from_ts, "to": to_ts, "min_wallets": min_wallets, "limit": limit})

    def edge(self, a: str, b: str, window_s: int, from_ts: int | None, to_ts: int | None, limit: int = 40) -> dict[str, Any]:
        return self._get(f"/api/edge/{a}/{b}", {"window": f"{window_s}s", "from": from_ts, "to": to_ts, "limit": limit, "exact": 1})

    def radar(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._get("/api/radar", params)

    def coin(self, addr: str, refresh: bool = False) -> dict[str, Any]:
        return self._get(f"/api/coin/{addr}", {"refresh": 1 if refresh else 0})

    def alerts(self) -> dict[str, Any]:
        return self._get("/api/alerts")

    def traders(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._get("/api/traders", params)

    def wallet(self, addr: str) -> dict[str, Any]:
        return self._get(f"/api/wallet/{addr}")

    # ---- stage 7 ----
    def paper(self, closed_limit: int = 100) -> dict[str, Any]:
        return self._get("/api/paper", {"closed_limit": closed_limit})

    def track_record(self) -> dict[str, Any]:
        return self._get("/api/track-record", {"alerts_limit": 0})

    def perf(self) -> dict[str, Any]:
        return self._get("/api/perf")

    def open_stream(self, on_event: Callable[[dict[str, Any]], None], on_status: Callable[[str, str | None], None], types: list[str] | None = None) -> StreamReader:
        """Start the SSE reader thread (live mode). The caller keeps the handle and calls `.stop()` on exit."""
        reader = StreamReader(self.base, on_event, on_status, types=types)
        reader.start()
        return reader
