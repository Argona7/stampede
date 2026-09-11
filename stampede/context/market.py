"""Market context: GeckoTerminal (current, live only) and curve-derived numbers (as-of any clock, always available).

GeckoTerminal indexes PONS v2 curves as `pons-v2` pools and graduated pools as `uniswap-v4-robinhood`, free at
30 requests/minute. Its numbers are *now*; in replay they are labelled as such and never mixed with the
as-of-clock figures computed from indexed trades.
"""
from __future__ import annotations

import json
import threading
import time
from collections import deque
from typing import Any

import requests

from ..store import Store

GT_BASE = "https://api.geckoterminal.com/api/v2"
NETWORK = "robinhood"


class RateLimiter:
    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self.calls: deque[float] = deque()
        self.lock = threading.Lock()

    def allow(self) -> bool:
        with self.lock:
            now = time.time()
            while self.calls and now - self.calls[0] > 60:
                self.calls.popleft()
            if len(self.calls) >= self.per_minute:
                return False
            self.calls.append(now)
            return True


class GeckoTerminal:
    def __init__(self, store: Store, per_minute: int = 25, timeout: float = 8.0):
        self.store = store
        self.limiter = RateLimiter(per_minute)
        self.timeout = timeout
        self.s = requests.Session()
        self.s.headers.update({"accept": "application/json", "user-agent": "stampede/0.3"})

    def cached(self, token: str, max_age_s: float) -> dict[str, Any] | None:
        r = self.store.db.execute("SELECT fetched_at, body FROM market_cache WHERE token=?", (token,)).fetchone()
        if r and time.time() - r[0] <= max_age_s:
            d = json.loads(r[1])
            d["cache_age_s"] = round(time.time() - r[0])
            return d
        return None

    def token_market(self, token: str, max_age_s: float = 60.0, allow_fetch: bool = True) -> dict[str, Any] | None:
        c = self.cached(token, max_age_s)
        if c is not None or not allow_fetch:
            return c
        if not self.limiter.allow():
            return self.cached(token, 3600)  # stale is better than nothing, but say so
        try:
            r = self.s.get(f"{GT_BASE}/networks/{NETWORK}/tokens/{token}/pools", params={"page": 1}, timeout=self.timeout)
            bump(self.store, "geckoterminal")
            if r.status_code == 404:
                body = {"found": False, "fetched_at": time.time()}
            elif r.status_code != 200:
                return self.cached(token, 3600)
            else:
                pools = r.json().get("data", [])
                best = None
                for p in pools:
                    a = p.get("attributes", {})
                    res = float(a.get("reserve_in_usd") or 0)
                    if best is None or res > best[0]:
                        best = (res, p)
                if not best:
                    body = {"found": False, "fetched_at": time.time()}
                else:
                    p = best[1]
                    a = p["attributes"]
                    body = {
                        "found": True,
                        "fetched_at": time.time(),
                        "dex": (p.get("relationships", {}).get("dex", {}).get("data") or {}).get("id"),
                        "pool": a.get("address"),
                        "price_usd": _f(a.get("base_token_price_usd")),
                        "fdv_usd": _f(a.get("fdv_usd")),
                        "market_cap_usd": _f(a.get("market_cap_usd")),
                        "reserve_usd": _f(a.get("reserve_in_usd")),
                        "vol_m5": _f((a.get("volume_usd") or {}).get("m5")),
                        "vol_h1": _f((a.get("volume_usd") or {}).get("h1")),
                        "vol_h24": _f((a.get("volume_usd") or {}).get("h24")),
                        "chg_m5": _f((a.get("price_change_percentage") or {}).get("m5")),
                        "chg_h1": _f((a.get("price_change_percentage") or {}).get("h1")),
                        "chg_h24": _f((a.get("price_change_percentage") or {}).get("h24")),
                        "buys_h1": ((a.get("transactions") or {}).get("h1") or {}).get("buys"),
                        "sells_h1": ((a.get("transactions") or {}).get("h1") or {}).get("sells"),
                        "pool_created_at": a.get("pool_created_at"),
                        "url": f"https://www.geckoterminal.com/{NETWORK}/pools/{a.get('address')}",
                    }
        except requests.RequestException:
            return self.cached(token, 3600)
        self.store.db.execute("INSERT OR REPLACE INTO market_cache(token, fetched_at, body) VALUES(?,?,?)", (token, body["fetched_at"], json.dumps(body)))
        self.store.commit()
        body["cache_age_s"] = 0
        return body


def _f(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def bump(store: Store, name: str) -> None:
    day = time.strftime("%Y-%m-%d")
    store.db.execute(
        "INSERT INTO api_budget(name, calls, day) VALUES(?,1,?) ON CONFLICT(name) DO UPDATE SET calls=CASE WHEN api_budget.day=excluded.day THEN api_budget.calls+1 ELSE 1 END, day=excluded.day",
        (name, day),
    )


def budget(store: Store) -> dict[str, Any]:
    return {r[0]: {"calls_today": r[1], "day": r[2]} for r in store.db.execute("SELECT name, calls, day FROM api_budget")}


def curve_stats(store: Store, token: str, as_of_ts: int, quote_decimals: dict[str, int]) -> dict[str, Any]:
    """As-of-clock numbers from indexed trades: last price in quote units, price change 5m/1h, volume 1h, buyers.
    Works in replay and live alike; never depends on an external API."""
    q = store.db.execute
    rows = q(
        "SELECT ts, side, CAST(token_amount AS REAL), CAST(quote_amount AS REAL), quote_token, venue FROM trades WHERE token=? AND ts>? AND ts<=? AND CAST(quote_amount AS REAL)>0 AND CAST(token_amount AS REAL)>0 ORDER BY ts",
        (token, as_of_ts - 3600, as_of_ts),
    ).fetchall()
    if not rows:
        return {"price_quote": None, "quote": None, "chg_5m": None, "chg_1h": None, "vol_1h_quote": 0.0, "trades_1h": 0, "buyers_1h": 0}
    qt = rows[-1][4]
    dec = quote_decimals.get(qt or "", 18)
    scale = 10 ** (18 - dec)  # token has 18 decimals; express price in quote units per token

    def price(r):
        return (r[3] / r[2]) * scale

    def med(rs):
        ps = sorted(price(r) for r in rs)
        return ps[len(ps) // 2] if ps else None

    # medians over a few trades: single fills (sells net of tax, dust buys) swing the raw ratio wildly
    last = med(rows[-5:])
    win5 = [r for r in rows if r[0] >= as_of_ts - 300]
    p5 = med(win5[:5]) if win5 else None
    p60 = med(rows[:5])
    vol = sum(r[3] for r in rows) / (10**dec)
    buyers = q("SELECT COUNT(DISTINCT wallet), COUNT(*) FROM trades WHERE token=? AND side='buy' AND ts>? AND ts<=?", (token, as_of_ts - 3600, as_of_ts)).fetchone()
    return {
        "price_quote": last,
        "quote": qt,
        "chg_5m": ((last / p5 - 1) * 100) if p5 else None,
        "chg_1h": ((last / p60 - 1) * 100) if p60 else None,
        "vol_1h_quote": vol,
        "trades_1h": len(rows),
        "buyers_1h": buyers[0],
        "buys_1h": buyers[1],
        "as_of_ts": as_of_ts,
    }
