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
    """Whole store (recorded sample + anything the live tail added). Rows without a known time are counted, not used as bounds."""
    r = store.db.execute("SELECT MIN(ts), MAX(ts), MAX(block), COUNT(*) FROM trades WHERE ts IS NOT NULL AND ts > 0").fetchone()
    unknown = store.db.execute("SELECT COUNT(*) FROM trades WHERE ts IS NULL OR ts = 0").fetchone()[0]
    return {"first_ts": r[0], "last_ts": r[1], "last_block": r[2], "trades": r[3] + unknown, "trades_with_time": r[3], "unknown_time_trades": unknown}


def sample_stats(store: Store, smp: dict[str, Any]) -> dict[str, Any]:
    """Counts inside the fixed sample bounds only (the numbers quoted in docs/COVERAGE.md)."""
    fr, to = smp.get("from_block"), smp.get("to_block")
    if fr is None or to is None:
        return {}
    r = store.db.execute("SELECT COUNT(*), COUNT(DISTINCT wallet), COUNT(DISTINCT token) FROM trades WHERE block BETWEEN ? AND ?", (fr, to)).fetchone()
    return {"trades": r[0], "wallets": r[1], "tokens": r[2]}


def encode_cursor(buy_ts: int, seq_id: int) -> str:
    return f"{buy_ts}:{seq_id}"


def decode_cursor(c: str | None) -> tuple[int, int] | None:
    if not c:
        return None
    try:
        a, b = c.split(":")
        return int(a), int(b)
    except ValueError:
        return None


def events(store: Store, window_s: int, until: int, after: str | None, limit: int = 500, backfill_s: int = 300, grades: tuple[str, ...] = ("direct", "clean")) -> dict[str, Any]:
    """Observed sequences as an ordered event stream.

    - with `after` (cursor "buy_ts:id"): the sequences observed after that point and up to `until`,
      oldest first. These are *new* events for a client that already holds the cursor.
    - without `after`: history for (until - backfill_s, until], oldest first, plus the cursor to
      continue from. A client uses this after a seek, and must not animate these rows as fresh.
    Ordering is total (buy_ts, id), ids are the stable `sequences.id`, so nothing is lost between polls
    as long as the client keeps paging while has_more is true.
    """
    q = store.db.execute
    gl = ",".join("?" * len(grades))
    cur = decode_cursor(after)
    if cur:
        rows = q(
            f"""SELECT s.id, s.buy_ts, s.wallet, s.sell_token, s.buy_token, s.grade, s.gap_s, tb.tx_hash, ts.tx_hash, tb.ts_exact, ts.ts
                FROM sequences s JOIN trades tb ON tb.id=s.buy_trade JOIN trades ts ON ts.id=s.sell_trade
                WHERE s.window_s=? AND s.grade IN ({gl}) AND s.buy_ts<=? AND (s.buy_ts>? OR (s.buy_ts=? AND s.id>?))
                ORDER BY s.buy_ts, s.id LIMIT ?""",
            (window_s, *grades, until, cur[0], cur[0], cur[1], limit + 1),
        ).fetchall()
        kind = "new"
    else:
        rows = q(
            f"""SELECT s.id, s.buy_ts, s.wallet, s.sell_token, s.buy_token, s.grade, s.gap_s, tb.tx_hash, ts.tx_hash, tb.ts_exact, ts.ts
                FROM sequences s JOIN trades tb ON tb.id=s.buy_trade JOIN trades ts ON ts.id=s.sell_trade
                WHERE s.window_s=? AND s.grade IN ({gl}) AND s.buy_ts>? AND s.buy_ts<=?
                ORDER BY s.buy_ts, s.id LIMIT ?""",
            (window_s, *grades, until - backfill_s, until, limit + 1),
        ).fetchall()
        kind = "history"
    has_more = len(rows) > limit
    rows = rows[:limit]
    labels = token_labels(store, {r[3] for r in rows} | {r[4] for r in rows})
    out = []
    for r in rows:
        out.append({
            "id": r[0],
            "cursor": encode_cursor(r[1], r[0]),
            "buy_ts": r[1],
            "buy_ts_exact": bool(r[9]),
            "sell_ts": r[10],
            "wallet": r[2],
            "from": r[3],
            "to": r[4],
            "from_symbol": labels[r[3]]["symbol"],
            "to_symbol": labels[r[4]]["symbol"],
            "from_short": labels[r[3]]["short"],
            "to_short": labels[r[4]]["short"],
            "grade": r[5],
            "gap_s": r[6],
            "buy_tx": r[7],
            "sell_tx": r[8],
        })
    if rows:
        next_cursor = encode_cursor(rows[-1][1], rows[-1][0])
    else:
        next_cursor = after if cur else encode_cursor(until, 0)
    return {"kind": kind, "window_s": window_s, "until": until, "count": len(out), "events": out, "next_cursor": next_cursor, "has_more": has_more}


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


