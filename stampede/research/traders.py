"""Trader intelligence: who actually earns on PONS after fees, and does it persist?

    .venv/bin/python -m stampede traders --db data/research-14d.sqlite --out docs/RESEARCH-TRADERS.md

Three streaming passes over `trades` (constant memory, indexes trades_token / trades_wallet):

1. token pass (ORDER BY token, ts): per coin, the minute-median price series, the last mark, the mark at the
   walk-forward split, the n-th distinct buyer since launch for every buy, the snipe-tax picture (who paid,
   who did not while others paid), and for every sell the exit quality (exit price / max price in the next
   15 min) and whether the coin fell >= 80% within 60 min after that exit. Facts are kept in flat arrays
   indexed by trade id (a few bytes per trade).
2. wallet pass (ORDER BY wallet, ts): FIFO ledger per (wallet, coin) with real fees: a buy adds a lot at
   cost = quote_amount + fee + tax; a sell realises PnL against the oldest lots (net quote received);
   open lots are marked at the last minute-median price. The same ledger runs three times per wallet: the
   full range, the first half and the second half (walk-forward).
3. copy-test: for the top-K wallets by first-half quality, replay their second-half buys with a 0.02 ETH
   order on the PONS constant-product curve (reserves rebuilt from the coin's cumulative flows, 1% fee +
   creator tax, our own impact included) and sell when they sell or after 30 min.

Nothing here is a return anyone earned; every number is a measurement on indexed trades with stated rules.
"""
from __future__ import annotations

import argparse
import bisect
import json
import random
import statistics
import sys
import time
from array import array
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import chain
from ..context.fx import Fx
from ..store import Store

SNIPE_S = 3  # entries under 3 s after launch pay snipe tax on PONS: our sniper threshold
EXIT_Q_S = 900  # exit quality horizon: max price in the following 15 min
RUG_S = 3600  # rug horizon: price fell >= RUG_DROP within 60 min after the exit
RUG_DROP = 0.8
DUST_SHARE = 0.01  # a position is closed when <= 1% of its peak inventory is left (the dust is written off)
BOT_TRADES_PER_HOUR = 60
BOT_TRADES = 500
BOT_MULTI_COIN_BLOCKS = 3  # blocks in which the wallet bought >= 2 different coins
COPY_ORDER_WEI = 20_000_000_000_000_000  # 0.02 ETH
COPY_HOLD_S = 1800
FEE_BPS = chain.CURVE_FEE_BPS
ETH_QUOTES = {chain.NATIVE, chain.WETH}
INFRA = set(chain.KNOWN_INFRA) | {chain.PONS_V2_LOCKER, chain.PONS_V2_LAUNCH_DEPLOYER, chain.PONS_V2_FEE_ESCROW}
QUALITY_WEIGHTS = {"win": 0.35, "roi": 0.25, "exit": 0.20, "rug": 0.20}
QUALITY_SHRINK_N = 5  # quality = n/(n+5) * measured + 5/(n+5) * 0.5 over n closed positions

TABLES = """
CREATE TABLE IF NOT EXISTS wallet_stats (
  wallet TEXT, label TEXT, range_from INTEGER, range_to INTEGER, run_ts REAL,
  trades INTEGER, buys INTEGER, sells INTEGER, coins INTEGER, positions_closed INTEGER, positions_open INTEGER, wins INTEGER,
  realized_eth REAL, unrealized_eth REAL, cost_eth REAL, fees_eth REAL, pnl_eth REAL,
  realized_usd REAL, unrealized_usd REAL, cost_usd REAL, pnl_usd REAL, usd_complete INTEGER,
  roi REAL, roi_realized REAL, win_rate REAL, median_hold_s REAL, trades_per_hour REAL,
  buyer_rank_median REAL, sniper_share REAL, exit_quality REAL, rug_avoid REAL, consistency REAL,
  weeks_active INTEGER, weeks_positive INTEGER, multi_coin_blocks INTEGER,
  is_bot INTEGER, deployer_linked INTEGER, quality REAL, tags TEXT, pnl_by_quote TEXT,
  first_ts INTEGER, last_ts INTEGER, fees_unknown INTEGER, unpriced INTEGER, unmatched_sells INTEGER, updated_at REAL,
  PRIMARY KEY (wallet, range_from, range_to));
CREATE INDEX IF NOT EXISTS wallet_stats_label ON wallet_stats(label, run_ts, quality);
CREATE INDEX IF NOT EXISTS wallet_stats_pnl ON wallet_stats(label, run_ts, pnl_eth);
CREATE TABLE IF NOT EXISTS wallet_positions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, wallet TEXT, token TEXT, quote_token TEXT, range_from INTEGER, range_to INTEGER,
  entry_ts INTEGER, exit_ts INTEGER, closed INTEGER, buys INTEGER, sells INTEGER,
  cost_quote REAL, proceeds_quote REAL, pnl_quote REAL, pnl_usd REAL, fees_quote REAL, unrealized_quote REAL,
  hold_s INTEGER, buyer_rank INTEGER, since_launch_s REAL, sniper INTEGER, snipe_paid INTEGER, exit_quality REAL, rug_after INTEGER,
  entry_tx TEXT, exit_tx TEXT);
CREATE INDEX IF NOT EXISTS wallet_positions_wallet ON wallet_positions(wallet, entry_ts);
CREATE INDEX IF NOT EXISTS wallet_positions_token ON wallet_positions(token, entry_ts);
"""

RUG_UNKNOWN = 2


def ensure_tables(store: Store) -> None:
    store.db.executescript(TABLES)
    store.commit()


