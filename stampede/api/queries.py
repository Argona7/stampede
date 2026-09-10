"""Read-only queries behind the API. Edges are aggregated on the fly from `sequences` for any time range,
so replay and live views need no recomputation of the edge table."""
from __future__ import annotations

import json
from typing import Any

from .. import chain
from ..store import Store

GRADE_MAIN = ("direct", "clean")


def short(a: str) -> str:
    return f"{a[:6]}…{a[-4:]}" if a and len(a) > 12 else a


def sample(store: Store) -> dict[str, Any]:
    fr, to = store.get_meta("sample_from_block"), store.get_meta("sample_to_block")
    b = store.blocks()
    return {
        "label": store.get_meta("sample_label", "sample"),
        "from_block": fr,
        "to_block": to,
        "from_ts": b.get(fr, (None, 0))[0] if fr else None,
        "to_ts": b.get(to, (None, 0))[0] if to else None,
    }


def data_bounds(store: Store) -> dict[str, Any]:
    r = store.db.execute("SELECT MIN(ts), MAX(ts), MAX(block), COUNT(*) FROM trades").fetchone()
    return {"first_ts": r[0], "last_ts": r[1], "last_block": r[2], "trades": r[3]}


def coverage_summary(store: Store) -> dict[str, Any]:
    q = store.db.execute
    ing = store.last_run("ingest") or {"stats": {}}
    nrm = store.last_run("normalize") or {"stats": {}}
    ver = store.last_run("verify") or {"stats": {}}
    s, n, v = ing["stats"], nrm["stats"], ver["stats"]
    return {
        "ingest_path": s.get("path"),
        "swap_logs": s.get("logs_swap_kept"),
        "transfer_logs": s.get("logs_transfer_kept"),
        "swap_txs": s.get("swap_txs"),
        "tokens": q("SELECT COUNT(*) FROM tokens").fetchone()[0],
        "pools_resolved": q("SELECT COUNT(*) FROM pools").fetchone()[0],
        "pools_unresolved": (s.get("pools") or {}).get("pools_unresolved"),
        "trades": q("SELECT COUNT(*) FROM trades").fetchone()[0],
        "wallets": q("SELECT COUNT(*) FROM wallets").fetchone()[0],
        "not_trades": n.get("notes", {}),
        "ts_exact_share": n.get("ts_exact_share"),
        "verify": {"sample": v.get("sample_size"), "completeness": v.get("completeness"), "reproducibility": v.get("reproducibility"), "tx_from_agreement": v.get("tx_from_agreement")} if v else None,
        "windows_s": [r[0] for r in q("SELECT DISTINCT window_s FROM sequences ORDER BY 1")],
        "venues": "PONS v2 bonding curves + graduated Uniswap v4 pools (V2MemeHook)",
    }


def token_labels(store: Store, addrs: set[str]) -> dict[str, dict]:
    if not addrs:
        return {}
    out: dict[str, dict] = {}
    lst = list(addrs)
    for i in range(0, len(lst), 500):
        chunk = lst[i : i + 500]
        for r in store.db.execute(f"SELECT address, symbol, name, source FROM tokens WHERE address IN ({','.join('?' * len(chunk))})", chunk):
            out[r[0]] = {"address": r[0], "symbol": r[1] or "?", "name": r[2] or "", "source": r[3], "short": short(r[0])}
    for a in addrs:
        out.setdefault(a, {"address": a, "symbol": "?", "name": "", "source": None, "short": short(a)})
    return out


