"""TRADERS: leaderboard over `wallet_stats` (the latest full-range run of `stampede traders`), one wallet's card,
batch lookup for the live strip, and the top-decile set the radar folds into `smart_inflow`.

Everything is read from tables `stampede traders` wrote; nothing is recomputed per request. When the tables are
missing or empty every function answers "unknown" (None / empty), never 0.
"""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from .. import chain
from ..research.traders import STATS_COLS
from ..store import Store
from . import queries

PRESETS: dict[str, dict[str, Any]] = {
    "top": {"sort": "pnl", "label": "Top: biggest ETH PnL after fees (realized + unrealized at the last price), every wallet"},
    "smart": {"sort": "quality", "exclude_bots": True, "exclude_deployer": True, "label": "Smart: highest quality (win rate, ROI, exit quality, rug avoidance) with enough trades; no bots, no deployer-linked wallets"},
    "snipers": {"sniper": True, "sort": "pnl", "label": "Snipers: ≥ 30% of entries under 3 s after launch or with snipe tax paid"},
    "bots": {"bots": True, "sort": "trades", "label": "Bots: > 60 trades/h, > 500 trades, or buys of several coins in the same block"},
}
SORTS = {
    "pnl": "pnl_eth DESC",
    "realized": "realized_eth DESC",
    "quality": "quality DESC, pnl_eth DESC",
    "roi": "roi DESC",
    "win_rate": "win_rate DESC, positions_closed DESC",
    "trades": "trades DESC",
    "recent": "last_ts DESC",
}
SMART_MIN_TRADES = 5
_smart_cache: dict[str, tuple[float, dict[str, float], float | None]] = {}


def _has_tables(store: Store) -> bool:
    r = store.db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name IN ('wallet_stats','wallet_positions')").fetchone()
    return bool(r and r[0] == 2)


def latest_run(store: Store) -> dict[str, Any] | None:
    if not _has_tables(store):
        return None
    r = store.db.execute("SELECT run_ts, range_from, range_to, COUNT(*) FROM wallet_stats WHERE label='full' GROUP BY run_ts, range_from, range_to ORDER BY run_ts DESC LIMIT 1").fetchone()
    return {"run_ts": r[0], "from_ts": r[1], "to_ts": r[2], "wallets": r[3]} if r else None


def row_dict(r: tuple) -> dict[str, Any]:
    d = dict(zip(STATS_COLS, r))
    d["tags"] = json.loads(d["tags"] or "[]")
    d["pnl_by_quote"] = json.loads(d["pnl_by_quote"] or "{}")
    d["is_bot"] = bool(d["is_bot"])
    d["deployer_linked"] = bool(d["deployer_linked"])
    d["usd_complete"] = bool(d["usd_complete"])
    d["short"] = queries.short(d["wallet"])
    d["explorer"] = chain.explorer_address(d["wallet"])
    return d


def leaderboard(store: Store, preset: str | None = "top", limit: int = 50, min_trades: int = 5, min_roi: float | None = None, min_win_rate: float | None = None, active_within_s: int | None = None, clock: int | None = None, sort: str | None = None) -> dict[str, Any]:
    run = latest_run(store)
    base = {"preset": preset, "presets": PRESETS, "rows": [], "total": 0, "run": run, "range": {"from_ts": run["from_ts"], "to_ts": run["to_ts"]} if run else None, "computed_at": time.time()}
    if not run:
        base["empty_reason"] = "no wallet_stats in this store: run `stampede traders --db <store>`"
        return base
    p = PRESETS.get(preset or "top", PRESETS["top"])
    cond = ["label='full'", "run_ts=?", "trades>=?"]
    args: list[Any] = [run["run_ts"], min_trades]
    if p.get("exclude_bots"):
        cond.append("is_bot=0")
    if p.get("exclude_deployer"):
        cond.append("deployer_linked=0")
    if p.get("sniper"):
        cond.append("tags LIKE '%\"sniper\"%'")
    if p.get("bots"):
        cond.append("is_bot=1")
    if min_roi is not None:
        cond.append("roi>=?")
        args.append(min_roi)
    if min_win_rate is not None:
        cond.append("win_rate>=?")
        args.append(min_win_rate)
    if active_within_s is not None:
        # recency = the wallet's last trade in the computed range lies within the window before the reference clock
        ref = clock if clock is not None else run["to_ts"]
        cond.append("last_ts BETWEEN ? AND ?")
        args.extend([int(ref) - int(active_within_s), int(ref)])
    order = SORTS.get(sort or p.get("sort", "pnl"), SORTS["pnl"])
    where = " AND ".join(cond)
    total = store.db.execute(f"SELECT COUNT(*) FROM wallet_stats WHERE {where}", args).fetchone()[0]
    rows = store.db.execute(f"SELECT {','.join(STATS_COLS)} FROM wallet_stats WHERE {where} ORDER BY {order} LIMIT ?", args + [max(1, min(limit, 500))]).fetchall()
    base.update({"rows": [row_dict(r) for r in rows], "total": total, "sort": sort or p.get("sort"), "min_trades": min_trades})
    return base


