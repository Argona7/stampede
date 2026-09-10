"""`stampede rotate`: trades -> observed sequences (sell A, then buy B by the same wallet) -> edges.

Grades (docs/ALGORITHM.md):
- direct:    the sell of A and the buy of B are in the same transaction and A is the only token sold in it.
- clean:     A is the only token the wallet sold in [t2 - W, t2], and no other universe token was bought
             between the sell (t1) and the buy (t2).
- ambiguous: anything else that still fits the window (several tokens sold, or another token bought in
             between). Listed and counted separately; never part of the main edge weight.
The main weight of an edge is the number of distinct wallets with at least one direct or clean sequence.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from .store import Store


@dataclass(frozen=True)
class T:
    id: int
    tx: str
    block: int
    ts: int
    token: str
    side: str


def parse_window(s: str) -> int:
    s = str(s).strip().lower()
    mult = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    return int(float(s[:-1]) * mult[s[-1]]) if s and s[-1] in mult else int(s)


MAX_AMBIGUOUS_CANDIDATES = 5  # per buy: only the 5 most recent sold tokens get an ambiguous row (the rest are listed in `candidates`)


def sequences_for_wallet(trades: list[T], window_s: int) -> list[dict]:
    """trades of one wallet, any order. Returns sequence dicts (no wallet field).

    Linear-ish: trades are ordered by (ts, block, id); a two-pointer window over the sells tracks the
    distinct tokens sold in the last `window_s` seconds; "bought in between" is answered with prefix
    counts, so bot wallets with thousands of trades do not explode.
    """
    order = sorted(trades, key=lambda t: (t.ts, t.block, t.id))
    pos = {t.id: i for i, t in enumerate(order)}
    buys = [t for t in order if t.side == "buy"]
    sells = [t for t in order if t.side == "sell"]
    if not buys or not sells:
        return []
    # prefix count of buys by position, and per-token buy positions (for "other token bought between")
    buy_pos = [pos[b.id] for b in buys]
    buy_pos_by_token: dict[str, list[int]] = defaultdict(list)
    for b in buys:
        buy_pos_by_token[b.token].append(pos[b.id])
    # sold tokens per tx (for the direct grade)
    sold_in_tx: dict[str, set[str]] = defaultdict(set)
    for s in sells:
        sold_in_tx[s.tx].add(s.token)

    def other_buy_between(p_lo: int, p_hi: int, token: str) -> list[str]:
        """tokens (except `token`) bought strictly between positions p_lo and p_hi."""
        import bisect as _b

        i, j = _b.bisect_right(buy_pos, p_lo), _b.bisect_left(buy_pos, p_hi)
        if j <= i:
            return []
        same = _b.bisect_left(buy_pos_by_token[token], p_hi) - _b.bisect_right(buy_pos_by_token[token], p_lo)
        if (j - i) - same <= 0:
            return []
        return sorted({order[buy_pos[k]].token for k in range(i, j) if order[buy_pos[k]].token != token})

    out: list[dict] = []
    # sliding window over sells: last sell per token inside [b.ts - W, b.ts] and before b in order
    window_last: dict[str, T] = {}
    token_count: dict[str, int] = defaultdict(int)
    si_lo = 0  # first sell index still inside the window
    si_hi = 0  # first sell index not yet added (sells at/after b)
    for b in buys:
        pb = pos[b.id]
        while si_hi < len(sells) and pos[sells[si_hi].id] < pb:
            s = sells[si_hi]
            token_count[s.token] += 1
            window_last[s.token] = s
            si_hi += 1
        lo = b.ts - window_s
        while si_lo < si_hi and sells[si_lo].ts < lo:
            s = sells[si_lo]
            token_count[s.token] -= 1
            if token_count[s.token] == 0:
                del token_count[s.token]
                window_last.pop(s.token, None)
            si_lo += 1
        cands = [(tok, s) for tok, s in window_last.items() if tok != b.token and token_count.get(tok, 0) > 0]
        if not cands:
            continue
        sold_tokens = sorted(tok for tok, _ in cands)
        cands.sort(key=lambda kv: (kv[1].ts, kv[1].block, kv[1].id), reverse=True)
        single = len(sold_tokens) == 1
        for n, (a_token, s) in enumerate(cands):
            between = other_buy_between(pos[s.id], pb, b.token)
            if s.tx == b.tx:
                grade = "direct" if sold_in_tx[b.tx] == {a_token} else "ambiguous"
            elif single and not between:
                grade = "clean"
            else:
                grade = "ambiguous"
            if grade == "ambiguous" and n >= MAX_AMBIGUOUS_CANDIDATES:
                continue
            out.append({
                "sell_token": a_token,
                "buy_token": b.token,
                "sell_trade": s.id,
                "buy_trade": b.id,
                "sell_ts": s.ts,
                "buy_ts": b.ts,
                "gap_s": b.ts - s.ts,
                "grade": grade,
                "candidates": json.dumps({"sold_in_window": sold_tokens[:20], "sold_in_window_n": len(sold_tokens), "bought_between": between[:20]}) if grade == "ambiguous" else None,
            })
    return out


def rotate(store: Store, window_s: int) -> dict[str, Any]:
    store.db.execute("DELETE FROM sequences WHERE window_s=?", (window_s,))
    store.db.execute("DELETE FROM edges WHERE window_s=?", (window_s,))
    by_wallet: dict[str, list[T]] = defaultdict(list)
    for tid, tx, block, ts, token, wallet, side in store.db.execute("SELECT id, tx_hash, block, ts, token, wallet, side FROM trades"):
        by_wallet[wallet].append(T(tid, tx, block, ts, token, side))
    rows: list[tuple] = []
    n_wallets_with_both = 0
    for wallet, trades in by_wallet.items():
        if not any(t.side == "sell" for t in trades) or not any(t.side == "buy" for t in trades):
            continue
        n_wallets_with_both += 1
        for s in sequences_for_wallet(trades, window_s):
            rows.append((window_s, wallet, s["sell_token"], s["buy_token"], s["sell_trade"], s["buy_trade"], s["sell_ts"], s["buy_ts"], s["gap_s"], s["grade"], s["candidates"]))
    store.db.executemany("INSERT OR IGNORE INTO sequences(window_s,wallet,sell_token,buy_token,sell_trade,buy_trade,sell_ts,buy_ts,gap_s,grade,candidates) VALUES(?,?,?,?,?,?,?,?,?,?,?)", rows)
    store.db.execute(
        """
        INSERT INTO edges(window_s,from_token,to_token,wallets_main,wallets_direct,wallets_clean,wallets_ambiguous,sequences,first_ts,last_ts)
        SELECT window_s, sell_token, buy_token,
               COUNT(DISTINCT CASE WHEN grade IN ('direct','clean') THEN wallet END),
               COUNT(DISTINCT CASE WHEN grade='direct' THEN wallet END),
               COUNT(DISTINCT CASE WHEN grade='clean' THEN wallet END),
               COUNT(DISTINCT CASE WHEN grade='ambiguous' THEN wallet END),
               COUNT(*), MIN(buy_ts), MAX(buy_ts)
        FROM sequences WHERE window_s=? GROUP BY sell_token, buy_token
        """,
        (window_s,),
    )
    store.commit()
    q = store.db.execute
    res = {
        "window_s": window_s,
        "wallets_total": len(by_wallet),
        "wallets_with_sell_and_buy": n_wallets_with_both,
        "sequences": q("SELECT COUNT(*) FROM sequences WHERE window_s=?", (window_s,)).fetchone()[0],
        "sequences_by_grade": dict(q("SELECT grade, COUNT(*) FROM sequences WHERE window_s=? GROUP BY grade", (window_s,)).fetchall()),
        "edges": q("SELECT COUNT(*) FROM edges WHERE window_s=?", (window_s,)).fetchone()[0],
        "edges_with_main_weight": q("SELECT COUNT(*) FROM edges WHERE window_s=? AND wallets_main>0", (window_s,)).fetchone()[0],
        "edges_main_weight_ge_3": q("SELECT COUNT(*) FROM edges WHERE window_s=? AND wallets_main>=3", (window_s,)).fetchone()[0],
        "buys_without_prior_sell_in_window": q(
            "SELECT COUNT(*) FROM trades b WHERE b.side='buy' AND NOT EXISTS (SELECT 1 FROM sequences s WHERE s.window_s=? AND s.buy_trade=b.id)", (window_s,)
        ).fetchone()[0],
        "top_edges": [
            {"from": r[0], "to": r[1], "wallets_main": r[2], "direct": r[3], "clean": r[4], "ambiguous": r[5], "sequences": r[6]}
            for r in q("SELECT from_token,to_token,wallets_main,wallets_direct,wallets_clean,wallets_ambiguous,sequences FROM edges WHERE window_s=? ORDER BY wallets_main DESC, sequences DESC LIMIT 10", (window_s,))
        ],
    }
    return res


def main_rotate(args) -> int:
    store = Store()
    windows = [300, 1800, 7200] if args.all_windows else [parse_window(args.window)]
    out = []
    for w in windows:
        run_id = store.start_run("rotate", {"window_s": w})
        res = rotate(store, w)
        store.finish_run(run_id, res)
        out.append(res)
        print(f"window {w}s: {res['sequences']} sequences {res['sequences_by_grade']}, {res['edges']} edges ({res['edges_with_main_weight']} with main weight)", file=sys.stderr)
    print(json.dumps(out, indent=1))
    return 0