def graph(store: Store, window_s: int, from_ts: int | None, to_ts: int | None, min_wallets: int = 1, limit: int = 300, include_ambiguous_only: bool = False) -> dict[str, Any]:
    q = store.db.execute
    cond = ["window_s=?"]
    args: list[Any] = [window_s]
    if from_ts is not None:
        cond.append("buy_ts>=?")
        args.append(from_ts)
    if to_ts is not None:
        cond.append("buy_ts<=?")
        args.append(to_ts)
    where = " AND ".join(cond)
    rows = q(
        f"""
        SELECT sell_token, buy_token,
               COUNT(DISTINCT CASE WHEN grade IN ('direct','clean') THEN wallet END) AS main,
               COUNT(DISTINCT CASE WHEN grade='direct' THEN wallet END) AS direct,
               COUNT(DISTINCT CASE WHEN grade='clean' THEN wallet END) AS clean,
               COUNT(DISTINCT CASE WHEN grade='ambiguous' THEN wallet END) AS amb,
               COUNT(*) AS seqs, MIN(buy_ts), MAX(buy_ts)
        FROM sequences WHERE {where}
        GROUP BY sell_token, buy_token
        HAVING {"amb" if include_ambiguous_only else "main"} >= ?
        ORDER BY main DESC, amb DESC, seqs DESC LIMIT ?
        """,
        args + [min_wallets, limit],
    ).fetchall()
    edges = [
        {"from": r[0], "to": r[1], "wallets_main": r[2], "wallets_direct": r[3], "wallets_clean": r[4], "wallets_ambiguous": r[5], "sequences": r[6], "first_ts": r[7], "last_ts": r[8]}
        for r in rows
    ]
    addrs = {e["from"] for e in edges} | {e["to"] for e in edges}
    labels = token_labels(store, addrs)
    # per-node activity in the same range (from trades)
    tcond = []
    targs: list[Any] = []
    if from_ts is not None:
        tcond.append("ts>=?")
        targs.append(from_ts)
    if to_ts is not None:
        tcond.append("ts<=?")
        targs.append(to_ts)
    twhere = (" AND " + " AND ".join(tcond)) if tcond else ""
    stats: dict[str, dict] = {}
    lst = list(addrs)
    for i in range(0, len(lst), 400):
        chunk = lst[i : i + 400]
        for r in q(
            f"""SELECT token, SUM(side='buy'), SUM(side='sell'), COUNT(DISTINCT CASE WHEN side='buy' THEN wallet END), COUNT(DISTINCT CASE WHEN side='sell' THEN wallet END), MAX(ts)
                FROM trades WHERE token IN ({','.join('?' * len(chunk))}){twhere} GROUP BY token""",
            chunk + targs,
        ):
            stats[r[0]] = {"buys": r[1], "sells": r[2], "buyers": r[3], "sellers": r[4], "last_trade_ts": r[5]}
    nodes = []
    for a in addrs:
        l = labels[a]
        st = stats.get(a, {"buys": 0, "sells": 0, "buyers": 0, "sellers": 0, "last_trade_ts": None})
        inflow = sum(e["wallets_main"] for e in edges if e["to"] == a)
        outflow = sum(e["wallets_main"] for e in edges if e["from"] == a)
        nodes.append({**l, **st, "in_wallets": inflow, "out_wallets": outflow})
    nodes.sort(key=lambda n: -(n["in_wallets"] + n["out_wallets"]))
    recent = [
        {"wallet": r[0], "from": r[1], "to": r[2], "grade": r[3], "buy_ts": r[4], "gap_s": r[5], "buy_tx": r[6]}
        for r in q(
            f"""SELECT s.wallet, s.sell_token, s.buy_token, s.grade, s.buy_ts, s.gap_s, t.tx_hash FROM sequences s JOIN trades t ON t.id=s.buy_trade
                WHERE {where} AND s.grade IN ('direct','clean') ORDER BY s.buy_ts DESC, s.id DESC LIMIT 25""",
            args,
        )
    ]
    for r in recent:
        r["from_label"] = labels.get(r["from"], token_labels(store, {r["from"]})[r["from"]])["symbol"]
        r["to_label"] = labels.get(r["to"], token_labels(store, {r["to"]})[r["to"]])["symbol"]
    total = q(f"SELECT COUNT(*), COUNT(DISTINCT wallet) FROM sequences WHERE {where}", args).fetchone()
    return {
        "window_s": window_s,
        "from_ts": from_ts,
        "to_ts": to_ts,
        "nodes": nodes,
        "edges": edges,
        "recent": recent,
        "totals": {"sequences_in_range": total[0], "wallets_in_range": total[1], "edges_returned": len(edges), "truncated": len(edges) >= limit},
    }


def trade_row(store: Store, trade_id: int, quotes: dict[str, dict]) -> dict[str, Any]:
    r = store.db.execute("SELECT tx_hash, block, ts, ts_exact, token, wallet, side, token_amount, quote_token, quote_amount, venue, flags FROM trades WHERE id=?", (trade_id,)).fetchone()
    if not r:
        return {}
    flags = json.loads(r[11] or "[]")
    qi = quotes.get(r[8] or "", {"symbol": short(r[8] or ""), "decimals": 18})
    return {
        "id": trade_id,
        "tx": r[0],
        "tx_url": chain.explorer_tx(r[0]),
        "block": r[1],
        "ts": r[2],
        "ts_exact": bool(r[3]),
        "token": r[4],
        "wallet": r[5],
        "side": r[6],
        "token_amount": float(int(r[7]) / 10**18) if r[7] else 0.0,
        "quote_symbol": qi["symbol"],
        "quote_amount": None if "two_sided_tx" in flags else float(int(r[9]) / 10 ** qi["decimals"]) if r[9] else 0.0,
        "venue": r[10],
        "flags": flags,
    }