def stats_of(store: Store, wallet: str, run: dict[str, Any]) -> dict[str, Any] | None:
    r = store.db.execute(f"SELECT {','.join(STATS_COLS)} FROM wallet_stats WHERE wallet=? AND label='full' AND run_ts=?", (wallet, run["run_ts"])).fetchone()
    return row_dict(r) if r else None


def wallet_card(store: Store, wallet: str, clock: int | None = None, trades_limit: int = 60) -> dict[str, Any]:
    wallet = wallet.lower()
    run = latest_run(store)
    stats = stats_of(store, wallet, run) if run else None
    quotes = store.quotes()
    positions: list[dict[str, Any]] = []
    if run:
        prow = store.db.execute(
            "SELECT token, quote_token, entry_ts, exit_ts, closed, buys, sells, cost_quote, proceeds_quote, pnl_quote, pnl_usd, fees_quote, unrealized_quote, hold_s, buyer_rank, since_launch_s, sniper, snipe_paid, exit_quality, rug_after, entry_tx, exit_tx "
            "FROM wallet_positions WHERE wallet=? AND range_from=? AND range_to=? ORDER BY entry_ts DESC LIMIT 200",
            (wallet, run["from_ts"], run["to_ts"]),
        ).fetchall()
        labels = queries.token_labels(store, {r[0] for r in prow})
        for r in prow:
            qi = quotes.get(r[1] or chain.NATIVE, {"symbol": queries.short(r[1] or ""), "decimals": 18})
            positions.append({
                "token": labels[r[0]], "quote_symbol": qi["symbol"], "entry_ts": r[2], "exit_ts": r[3], "closed": bool(r[4]), "buys": r[5], "sells": r[6],
                "cost_quote": r[7], "proceeds_quote": r[8], "pnl_quote": r[9], "pnl_usd": r[10], "fees_quote": r[11], "unrealized_quote": r[12], "hold_s": r[13],
                "buyer_rank": r[14], "since_launch_s": r[15], "sniper": bool(r[16]), "snipe_paid": bool(r[17]), "exit_quality": r[18], "rug_after": (None if r[19] is None else bool(r[19])),
                "entry_tx": r[20], "entry_tx_url": chain.explorer_tx(r[20]) if r[20] else None, "exit_tx": r[21], "exit_tx_url": chain.explorer_tx(r[21]) if r[21] else None,
            })
    cond = "wallet=?" + (" AND ts<=?" if clock is not None else "")
    targs: list[Any] = [wallet] + ([clock] if clock is not None else [])
    trows = store.db.execute(f"SELECT id, tx_hash, block, ts, ts_exact, token, side, token_amount, quote_token, quote_amount, venue, fee_raw, tax_raw, snipe_raw FROM trades WHERE {cond} ORDER BY ts DESC, id DESC LIMIT ?", targs + [trades_limit]).fetchall()
    crows = store.db.execute(f"SELECT token, COUNT(*), SUM(side='buy'), MIN(ts), MAX(ts) FROM trades WHERE {cond} GROUP BY token ORDER BY MAX(ts) DESC LIMIT 100", targs).fetchall()
    labels = queries.token_labels(store, {r[5] for r in trows} | {r[0] for r in crows})
    trades = []
    for r in trows:
        qi = quotes.get(r[8] or chain.NATIVE, {"symbol": queries.short(r[8] or ""), "decimals": 18})
        dec = qi["decimals"]
        qa = int(r[9]) if r[9] else 0
        trades.append({
            "id": r[0], "tx": r[1], "tx_url": chain.explorer_tx(r[1]), "block": r[2], "ts": r[3], "ts_exact": bool(r[4]), "token": labels[r[5]], "side": r[6],
            "token_amount": int(r[7]) / 1e18 if r[7] else 0.0, "quote_symbol": qi["symbol"], "quote_amount": (qa / 10**dec) if qa > 0 else None, "venue": r[10],
            "fee_quote": (int(r[11]) / 10**dec) if (r[11] is not None and r[10] == "curve") else None, "tax_quote": (int(r[12]) / 10**dec) if (r[12] is not None and r[10] == "curve") else None,
            "snipe_paid": bool(r[13] and int(r[13]) > 0),
        })
    coins = [{**labels[r[0]], "trades": r[1], "buys": r[2], "sells": r[1] - (r[2] or 0), "first_ts": r[3], "last_ts": r[4]} for r in crows]
    total_trades = store.db.execute(f"SELECT COUNT(*) FROM trades WHERE {cond}", targs).fetchone()[0]
    contract = store.db.execute("SELECT is_contract FROM wallets WHERE address=?", (wallet,)).fetchone()
    launched = store.db.execute("SELECT COUNT(*) FROM launches WHERE deployer=?", (wallet,)).fetchone()[0]
    copy_note = None
    if stats:
        bits = []
        if stats["sniper_share"] and stats["sniper_share"] >= 0.3:
            bits.append(f"{round(stats['sniper_share'] * 100)}% of its entries were under 3 s after launch: those cannot be copied, the snipe tax (up to 99%) hits the follower")
        if stats["median_hold_s"] is not None and stats["median_hold_s"] < 60:
            bits.append(f"median hold {int(stats['median_hold_s'])} s: the exit is usually gone before a copier can act")
        if stats["is_bot"]:
            bits.append("tagged bot: its edge is speed, not selection")
        if stats["deployer_linked"]:
            bits.append("deployer-linked: it buys without snipe tax or holds the creator side of the fee")
        bits.append("a copier pays the 1% fee and the creator tax twice and buys after this wallet moved the curve; see docs/RESEARCH-TRADERS.md for the measured copy-test")
        copy_note = " · ".join(bits)
    return {
        "address": wallet,
        "short": queries.short(wallet),
        "explorer": chain.explorer_address(wallet),
        "run": run,
        "stats": stats,
        "positions": positions,
        "trades": trades,
        "trades_total": total_trades,
        "coins": coins,
        "is_contract": bool(contract[0]) if contract and contract[0] is not None else None,
        "launched_coins": launched,
        "copy_test": copy_note,
        "note": "Same address, observed trades, FIFO with the fees the curve charged. PnL in quote units; USD only where an hourly rate exists. Nothing here says who controls the wallet or why it traded.",
    }


