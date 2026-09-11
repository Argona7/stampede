"""Background refresh of external context for the coins the radar currently ranks highest, plus the alert
engine with outcome tracking. Runs inside the API process; never blocks a request.

Policy: external "now" data (GeckoTerminal, X, holders) is fetched for the *live* clock only, or when the
server was started with context=always. In replay the radar keeps to as-of-clock on-chain numbers.
"""
from __future__ import annotations

import json
import subprocess
import threading
import time
from typing import Any, Callable

from ..context.holders import holders as fetch_holders
from ..context.market import GeckoTerminal, curve_stats
from ..context.pons import launch_socials
from ..context.xmentions import XMentions
from ..store import Store
from . import radar as radar_mod


class ContextWorker(threading.Thread):
    def __init__(self, db_path, clock_fn: Callable[[], int | None], mode: str, window_s: int, span_s: int, api_key: str | None, rpc_factory: Callable[[], Any], context_policy: str = "live", notify: bool = False, top_n: int = 30):
        super().__init__(daemon=True, name="context-worker")
        self.db_path = db_path
        self.clock_fn = clock_fn
        self.mode = mode
        self.window_s = window_s
        self.span_s = span_s
        self.api_key = api_key
        self.rpc_factory = rpc_factory
        self.policy = context_policy
        self.notify = notify
        self.top_n = top_n
        self.status: dict[str, Any] = {"running": False, "last_cycle": None, "last_error": None, "cycles": 0, "context_enabled": False, "alerts_fired": 0}
        self._stop = threading.Event()
        self.rules = {"under_radar_top5": {"score_min": 60, "mentions_max": 3, "rank_max": 5, "inflow_min": 8, "inflow_max": 40, "age_max_s": 3600, "chg_10m_max": 100}}  # inflow 8-40 & age < 1 h & not already +100%: 20% runner rate (3.8x base) in docs/RESEARCH-RUNNERS.md

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        self.status["running"] = True
        store = Store(self.db_path)
        rpc = None
        gt = GeckoTerminal(store)
        xm = XMentions(store, self.api_key)
        while not self._stop.is_set():
            try:
                clock = self.clock_fn()
                enabled = self.policy == "always" or (self.policy == "live" and self.mode == "live")
                self.status["context_enabled"] = enabled
                if clock is not None:
                    rows = radar_mod.rows_with_context(store, self.window_s, clock, self.span_s, True, self.mode == "live")
                    rad = radar_mod.radar(store, self.window_s, clock, self.span_s, min_wallets=2, exclude_bots=True, limit=self.top_n, rows=rows)
                    rows = rad["rows"]
                    if enabled and rows:
                        if rpc is None:
                            rpc = self.rpc_factory()
                        head = None
                        try:
                            head = int(rpc.call("eth_blockNumber", [], prefer="alchemy"), 16)
                        except Exception:  # noqa: BLE001
                            head = None
                        for i, r in enumerate(rows):
                            if self._stop.is_set():
                                break
                            tok = r["address"]
                            if xm.enabled:
                                xm.mentions(r.get("symbol_raw") or r["symbol"], tok, max_age_s=300)
                            gt.token_market(tok, max_age_s=60)
                            if i < 10 and head:
                                fetch_holders(store, rpc, tok, head, max_age_s=600)
                            if i < 10:
                                launch_socials(store, rpc, tok)
                    self._alerts(store, clock, rad, xm)
                self.status["last_cycle"] = time.time()
                self.status["cycles"] += 1
                self.status["last_error"] = None
            except Exception as e:  # noqa: BLE001
                self.status["last_error"] = f"{type(e).__name__}: {str(e)[:160]}"
            self._stop.wait(20)
        store.close()
        self.status["running"] = False

    # ---- alerts ----
    def _alerts(self, store: Store, clock: int, rad: dict[str, Any], xm: XMentions) -> None:
        rule = self.rules["under_radar_top5"]
        mentions = load_context(store, "mentions", [r["address"] for r in rad["rows"]])
        fired = 0
        for rank, r in enumerate(rad["rows"][: rule["rank_max"]], 1):
            m = mentions.get(r["address"])
            m1h = m.get("mentions_1h") if m else None
            if r["score"] < rule["score_min"] or r["inflow_10m"] < rule["inflow_min"] or r["inflow_10m"] > rule["inflow_max"]:
                continue
            if r.get("age_s") is None or r["age_s"] > rule["age_max_s"]:
                continue
            if r.get("chg_10m") is not None and r["chg_10m"] >= rule["chg_10m_max"]:
                continue
            if m1h is not None and m1h > rule["mentions_max"]:
                continue
            recent = store.db.execute("SELECT 1 FROM alerts WHERE token=? AND mode=? AND clock_ts>? LIMIT 1", (r["address"], self.mode, clock - 1800)).fetchone()
            if recent:
                continue
            store.db.execute(
                "INSERT INTO alerts(created_ts, clock_ts, mode, token, symbol, rule, score, inflow, mentions_1h, price, detail) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (int(time.time()), clock, self.mode, r["address"], r["symbol"], "under_radar_top5", r["score"], r["inflow_10m"], m1h, r.get("price_quote"), json.dumps({"rank": rank, "sources": r["sources"], "accel": r["accel"], "breadth": r["breadth"], "age_s": r["age_s"], "stage": r["stage"], "mentions_known": m1h is not None})),
            )
            fired += 1
            if self.notify:
                self._notify(f"STAMPEDE · {r['symbol']}", f"{r['inflow_10m']} wallets rotated in (10 min) · score {r['score']:.0f} · X 1h: {m1h if m1h is not None else 'n/a'}")
        store.commit()
        self.status["alerts_fired"] += fired
        # outcomes: 30/60 min later (in clock time), measured from indexed trades
        qdec = {r[0]: r[1] for r in store.db.execute("SELECT address, decimals FROM quotes")}
        for aid, tok, cts, price, o30, o60 in store.db.execute("SELECT id, token, clock_ts, price, outcome_30m, outcome_60m FROM alerts WHERE mode=? AND (outcome_30m IS NULL OR outcome_60m IS NULL) AND clock_ts<=?", (self.mode, clock - 1800)).fetchall():
            upd: dict[str, Any] = {}
            if o30 is None and clock >= cts + 1800:
                cs = curve_stats(store, tok, cts + 1800, qdec)
                upd["outcome_30m"] = ((cs["price_quote"] / price - 1) * 100) if cs["price_quote"] and price else None
            if o60 is None and clock >= cts + 3600:
                cs = curve_stats(store, tok, cts + 3600, qdec)
                upd["outcome_60m"] = ((cs["price_quote"] / price - 1) * 100) if cs["price_quote"] and price else None
                g = store.db.execute("SELECT ts FROM graduations WHERE token=? AND ts BETWEEN ? AND ?", (tok, cts, cts + 3600)).fetchone()
                upd["graduated_after"] = 1 if g else 0
            if upd:
                sets = ", ".join(f"{k}=?" for k in upd) + ", outcome_checked_ts=?"
                store.db.execute(f"UPDATE alerts SET {sets} WHERE id=?", (*upd.values(), int(time.time()), aid))
        store.commit()

    @staticmethod
    def _notify(title: str, text: str) -> None:
        try:
            subprocess.run(["osascript", "-e", f'display notification "{text}" with title "{title}"'], timeout=5, check=False)
        except Exception:  # noqa: BLE001
            pass


def load_context(store: Store, kind: str, tokens: list[str]) -> dict[str, dict[str, Any]]:
    """Cached context blobs for a set of tokens (no fetching)."""
    if not tokens:
        return {}
    out: dict[str, dict[str, Any]] = {}
    ph = ",".join("?" * len(tokens))
    if kind == "mentions":
        for k, fa, body in store.db.execute(f"SELECT key, fetched_at, body FROM x_cache WHERE key IN ({','.join('?' * len(tokens))})", [f"m:{t}" for t in tokens]):
            d = json.loads(body)
            d["fetched_at"] = fa
            out[k[2:]] = d
    elif kind == "market":
        for t, fa, body in store.db.execute(f"SELECT token, fetched_at, body FROM market_cache WHERE token IN ({ph})", tokens):
            d = json.loads(body)
            d["fetched_at"] = fa
            out[t] = d
    elif kind == "holders":
        for t, fa, body in store.db.execute(f"SELECT token, fetched_at, body FROM holders_cache WHERE token IN ({ph})", tokens):
            d = json.loads(body)
            d["fetched_at"] = fa
            out[t] = d
    return out
