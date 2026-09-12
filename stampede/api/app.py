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
import json
import threading

from ..context.holders import holders as fetch_holders
from ..context.launch_intel import fetch_exempt
from ..context.market import GeckoTerminal, budget
from ..context.pons import launch_socials, sync_lifecycle
from ..context.xmentions import XMentions
from . import queries
from . import radar as radar_mod
from . import traders_api
from .context_worker import ContextWorker, load_context
from .live import LiveTail
from .session import SessionClock, SessionError

WEB_DIST = ROOT / "web" / "dist"


def _cached_socials(s: Store, addr: str) -> dict[str, Any] | None:
    r = s.db.execute("SELECT twitter, telegram, website, description FROM launch_meta WHERE token=?", (addr,)).fetchone()
    return {"twitter": r[0], "telegram": r[1], "website": r[2], "description": r[3], "declared_in": "launch calldata"} if r else None


def create_app(mode: str = "fixture", window: str = "30m", db: Path | None = None, speed: float = 10.0, context_policy: str = "live", notify: bool = False) -> FastAPI:
    app = FastAPI(title="STAMPEDE API", version="0.3.0")
    path = db or db_path()
    state: dict[str, Any] = {"mode": mode, "window_s": parse_window(window), "started": time.time(), "live": None}

    def store() -> Store:
        return Store(path)

    if mode == "live":
        tail = LiveTail(path, window_s=state["window_s"])
        tail.start()
        state["live"] = tail

    s0 = store()
    try:
        smp0 = queries.sample(s0)
        sync_lifecycle(s0)  # launches / graduations from lifecycle logs already indexed
    finally:
        s0.close()
    session = SessionClock(mode, smp0.get("from_ts"), smp0.get("to_ts"), window_s=state["window_s"], span_s=1800, speed=speed)
    state["session"] = session

    def live_last_ts() -> int | None:
        lv = state["live"].status if state["live"] else None
        return lv.get("last_ts") if lv else None

    def session_state() -> dict[str, Any]:
        st = session.state(live_last_ts())
        if mode == "live":
            lv = state["live"].status if state["live"] else {}
            st["label"] = "LIVE · PAUSED (provider error)" if lv.get("paused") else "LIVE"
            st["live_paused"] = bool(lv.get("paused"))
        return st

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
            stale = mode == "live" and age is not None and age > 60
            return {
                "mode": mode,  # fixture | replay | live
                "mode_label": {"fixture": "FIXTURE · recorded sample, not live", "replay": "REPLAY · recorded sample played back", "live": "LIVE · tailing the chain head"}[mode],
                "chain": {"id": chain.CHAIN_ID, "name": "Robinhood Chain", "explorer": chain.EXPLORER},
                # three different scopes, never to be mixed up in a label:
                "sample": {**smp, **queries.sample_stats(s, smp), "scope": "fixed recorded sample (docs/COVERAGE.md)"},
                "store": {**bounds, "scope": "everything in the database, sample plus live tail"},
                "data": {**bounds, "age_s": age, "server_time": now},  # kept for older clients
                "source": cov.get("ingest_path"),
                "coverage": cov,
                "live": live,
                "context_worker": state["worker"].status if state.get("worker") else None,
                "session": session_state(),
                "default_window_s": state["window_s"],
                "connection": ("error" if (live and live.get("paused")) else ("stale" if stale else "ok")) if mode == "live" else "static",
            }
        finally:
            s.close()

    @app.get("/api/session")
    def get_session() -> dict[str, Any]:
        return session_state()

    @app.post("/api/session")
    def post_session(body: dict[str, Any]) -> dict[str, Any]:
        try:
            session.control(str(body.get("action", "")), ts=body.get("ts"), speed=body.get("speed"), span_s=body.get("span_s"), window_s=body.get("window_s"))
        except SessionError as e:
            raise HTTPException(e.status, e.message)
        return session_state()

    # ---- radar / coin / alerts ----
    from ..env import env as _env

    def _rpc():
        from ..rpc import Rpc

        return Rpc()

    def clock_now() -> int | None:
        st = session_state()
        return st["clock_ts"] if st["clock_ts"] is not None else (int(time.time()) if mode == "live" else None)

    worker = ContextWorker(path, clock_now, mode, state["window_s"], 1800, _env("TWITTERAPI_KEY"), _rpc, context_policy=context_policy, notify=notify)
    worker.start()
    state["worker"] = worker

    radar_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
    radar_lock = threading.Lock()

    def radar_rows_cached(s: Store, w: int, clock: int, sp: int, exclude_bots: bool, ttl: float = 4.0) -> list[dict[str, Any]]:
        """One expensive computation per (clock, window, span) at a time; others reuse it for a few seconds."""
        key = f"{w}:{clock}:{sp}:{int(exclude_bots)}"
        hit = radar_cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
        with radar_lock:
            hit = radar_cache.get(key)
            if hit and time.time() - hit[0] < ttl:
                return hit[1]
            rows = radar_mod.rows_with_context(s, w, clock, sp, exclude_bots, mode == "live")
            if len(radar_cache) > 32:
                radar_cache.clear()
            radar_cache[key] = (time.time(), rows)
            return rows

    @app.get("/api/radar")
    def get_radar(
        window: str | None = None,
        span: str | None = None,
        preset: str | None = None,
        min_wallets: int = 2,
        age_max: int | None = None,
        stage: str | None = None,
        exclude_bots: int = 1,
        quality_min: float | None = None,
        mentions_max: int | None = None,
        progress_min: float | None = None,
        sort: str = "score",
        limit: int = 50,
        bundle_max: int | None = None,
        dev_buy_max: float | None = None,
        exclude_farm: int = 0,
    ) -> dict[str, Any]:
        s = store()
        try:
            st = session_state()
            w = parse_window(window) if window else st["window_s"]
            sp = parse_window(span) if span else st["span_s"]
            clock = st["clock_ts"] if st["clock_ts"] is not None else (int(time.time()) if mode == "live" else None)
            if clock is None:
                return {"clock": None, "rows": [], "total": 0, "presets": radar_mod.PRESETS, "waiting": True}
            params: dict[str, Any] = {"min_wallets": min_wallets, "age_max_s": age_max, "stage": stage, "exclude_bots": bool(exclude_bots), "quality_min": quality_min, "mentions_max": mentions_max, "progress_min": progress_min, "sort": sort}
            params.update({"bundle_max": bundle_max, "dev_buy_max": dev_buy_max, "exclude_farm": bool(exclude_farm)})  # launch-quality filters (stage 3)
            if preset and preset in radar_mod.PRESETS:
                for k, v in radar_mod.PRESETS[preset].items():
                    if k != "label":
                        params[k] = v
            rows = radar_rows_cached(s, w, clock, sp, bool(params["exclude_bots"]))
            out = radar_mod.radar(s, w, clock, sp, limit=limit, rows=rows, **params)
            out["session"] = st
            out["context"] = {"policy": context_policy, "enabled": worker.status.get("context_enabled"), "x_enabled": bool(_env("TWITTERAPI_KEY")), "last_cycle": worker.status.get("last_cycle"), "budget": budget(s)}
            out["preset"] = preset
            return out
        finally:
            s.close()

    @app.get("/api/coin/{addr}")
    def get_coin(addr: str, window: str | None = None, span: str | None = None, refresh: int = 0) -> dict[str, Any]:
        s = store()
        try:
            st = session_state()
            w = parse_window(window) if window else st["window_s"]
            sp = parse_window(span) if span else st["span_s"]
            clock = st["clock_ts"] if st["clock_ts"] is not None else int(time.time())
            addr = addr.lower()
            rpc = _rpc() if refresh else None
            ctx: dict[str, Any] = {}
            ctx["mentions"] = load_context(s, "mentions", [addr]).get(addr)
            ctx["market"] = load_context(s, "market", [addr]).get(addr)
            ctx["holders"] = load_context(s, "holders", [addr]).get(addr)
            ctx["socials"] = launch_socials(s, rpc, addr) if rpc else _cached_socials(s, addr)
            if rpc:
                fetch_exempt(s, rpc, addr)  # exact SnipeTaxExempted set from the launch tx receipt (stage 3); cached in `logs`
            if refresh:
                gt = GeckoTerminal(s)
                ctx["market"] = gt.token_market(addr, max_age_s=60)
                xm = XMentions(s, _env("TWITTERAPI_KEY"))
                lab = queries.token_labels(s, {addr})[addr]
                if xm.enabled:
                    ctx["mentions"] = xm.mentions(lab.get("symbol_raw") or lab["symbol"], addr, max_age_s=300)
                    if ctx["socials"] and ctx["socials"].get("twitter"):
                        ctx["x_account"] = xm.account(ctx["socials"]["twitter"])
                try:
                    head = int(rpc.call("eth_blockNumber", [], prefer="alchemy"), 16)
                    ctx["holders"] = fetch_holders(s, rpc, addr, head, max_age_s=300)
                except Exception as e:  # noqa: BLE001
                    ctx["holders"] = {"error": str(e)[:120]}
            return radar_mod.coin(s, addr, w, clock, sp, rpc=rpc, context=ctx, live=(mode == "live"))
        finally:
            s.close()

    @app.get("/api/alerts")
    def get_alerts(limit: int = 100) -> dict[str, Any]:
        s = store()
        try:
            rows = s.db.execute("SELECT id, created_ts, clock_ts, mode, token, symbol, rule, score, inflow, mentions_1h, price, detail, outcome_30m, outcome_60m, graduated_after FROM alerts WHERE mode=? ORDER BY id DESC LIMIT ?", (mode, limit)).fetchall()
            out = [dict(zip(["id", "created_ts", "clock_ts", "mode", "token", "symbol", "rule", "score", "inflow", "mentions_1h", "price", "detail", "outcome_30m", "outcome_60m", "graduated_after"], r)) for r in rows]
            for o in out:
                o["detail"] = json.loads(o["detail"]) if o["detail"] else None
            done = [o for o in out if o["outcome_30m"] is not None]
            return {
                "alerts": out,
                "rules": worker.rules,
                "track_record": {
                    "fired": len(out),
                    "with_outcome_30m": len(done),
                    "up_30m": sum(1 for o in done if (o["outcome_30m"] or 0) > 0),
                    "median_30m": sorted(o["outcome_30m"] for o in done)[len(done) // 2] if done else None,
                    "note": "Outcome = price change from the alert to +30/+60 min measured on indexed trades; not a return anyone earned.",
                },
                "worker": worker.status,
            }
        finally:
            s.close()

    @app.get("/api/events")
    def get_events(window: str | None = None, until: int | None = None, after: str | None = None, limit: int = 500, backfill_s: int = 300, ambiguous: int = 0) -> dict[str, Any]:
        s = store()
        try:
            st = session_state()
            w = parse_window(window) if window else st["window_s"]
            u = until if until is not None else (st["clock_ts"] if st["clock_ts"] is not None else int(time.time()))
            grades = ("direct", "clean", "ambiguous") if ambiguous else ("direct", "clean")
            out = queries.events(s, w, int(u), after, min(limit, 2000), backfill_s, grades)
            out["session"] = st
            return out
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

    # ---- traders (stage 2): wallet_stats / wallet_positions written by `stampede traders` ----
    @app.get("/api/traders")
    def get_traders(preset: str | None = "top", limit: int = 50, min_trades: int = 5, min_roi: float | None = None, min_win_rate: float | None = None, active_within_s: int | None = None, clock: int | None = None, sort: str | None = None) -> dict[str, Any]:
        s = store()
        try:
            st = session_state()
            ref = clock if clock is not None else (st["clock_ts"] if st["clock_ts"] is not None else None)
            out = traders_api.leaderboard(s, preset, limit, min_trades, min_roi, min_win_rate, active_within_s, ref, sort)
            out["session"] = st
            return out
        finally:
            s.close()

    @app.get("/api/traders/lookup")
    def get_traders_lookup(wallets: str = "") -> dict[str, Any]:
        s = store()
        try:
            return traders_api.lookup(s, [w for w in wallets.split(",") if w.strip()])
        finally:
            s.close()

    @app.get("/api/wallet/{addr}")
    def get_wallet(addr: str, clock: int | None = None) -> dict[str, Any]:
        s = store()
        try:
            return traders_api.wallet_card(s, addr, clock)
        finally:
            s.close()

    if WEB_DIST.exists():
        app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

        @app.get("/{full_path:path}")
        def spa(full_path: str):
            f = WEB_DIST / full_path
            if full_path and f.is_file():
                return FileResponse(f)
            # the shell must never be cached: hashed asset names change on every build
            return FileResponse(WEB_DIST / "index.html", headers={"Cache-Control": "no-store"})
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

    app = create_app(mode=args.mode, window=args.window, speed=getattr(args, "speed", 10.0), context_policy=getattr(args, "context", "live"), notify=bool(getattr(args, "notify", False)))
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0