def edge(store: Store, a: str, b: str, window_s: int, from_ts: int | None, to_ts: int | None, limit: int = 60) -> dict[str, Any]:
    q = store.db.execute
    cond = ["window_s=?", "sell_token=?", "buy_token=?"]
    args: list[Any] = [window_s, a, b]
    if from_ts is not None:
        cond.append("buy_ts>=?")
        args.append(from_ts)
    if to_ts is not None:
        cond.append("buy_ts<=?")
        args.append(to_ts)
    where = " AND ".join(cond)
    quotes = store.quotes()
    rows = q(f"SELECT id, wallet, grade, gap_s, sell_trade, buy_trade, candidates FROM sequences WHERE {where} ORDER BY CASE grade WHEN 'direct' THEN 0 WHEN 'clean' THEN 1 ELSE 2 END, buy_ts DESC LIMIT ?", args + [limit]).fetchall()
    contracts = {r[0]: r[1] for r in q("SELECT address, is_contract FROM wallets WHERE address IN (%s)" % ",".join("?" * len(rows)), [r[1] for r in rows])} if rows else {}
    seqs = []
    for r in rows:
        seqs.append({
            "id": r[0],
            "wallet": r[1],
            "wallet_url": chain.explorer_address(r[1]),
            "wallet_is_contract": bool(contracts.get(r[1])),
            "grade": r[2],
            "gap_s": r[3],
            "sell": trade_row(store, r[4], quotes),
            "buy": trade_row(store, r[5], quotes),
            "candidates": json.loads(r[6]) if r[6] else None,
        })
    counts = dict(q(f"SELECT grade, COUNT(DISTINCT wallet) FROM sequences WHERE {where} GROUP BY grade", args).fetchall())
    total = q(f"SELECT COUNT(*) FROM sequences WHERE {where}", args).fetchone()[0]
    labels = token_labels(store, {a, b})
    # tx.from enrichment when available (verify / live / on-demand fetches store it)
    txh = {s["sell"]["tx"] for s in seqs if s["sell"]} | {s["buy"]["tx"] for s in seqs if s["buy"]}
    txinfo = {}
    if txh:
        lst = list(txh)
        for r in q(f'SELECT hash, "from", "to" FROM txs WHERE hash IN ({",".join("?" * len(lst))})', lst):
            txinfo[r[0]] = {"from": r[1], "to": r[2]}
    for s in seqs:
        for side in ("sell", "buy"):
            t = s[side]
            if t and t["tx"] in txinfo:
                t["tx_from"] = txinfo[t["tx"]]["from"]
                t["tx_to"] = txinfo[t["tx"]]["to"]
    return {
        "from": labels[a],
        "to": labels[b],
        "window_s": window_s,
        "wallets_main": (counts.get("direct", 0) + counts.get("clean", 0)),
        "wallets_by_grade": counts,
        "sequences_total": total,
        "sequences": seqs,
        "truncated": total > len(seqs),
        "meaning": "Each row is one address that sold the first coin and later bought the second inside the window. Same address, observed order of trades. Not proof that the sale paid for the purchase; not proof that different addresses share an owner.",
    }


