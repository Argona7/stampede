"""RADAR: coins ranked by what wallets are rotating INTO right now, with a transparent score and context.

The score is a ranking aid over *observed* inflow (distinct wallets that sold something else and then bought
this coin), not a prediction. Every component is returned next to the total.
"""
from __future__ import annotations

import math
import time
from typing import Any

from .. import chain
from ..context import pons
from ..context.market import curve_stats
from ..store import Store
from . import queries

MAIN = ("direct", "clean")
PRESETS: dict[str, dict[str, Any]] = {
    "under_radar": {"stage": "curve", "age_max_s": 4 * 3600, "mentions_max": 3, "exclude_bots": True, "sort": "score", "label": "Under radar: young, still on the curve, wallets rotating in, almost no X mentions"},
    "graduating": {"progress_min": 0.6, "stage": "curve", "sort": "progress", "label": "Graduating now: curve at 60%+ of its threshold with rotation inflow"},
    "smart_rotators": {"quality_min": 0.55, "exclude_bots": True, "sort": "quality", "label": "Smart rotators: inflow dominated by wallets whose past rotations preceded runners"},
    "all": {"label": "Everything with rotation inflow in the range"},
}


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def score(inflow_10m: int, accel: float, breadth: int, quality: float | None, mentions_1h: int | None, age_s: int | None, stage: str) -> dict[str, Any]:
    # log scales so 80 wallets still ranks above 40 instead of both pinning at the cap
    s_inflow = _clamp(math.log1p(inflow_10m) / math.log1p(150))
    s_accel = _clamp(math.log1p(max(0.0, accel)) / math.log1p(60))
    s_breadth = _clamp(math.log1p(breadth) / math.log1p(40))
    s_quality = quality if quality is not None else 0.5
    attention = _clamp(math.log1p(mentions_1h) / math.log1p(200), 0, 0.6) if mentions_1h is not None else 0.0
    age_bonus = 0.12 if (age_s is not None and age_s < 1800) else (0.06 if (age_s is not None and age_s < 2 * 3600) else 0.0)
    stage_bonus = 0.06 if stage == "curve" else 0.0
    total = 100 * _clamp(0.40 * s_inflow + 0.25 * s_accel + 0.15 * s_breadth + 0.20 * s_quality + age_bonus + stage_bonus - attention)
    return {
        "score": round(total, 1),
        "parts": {
            "inflow": round(40 * s_inflow, 1),
            "acceleration": round(25 * s_accel, 1),
            "breadth": round(15 * s_breadth, 1),
            "wallet_quality": round(20 * s_quality, 1),
            "age_bonus": round(100 * age_bonus, 1),
            "curve_bonus": round(100 * stage_bonus, 1),
            "attention_penalty": round(-100 * attention, 1),
            "quality_known": quality is not None,
            "mentions_known": mentions_1h is not None,
        },
    }