def median(xs: list[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def spearman(x: list[float], y: list[float]) -> float | None:
    if len(x) < 3:
        return None
    try:
        return statistics.correlation(x, y, method="ranked")
    except statistics.StatisticsError:
        return None


def bootstrap_ci(xs: list[float], n: int = 1000, seed: int = 7) -> tuple[float, float] | None:
    if len(xs) < 2:
        return None
    rng = random.Random(seed)
    k = len(xs)
    means = sorted(sum(rng.choices(xs, k=k)) / k for _ in range(n))
    return means[int(0.025 * n)], means[min(n - 1, int(0.975 * n))]


def _int(x: Any) -> int:
    try:
        return int(x) if x not in (None, "") else 0
    except (TypeError, ValueError):
        return 0


# ---- launch times ------------------------------------------------------------------------------------------------
class Launches:
    """token -> launch facts. `launches.ts` is often NULL in backfilled stores (the lifecycle pass runs before the
    blocks are known), so the time is taken from the exact block timestamps in `blocks` (interpolated between
    anchors, marked inexact) when the launch block is inside the store's block range."""

    def __init__(self, store: Store):
        q = store.db.execute
        from ..normalize import Interp

        anchors = {r[0]: r[1] for r in q("SELECT number, timestamp FROM blocks WHERE exact=1 AND timestamp>0")}
        self.first_block = min(anchors) if anchors else None
        interp = Interp(anchors)
        self.by_token: dict[str, dict[str, Any]] = {}
        self.deployers: set[str] = set()
        for token, deployer, block, ts, threshold in q("SELECT token, deployer, block, ts, threshold FROM launches"):
            exact = 1
            if ts is None and block is not None and self.first_block is not None and block >= self.first_block:
                ts, exact = interp(block)
            self.by_token[token] = {"ts": ts, "exact": exact, "deployer": deployer, "block": block, "threshold": _int(threshold) or None}
            if deployer:
                self.deployers.add(deployer)

    def ts(self, token: str) -> int | None:
        la = self.by_token.get(token)
        return la["ts"] if la else None

    def deployer(self, token: str) -> str | None:
        la = self.by_token.get(token)
        return la["deployer"] if la else None


# ---- token pass ---------------------------------------------------------------------------------------------------
class Facts:
    """Per-trade facts from the token pass, indexed by trade id."""

    def __init__(self, max_id: int):
        n = max_id + 1
        self.rank = array("i", [0]) * n  # n-th distinct buyer since launch (0 = unknown: launch before the range)
        self.exitq = array("f", [float("nan")]) * n  # sells: exit price / max minute-median price in the next 15 min
        self.rug = array("b", [RUG_UNKNOWN]) * n  # sells: 1 = fell >= 80% within 60 min, 0 = did not, 2 = no trades after
        self.exempt = array("b", [0]) * n  # buys: paid no snipe tax while later buyers of the coin still did


def token_pass(store: Store, lo: int, hi: int, mid: int, launches: Launches, facts: Facts, progress=None) -> dict[str, Any]:
    """One streaming pass ordered by (token, ts); returns marks: token -> {last, mid, quote, last_ts}."""
    q = store.db.execute
    marks: dict[str, dict[str, Any]] = {}
    cur = q("SELECT id, token, ts, wallet, side, token_amount, quote_amount, quote_token, venue, snipe_raw FROM trades WHERE ts>=? AND ts<=? ORDER BY token, ts", (lo, hi))
    buf: list[tuple] = []
    tok: str | None = None
    n_rows = n_tokens = 0
    t0 = time.time()
    for row in cur:
        if row[1] != tok:
            if buf:
                _process_token(tok, buf, lo, hi, mid, launches, facts, marks)
                n_tokens += 1
            buf = []
            tok = row[1]
        buf.append(row)
        n_rows += 1
        if progress and n_rows % 2_000_000 == 0:
            progress(f"token pass: {n_rows:,} trades, {n_tokens:,} coins, {time.time() - t0:.0f} s")
    if buf:
        _process_token(tok, buf, lo, hi, mid, launches, facts, marks)
        n_tokens += 1
    return {"marks": marks, "rows": n_rows, "tokens": n_tokens, "seconds": round(time.time() - t0, 1)}


def _process_token(tok: str, rows: list[tuple], lo: int, hi: int, mid: int, launches: Launches, F: Facts, marks: dict[str, dict[str, Any]]) -> None:
    rows.sort(key=lambda r: (r[2], r[0]))
    per_min: dict[int, list[float]] = defaultdict(list)
    quote = None
    for r in rows:
        ta, qa = _int(r[5]), _int(r[6])
        if ta > 0 and qa > 0:
            per_min[r[2] // 60].append(qa / ta)
            quote = r[7]
    M = sorted(per_min)
    P = [median(per_min[m]) or 0.0 for m in M]
    if M:
        i_mid = bisect.bisect_right(M, mid // 60) - 1
        marks[tok] = {"last": P[-1], "last_ts": M[-1] * 60, "mid": P[i_mid] if i_mid >= 0 else None, "quote": quote}
    launch_ts = launches.ts(tok)
    rank_known = launch_ts is not None and launch_ts >= lo  # buyers before the range are unobserved
    snipe_last_ts = max((r[2] for r in rows if r[4] == "buy" and _int(r[9]) > 0), default=None)
    seen: dict[str, int] = {}
    for r in rows:
        tid, ts, w, side = r[0], r[2], r[3], r[4]
        if side == "buy":
            if rank_known:
                rk = seen.get(w)
                if rk is None:
                    rk = len(seen) + 1
                    seen[w] = rk
                F.rank[tid] = rk
            if snipe_last_ts is not None and ts <= snipe_last_ts and _int(r[9]) == 0 and _int(r[6]) > 0 and r[8] == "curve":
                F.exempt[tid] = 1
            continue
        ta, qa = _int(r[5]), _int(r[6])
        if ta <= 0 or qa <= 0 or not M:
            continue
        p = qa / ta
        m = ts // 60
        i = bisect.bisect_right(M, m)
        # a horizon that runs past the end of the data is not observed: the fact stays unknown (never "sold the top")
        if ts + EXIT_Q_S <= hi:
            j = bisect.bisect_right(M, m + EXIT_Q_S // 60)
            if j > i:
                mx = max(P[i:j])
                if mx > 0:
                    F.exitq[tid] = min(1.0, p / mx)
        if ts + RUG_S <= hi:
            k = bisect.bisect_right(M, m + RUG_S // 60)
            if k > i:
                mn = min(P[i:k])
                F.rug[tid] = 1 if mn <= (1 - RUG_DROP) * p else 0


# ---- wallet pass: FIFO ledgers -------------------------------------------------------------------------------------
class Position:
    __slots__ = ("token", "quote", "entry_ts", "exit_ts", "buys", "sells", "cost", "cost_matched", "proceeds", "pnl", "fees", "tokens_in", "inventory", "peak", "rank", "since_launch", "sniper", "snipe_paid", "exitq_w", "exitq_num", "rug", "entry_tx", "exit_tx", "usd_pnl", "usd_ok", "exempt")

    def __init__(self, token: str, quote: str | None, ts: int, tx: str | None):
        self.token = token
        self.quote = quote
        self.entry_ts = ts
        self.exit_ts: int | None = None
        self.buys = self.sells = 0
        self.cost = self.cost_matched = self.proceeds = self.pnl = self.fees = 0
        self.tokens_in = self.inventory = self.peak = 0
        self.rank = 0
        self.since_launch: float | None = None
        self.sniper = False
        self.snipe_paid = False
        self.exempt = False
        self.exitq_w = 0.0  # proceeds-weighted exit quality
        self.exitq_num = 0.0
        self.rug = RUG_UNKNOWN
        self.entry_tx = tx
        self.exit_tx: str | None = None
        self.usd_pnl = 0.0
        self.usd_ok = True

    @property
    def exit_quality(self) -> float | None:
        return self.exitq_num / self.exitq_w if self.exitq_w > 0 else None


def wallet_ledger(rows: list[tuple], lo: int, hi: int, marks: dict[str, dict[str, Any]], mark_key: str, facts: Facts, launches: Launches, fx: Fx | None, qdec: dict[str, int], fees: str) -> dict[str, Any]:
    """FIFO ledger of one wallet over [lo, hi]. rows: (id, wallet, token, ts, block, side, token_amount, quote_token,
    quote_amount, venue, fee_raw, tax_raw, snipe_raw, tx_hash) sorted by (ts, id)."""
    lots: dict[str, deque] = defaultdict(deque)  # token -> deque of [tokens_left, cost_left]
    inv: dict[str, int] = defaultdict(int)
    open_pos: dict[str, Position] = {}
    closed: list[Position] = []
    per_quote: dict[str, dict[str, float]] = defaultdict(lambda: {"realized": 0.0, "unrealized": 0.0, "cost": 0.0, "proceeds": 0.0, "fees": 0.0})
    usd = {"realized": 0.0, "unrealized": 0.0, "cost": 0.0, "complete": fx is not None}  # no rates at all: USD is unknown, never 0
    n = buys = sells = fees_unknown = unpriced = unmatched = 0
    coins: set[str] = set()
    first_ts = last_ts = None
    block_buys: dict[int, set[str]] = defaultdict(set)
    weekly: dict[int, float] = defaultdict(float)
    deployer_linked = False
    snipe_exempt = False
    for r in rows:
        tid, _w, tok, ts, block, side, ta_s, qt, qa_s, venue, fee_s, tax_s, snipe_s, tx = r
        if ts < lo or ts > hi:
            continue
        n += 1
        coins.add(tok)
        first_ts = ts if first_ts is None else first_ts
        last_ts = ts
        ta, qa = _int(ta_s), _int(qa_s)
        if side == "buy":
            buys += 1
            block_buys[block].add(tok)
        else:
            sells += 1
        if launches.deployer(tok) == r[1]:
            deployer_linked = True
        if qa <= 0 or ta <= 0:
            unpriced += 1
            continue
        if fee_s is None:
            if venue == "curve" and fees == "strict":
                fees_unknown += 1
                continue
            fee = qa // 100 if venue == "curve" else 0  # estimate: the 1% base fee; creator tax unknown
            tax = 0
            if venue == "curve":
                fees_unknown += 1
        else:
            fee, tax = (_int(fee_s), _int(tax_s)) if venue == "curve" else (0, 0)  # v4 hook fees sit in the net amounts
        dec = qdec.get(qt or chain.NATIVE, 18)
        scale = 10**dec
        pq = per_quote[qt or chain.NATIVE]
        if side == "buy":
            cost = qa + fee + tax
            lots[tok].append([ta, cost])
            inv[tok] += ta
            p = open_pos.get(tok)
            if p is None:
                p = Position(tok, qt, ts, tx)
                p.rank = facts.rank[tid]
                lts = launches.ts(tok)
                p.since_launch = (ts - lts) if lts is not None else None
                p.snipe_paid = _int(snipe_s) > 0
                p.sniper = p.snipe_paid or (p.since_launch is not None and p.since_launch < SNIPE_S)
                open_pos[tok] = p
            if facts.exempt[tid]:
                p.exempt = True
                snipe_exempt = True
            p.buys += 1
            p.cost += cost
            p.fees += fee + tax
            p.tokens_in += ta
            p.inventory += ta
            p.peak = max(p.peak, p.inventory)
            pq["cost"] += cost / scale
            pq["fees"] += (fee + tax) / scale
            if fx is not None:
                rate = fx.usd(qt, ts)
                if rate is None:
                    usd["complete"] = False
                else:
                    usd["cost"] += cost / scale * rate
            continue
        # sell: match against FIFO lots
        have = inv[tok]
        matched = min(ta, have)
        if matched <= 0:
            unmatched += 1
            continue
        proceeds = qa * matched // ta
        fee_m = (fee + tax) * matched // ta
        cost_m = 0
        need = matched
        dq = lots[tok]
        while need > 0 and dq:
            lot = dq[0]
            take = min(need, lot[0])
            c = lot[1] * take // lot[0]
            lot[0] -= take
            lot[1] -= c
            cost_m += c
            need -= take
            if lot[0] == 0:
                dq.popleft()
        inv[tok] -= matched
        pnl = proceeds - cost_m
        p = open_pos.get(tok)
        if p is None:  # lots without a position cannot happen; guard for dust left behind
            p = Position(tok, qt, ts, tx)
            open_pos[tok] = p
        p.sells += 1
        p.proceeds += proceeds
        p.cost_matched += cost_m
        p.pnl += pnl
        p.fees += fee_m
        p.inventory -= matched
        p.exit_tx = tx
        eq = facts.exitq[tid]
        if eq == eq:  # not NaN
            p.exitq_w += proceeds
            p.exitq_num += eq * proceeds
        p.rug = facts.rug[tid]
        pq["realized"] += pnl / scale
        pq["proceeds"] += proceeds / scale
        pq["fees"] += fee_m / scale
        weekly[ts // (7 * 86400)] += pnl / scale if (qt or chain.NATIVE) in ETH_QUOTES else 0.0
        if fx is not None:
            rate = fx.usd(qt, ts)
            if rate is None:
                usd["complete"] = False
                p.usd_ok = False
            else:
                usd["realized"] += pnl / scale * rate
                p.usd_pnl += pnl / scale * rate
        if p.inventory <= p.peak * DUST_SHARE:
            # closed: the dust that is left is written off (its cost counts as a loss of this position)
            dust_cost = sum(l[1] for l in dq)
            if dust_cost:
                p.pnl -= dust_cost
                pq["realized"] -= dust_cost / scale
            dq.clear()
            inv[tok] = 0
            p.exit_ts = ts
            closed.append(p)
            del open_pos[tok]
    # unrealised: open lots at the mark
    open_marked = 0
    for tok, p in open_pos.items():
        mk = marks.get(tok)
        price = mk.get(mark_key) if mk else None
        cost_left = sum(l[1] for l in lots[tok])
        if price is None or inv[tok] <= 0:
            continue
        dec = qdec.get(p.quote or chain.NATIVE, 18)
        value = inv[tok] * price
        pq = per_quote[p.quote or chain.NATIVE]
        pq["unrealized"] += (value - cost_left) / 10**dec
        open_marked += 1
        if fx is not None:
            rate = fx.usd(p.quote, hi)
            if rate is None:
                usd["complete"] = False
            else:
                usd["unrealized"] += (value - cost_left) / 10**dec * rate
    eth = {"realized": 0.0, "unrealized": 0.0, "cost": 0.0, "fees": 0.0}
    for qt, v in per_quote.items():
        if qt in ETH_QUOTES:
            for k in eth:
                eth[k] += v[k]
    return {
        "trades": n, "buys": buys, "sells": sells, "coins": len(coins), "closed": closed, "open": list(open_pos.values()), "open_marked": open_marked,
        "per_quote": dict(per_quote), "eth": eth, "usd": usd, "first_ts": first_ts, "last_ts": last_ts,
        "fees_unknown": fees_unknown, "unpriced": unpriced, "unmatched_sells": unmatched,
        "multi_coin_blocks": sum(1 for s in block_buys.values() if len(s) >= 2), "weekly": dict(weekly),
        "deployer_linked": deployer_linked, "snipe_exempt": snipe_exempt,
    }


def quality_score(closed_n: int, wins: int, roi_realized: float | None, exit_q: float | None, rug_avoid: float | None) -> float:
    """0..1, 0.5 = no evidence. Laplace win rate, ROI clipped to [-100%, +100%], exit quality and rug avoidance,
    shrunk towards 0.5 with n/(n+5) closed positions. Weights: QUALITY_WEIGHTS."""
    w = QUALITY_WEIGHTS
    win_s = (wins + 1) / (closed_n + 2)
    roi_s = 0.5 if roi_realized is None else max(0.0, min(1.0, (roi_realized + 1) / 2))
    exit_s = 0.5 if exit_q is None else exit_q
    rug_s = 0.5 if rug_avoid is None else rug_avoid
    measured = w["win"] * win_s + w["roi"] * roi_s + w["exit"] * exit_s + w["rug"] * rug_s
    shrink = closed_n / (closed_n + QUALITY_SHRINK_N)
    return round(shrink * measured + (1 - shrink) * 0.5, 4)


def wallet_row(wallet: str, led: dict[str, Any], label: str, lo: int, hi: int, run_ts: float, launches: Launches, is_contract: bool, fees: str = "strict") -> dict[str, Any]:
    closed: list[Position] = led["closed"]
    wins = sum(1 for p in closed if p.pnl > 0)
    eth, usd = led["eth"], led["usd"]
    cost_matched_eth = sum(p.cost_matched for p in closed + led["open"] if (p.quote or chain.NATIVE) in ETH_QUOTES) / 1e18
    roi_realized = (eth["realized"] / cost_matched_eth) if cost_matched_eth > 0 else None
    roi = ((eth["realized"] + eth["unrealized"]) / eth["cost"]) if eth["cost"] > 0 else None
    holds = [p.exit_ts - p.entry_ts for p in closed if p.exit_ts is not None]
    entries = closed + led["open"]
    ranks = [p.rank for p in entries if p.rank > 0]
    snipers = sum(1 for p in entries if p.sniper)
    eqs = [p.exit_quality for p in closed if p.exit_quality is not None]
    rugs = [p.rug for p in closed if p.rug != RUG_UNKNOWN]
    weeks = {k: v for k, v in led["weekly"].items()}
    span_h = max(1.0, ((led["last_ts"] or 0) - (led["first_ts"] or 0)) / 3600)
    tph = led["trades"] / span_h
    is_bot = tph > BOT_TRADES_PER_HOUR or led["trades"] > BOT_TRADES or led["multi_coin_blocks"] >= BOT_MULTI_COIN_BLOCKS
    exit_q = (sum(eqs) / len(eqs)) if eqs else None
    rug_avoid = (sum(rugs) / len(rugs)) if rugs else None
    tags = []
    if is_bot:
        tags.append("bot")
    if led["deployer_linked"]:
        tags.append("deployer")
    if wallet in launches.deployers and not led["deployer_linked"]:
        tags.append("launcher")
    if led["snipe_exempt"]:
        tags.append("snipe_exempt")
    if entries and snipers / len(entries) >= 0.3:
        tags.append("sniper")
    if is_contract:
        tags.append("contract")
    if led["fees_unknown"]:
        word = "fees_estimated" if fees == "estimate" else "fees_unknown"
        tags.append(word if led["fees_unknown"] == led["trades"] else word.replace("fees_", "fees_partly_"))
    return {
        "wallet": wallet, "label": label, "range_from": lo, "range_to": hi, "run_ts": run_ts,
        "trades": led["trades"], "buys": led["buys"], "sells": led["sells"], "coins": led["coins"],
        "positions_closed": len(closed), "positions_open": len(led["open"]), "wins": wins,
        "realized_eth": eth["realized"], "unrealized_eth": eth["unrealized"], "cost_eth": eth["cost"], "fees_eth": eth["fees"], "pnl_eth": eth["realized"] + eth["unrealized"],
        "realized_usd": usd["realized"] if usd["complete"] else None, "unrealized_usd": usd["unrealized"] if usd["complete"] else None, "cost_usd": usd["cost"] if usd["complete"] else None,
        "pnl_usd": (usd["realized"] + usd["unrealized"]) if usd["complete"] else None, "usd_complete": int(usd["complete"]),
        "roi": roi, "roi_realized": roi_realized, "win_rate": (wins / len(closed)) if closed else None,
        "median_hold_s": median([float(h) for h in holds]), "trades_per_hour": round(tph, 3),
        "buyer_rank_median": median([float(x) for x in ranks]), "sniper_share": (snipers / len(entries)) if entries else None,
        "exit_quality": exit_q, "rug_avoid": rug_avoid,
        "consistency": (sum(1 for v in weeks.values() if v > 0) / len(weeks)) if weeks else None,
        "weeks_active": len(weeks), "weeks_positive": sum(1 for v in weeks.values() if v > 0), "multi_coin_blocks": led["multi_coin_blocks"],
        "is_bot": int(is_bot), "deployer_linked": int(led["deployer_linked"] or led["snipe_exempt"]),
        "quality": quality_score(len(closed), wins, roi_realized, exit_q, rug_avoid), "tags": json.dumps(tags),
        "pnl_by_quote": json.dumps({k: {kk: round(vv, 8) for kk, vv in v.items()} for k, v in led["per_quote"].items()}),
        "first_ts": led["first_ts"], "last_ts": led["last_ts"], "fees_unknown": led["fees_unknown"], "unpriced": led["unpriced"], "unmatched_sells": led["unmatched_sells"], "updated_at": run_ts,
    }


STATS_COLS = [
    "wallet", "label", "range_from", "range_to", "run_ts", "trades", "buys", "sells", "coins", "positions_closed", "positions_open", "wins",
    "realized_eth", "unrealized_eth", "cost_eth", "fees_eth", "pnl_eth", "realized_usd", "unrealized_usd", "cost_usd", "pnl_usd", "usd_complete",
    "roi", "roi_realized", "win_rate", "median_hold_s", "trades_per_hour", "buyer_rank_median", "sniper_share", "exit_quality", "rug_avoid", "consistency",
    "weeks_active", "weeks_positive", "multi_coin_blocks", "is_bot", "deployer_linked", "quality", "tags", "pnl_by_quote",
    "first_ts", "last_ts", "fees_unknown", "unpriced", "unmatched_sells", "updated_at",
]
STATS_INSERT = f"INSERT OR REPLACE INTO wallet_stats({','.join(STATS_COLS)}) VALUES({','.join('?' * len(STATS_COLS))})"
POS_COLS = ["wallet", "token", "quote_token", "range_from", "range_to", "entry_ts", "exit_ts", "closed", "buys", "sells", "cost_quote", "proceeds_quote", "pnl_quote", "pnl_usd", "fees_quote", "unrealized_quote", "hold_s", "buyer_rank", "since_launch_s", "sniper", "snipe_paid", "exit_quality", "rug_after", "entry_tx", "exit_tx"]
POS_INSERT = f"INSERT INTO wallet_positions({','.join(POS_COLS)}) VALUES({','.join('?' * len(POS_COLS))})"


def position_rows(wallet: str, led: dict[str, Any], lo: int, hi: int, marks: dict[str, dict[str, Any]], mark_key: str, qdec: dict[str, int], fx: Fx | None) -> list[tuple]:
    out = []
    for p in led["closed"] + led["open"]:
        dec = qdec.get(p.quote or chain.NATIVE, 18)
        s = 10**dec
        closed = p.exit_ts is not None
        unreal = None
        if not closed:
            mk = marks.get(p.token)
            price = mk.get(mark_key) if mk else None
            if price is not None and p.inventory > 0:
                unreal = (p.inventory * price - (p.cost - p.cost_matched)) / s
        out.append((
            wallet, p.token, p.quote, lo, hi, p.entry_ts, p.exit_ts, int(closed), p.buys, p.sells,
            p.cost / s, p.proceeds / s, p.pnl / s, (p.usd_pnl if (fx is not None and p.usd_ok and p.sells) else None), p.fees / s, unreal,
            (p.exit_ts - p.entry_ts) if closed else None, p.rank or None, p.since_launch, int(p.sniper), int(p.snipe_paid), p.exit_quality, (p.rug if p.rug != RUG_UNKNOWN else None), p.entry_tx, p.exit_tx,
        ))
    return out


def wallet_pass(store: Store, lo: int, hi: int, mid: int, marks: dict[str, dict[str, Any]], facts: Facts, launches: Launches, fx: Fx | None, fees: str, run_ts: float, write: bool = True, positions_min_trades: int = 2, progress=None) -> dict[str, Any]:
    """Streams trades ordered by (wallet, ts); writes wallet_stats (full, first half, second half) and wallet_positions (full)."""
    q = store.db.execute
    qdec = {a: v["decimals"] for a, v in store.quotes().items()}
    contracts = {r[0] for r in q("SELECT address FROM wallets WHERE is_contract=1")}
    ranges = [("full", lo, hi, "last"), ("h1", lo, mid - 1, "mid"), ("h2", mid, hi, "last")]
    stats: dict[str, dict[str, dict[str, Any]]] = {"full": {}, "h1": {}, "h2": {}}
    if write:
        ensure_tables(store)
        for _, a, b, _ in ranges:
            store.db.execute("DELETE FROM wallet_stats WHERE range_from=? AND range_to=?", (a, b))
        store.db.execute("DELETE FROM wallet_positions WHERE range_from=? AND range_to=?", (lo, hi))
        store.commit()
    cur = q("SELECT id, wallet, token, ts, block, side, token_amount, quote_token, quote_amount, venue, fee_raw, tax_raw, snipe_raw, tx_hash FROM trades WHERE ts>=? AND ts<=? ORDER BY wallet, ts", (lo, hi))
    buf: list[tuple] = []
    w: str | None = None
    n_rows = n_wallets = 0
    t0 = time.time()
    stat_batch: list[tuple] = []
    pos_batch: list[tuple] = []

    def flush() -> None:
        nonlocal stat_batch, pos_batch
        if write and stat_batch:
            store.db.executemany(STATS_INSERT, stat_batch)
        if write and pos_batch:
            store.db.executemany(POS_INSERT, pos_batch)
        if write:
            store.commit()
        stat_batch, pos_batch = [], []

    def process(wallet: str, rows: list[tuple]) -> None:
        nonlocal n_wallets
        if wallet in INFRA:
            return
        rows.sort(key=lambda r: (r[3], r[0]))
        n_wallets += 1
        for label, a, b, mk in ranges:
            led = wallet_ledger(rows, a, b, marks, mk, facts, launches, fx, qdec, fees)
            if led["trades"] == 0:
                continue
            row = wallet_row(wallet, led, label, a, b, run_ts, launches, wallet in contracts, fees)
            stats[label][wallet] = row
            stat_batch.append(tuple(row[c] for c in STATS_COLS))
            if label == "full" and led["trades"] >= positions_min_trades:
                pos_batch.extend(position_rows(wallet, led, a, b, marks, mk, qdec, fx))
        if len(stat_batch) >= 5000 or len(pos_batch) >= 20000:
            flush()

    for row in cur:
        if row[1] != w:
            if buf:
                process(w, buf)
            buf = []
            w = row[1]
        buf.append(row)
        n_rows += 1
        if progress and n_rows % 2_000_000 == 0:
            progress(f"wallet pass: {n_rows:,} trades, {n_wallets:,} wallets, {time.time() - t0:.0f} s")
    if buf:
        process(w, buf)
    flush()
    return {"stats": stats, "rows": n_rows, "wallets": n_wallets, "seconds": round(time.time() - t0, 1)}


# ---- walk-forward -----------------------------------------------------------------------------------------------
def eligible(row: dict[str, Any], min_trades: int) -> bool:
    return row["trades"] >= min_trades and not row["is_bot"] and not row["deployer_linked"]


def walk_forward(h1: dict[str, dict[str, Any]], h2: dict[str, dict[str, Any]], min_trades: int, top_n: int = 50, seed: int = 7) -> dict[str, Any]:
    common = [w for w, r in h1.items() if r["trades"] >= min_trades and w in h2]
    out: dict[str, Any] = {"wallets_h1": len(h1), "wallets_h2": len(h2), "common": len(common), "min_trades": min_trades, "top_n": top_n}
    if not common:
        return out
    pnl1 = [h1[w]["pnl_eth"] for w in common]
    pnl2 = [h2[w]["pnl_eth"] for w in common]
    q1 = [h1[w]["quality"] for w in common]
    q2 = [h2[w]["quality"] for w in common]
    roi1 = [h1[w]["roi"] if h1[w]["roi"] is not None else 0.0 for w in common]
    roi2 = [h2[w]["roi"] if h2[w]["roi"] is not None else 0.0 for w in common]
    out["spearman"] = {"pnl_h1_vs_pnl_h2": spearman(pnl1, pnl2), "quality_h1_vs_pnl_h2": spearman(q1, pnl2), "quality_h1_vs_quality_h2": spearman(q1, q2), "roi_h1_vs_roi_h2": spearman(roi1, roi2)}
    cands = [w for w in common if eligible(h1[w], min_trades)]
    ranked = sorted(cands, key=lambda w: (-h1[w]["quality"], -h1[w]["pnl_eth"]))
    top = ranked[:top_n]
    bottom = ranked[-top_n:] if len(ranked) >= 2 * top_n else []
    by_pnl = sorted(cands, key=lambda w: -h1[w]["pnl_eth"])[:top_n]

    def summary(ws: list[str]) -> dict[str, Any]:
        xs = [h2[w]["pnl_eth"] for w in ws]
        if not xs:
            return {"n": 0}
        ci = bootstrap_ci(xs, 1000, seed)
        return {"n": len(xs), "mean": sum(xs) / len(xs), "median": median(xs), "ci95": ci, "positive_share": sum(1 for x in xs if x > 0) / len(xs), "sum": sum(xs), "realized_sum": sum(h2[w]["realized_eth"] for w in ws), "usd_sum": (sum(h2[w]["pnl_usd"] for w in ws) if all(h2[w]["pnl_usd"] is not None for w in ws) else None)}

    out["top_by_quality"] = summary(top)
    out["top_by_pnl"] = summary(by_pnl)
    out["bottom_by_quality"] = summary(bottom)
    out["all_eligible"] = summary(cands)
    out["top_wallets"] = top
    out["eligible"] = len(cands)
    return out


# ---- copy-test ----------------------------------------------------------------------------------------------------
def curve_buy(rq: int, rt: int, order: int, tax_rate: float) -> tuple[int, int, int]:
    """Spend `order` quote on the constant-product curve: fee (1%) and creator tax come off the top, the rest enters the
    reserve. Returns (tokens_out, quote_into_reserve, fees_paid)."""
    q_net = int(order / (1 + FEE_BPS / 10000 + tax_rate))
    tokens = rt - (rq * rt) // (rq + q_net)
    return tokens, q_net, order - q_net


def curve_sell(rq: int, rt: int, tokens: int, tax_rate: float) -> tuple[int, int]:
    """Sell `tokens` into the curve: gross quote out of the reserve, net after 1% fee and creator tax. Returns (net, gross)."""
    gross = rq - (rq * rt) // (rt + tokens)
    net = int(gross * (1 - FEE_BPS / 10000 - tax_rate))
    return net, gross


def copy_test(store: Store, leaders_by_k: dict[int, list[str]], lo: int, hi: int, launches: Launches, order_wei: int = COPY_ORDER_WEI, hold_s: int = COPY_HOLD_S, history_from: int | None = None) -> dict[str, Any]:
    """Follow the leaders' buys in [lo, hi] with a fixed order; exit on their first sell or after hold_s.
    `history_from`: first timestamp the store covers; curve reserves are rebuilt only for coins launched after it."""
    q = store.db.execute
    history_from = lo if history_from is None else history_from
    leaders = sorted({w for ws in leaders_by_k.values() for w in ws})
    events: list[dict[str, Any]] = []
    for w in leaders:
        rows = q("SELECT id, token, ts, side, quote_token, quote_amount, token_amount FROM trades WHERE wallet=? AND ts>=? AND ts<=? ORDER BY ts, id", (w, lo, hi)).fetchall()
        open_by_tok: dict[str, dict[str, Any]] = {}
        for tid, tok, ts, side, qt, qa, ta in rows:
            if (qt or chain.NATIVE) not in ETH_QUOTES or _int(qa) <= 0 or _int(ta) <= 0:
                continue
            if side == "buy":
                if tok not in open_by_tok:
                    ev = {"leader": w, "token": tok, "buy_id": tid, "buy_ts": ts, "sell_id": None, "sell_ts": None}
                    open_by_tok[tok] = ev
                    events.append(ev)
            elif tok in open_by_tok:
                ev = open_by_tok.pop(tok)
                if ts <= ev["buy_ts"] + hold_s:
                    ev["sell_id"], ev["sell_ts"] = tid, ts
    by_tok: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ev in events:
        by_tok[ev["token"]].append(ev)
    results: list[dict[str, Any]] = []
    for tok, evs in by_tok.items():
        rows = q("SELECT id, ts, side, token_amount, quote_amount, venue, fee_raw, tax_raw FROM trades WHERE token=? ORDER BY ts, id", (tok,)).fetchall()
        lts = launches.ts(tok)
        first_ts = rows[0][1] if rows else None
        reserves_known = lts is not None and first_ts is not None and lts >= history_from - 1 and first_ts >= lts - 1  # the whole curve history is in the store
        la = launches.by_token.get(tok) or {}
        thr = la.get("threshold")
        if thr and thr != chain.CURVE_GRADUATION_THRESHOLD_WEI:
            reserves_known = False  # non-default launch config: the virtual reserve is not the one we know
        # creator tax rate: median tax/quote over curve buys with known fees
        taxes = sorted(_int(r[7]) / _int(r[4]) for r in rows if r[5] == "curve" and r[2] == "buy" and r[6] is not None and _int(r[4]) > 0)
        tax_rate = taxes[len(taxes) // 2] if taxes else 0.0
        # cumulative state after each row: (rq, rt, price)
        rq, rt = chain.CURVE_PHANTOM_QUOTE_WEI, chain.CURVE_SUPPLY_WEI
        state: list[tuple[int, int]] = []
        prices: list[float | None] = []
        idx_by_id: dict[int, int] = {}
        for i, (tid, ts, side, ta_s, qa_s, venue, fee_s, tax_s) in enumerate(rows):
            ta, qa = _int(ta_s), _int(qa_s)
            if venue == "curve" and ta > 0 and qa > 0:
                if side == "buy":
                    rq += qa
                    rt -= ta
                else:
                    rq -= qa + _int(fee_s) + _int(tax_s)
                    rt += ta
            state.append((rq, rt))
            prices.append(qa / ta if ta > 0 and qa > 0 else None)
            idx_by_id[tid] = i

        def next_price(i: int) -> float | None:
            for j in range(i + 1, min(len(rows), i + 200)):
                if prices[j] is not None:
                    return prices[j]
            return None

        def last_price(i: int) -> float | None:
            for j in range(i, max(-1, i - 200), -1):
                if prices[j] is not None:
                    return prices[j]
            return None

        for ev in evs:
            k = idx_by_id.get(ev["buy_id"])
            if k is None:
                continue
            venue_k = rows[k][5]
            res: dict[str, Any] = {"leader": ev["leader"], "token": tok, "buy_ts": ev["buy_ts"], "exit_reason": "leader_sold" if ev["sell_id"] else "timeout", "method": None}
            if venue_k == "curve" and reserves_known and state[k][0] > 0 and state[k][1] > 0:
                tokens, q_net, _fees = curve_buy(state[k][0], state[k][1], order_wei, tax_rate)
                if tokens <= 0:
                    continue
                res["method"] = "curve"
                res["impact_pct"] = (order_wei / tokens) / (state[k][0] / state[k][1]) * 100 - 100
                entry_delta = (q_net, -tokens)
            else:
                p = next_price(k)
                if p is None:
                    continue
                tokens = int(order_wei / (1 + FEE_BPS / 10000 + tax_rate) / p)
                res["method"] = "v4_flat" if venue_k != "curve" else "curve_unknown_reserves"
                res["impact_pct"] = None
                entry_delta = (0, 0)
            if ev["sell_id"] is not None:
                j = idx_by_id.get(ev["sell_id"], k)
            else:
                j = bisect.bisect_right([r[1] for r in rows], ev["buy_ts"] + hold_s) - 1
                j = max(j, k)
            venue_j = rows[j][5]
            if venue_j == "curve" and reserves_known and res["method"] == "curve":
                rq_e, rt_e = state[j][0] + entry_delta[0], state[j][1] + entry_delta[1]
                if rq_e <= 0 or rt_e <= 0:
                    continue
                net, _gross = curve_sell(rq_e, rt_e, tokens, tax_rate)
            else:
                p = last_price(j) if j > k else next_price(k)
                if p is None:
                    continue
                net = int(tokens * p * (1 - FEE_BPS / 10000 - tax_rate))
                res["method"] = res["method"] if res["method"] != "curve" else "curve_then_v4"
            res["pnl_wei"] = net - order_wei
            res["ret"] = (net - order_wei) / order_wei
            res["hold_s"] = rows[j][1] - ev["buy_ts"]
            results.append(res)

    def agg(rs: list[dict[str, Any]]) -> dict[str, Any]:
        if not rs:
            return {"n": 0}
        rets = [r["ret"] for r in rs]
        ci = bootstrap_ci(rets, 1000, 7)
        imp = [r["impact_pct"] for r in rs if r.get("impact_pct") is not None]
        return {
            "n": len(rs), "hit_rate": sum(1 for r in rets if r > 0) / len(rs), "mean_ret": sum(rets) / len(rets), "median_ret": median(rets), "ci95_mean_ret": ci,
            "total_pnl_eth": sum(r["pnl_wei"] for r in rs) / 1e18, "leader_sold": sum(1 for r in rs if r["exit_reason"] == "leader_sold"),
            "mean_ret_leader_sold": (lambda xs: sum(xs) / len(xs) if xs else None)([r["ret"] for r in rs if r["exit_reason"] == "leader_sold"]),
            "mean_ret_timeout": (lambda xs: sum(xs) / len(xs) if xs else None)([r["ret"] for r in rs if r["exit_reason"] == "timeout"]),
            "curve_method": sum(1 for r in rs if r["method"] == "curve"), "median_impact_pct": median(imp) if imp else None, "median_hold_s": median([float(r["hold_s"]) for r in rs]),
        }

    out: dict[str, Any] = {"order_eth": order_wei / 1e18, "hold_s": hold_s, "events": len(events), "priced": len(results), "by_k": {}}
    for k, ws in sorted(leaders_by_k.items()):
        ws_set = set(ws)
        out["by_k"][k] = agg([r for r in results if r["leader"] in ws_set])
    return out


# ---- wallet_scores compatibility ---------------------------------------------------------------------------------
def update_wallet_scores(store: Store, full: dict[str, dict[str, Any]], run_ts: float) -> int:
    """Bot flags and trade rates into wallet_scores without touching `score` (the walk-forward runner rate)."""
    rows = [(w, r["is_bot"], r["trades_per_hour"], run_ts) for w, r in full.items() if r["is_bot"]]
    store.db.executemany(
        "INSERT INTO wallet_scores(wallet, rotations, runner_hits, score, is_bot, trades_per_hour, updated_at) VALUES(?,0,0,NULL,?,?,?) "
        "ON CONFLICT(wallet) DO UPDATE SET is_bot=MAX(COALESCE(wallet_scores.is_bot, 0), excluded.is_bot), trades_per_hour=excluded.trades_per_hour, updated_at=excluded.updated_at",
        rows,
    )
    store.commit()
    return len(rows)


# ---- report -------------------------------------------------------------------------------------------------------
def pct(x: float | None, d: int = 1) -> str:
    return "n/a" if x is None else f"{100 * x:.{d}f}%"


def num(x: float | None, d: int = 4) -> str:
    return "n/a" if x is None else f"{x:,.{d}f}"


def fmt_ts(ts: int | None) -> str:
    return "?" if not ts else datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M")


def dur(s: float | None) -> str:
    if s is None:
        return "n/a"
    s = int(s)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m{s % 60:02d}s"
    return f"{s // 3600}h{(s % 3600) // 60:02d}m"


def leaderboard_table(rows: list[dict[str, Any]], title: str, n: int = 20) -> list[str]:
    lines = [f"### {title}", "", "| # | wallet | trades | coins | closed | win rate | PnL ETH (real + unreal) | ROI | median hold | exit q | rug avoid | sniper | quality | tags |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(rows[:n], 1):
        tags = ", ".join(json.loads(r["tags"])) or "—"
        lines.append(f"| {i} | `{r['wallet'][:10]}…{r['wallet'][-4:]}` | {r['trades']} | {r['coins']} | {r['positions_closed']} | {pct(r['win_rate'], 0)} | {r['pnl_eth']:+.4f} ({r['realized_eth']:+.4f} + {r['unrealized_eth']:+.4f}) | {pct(r['roi'], 0)} | {dur(r['median_hold_s'])} | {num(r['exit_quality'], 2)} | {pct(r['rug_avoid'], 0)} | {pct(r['sniper_share'], 0)} | {r['quality']:.3f} | {tags} |")
    lines.append("")
    return lines


def report(a: argparse.Namespace, store: Store, lo: int, hi: int, mid: int, tp: dict[str, Any], wp: dict[str, Any], wf: dict[str, Any], ct: dict[str, Any], fx_rows: int, wrote_scores: int, t0: float) -> str:
    full = wp["stats"]["full"]
    h1, h2 = wp["stats"]["h1"], wp["stats"]["h2"]
    n_trades = store.db.execute("SELECT COUNT(*) FROM trades WHERE ts>=? AND ts<=?", (lo, hi)).fetchone()[0]
    rows = list(full.values())
    fees_unknown = sum(r["fees_unknown"] for r in rows)
    unpriced = sum(r["unpriced"] for r in rows)
    bots = sum(1 for r in rows if r["is_bot"])
    deployers = sum(1 for r in rows if r["deployer_linked"])
    snipers = sum(1 for r in rows if "sniper" in r["tags"])
    exempt = sum(1 for r in rows if "snipe_exempt" in r["tags"])
    closed_total = sum(r["positions_closed"] for r in rows)
    active = [r for r in rows if r["positions_closed"] >= 1]
    winners = [r for r in active if r["pnl_eth"] > 0]
    md: list[str] = []
    md.append("# Trader intelligence: who earns on PONS after fees, and does it persist?")
    md.append("")
    md.append(f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} by `stampede traders` from `{a.db}`.")
    md.append("")
    md.append("## Setup")
    md.append("")
    md.append(f"- Data: {n_trades:,} trades by {wp['wallets']:,} non-infrastructure wallets on {tp['tokens']:,} coins, {fmt_ts(lo)}–{fmt_ts(hi)} UTC ({(hi - lo) / 3600:.1f} h). Walk-forward split at {fmt_ts(mid)} UTC (first half: {len(h1):,} wallets, second half: {len(h2):,}).")
    md.append(f"- Fees: `{a.fees}`. Rows without `fee_raw` on the curve: {fees_unknown:,} ({'skipped' if a.fees == 'strict' else 'base fee estimated at 1%, creator tax unknown'}); rows without a quote amount (multi-hop / two-sided transactions): {unpriced:,} counted as trades but not priced. Graduated-pool (v4) rows: fees sit inside the net amounts (the hook takes them in the unspecified currency), so they are not listed separately.")
    md.append(f"- USD: {'fx_rates loaded (' + str(fx_rows) + ' hourly points); USD columns are filled where every leg has a rate' if fx_rows else 'no fx_rates in this store: USD columns are n/a (run `stampede fx --db ...`)'}. PnL is stated in quote units first; the ETH columns cover positions quoted in native ETH/WETH ({sum(1 for r in rows if r['cost_eth'] > 0):,} wallets); other quote assets are kept per asset in `pnl_by_quote`.")
    md.append(f"- Wallets tagged: {bots:,} bots (> {BOT_TRADES_PER_HOUR} trades/h over their active span, > {BOT_TRADES} trades, or ≥ {BOT_MULTI_COIN_BLOCKS} blocks with buys of several coins), {deployers:,} deployer-linked (deployer of a coin they traded, or bought without snipe tax while later buyers still paid it: {exempt:,}), {snipers:,} snipers (≥ 30% of entries under {SNIPE_S} s after launch or with snipe tax paid).")
    md.append(f"- Positions: {closed_total:,} closed (wallet, coin) episodes; a position closes when ≤ {int(DUST_SHARE * 100)}% of its peak inventory is left (the dust is written off). Sells without matching lots (tokens received by transfer, or bought before the range) are ignored, never counted as profit.")
    md.append(f"- Compute: token pass {tp['seconds']} s, wallet pass {wp['seconds']} s, total {time.time() - t0:.0f} s.")
    md.append("")
    md.append("## Definitions")
    md.append("")
    md.append("- **Cost of a lot** = quote paid + 1% fee (incl. snipe tax) + creator tax. **Proceeds** = net quote received. **Realized PnL** = FIFO proceeds − matched cost. **Unrealized** = open inventory × last minute-median price − remaining cost. **ROI** = (realized + unrealized) / total cost of buys; `roi_realized` = realized / cost of the lots actually sold.")
    md.append("- **Win rate** = closed positions with PnL > 0 / closed positions. **Median hold** = median(last sell − first buy) over closed positions. **Trades/hour** over the wallet's active span (≥ 1 h).")
    md.append(f"- **Buyer rank** = n-th distinct buyer of the coin since its launch (known only when the launch is inside the range). **Sniper** = entry < {SNIPE_S} s after launch or snipe tax paid. **Exit quality** = exit price / max minute-median price in the following {EXIT_Q_S // 60} min (1 = sold the top), proceeds-weighted per position. **Rug avoidance** = share of closed positions after which the coin fell ≥ {int(RUG_DROP * 100)}% within {RUG_S // 60} min. **Consistency** = share of active ISO weeks with positive realized PnL.")
    md.append(f"- **Quality** (0–1, 0.5 = no evidence) = shrink × ({QUALITY_WEIGHTS['win']} × Laplace win rate + {QUALITY_WEIGHTS['roi']} × clip((roi_realized + 1)/2) + {QUALITY_WEIGHTS['exit']} × exit quality + {QUALITY_WEIGHTS['rug']} × rug avoidance) + (1 − shrink) × 0.5, shrink = n / (n + {QUALITY_SHRINK_N}) over n closed positions. Bots and deployer-linked wallets keep their quality but are excluded from the `smart` preset and from the walk-forward top lists.")
    md.append("")
    md.append("## Population")
    md.append("")
    md.append(f"- {len(rows):,} wallets with ≥ 1 trade; {len(active):,} with ≥ 1 closed position; of those {len(winners):,} ({pct(len(winners) / len(active) if active else None)}) ended the range with a positive ETH PnL (realized + unrealized).")
    if active:
        pnls = sorted(r["pnl_eth"] for r in active)
        md.append(f"- ETH PnL across wallets with a closed position: median {median(pnls):+.4f} ETH, mean {sum(pnls) / len(pnls):+.4f}, sum {sum(pnls):+.2f}; fees paid {sum(r['fees_eth'] for r in active):.2f} ETH.")
        wr = [r["win_rate"] for r in active if r["win_rate"] is not None]
        eq = [r["exit_quality"] for r in active if r["exit_quality"] is not None]
        ra = [r["rug_avoid"] for r in active if r["rug_avoid"] is not None]
        md.append(f"- Median win rate {pct(median(wr))}, median exit quality {num(median(eq), 2)}, median rug-avoidance share {pct(median(ra))}, median hold {dur(median([r['median_hold_s'] for r in active if r['median_hold_s'] is not None]))}.")
    md.append("")
    md.append("## Leaderboards (full range)")
    md.append("")
    md += leaderboard_table(sorted(rows, key=lambda r: -r["pnl_eth"]), "Top by ETH PnL (realized + unrealized), every wallet")
    md += leaderboard_table(sorted([r for r in rows if eligible(r, a.min_trades)], key=lambda r: (-r["quality"], -r["pnl_eth"])), f"Top by quality · ≥ {a.min_trades} trades · no bots, no deployer-linked")
    md += leaderboard_table(sorted([r for r in rows if "sniper" in r["tags"]], key=lambda r: -r["pnl_eth"]), "Snipers by ETH PnL")
    md += leaderboard_table(sorted([r for r in rows if r["is_bot"]], key=lambda r: -r["pnl_eth"]), "Bots by ETH PnL")
    md.append("## Walk-forward: first half → second half")
    md.append("")
    md.append(f"Stats are built on {fmt_ts(lo)}–{fmt_ts(mid)} and the same wallets are measured again on {fmt_ts(mid)}–{fmt_ts(hi)} UTC (positions opened in the second half only; open lots marked at the end of each half). Wallets with ≥ {wf.get('min_trades')} first-half trades that traded again in the second half: **{wf.get('common', 0):,}**; eligible for the top lists (not bot, not deployer-linked): {wf.get('eligible', 0):,}.")
    md.append("")
    sp = wf.get("spearman") or {}
    md.append("| rank correlation (Spearman) | ρ |")
    md.append("|---|---|")
    for k, v in sp.items():
        md.append(f"| {k.replace('_', ' ')} | {num(v, 3)} |")
    md.append("")
    md.append(f"| second-half ETH PnL of… | wallets | mean | median | bootstrap 95% CI of the mean (1000 resamples) | positive | sum |")
    md.append("|---|---|---|---|---|---|---|")
    for key, label in (("top_by_quality", f"top-{wf.get('top_n', 50)} by first-half quality"), ("top_by_pnl", f"top-{wf.get('top_n', 50)} by first-half PnL"), ("bottom_by_quality", f"bottom-{wf.get('top_n', 50)} by first-half quality"), ("all_eligible", "all eligible wallets")):
        s = wf.get(key) or {"n": 0}
        if not s.get("n"):
            md.append(f"| {label} | 0 | n/a | n/a | n/a | n/a | n/a |")
            continue
        ci = s["ci95"]
        md.append(f"| {label} | {s['n']} | {s['mean']:+.4f} | {s['median']:+.4f} | {f'[{ci[0]:+.4f}, {ci[1]:+.4f}]' if ci else 'n/a'} | {pct(s['positive_share'], 0)} | {s['sum']:+.3f} |")
    md.append("")
    md.append("## Copy-test: follow the top-K wallets in the second half")
    md.append("")
    md.append(f"Rule: when a top-K wallet (by first-half quality) buys an ETH-quoted coin, buy {ct['order_eth']} ETH at the curve state right after its trade (constant product, reserves = 1.68 ETH virtual + net quote in / 1e9 supply − net tokens out, 1% fee + the coin's median creator tax, our own impact included on entry and exit); sell when it sells, or after {ct['hold_s'] // 60} min. Graduated pools: observed price with a flat 1% + tax. One copy position per wallet-coin episode; {ct['events']:,} leader buys, {ct['priced']:,} priced.")
    md.append("")
    md.append("| K | copy trades | hit rate | mean return | median return | 95% CI of the mean | total PnL (ETH) | exit on leader's sell | mean ret · leader sold | mean ret · 30 min | curve method | median impact |")
    md.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for k, s in ct["by_k"].items():
        if not s.get("n"):
            md.append(f"| {k} | 0 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |")
            continue
        ci = s["ci95_mean_ret"]
        md.append(f"| {k} | {s['n']} | {pct(s['hit_rate'], 0)} | {pct(s['mean_ret'])} | {pct(s['median_ret'])} | {f'[{pct(ci[0])}, {pct(ci[1])}]' if ci else 'n/a'} | {s['total_pnl_eth']:+.4f} | {s['leader_sold']} | {pct(s['mean_ret_leader_sold'])} | {pct(s['mean_ret_timeout'])} | {s['curve_method']} / {s['n']} | {num(s['median_impact_pct'], 2)}% |")
    md.append("")
    md.append("## Reading this honestly")
    md.append("")
    verdicts = []
    for k, s in ct["by_k"].items():
        if s.get("n"):
            verdicts.append(f"K={k}: {'loses' if s['mean_ret'] < 0 else 'earns'} {pct(s['mean_ret'])} per trade over {s['n']} trades ({pct(s['hit_rate'], 0)} hit rate)")
    md.append(f"- Copy-test verdict: {'; '.join(verdicts) if verdicts else 'no priced copy trades in this range'}. A copier pays the 1% fee and the creator tax twice and buys after the leader moved the curve; the leader's own numbers do not include that.")
    tq = wf.get("top_by_quality") or {}
    if tq.get("n"):
        md.append(f"- Persistence: Spearman ρ of first-half vs second-half PnL is {num(sp.get('pnl_h1_vs_pnl_h2'), 3)}; of first-half quality vs second-half PnL {num(sp.get('quality_h1_vs_pnl_h2'), 3)}. The top-{wf.get('top_n')} by quality made {tq['mean']:+.4f} ETH each on average in the second half (95% CI {f'[{tq['ci95'][0]:+.4f}, {tq['ci95'][1]:+.4f}]' if tq.get('ci95') else 'n/a'}); a CI that includes 0 means the forward edge is not established by this sample.")
    else:
        md.append("- Persistence: too few wallets traded in both halves for a walk-forward reading in this range; rerun on a longer store.")
    md.append("- Ranks, sniper flags and rug flags are only as good as the launch time: `launches.ts` is derived from exact block timestamps when the launch block is inside the store; coins launched before the range have unknown buyer ranks (shown as n/a, never 0).")
    md.append("- Unrealized PnL marks open bags at the last minute-median trade price; illiquid coins can rarely be sold at that mark. A wallet's ETH PnL ignores its positions quoted in other assets (kept in `pnl_by_quote`).")
    md.append("- Same address, observed trades. Nothing here says who controls a wallet or why it traded; deployer links are on-chain facts (launch events and snipe-tax exemption), not accusations.")
    md.append("")
    md.append("## Exact commands")
    md.append("")
    md.append("```")
    md.append(f"uv run stampede traders --db {a.db} --fees {a.fees} --min-trades {a.min_trades} --top {a.top} --out {a.out or 'docs/RESEARCH-TRADERS.md'}")
    md.append("# then: GET /api/traders?preset=smart · GET /api/wallet/<address> · web TRADERS view (key 4) · TUI screen 3")
    md.append("```")
    md.append("")
    md.append(f"Written: wallet_stats {sum(len(v) for v in wp['stats'].values()):,} rows (full / first half / second half), wallet_positions for wallets with ≥ 2 trades, wallet_scores bot flags for {wrote_scores:,} wallets (`score` untouched).")
    return "\n".join(md)


# ---- entry point --------------------------------------------------------------------------------------------------
def run(store: Store, a: argparse.Namespace, progress=print) -> dict[str, Any]:
    t0 = time.time()
    q = store.db.execute
    lo0, hi0 = q("SELECT MIN(ts), MAX(ts) FROM trades WHERE ts>0").fetchone()
    if lo0 is None:
        raise SystemExit("no trades with a timestamp in this store")
    lo = int(a.from_ts) if a.from_ts else int(lo0)
    hi = int(a.to_ts) if a.to_ts else int(hi0)
    mid = (lo + hi) // 2 + 1
    max_id = q("SELECT MAX(id) FROM trades").fetchone()[0] or 0
    progress(f"traders: {fmt_ts(lo)}–{fmt_ts(hi)} UTC, split {fmt_ts(mid)}, max trade id {max_id:,}")
    launches = Launches(store)
    facts = Facts(max_id)
    tp = token_pass(store, lo, hi, mid, launches, facts, progress)
    progress(f"token pass done: {tp['rows']:,} trades, {tp['tokens']:,} coins, {tp['seconds']} s")
    fx_rows = q("SELECT COUNT(*) FROM fx_rates").fetchone()[0]
    fx = Fx(store) if fx_rows else None
    run_ts = time.time()
    wp = wallet_pass(store, lo, hi, mid, tp["marks"], facts, launches, fx, a.fees, run_ts, write=not a.no_write, progress=progress)
    progress(f"wallet pass done: {wp['rows']:,} trades, {wp['wallets']:,} wallets, {wp['seconds']} s")
    wf = walk_forward(wp["stats"]["h1"], wp["stats"]["h2"], a.min_trades, a.top, a.seed)
    ks = [int(k) for k in str(a.copy_k).split(",") if k.strip()]
    top = wf.get("top_wallets", [])
    ct = copy_test(store, {k: top[:k] for k in ks}, mid, hi, launches, int(a.order_eth * 1e18), a.hold_s, history_from=lo)
    wrote = update_wallet_scores(store, wp["stats"]["full"], run_ts) if not a.no_write else 0
    text = report(a, store, lo, hi, mid, tp, wp, wf, ct, fx_rows, wrote, t0)
    return {"lo": lo, "hi": hi, "mid": mid, "token_pass": tp, "wallet_pass": {k: v for k, v in wp.items() if k != "stats"}, "walk_forward": {k: v for k, v in wf.items() if k != "top_wallets"}, "copy_test": ct, "report": text, "stats": wp["stats"]}


def build_parser(ap: argparse.ArgumentParser | None = None) -> argparse.ArgumentParser:
    ap = ap or argparse.ArgumentParser(prog="stampede traders")
    ap.add_argument("--db", default="data/research-14d.sqlite")
    ap.add_argument("--from-ts", type=int, default=None)
    ap.add_argument("--to-ts", type=int, default=None)
    ap.add_argument("--out", default="", help="write the markdown report here (default: stdout)")
    ap.add_argument("--fees", default="strict", choices=["strict", "estimate"], help="strict: skip curve rows without fee_raw; estimate: 1%% base fee, creator tax unknown")
    ap.add_argument("--min-trades", type=int, default=5, help="first-half trades needed for the walk-forward and the smart preset")
    ap.add_argument("--top", type=int, default=50, help="size of the top list evaluated forward")
    ap.add_argument("--copy-k", default="10,25,50")
    ap.add_argument("--order-eth", type=float, default=0.02)
    ap.add_argument("--hold-s", type=int, default=COPY_HOLD_S)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--no-write", action="store_true", help="do not write wallet_stats / wallet_positions / wallet_scores")
    ap.add_argument("--json", default="", help="also write the numbers as json here")
    return ap


def main_traders(a: argparse.Namespace) -> int:
    store = Store(a.db)
    store.db.execute("PRAGMA cache_size=-262144")
    res = run(store, a, progress=lambda m: print(m, flush=True))
    if a.out:
        Path(a.out).write_text(res["report"])
        print(f"written {a.out}")
    else:
        print(res["report"])
    if a.json:
        Path(a.json).write_text(json.dumps({k: v for k, v in res.items() if k not in ("report", "stats")}, indent=1, default=str))
    return 0


def main(argv: list[str] | None = None) -> int:
    return main_traders(build_parser().parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
