"""FastAPI application: JSON API + the built web terminal (web/dist) as static files."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .. import chain
from ..env import ROOT, db_path
from ..rotation import parse_window
from ..store import Store
from . import queries
from .live import LiveTail

WEB_DIST = ROOT / "web" / "dist"


def create_app(mode: str = "fixture", window: str = "30m", db: Path | None = None) -> FastAPI:
    app = FastAPI(title="STAMPEDE API", version="0.1.0")
    path = db or db_path()
    state: dict[str, Any] = {"mode": mode, "window_s": parse_window(window), "started": time.time(), "live": None}

    def store() -> Store:
        return Store(path)

    if mode == "live":
        tail = LiveTail(path, window_s=state["window_s"])
        tail.start()
        state["live"] = tail

    @app.get("/api/status")
    def status() -> dict[str, Any]:
        s = store()
        try:
            smp = queries.sample(s)
            bounds = queries.data_bounds(s)
            cov = queries.coverage_summary(s)
            live = state["live"].status if state["live"] else None
            now = int(time.time())
            if mode == "live":
                age = (now - live["last_ts"]) if live and live.get("last_ts") else None
            else:
                age = (now - bounds["last_ts"]) if bounds["last_ts"] else None
            return {
                "mode": mode,  # fixture | replay | live
                "mode_label": {"fixture": "FIXTURE · recorded sample, not live", "replay": "REPLAY · recorded sample played back", "live": "LIVE · tailing the chain head"}[mode],
                "chain": {"id": chain.CHAIN_ID, "name": "Robinhood Chain", "explorer": chain.EXPLORER},
                "sample": smp,
                "data": {**bounds, "age_s": age, "server_time": now},
                "source": cov.get("ingest_path"),
                "coverage": cov,
                "live": live,
                "default_window_s": state["window_s"],
                "connection": ("error" if (live and live.get("paused")) else "ok") if mode == "live" else "static",
            }
        finally:
            s.close()

    @app.get("/api/graph")
    def graph(window: str = "30m", from_ts: int | None = Query(None, alias="from"), to_ts: int | None = Query(None, alias="to"), min_wallets: int = 1, limit: int = 300) -> dict[str, Any]:
        s = store()
        try:
            return queries.graph(s, parse_window(window), from_ts, to_ts, min_wallets, min(limit, 1000))
        finally:
            s.close()

    @app.get("/api/edge/{a}/{b}")
    def edge(a: str, b: str, window: str = "30m", from_ts: int | None = Query(None, alias="from"), to_ts: int | None = Query(None, alias="to"), limit: int = 60, exact: int = 1) -> dict[str, Any]:
        s = store()
        try:
            out = queries.edge(s, a.lower(), b.lower(), parse_window(window), from_ts, to_ts, min(limit, 200))
            if exact:
                _exactify_and_enrich(s, out)
                out = queries.edge(s, a.lower(), b.lower(), parse_window(window), from_ts, to_ts, min(limit, 200))
            return out
        finally:
            s.close()

    @app.get("/api/token/{addr}")
    def token(addr: str, window: str = "30m", from_ts: int | None = Query(None, alias="from"), to_ts: int | None = Query(None, alias="to")) -> dict[str, Any]:
        s = store()
        try:
            out = queries.token(s, addr.lower(), from_ts, to_ts, parse_window(window))
            if not out:
                raise HTTPException(404, "unknown token in this sample")
            return out
        finally:
            s.close()

    @app.get("/api/search")
    def search(q: str) -> list[dict]:
        s = store()
        try:
            return queries.search(s, q)
        finally:
            s.close()

    if WEB_DIST.exists():
        app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

        @app.get("/{full_path:path}")
        def spa(full_path: str):
            f = WEB_DIST / full_path
            if full_path and f.is_file():
                return FileResponse(f)
            return FileResponse(WEB_DIST / "index.html")
    else:

        @app.get("/")
        def root():
            return JSONResponse({"stampede": "API only — build the web terminal with `cd web && npm run build`", "status": "/api/status"})

    return app


_exact_cache: set[str] = set()


def _exactify_and_enrich(s: Store, edge_out: dict[str, Any]) -> None:
    """On demand: exact block timestamps + tx.from/to for the evidence transactions (cached in the DB)."""
    from ..rpc import Rpc

    try:
        rpc = Rpc()
        if not rpc.alchemy_url:
            return
    except Exception:  # noqa: BLE001
        return
    trades = [t for q in edge_out.get("sequences", []) for t in (q.get("sell"), q.get("buy")) if t]
    blocks = sorted({t["block"] for t in trades if not t.get("ts_exact")})
    if blocks:
        got = rpc.get_blocks(blocks[:100])
        s.upsert_blocks((n, int(b["timestamp"], 16), 1) for n, b in got.items())
        for n, b in got.items():
            s.db.execute("UPDATE trades SET ts=?, ts_exact=1 WHERE block=?", (int(b["timestamp"], 16), n))
    txs = sorted({t["tx"] for t in trades if "tx_from" not in t and t["tx"] not in _exact_cache})[:40]
    if txs:
        res = rpc.batch([("eth_getTransactionByHash", [h]) for h in txs])
        rows = []
        for h, r in zip(txs, res):
            if isinstance(r, dict) and r.get("from"):
                rows.append((h, int(r["blockNumber"], 16), r["from"].lower(), (r.get("to") or "").lower(), "alchemy_on_demand"))
                _exact_cache.add(h)
        s.insert_txs(rows)
    s.commit()


def main_serve(args) -> int:
    import uvicorn

    app = create_app(mode=args.mode, window=args.window)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0
