"""`GET /api/stream` (Server-Sent Events) and `GET /api/perf` for the realtime engine.

Contract (docs/ENGINE.md): every frame is `id: <monotonic>` / `event: <type>` / `data: <json>` with the JSON carrying
`id`, `type`, `ts_emit`, `block` and the typed `data`. Types: block, trade, sequence, radar_delta, alert, verdict
(reserved for Stage 4), session. A client that reconnects with `Last-Event-ID` (or `?last_event_id=`) gets every
event it missed from the bounded ring (last 5,000); if the ring no longer holds them, the first frame is a `session`
event with `replay_gap: true` so the client knows to refetch `/api/radar`. A `Last-Event-ID` larger than the server's
counter (the client survived a server restart; every process counts from 1) is the same gap: the hello says
`replay_gap: true` and the stream continues from the current position instead of dropping events until the new counter
passes the old id. The hello also carries `started_at` of this process. The first frame of every connection is a
`session` hello without an id (it does not move the client's Last-Event-ID). `?types=trade,alert` filters server-side,
`?limit=N` closes the stream after N events (scripts, tests). Works in every mode: without the engine the bus only
carries what the API publishes (nothing today), so fixture/replay clients see the hello and keepalives.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

from ..engine.bus import EVENT_TYPES, Bus, sse_frame

KEEPALIVE_S = 15.0


def install(app: FastAPI, state: dict[str, Any]) -> Bus:
    bus: Bus = state.setdefault("bus", Bus())
    app.state.bus = bus  # reachable for tests and for other routers that want to publish

    def hello(replay_gap: bool, since: int | None) -> str:
        sess = state.get("session")
        live = state.get("live")
        st = live.status if live is not None else {}
        body = {
            "id": None,
            "type": "session",
            "ts_emit": time.time(),
            "block": st.get("last_block"),
            "data": {
                **(sess.state(st.get("last_ts")) if sess is not None else {"mode": state.get("mode")}),
                "engine": st.get("engine", "alchemy") if live is not None else "none",
                "head_block": st.get("head_block"),
                "head_lag_s": st.get("head_lag_s"),
                "paused": bool(st.get("paused")),
                "feed": st.get("feed"),
                "last_event_id": bus.last_id,
                "replay_from": since,
                "replay_gap": replay_gap,
                "started_at": bus.created,  # this server process; a client whose Last-Event-ID comes from another process must resync
                "types": list(EVENT_TYPES),
                "hello": True,
            },
        }
        return sse_frame(None, "session", body)

    @app.get("/api/stream")
    async def stream(request: Request, types: str | None = None, last_event_id: int | None = None, limit: int | None = None):
        wanted = {t.strip() for t in types.split(",") if t.strip()} if types else None
        hdr = request.headers.get("last-event-id")
        since: int | None = last_event_id
        if since is None and hdr:
            try:
                since = int(hdr)
            except ValueError:
                since = None
        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue()

        async def gen():
            sent = 0
            sub = bus.subscribe(loop, q, wanted)  # subscribe first: nothing published during the replay is lost
            replay, gap = bus.replay(since, wanted) if since is not None else ([], False)
            stale = since is not None and since > bus.last_id  # an id of a previous server process: the counter restarted at 1
            try:
                yield hello(gap, since)
                # a stale id would otherwise make `ev.id <= last_sent` drop every live event until the new counter passes it
                last_sent = 0 if stale else (since or 0)
                for ev in replay:
                    yield ev.sse
                    last_sent = ev.id
                    sent += 1
                    if limit is not None and sent >= limit:
                        return
                while True:
                    try:
                        ev = await asyncio.wait_for(q.get(), timeout=KEEPALIVE_S)
                    except asyncio.TimeoutError:
                        if await request.is_disconnected():
                            return
                        yield ": keepalive\n\n"
                        continue
                    if ev.id <= last_sent:
                        continue  # published during the replay window: already delivered
                    last_sent = ev.id
                    yield ev.sse
                    sent += 1
                    if limit is not None and sent >= limit:
                        return
            finally:
                bus.unsubscribe(sub)

        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no", "Connection": "keep-alive"})

    @app.get("/api/perf")
    def perf() -> dict[str, Any]:
        live = state.get("live")
        if live is not None and hasattr(live, "perf"):
            return live.perf()
        return {"engine": getattr(live, "status", {}).get("engine", "alchemy") if live is not None else None, "mode": state.get("mode"), "events": bus.stats(), "latency_ms": {}, "blocks": {}, "note": "the realtime engine is not running in this process (serve --mode live --feed wss)"}

    return bus