def lookup(store: Store, wallets: list[str]) -> dict[str, Any]:
    run = latest_run(store)
    out: dict[str, Any] = {"run": run, "wallets": {}, "top_decile_min_quality": None}
    if not run or not wallets:
        return out
    ws = [w.lower() for w in wallets][:400]
    smart, thr = smart_set(store)
    out["top_decile_min_quality"] = thr
    for i in range(0, len(ws), 200):
        chunk = ws[i : i + 200]
        ph = ",".join("?" * len(chunk))
        for r in store.db.execute(f"SELECT wallet, quality, pnl_eth, realized_eth, trades, positions_closed, win_rate, is_bot, deployer_linked, tags, last_ts, sniper_share FROM wallet_stats WHERE label='full' AND run_ts=? AND wallet IN ({ph})", [run["run_ts"]] + chunk):
            out["wallets"][r[0]] = {"quality": r[1], "pnl_eth": r[2], "realized_eth": r[3], "trades": r[4], "positions_closed": r[5], "win_rate": r[6], "is_bot": bool(r[7]), "deployer_linked": bool(r[8]), "tags": json.loads(r[9] or "[]"), "last_ts": r[10], "sniper_share": r[11], "smart": r[0] in smart}
    return out


def smart_set(store: Store) -> tuple[dict[str, float], float | None]:
    """Top-decile wallets by quality among eligible ones (≥ SMART_MIN_TRADES trades, not bot, not deployer-linked) in the
    latest full-range run: wallet -> quality, plus the decile threshold. Cached per store path and run."""
    try:
        run = latest_run(store)
    except sqlite3.OperationalError:
        return {}, None
    if not run:
        return {}, None
    key = str(store.path)
    hit = _smart_cache.get(key)
    if hit and hit[0] == run["run_ts"]:
        return hit[1], hit[2]
    rows = store.db.execute("SELECT wallet, quality FROM wallet_stats WHERE label='full' AND run_ts=? AND trades>=? AND is_bot=0 AND deployer_linked=0 AND quality IS NOT NULL ORDER BY quality DESC", (run["run_ts"], SMART_MIN_TRADES)).fetchall()
    n = max(1, len(rows) // 10) if rows else 0
    top = {r[0]: r[1] for r in rows[:n]}
    thr = rows[n - 1][1] if n else None
    _smart_cache[key] = (run["run_ts"], top, thr)
    return top, thr


def smart_inflow(store: Store, wallets: set[str]) -> dict[str, Any] | None:
    """For a set of inflow wallets: how many are top-decile traders and their mean quality; None when no stats exist."""
    smart, thr = smart_set(store)
    if not smart:
        return None
    hits = [smart[w] for w in wallets if w in smart]
    return {"count": len(hits), "mean_quality": round(sum(hits) / len(hits), 3) if hits else None, "threshold": thr}