def token(store: Store, addr: str, from_ts: int | None, to_ts: int | None, window_s: int = 1800) -> dict[str, Any]:
    q = store.db.execute
    t = q("SELECT address, symbol, name, source, curve FROM tokens WHERE address=?", (addr,)).fetchone()
    if not t:
        return {}
    cond = ["token=?"]
    args: list[Any] = [addr]
    if from_ts is not None:
        cond.append("ts>=?")
        args.append(from_ts)
    if to_ts is not None:
        cond.append("ts<=?")
        args.append(to_ts)
    where = " AND ".join(cond)
    buyers = [r[0] for r in q(f"SELECT DISTINCT wallet FROM trades WHERE {where} AND side='buy'", args)]
    sellers = [r[0] for r in q(f"SELECT DISTINCT wallet FROM trades WHERE {where} AND side='sell'", args)]
    # "new" = this wallet's first buy of the token in the whole sample happens inside the range
    new_buyers = 0
    if buyers:
        first = {}
        lst = buyers
        for i in range(0, len(lst), 500):
            chunk = lst[i : i + 500]
            for r in q(f"SELECT wallet, MIN(ts) FROM trades WHERE token=? AND side='buy' AND wallet IN ({','.join('?' * len(chunk))}) GROUP BY wallet", [addr] + chunk):
                first[r[0]] = r[1]
        new_buyers = sum(1 for w in buyers if (from_ts is None or first.get(w, 0) >= from_ts))
    counts = q(f"SELECT SUM(side='buy'), SUM(side='sell'), SUM(ts_exact), COUNT(*), MIN(ts), MAX(ts) FROM trades WHERE {where}", args).fetchone()
    contract_buyers = q(f"SELECT COUNT(DISTINCT t.wallet) FROM trades t JOIN wallets w ON w.address=t.wallet WHERE {where} AND w.is_contract=1", args).fetchone()[0]
    venues = dict(q(f"SELECT venue, COUNT(*) FROM trades WHERE {where} GROUP BY venue", args).fetchall())
    curve = q("SELECT curve, pair_token FROM curves WHERE token=?", (addr,)).fetchone()
    pools = [r[0] for r in q("SELECT pool_id FROM pools WHERE currency0=? OR currency1=?", (addr, addr))]
    quotes = store.quotes()
    pair = quotes.get(curve[1], {}).get("symbol") if curve else None
    econd = ["window_s=?"]
    eargs: list[Any] = [window_s]
    if from_ts is not None:
        econd.append("buy_ts>=?")
        eargs.append(from_ts)
    if to_ts is not None:
        econd.append("buy_ts<=?")
        eargs.append(to_ts)
    ewhere = " AND ".join(econd)
    inbound = q(f"SELECT sell_token, COUNT(DISTINCT CASE WHEN grade IN ('direct','clean') THEN wallet END) m, COUNT(DISTINCT wallet) FROM sequences WHERE {ewhere} AND buy_token=? GROUP BY sell_token ORDER BY m DESC LIMIT 8", eargs + [addr]).fetchall()
    outbound = q(f"SELECT buy_token, COUNT(DISTINCT CASE WHEN grade IN ('direct','clean') THEN wallet END) m, COUNT(DISTINCT wallet) FROM sequences WHERE {ewhere} AND sell_token=? GROUP BY buy_token ORDER BY m DESC LIMIT 8", eargs + [addr]).fetchall()
    labels = token_labels(store, {r[0] for r in inbound} | {r[0] for r in outbound})
    return {
        "address": addr,
        "address_url": chain.explorer_address(addr),
        "symbol": t[1] or "?",
        "name": t[2] or "",
        "short": short(addr),
        "source": t[3],
        "curve": curve[0] if curve else None,
        "pair_symbol": pair,
        "pools": pools,
        "range": {"from_ts": from_ts, "to_ts": to_ts},
        "buys": counts[0] or 0,
        "sells": counts[1] or 0,
        "buyers": len(buyers),
        "sellers": len(sellers),
        "new_buyers_in_range": new_buyers,
        "repeat_buyers_in_range": len(buyers) - new_buyers,
        "contract_wallets": contract_buyers,
        "venues": venues,
        "first_trade_ts": counts[4],
        "last_trade_ts": counts[5],
        "coverage": {
            "trades": counts[3] or 0,
            "exact_timestamps": counts[2] or 0,
            "note": "Counts cover PONS v2 curve and graduated v4 pool trades attributed by Transfer logs inside this sample only. 'New buyer' means the wallet's first buy of this coin inside the sample lies in the selected range.",
        },
        "inbound": [{"token": labels[r[0]], "wallets_main": r[1], "wallets_all": r[2]} for r in inbound],
        "outbound": [{"token": labels[r[0]], "wallets_main": r[1], "wallets_all": r[2]} for r in outbound],
    }


def search(store: Store, text: str, limit: int = 20) -> list[dict]:
    t = text.strip().lower()
    if not t:
        return []
    rows = store.db.execute(
        "SELECT address, symbol, name FROM tokens WHERE lower(symbol) LIKE ? OR lower(name) LIKE ? OR address LIKE ? ORDER BY CASE WHEN lower(symbol)=? THEN 0 ELSE 1 END, symbol LIMIT ?",
        (f"%{t}%", f"%{t}%", f"{t}%", t, limit),
    ).fetchall()
    return [{"address": r[0], "symbol": r[1] or "?", "name": r[2] or "", "short": short(r[0])} for r in rows]