def radar(
    store: Store,
    window_s: int,
    clock: int,
    span_s: int = 1800,
    min_wallets: int = 2,
    age_max_s: int | None = None,
    stage: str | None = None,
    exclude_bots: bool = True,
    quality_min: float | None = None,
    mentions_max: int | None = None,
    progress_min: float | None = None,
    sort: str = "score",
    limit: int = 50,
    context: dict[str, Any] | None = None,
    rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """One row per coin with rotation inflow in (clock - span, clock]; then filter + sort.
    Pass precomputed `rows` (from compute_rows) to avoid recomputing when only filters change."""
    base = rows if rows is not None else compute_rows(store, window_s, clock, span_s, exclude_bots, context)
    out = []
    for row in base:
        if row["wallets_range"] < min_wallets:
            continue
        if age_max_s is not None and (row["age_s"] is None or row["age_s"] > age_max_s):
            continue
        if stage and row["stage"] != stage:
            continue
        if quality_min is not None and (row["quality"] is None or row["quality"] < quality_min):
            continue
        if mentions_max is not None and row["mentions_1h"] is not None and row["mentions_1h"] > mentions_max:
            continue
        if progress_min is not None and (row["progress"] is None or row["progress"] < progress_min):
            continue
        out.append(row)
    key = {
        "score": lambda r: -r["score"],
        "inflow": lambda r: -r["inflow_10m"],
        "accel": lambda r: -r["accel"],
        "quality": lambda r: -(r["quality"] or 0),
        "progress": lambda r: -(r["progress"] or 0),
        "age": lambda r: (r["age_s"] if r["age_s"] is not None else 10**9),
        "mentions": lambda r: (r["mentions_1h"] if r["mentions_1h"] is not None else 10**9),
    }.get(sort, lambda r: -r["score"])
    out.sort(key=key)
    bots = store.db.execute("SELECT COUNT(*) FROM wallet_scores WHERE is_bot=1").fetchone()[0] if exclude_bots else 0
    known = store.db.execute("SELECT COUNT(*) FROM wallet_scores WHERE score IS NOT NULL").fetchone()[0]
    return {"clock": clock, "window_s": window_s, "span_s": span_s, "rows": out[:limit], "total": len(out), "presets": PRESETS, "bots_excluded": bots, "wallet_scores_known": known, "computed_at": time.time()}


def rows_with_context(store: Store, window_s: int, clock: int, span_s: int, exclude_bots: bool, live: bool) -> list[dict[str, Any]]:
    """compute_rows with the cached external context of every candidate coin attached (no fetching)."""
    from .context_worker import load_context

    toks = [r[0] for r in store.db.execute("SELECT DISTINCT buy_token FROM sequences WHERE window_s=? AND grade IN ('direct','clean') AND buy_ts>? AND buy_ts<=?", (window_s, clock - span_s, clock))]
    ctx = {"mentions": load_context(store, "mentions", toks), "market": load_context(store, "market", toks) if live else {}, "holders": load_context(store, "holders", toks)}
    return compute_rows(store, window_s, clock, span_s, exclude_bots, ctx)


def compute_rows(store: Store, window_s: int, clock: int, span_s: int = 1800, exclude_bots: bool = True, context: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Unfiltered radar rows (every coin with rotation inflow in range). This is the expensive part; cache it per clock."""
    q = store.db.execute
    lo = clock - span_s
    t10 = clock - 600
    bots = {r[0] for r in q("SELECT wallet FROM wallet_scores WHERE is_bot=1")} if exclude_bots else set()
    scores = {r[0]: r[1] for r in q("SELECT wallet, score FROM wallet_scores WHERE score IS NOT NULL")}
    rows = q(
        "SELECT buy_token, sell_token, wallet, buy_ts FROM sequences WHERE window_s=? AND grade IN ('direct','clean') AND buy_ts>? AND buy_ts<=?",
        (window_s, lo, clock),
    ).fetchall()
    per: dict[str, dict[str, Any]] = {}
    for tok, src, w, ts in rows:
        if w in bots:
            continue
        d = per.setdefault(tok, {"w10": set(), "wprev": set(), "wall": set(), "src10": {}, "seq10": 0, "seq": 0, "first": ts, "last": ts, "minute": {}})
        d["wall"].add(w)
        d["seq"] += 1
        d["first"] = min(d["first"], ts)
        d["last"] = max(d["last"], ts)
        m = (ts - lo) // 60
        d["minute"][m] = d["minute"].get(m, 0) + 1
        if ts > t10:
            d["w10"].add(w)
            d["seq10"] += 1
            d["src10"][src] = d["src10"].get(src, set())
            d["src10"][src].add(w)
        else:
            d["wprev"].add(w)
    if not per:
        return []
    labels = queries.token_labels(store, set(per) | {s for d in per.values() for s in d["src10"]})
    qdec = {r[0]: r[1] for r in q("SELECT address, decimals FROM quotes")}
    qsym = {r[0]: r[1] for r in q("SELECT address, symbol FROM quotes")}
    ctx = context or {}
    toks = list(per)
    # batched per-coin facts (one query each instead of several per coin)
    launches: dict[str, dict[str, Any]] = {}
    grads: dict[str, dict[str, Any]] = {}
    first_ts: dict[str, int] = {}
    net_quote: dict[str, float] = {}
    hour_trades: dict[str, list[tuple]] = {t: [] for t in toks}
    for i in range(0, len(toks), 400):
        chunk = toks[i : i + 400]
        ph = ",".join("?" * len(chunk))
        for r in q(f"SELECT token, curve, deployer, pair_token, threshold, block, ts FROM launches WHERE token IN ({ph})", chunk):
            launches[r[0]] = {"curve": r[1], "deployer": r[2], "pair_token": r[3], "threshold": int(r[4]) if r[4] else None, "block": r[5], "ts": r[6]}
        for r in q(f"SELECT token, stage, ts, tx_hash FROM graduations WHERE token IN ({ph}) AND ts IS NOT NULL AND ts<=?", chunk + [clock]):
            grads[r[0]] = {"stage": r[1], "ts": r[2], "tx_hash": r[3]}
        for r in q(f"SELECT token, MIN(ts) FROM trades WHERE token IN ({ph}) AND ts>0 GROUP BY token", chunk):
            first_ts[r[0]] = r[1]
        for r in q(f"SELECT token, COALESCE(SUM(CASE WHEN side='buy' THEN CAST(quote_amount AS REAL) ELSE -CAST(quote_amount AS REAL) END),0) FROM trades WHERE token IN ({ph}) AND venue='curve' AND ts<=? GROUP BY token", chunk + [clock]):
            net_quote[r[0]] = float(r[1] or 0)
        for r in q(f"SELECT token, ts, side, CAST(token_amount AS REAL), CAST(quote_amount AS REAL), quote_token, wallet FROM trades WHERE token IN ({ph}) AND ts>? AND ts<=? AND CAST(quote_amount AS REAL)>0 AND CAST(token_amount AS REAL)>0 ORDER BY ts", chunk + [clock - 3600, clock]):
            hour_trades[r[0]].append(r[1:])
    out = []
    for tok, d in per.items():
        inflow10 = len(d["w10"])
        prev_rate = len(d["wprev"]) / max(1.0, (t10 - lo) / 600)  # wallets per 10 min before
        accel = inflow10 / prev_rate if prev_rate > 0 else (float(inflow10) if inflow10 else 0.0)
        breadth = len(d["src10"])
        qs = [scores[w] for w in d["w10"] if w in scores]
        quality = (sum(qs) / len(qs)) if qs else None
        la = launches.get(tok)
        age_s = None
        if la and la.get("ts"):
            age_s = clock - la["ts"]
        elif first_ts.get(tok):
            age_s = clock - first_ts[tok]
        thr = float(la["threshold"]) if la and la.get("threshold") else None
        graduated = tok in grads
        prog = {"progress": (min(1.0, net_quote.get(tok, 0.0) / thr) if thr and thr > 0 else None) if not graduated else 1.0, "graduated": graduated, "graduation": grads.get(tok), "stage": "graduated" if graduated else ("curve" if net_quote.get(tok) is not None else "unknown")}
        st = prog["stage"]
        mentions = (ctx.get("mentions") or {}).get(tok)
        m1h = mentions.get("mentions_1h") if mentions else None
        sc = score(inflow10, accel, breadth, quality, m1h, age_s, st)
        row = {
            **labels[tok],
            "inflow_10m": inflow10,
            "inflow_prev_per_10m": round(prev_rate, 2),
            "accel": round(accel, 2),
            "breadth": breadth,
            "wallets_range": len(d["wall"]),
            "sequences_range": d["seq"],
            "sequences_10m": d["seq10"],
            "sources": sorted(({"address": s, "symbol": labels[s]["symbol"], "wallets": len(ws)} for s, ws in d["src10"].items()), key=lambda x: -x["wallets"])[:3],
            "quality": round(quality, 3) if quality is not None else None,
            "age_s": age_s,
            "stage": st,
            "progress": prog["progress"],
            "graduated_ts": (prog.get("graduation") or {}).get("ts") if prog.get("graduated") else None,
            "first_inflow_ts": d["first"],
            "last_inflow_ts": d["last"],
            "spark": [d["minute"].get(i, 0) for i in range(span_s // 60)],
            "mentions_1h": m1h,
            "mentions_24h": mentions.get("mentions_24h") if mentions else None,
            **sc,
        }
        cs = stats_from_rows(hour_trades.get(tok, []), clock, qdec)
        row["price_quote"] = cs["price_quote"]
        row["quote_symbol"] = qsym.get(cs["quote"] or "", None)
        row["chg_5m"] = cs["chg_5m"]
        row["chg_1h"] = cs["chg_1h"]
        row["vol_1h_quote"] = cs["vol_1h_quote"]
        row["buyers_1h"] = cs["buyers_1h"]
        mk = (ctx.get("market") or {}).get(tok)
        if mk and mk.get("found"):
            row["market_now"] = {k: mk.get(k) for k in ("price_usd", "fdv_usd", "reserve_usd", "vol_h1", "chg_h1", "url", "fetched_at")}
        hd = (ctx.get("holders") or {}).get(tok)
        if hd and "holders" in hd:
            row["holders"] = {k: hd.get(k) for k in ("holders", "top10_share", "dev_share", "dev_sold_share", "launch_block_buyers", "as_of_block")}
        out.append(row)
    return out


def stats_from_rows(rows: list[tuple], as_of_ts: int, quote_decimals: dict[str, int]) -> dict[str, Any]:
    """Same numbers as context.market.curve_stats, computed from rows preloaded for many coins at once."""
    if not rows:
        return {"price_quote": None, "quote": None, "chg_5m": None, "chg_1h": None, "vol_1h_quote": 0.0, "trades_1h": 0, "buyers_1h": 0}
    qt = rows[-1][4]
    dec = quote_decimals.get(qt or "", 18)
    scale = 10 ** (18 - dec)

    def med(rs):
        ps = sorted((r[3] / r[2]) * scale for r in rs)
        return ps[len(ps) // 2] if ps else None

    last = med(rows[-5:])
    win5 = [r for r in rows if r[0] >= as_of_ts - 300]
    p5 = med(win5[:5]) if win5 else None
    p60 = med(rows[:5])
    buyers = {r[5] for r in rows if r[1] == "buy"}
    return {
        "price_quote": last,
        "quote": qt,
        "chg_5m": ((last / p5 - 1) * 100) if p5 and last else None,
        "chg_1h": ((last / p60 - 1) * 100) if p60 and last else None,
        "vol_1h_quote": sum(r[3] for r in rows) / (10**dec),
        "trades_1h": len(rows),
        "buyers_1h": len(buyers),
    }


def coin(store: Store, token: str, window_s: int, clock: int, span_s: int, rpc=None, context: dict[str, Any] | None = None, live: bool = False) -> dict[str, Any]:
    """Everything about one coin: on-chain lifecycle, as-of stats, inbound/outbound rotation, external context."""
    labels = queries.token_labels(store, {token})
    la = pons.launch_of(store, token) or (pons.fetch_launch(store, rpc, token) if rpc else None)
    prog = pons.curve_progress(store, token, clock)
    qdec = {r[0]: r[1] for r in store.db.execute("SELECT address, decimals FROM quotes")}
    qsym = {r[0]: r[1] for r in store.db.execute("SELECT address, symbol FROM quotes")}
    cs = curve_stats(store, token, clock, qdec)
    tokd = queries.token(store, token, clock - span_s, clock, window_s)
    ctx = context or {}
    bt = store.db.execute("SELECT ts FROM trades WHERE token=? ORDER BY block LIMIT 1", (token,)).fetchone()
    age_s = (clock - la["ts"]) if la and la.get("ts") else ((clock - bt[0]) if bt and bt[0] else None)
    return {
        **labels[token],
        "launch": la,
        "age_s": age_s,
        "progress": prog,
        "as_of": {**cs, "quote_symbol": qsym.get(cs["quote"] or "")},
        "inbound": tokd.get("inbound", []),
        "outbound": tokd.get("outbound", []),
        "buyers": tokd.get("buyers"),
        "sellers": tokd.get("sellers"),
        "new_buyers": tokd.get("new_buyers_in_range"),
        "socials": ctx.get("socials"),
        "market_now": ctx.get("market") if live else None,
        "market_cached": ctx.get("market") if not live else None,
        "mentions": ctx.get("mentions"),
        "x_account": ctx.get("x_account"),
        "holders": ctx.get("holders"),
        "links": {
            "explorer": chain.explorer_address(token),
            "geckoterminal": f"https://www.geckoterminal.com/robinhood/tokens/{token}",
            "pons": f"https://pons.fun/token/{token}",
        },
        "note": "Rotation inflow is observed order of trades by the same address; not proof of money flow, shared ownership, or future price.",
    }