def duplicate_symbols(store: Store) -> set[str]:
    """Symbols used by more than one token in the store (PONS lets anyone reuse a ticker: RBNHD x28, MARIO x13...)."""
    return {r[0] for r in store.db.execute("SELECT symbol FROM tokens WHERE symbol IS NOT NULL AND symbol<>'' GROUP BY symbol HAVING COUNT(*)>1")}


def disambiguate(symbol: str, address: str, dups: set[str]) -> str:
    """`MARIO` -> `MARIO·9b72` (last 4 hex of the address) when several coins share the ticker, so A -> B is never two different coins with one name."""
    return f"{symbol}·{address[-4:]}" if symbol in dups else symbol


def token_labels(store: Store, addrs: set[str]) -> dict[str, dict]:
    if not addrs:
        return {}
    out: dict[str, dict] = {}
    dups = duplicate_symbols(store)
    lst = list(addrs)
    for i in range(0, len(lst), 500):
        chunk = lst[i : i + 500]
        for r in store.db.execute(f"SELECT address, symbol, name, source FROM tokens WHERE address IN ({','.join('?' * len(chunk))})", chunk):
            raw = r[1] or "?"
            out[r[0]] = {"address": r[0], "symbol": disambiguate(raw, r[0], dups), "symbol_raw": raw, "name": r[2] or "", "source": r[3], "short": short(r[0]), "ambiguous_symbol": raw in dups}
    for a in addrs:
        out.setdefault(a, {"address": a, "symbol": "?", "symbol_raw": "?", "name": "", "source": None, "short": short(a), "ambiguous_symbol": False})
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
    # unique wallets per node across ALL its edges in range (an address on two edges counts once here)
    uniq_in: dict[str, int] = {}
    uniq_out: dict[str, int] = {}
    for i in range(0, len(lst), 400):
        chunk = lst[i : i + 400]
        ph = ",".join("?" * len(chunk))
        for r in q(f"SELECT buy_token, COUNT(DISTINCT wallet) FROM sequences WHERE {where} AND grade IN ('direct','clean') AND buy_token IN ({ph}) GROUP BY buy_token", args + chunk):
            uniq_in[r[0]] = r[1]
        for r in q(f"SELECT sell_token, COUNT(DISTINCT wallet) FROM sequences WHERE {where} AND grade IN ('direct','clean') AND sell_token IN ({ph}) GROUP BY sell_token", args + chunk):
            uniq_out[r[0]] = r[1]
    nodes = []
    for a in addrs:
        l = labels[a]
        st = stats.get(a, {"buys": 0, "sells": 0, "buyers": 0, "sellers": 0, "last_trade_ts": None})
        inflow = sum(e["wallets_main"] for e in edges if e["to"] == a)
        outflow = sum(e["wallets_main"] for e in edges if e["from"] == a)
        nodes.append({
            **l,
            **st,
            "in_edge_wallet_sum": inflow,  # sum over drawn edges; one address can appear on several edges
            "out_edge_wallet_sum": outflow,
            "in_unique_wallets": uniq_in.get(a, 0),  # distinct addresses that rotated INTO this coin (all edges in range)
            "out_unique_wallets": uniq_out.get(a, 0),
        })
    nodes.sort(key=lambda n: -(n["in_edge_wallet_sum"] + n["out_edge_wallet_sum"]))
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
    total = q(f"SELECT COUNT(*), COUNT(DISTINCT wallet), COUNT(DISTINCT CASE WHEN grade IN ('direct','clean') THEN wallet END) FROM sequences WHERE {where}", args).fetchone()
    edges_total = q(f"SELECT COUNT(*) FROM (SELECT 1 FROM sequences WHERE {where} GROUP BY sell_token, buy_token HAVING COUNT(DISTINCT CASE WHEN grade IN ('direct','clean') THEN wallet END) >= ?)", args + [min_wallets]).fetchone()[0]
    return {
        "window_s": window_s,
        "from_ts": from_ts,
        "to_ts": to_ts,
        "nodes": nodes,
        "edges": edges,
        "recent": recent,
        "totals": {
            "sequences_in_range": total[0],
            "wallets_in_range": total[1],
            "wallets_main_in_range": total[2],
            "edges_returned": len(edges),
            "edges_matching": edges_total,
            "truncated": edges_total > len(edges),
        },
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
    dups = duplicate_symbols(store)
    return [{"address": r[0], "symbol": disambiguate(r[1] or "?", r[0], dups), "symbol_raw": r[1] or "?", "name": r[2] or "", "short": short(r[0])} for r in rows]
