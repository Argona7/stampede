"""`GET /api/paper` and `GET /api/track-record` (stage 7): the paper ledger and the honest track record.

Both read from the engine's memory when `serve --mode live --feed wss` runs in this process (positions are marked every
block there) and from the `paper_*` / `alerts` tables otherwise (fixture, replay, another process's store). Every
response says `simulated: true`: the fills are curve arithmetic at the next block's observed reserves, no order was sent.
"""
from __future__ import annotations

from typing import Any, Callable

from fastapi import FastAPI

from ..engine import paper as paper_mod
from ..store import Store


def install(app: FastAPI, state: dict[str, Any], store_factory: Callable[[], Store]) -> None:
    def engine_state():
        live = state.get("live")
        st = getattr(live, "state", None)
        return st if (st is not None and getattr(st, "paper", None) is not None) else None

    @app.get("/api/paper")
    def get_paper(closed_limit: int = 200, since: int | None = None) -> dict[str, Any]:
        st = engine_state()
        if st is not None:
            out = st.paper.snapshot(st.clock or None, closed_limit=min(max(1, closed_limit), 2000))
            out["source"] = "engine"
            out["mode"] = state.get("mode")
            return out
        s = store_factory()
        try:
            out = paper_mod.read_snapshot(s, closed_limit=min(max(1, closed_limit), 2000), since_ts=since)
            out["mode"] = state.get("mode")
            return out
        finally:
            s.close()

    @app.get("/api/track-record")
    def get_track_record(since: int | None = None, alerts_limit: int = 200) -> dict[str, Any]:
        """The whole journal of this mode by default (`since=0`); `since=<unix ts>` narrows it (the CLI defaults to the
        latest engine start instead, so a restart does not empty the panel a person is looking at)."""
        from .. import track_record

        live = state.get("live")
        perf = live.perf() if (live is not None and hasattr(live, "perf")) else None
        st = engine_state()
        s = store_factory()
        try:
            return track_record.build(s, since_ts=since if since is not None else 0, perf=perf, paper=st.paper.snapshot(st.clock or None, closed_limit=2000) if st is not None else None, mode=state.get("mode") or "live", alerts_limit=alerts_limit)
        finally:
            s.close()
