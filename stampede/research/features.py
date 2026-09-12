"""Streaming per-coin-minute feature table for the runner model (stage 4): `coin_minutes`.

    stampede edge-features --db data/research-12h.sqlite            # or: python -m stampede.research.features

One row per (coin, minute) that had at least one trade. Every feature uses only trades and sequences at or before
the end of that minute (the "decision instant" `ts = (minute + 1) * 60`); every label looks strictly forward from it.

Two passes, both in bounded memory:
- pass A walks `trades` in `(token, ts)` order (the `trades_token` index, no sort) and the 30-min direct/clean
  `sequences` in `buy_token` order (one temp-sorted stream), so one coin is in memory at a time. It reconstructs the
  curve reserves, aggregates the flow windows, joins launch intel and writes the rows plus the labels.
- pass B walks the rows with rotation inflow in time order and fills the walk-forward wallet quality (mean past runner
  rate of the inflow wallets, known only once the rotation's 30-min outcome had passed - no future information).

Curve reconstruction. PONS v2 curves are constant-product on virtual reserves; fees and taxes are taken *outside* the
pool (from quoteIn on buys, from gross quoteOut on sells), so `k = (phantom + quote) * tokens = phantom * supply` never
changes while the curve lives (verified exact on the real-fee smoke store: every buy's tokensOut and every sell's gross
quote matched). Hence `Q = k / T` and T follows from the token amounts alone, which every store has. A coin whose first
trades are not in the store (launched before the range, or a launch-tx buy the normalizer did not attribute) starts
from a state inferred from its first observed trade; when a store has no fee columns the creator tax is fitted per coin
from the first two trades (a sell or a second buy pins it) and labelled `est_tax_bps` with `fees_known = 0`.

Prices are quote units per token (decimals from `quotes`), so USDG- and stock-quoted coins are comparable to ETH ones.
"""
from __future__ import annotations

import argparse
import bisect
import json
import math
import sqlite3
import sys
import time
from collections import defaultdict
from statistics import median
from typing import Any, Iterable, Iterator

from .. import chain
from ..context import launch_intel
from ..store import Store

MAIN = ("direct", "clean")
PHANTOM = chain.CURVE_PHANTOM_QUOTE_WEI
SUPPLY = chain.CURVE_SUPPLY_WEI
K = PHANTOM * SUPPLY  # invariant of every ETH-quoted launch-config-0 curve
FEE_BPS = chain.CURVE_FEE_BPS
DEFAULT_TAX_BPS = 100  # median creator tax on the real-fee smoke store (0 / 100 / 300 / 200 bps are the common values)
HORIZONS = (5, 15, 30, 60)

SCHEMA = """
CREATE TABLE IF NOT EXISTS coin_minutes (
  run_id INTEGER, token TEXT, minute INTEGER, ts INTEGER,
  quote_token TEXT, quote_decimals INTEGER, is_eth INTEGER, stage TEXT, launch_ts INTEGER, age_s INTEGER, since_snipe_zero_s INTEGER,
  price REAL, spot REAL, q_virtual REAL, t_reserve REAL, progress REAL, progress_5m REAL, recon TEXT, est_tax_bps INTEGER, fees_known INTEGER,
  buys_10m INTEGER, sells_10m INTEGER, buyers_10m INTEGER, sellers_10m INTEGER, quote_in_10m REAL, quote_out_10m REAL, vol_10m REAL,
  sell_ratio_10m REAL, out_in_10m REAL, top3_share_10m REAL, holders INTEGER, trades_1h INTEGER, buyers_1h INTEGER, vol_1h REAL,
  mom_5m REAL, mom_10m REAL, range_5m REAL,
  smart_buyers_10m INTEGER, smart_sellers_10m INTEGER, insider_sells_10m INTEGER, dev_sold_10m REAL,
  inflow_10m INTEGER, inflow_prev_per_10m REAL, accel REAL, breadth INTEGER, sequences_10m INTEGER, rot_share REAL,
  quality REAL, quality_cov REAL, smart_inflow INTEGER, smart_inflow_q REAL, inflow_wallets TEXT,
  li_observed INTEGER, dev_buy_share REAL, bundle_n INTEGER, creator_tax_bps INTEGER, deployer_prior_launches_30d INTEGER,
  deployer_graduation_rate REAL, launch_farm INTEGER, socials_present INTEGER, first_buyers_5s INTEGER, taxed_snipers_3s INTEGER,
  gain_5 REAL, gain_15 REAL, gain_30 REAL, gain_60 REAL, dd_30 REAL, t_peak_30 INTEGER, ret_30 REAL, ret_60 REAL,
  grad_30 INTEGER, grad_60 INTEGER, runner_30 INTEGER, horizon_30 INTEGER, horizon_60 INTEGER,
  PRIMARY KEY (run_id, token, minute));
CREATE INDEX IF NOT EXISTS coin_minutes_ts ON coin_minutes(run_id, ts);
CREATE TABLE IF NOT EXISTS coin_minutes_runs (
  run_id INTEGER PRIMARY KEY, db TEXT, started REAL, finished REAL, trades INTEGER, coins INTEGER, rows INTEGER,
  range_from INTEGER, range_to INTEGER, params TEXT, stats TEXT);
"""

COLUMNS = [c.strip().split()[0] for c in SCHEMA.split("coin_minutes (")[1].split("PRIMARY KEY")[0].replace("\n", " ").split(",") if c.strip()]

# features the model may use (labels, identifiers and bookkeeping excluded); the radar row provides the same names
MODEL_FEATURES = [
    "is_eth", "age_s", "since_snipe_zero_s", "progress", "progress_5m",
    "buys_10m", "sells_10m", "buyers_10m", "sellers_10m", "quote_in_10m", "quote_out_10m", "vol_10m", "sell_ratio_10m", "out_in_10m", "top3_share_10m",
    "holders", "trades_1h", "buyers_1h", "vol_1h", "mom_5m", "mom_10m", "range_5m",
    "smart_buyers_10m", "smart_sellers_10m", "insider_sells_10m", "dev_sold_10m",
    "inflow_10m", "inflow_prev_per_10m", "accel", "breadth", "sequences_10m", "rot_share", "quality", "quality_cov", "smart_inflow", "smart_inflow_q",
    "li_observed", "dev_buy_share", "bundle_n", "creator_tax_bps", "deployer_prior_launches_30d", "deployer_graduation_rate", "launch_farm", "socials_present", "first_buyers_5s", "taxed_snipers_3s",
]


# ---- curve arithmetic (pure; shared by the simulator, the verdict and the tests) -------------------------------------
def buy_fill(q_virtual: int, t_reserve: int, spent: int, tax_bps: int, elapsed_s: int | None = None, fee_bps: int = FEE_BPS) -> dict[str, int]:
    """A buy of `spent` raw quote on a curve with virtual quote `q_virtual` (phantom + real) and `t_reserve` tokens.
    fee = 1% (+ snipe tax inside the 3-s window), tax = creator tax, both on quoteIn; tokensOut = net*T/(Q+net)."""
    snipe_bps = launch_intel.snipe_tax_bps(elapsed_s, creator_tax_bps=tax_bps) if elapsed_s is not None and elapsed_s < chain.SNIPE_TAX_SECONDS else 0
    fee = spent * fee_bps // 10_000
    snipe = spent * snipe_bps // 10_000
    tax = spent * tax_bps // 10_000
    net = spent - fee - snipe - tax
    d = q_virtual + net
    out = net * t_reserve // d if d > 0 and net > 0 else 0
    return {"tokens_out": out, "fee": fee, "snipe": snipe, "tax": tax, "net": net}


def sell_fill(q_virtual: int, t_reserve: int, tokens_in: int, tax_bps: int, fee_bps: int = FEE_BPS) -> dict[str, int]:
    """A sell of `tokens_in` raw tokens: gross = tokensIn*Q/(T+tokensIn); out = gross - 1% - creator tax."""
    d = t_reserve + tokens_in
    gross = tokens_in * q_virtual // d if d > 0 and tokens_in > 0 else 0
    fee = gross * fee_bps // 10_000
    tax = gross * tax_bps // 10_000
    return {"quote_out": gross - fee - tax, "gross": gross, "fee": fee, "tax": tax}


def q0_from_buy(net: int, tokens_out: int, k: int = K) -> int:
    """Virtual quote before a buy that turned `net` quote into `tokens_out` tokens on a curve with invariant k:
    tokensOut = net*(k/Q)/(Q+net)  =>  Q^2 + net*Q - net*k/tokensOut = 0."""
    c = net * k // tokens_out
    return (-net + math.isqrt(net * net + 4 * c)) // 2


def q0_from_sell(gross: int, tokens_in: int, k: int = K) -> int:
    """Virtual quote before a sell that paid `gross` quote for `tokens_in` tokens: gross = tokensIn*Q/(k/Q + tokensIn)
    => gross*tokensIn*Q^2 ... solved as c*tokensIn*Q^2 - gross*tokensIn*Q - gross*k = 0 with c = 1."""
    a = tokens_in
    b = gross * tokens_in
    cc = gross * k
    return (b + math.isqrt(b * b + 4 * a * cc)) // (2 * a)


class Curve:
    """Reconstructed ETH-quoted curve: `q` = virtual quote (phantom + real), `t` = tokens on the curve; k = q*t."""

    __slots__ = ("q", "t", "tax_bps", "valid")

    def __init__(self, q: int = PHANTOM, t: int = SUPPLY, tax_bps: int = 0):
        self.q, self.t, self.tax_bps, self.valid = q, t, tax_bps, True

    def apply(self, side: str, tokens: int) -> None:
        # the invariant fixes q from t: fees never enter the pool
        self.t = self.t - tokens if side == "buy" else self.t + tokens
        if self.t <= 0:
            self.valid = False
            self.t = 1
        self.q = K // self.t

    def spot(self) -> float:
        return self.q / self.t

    def progress(self, threshold: int) -> float | None:
        if not threshold:
            return None
        return max(0.0, min(1.0, (self.q - PHANTOM) / threshold))


class FitTrade:
    """One observed curve trade for the reconstruction fit. `snipe`: inside the 3-s window and not by the deployer, so
    the snipe tax (unknown without the exemption list) may be inside its fee; `ts`: block time (trades of one block
    are validated only at the block boundary - their order inside the block is not the execution order)."""

    __slots__ = ("ts", "side", "tokens", "quote", "fee", "tax", "snipe")

    def __init__(self, ts: int, side: str, tokens: int, quote: int, fee: int | None = None, tax: int | None = None, snipe: bool = False):
        self.ts, self.side, self.tokens, self.quote, self.fee, self.tax, self.snipe = ts, side, tokens, quote, fee, tax, snipe

    def gross(self) -> int:
        """Quote before fees: quoteIn on buys, quoteOut + fee + tax on sells (fee columns known)."""
        return self.quote if self.side == "buy" else self.quote + (self.fee or 0) + (self.tax or 0)


def fit_curve(first: list[FitTrade], tax_hint: int | None, fees_known: bool, fresh: bool) -> tuple[Curve | None, str, int]:
    """State before the first usable curve trade and the creator tax, from the first trades of one coin.

    `first`: up to N trades in time order, curve venue only. `fresh`: the first observed trade is the first trade of
    the curve (launch inside the range). Returns (curve at the state *before* the first usable trade, recon label,
    tax_bps). recon: exact (fresh curve reproduces the observed amounts) | inferred (state inferred from the first trade,
    later trades reproduced) | approx (median residual <= 2%) | none."""
    if not first:
        return None, "none", tax_hint if tax_hint is not None else DEFAULT_TAX_BPS
    if fees_known:
        tax = tax_hint
        for tr in first:  # the first trade with a plausible tax ratio; the snipe tax is inside fee_raw and irrelevant
            r = launch_intel.creator_tax_bps_of(tr.side, tr.quote, tr.fee or 0, tr.tax or 0)
            if r is not None:
                tax = r
                break
        if tax is None:
            tax = DEFAULT_TAX_BPS
        c = _state_before(first[0], tax, fresh, known=True)
        if c is None:
            return None, "none", tax
        res = _residual(c, first, known=True)
        if fresh and c.q == PHANTOM and res <= 1e-6:
            return c, "exact", tax
        label = "inferred" if res <= 1e-4 else ("approx" if res <= 0.02 else "none")
        return (c if label != "none" else None), label, tax
    # ---- fees unknown: fit (Q0, tax) on the first usable trades, validate on the rest ----
    usable = [x for x in first if not x.snipe]
    if not usable:
        return None, "none", tax_hint if tax_hint is not None else DEFAULT_TAX_BPS
    fresh_ok = fresh and usable[0] is first[0]
    if tax_hint is not None or len(usable) < 2:
        tax = tax_hint if tax_hint is not None else DEFAULT_TAX_BPS
    else:
        # coarse-to-fine grid over the creator tax: the second usable trade pins it (a wrong tax shows up as a
        # residual of the same size on every later trade)
        def resid(t: int) -> float:
            c = _state_before(usable[0], t, fresh_ok)
            return _residual(c, usable[:6]) if c is not None else 1.0

        coarse = min(range(0, launch_intel.MAX_CREATOR_TAX_BPS + 1, 25), key=resid)
        tax = min(range(max(0, coarse - 20), min(launch_intel.MAX_CREATOR_TAX_BPS, coarse + 20) + 1, launch_intel.TAX_ROUND_BPS), key=resid)
    c = _state_before(usable[0], tax, fresh_ok)
    if c is None:
        return None, "none", tax
    res = _residual(c, usable)
    if fresh_ok and res <= 1e-6 and c.q == PHANTOM:
        return c, "exact", tax
    label = "inferred" if res <= 1e-4 else ("approx" if res <= 0.02 else "none")
    return (c if label != "none" else None), label, tax


def _net_of(tr: FitTrade, tax: int, known: bool) -> int:
    if known:
        return tr.quote - (tr.fee or 0) - (tr.tax or 0)
    return tr.quote - tr.quote * FEE_BPS // 10_000 - tr.quote * tax // 10_000


def _gross_of(tr: FitTrade, tax: int, known: bool) -> int:
    if known:
        return tr.gross()
    rate = 10_000 - FEE_BPS - tax
    return tr.quote * 10_000 // rate if rate > 0 else 0


def _state_before(tr: FitTrade, tax: int, fresh: bool, known: bool = False) -> Curve | None:
    if tr.tokens <= 0 or tr.quote <= 0:
        return None
    if tr.side == "buy":
        net = _net_of(tr, tax, known)
        if net <= 0:
            return None
        if fresh:
            c = Curve(tax_bps=tax)
            pred = net * c.t // (c.q + net)
            if abs(pred - tr.tokens) <= max(1, tr.tokens // 10**6):
                return c
        q0 = q0_from_buy(net, tr.tokens)
    else:
        gross = _gross_of(tr, tax, known)
        if gross <= 0:
            return None
        q0 = q0_from_sell(gross, tr.tokens)
    if q0 <= 0:
        return None
    return Curve(q0, K // q0, tax)


def _residual(c0: Curve, trades: list[FitTrade], known: bool = False) -> float:
    """Median relative residual of the observed trades against the reconstruction (buys: tokensOut; sells: gross
    quote when the fees are known, net quote out otherwise). Only the first trade of each block is compared: the
    state before it is order-independent, the order of trades inside a block is not."""
    c = Curve(c0.q, c0.t, c0.tax_bps)
    res: list[float] = []
    prev_ts = None
    for tr in trades[:16]:
        if tr.tokens <= 0 or tr.quote <= 0:
            continue
        if tr.ts != prev_ts:
            if tr.side == "buy":
                net = _net_of(tr, c.tax_bps, known)
                pred = net * c.t // (c.q + net) if c.q + net > 0 else 0
                res.append(abs(pred - tr.tokens) / tr.tokens)
            else:
                gross = tr.tokens * c.q // (c.t + tr.tokens)
                if known:
                    g = tr.gross()
                    res.append(abs(gross - g) / g if g else 1.0)
                else:
                    out = gross - gross * FEE_BPS // 10_000 - gross * c.tax_bps // 10_000
                    res.append(abs(out - tr.quote) / tr.quote)
        c.apply(tr.side, tr.tokens)
        prev_ts = tr.ts
    return median(res) if res else 1.0


def curve_path(trades: list[tuple], *, launch_ts: int | None, deployer: str | None, grad_ts: int | None, tax_hint: int | None) -> dict[str, Any]:
    """Reserve state after every trade of one ETH-quoted coin.

    trades: (ts, side, token_amount, quote_amount, wallet, venue, fee_raw, tax_raw) in time order (all venues; a v4
    row ends the path). Returns {recon, tax_bps, fees_known, start, spot[], q[], t[]} with per-trade arrays aligned to
    `trades`; entries before the first fitted trade, after graduation or on v4 are 0. The simulator and the feature
    pass share this function."""
    n = len(trades)
    fees_known = any(t[6] is not None for t in trades[:5])
    first_ts = trades[0][0] if trades else 0
    fresh = bool(launch_ts and trades and first_ts >= launch_ts and (grad_ts is None or first_ts < grad_ts) and trades[0][5] == "curve" and trades[0][1] == "buy")
    out: dict[str, Any] = {"recon": "none", "tax_bps": tax_hint if tax_hint is not None else DEFAULT_TAX_BPS, "fees_known": fees_known, "spot": [0.0] * n, "q": [0] * n, "t": [0] * n, "start": None}
    curve_idx = [i for i in range(n) if trades[i][5] == "curve"]
    if not curve_idx:
        return out
    first: list[FitTrade] = []
    first_idx: list[int] = []
    for i in curve_idx[:24]:
        ts, side, ta, qa, w, _venue, fee, tax = trades[i]
        if not ta or not qa or not int(ta) or not int(qa):
            continue
        snipe = bool(launch_ts) and (ts - launch_ts) < chain.SNIPE_TAX_SECONDS and w != deployer and not fees_known
        first.append(FitTrade(ts, side, int(ta), int(qa), int(fee) if fee is not None else None, int(tax) if tax is not None else None, snipe))
        first_idx.append(i)
    curve, recon, tax_bps = fit_curve(first, tax_hint, fees_known, fresh)
    out["recon"], out["tax_bps"] = recon, tax_bps
    if curve is None:
        return out
    skip = 0
    while skip < len(first) and first[skip].snipe:
        skip += 1
    if skip >= len(first_idx):
        out["recon"] = "none"
        return out
    start = first_idx[skip]
    out["start"] = start
    c = Curve(curve.q, curve.t, curve.tax_bps)
    spot, qa_, ta_ = out["spot"], out["q"], out["t"]
    for i in range(start, n):
        ts, side, tok, _q, _w, venue, _f, _t = trades[i]
        if venue != "curve" or (grad_ts and ts >= grad_ts):
            break
        if tok:
            c.apply(side, int(tok))
        spot[i] = c.spot()
        qa_[i] = c.q
        ta_[i] = c.t
    return out


# ---- shared 10-minute flow statistics (the radar computes the same numbers from its preloaded hour of trades) --------
def flow_stats(rows: Iterable[tuple], scale: float) -> dict[str, Any]:
    """rows: (ts, side, token_amount_raw float, quote_amount_raw float, wallet) inside the window. `scale` converts raw
    quote to quote units (10**-decimals)."""
    buys = sells = 0
    qin = qout = 0.0
    buyers: dict[str, float] = {}
    sellers: set[str] = set()
    for _ts, side, _ta, qa, w in rows:
        if side == "buy":
            buys += 1
            qin += qa
            buyers[w] = buyers.get(w, 0.0) + qa
        else:
            sells += 1
            qout += qa
            sellers.add(w)
    top3 = sum(sorted(buyers.values(), reverse=True)[:3]) / qin if qin > 0 else None
    return {
        "buys_10m": buys,
        "sells_10m": sells,
        "buyers_10m": len(buyers),
        "sellers_10m": len(sellers),
        "quote_in_10m": qin * scale,
        "quote_out_10m": qout * scale,
        "vol_10m": (qin + qout) * scale,
        "sell_ratio_10m": (sells / buys) if buys else (float(sells) if sells else 0.0),
        "out_in_10m": (qout / qin) if qin > 0 else (1.0 if qout > 0 else 0.0),
        "top3_share_10m": top3,
    }


# ---- pass A ----------------------------------------------------------------------------------------------------------
def _sequences_by_token(db: sqlite3.Connection, window_s: int) -> Iterator[tuple[str, int, str, str]]:
    cur = db.cursor()
    cur.arraysize = 10_000
    cur.execute("SELECT buy_token, buy_ts, wallet, sell_token FROM sequences WHERE window_s=? AND grade IN ('direct','clean') AND buy_ts>0 ORDER BY buy_token, buy_ts", (window_s,))
    yield from cur


def _trades_by_token(db: sqlite3.Connection) -> Iterator[tuple]:
    cur = db.cursor()
    cur.arraysize = 20_000
    cur.execute("SELECT token, ts, side, token_amount, quote_amount, quote_token, wallet, venue, fee_raw, tax_raw FROM trades WHERE ts>0 ORDER BY token, ts, id")
    yield from cur


class _Peek:
    """Iterator with one-item lookahead; used to merge the sequences stream into the per-token trade groups."""

    def __init__(self, it: Iterator):
        self.it = it
        self.head = next(it, None)

    def take_token(self, token: str) -> list[tuple]:
        out: list[tuple] = []
        while self.head is not None and self.head[0] < token:
            self.head = next(self.it, None)  # sequences of coins without any trade row (should not happen)
        while self.head is not None and self.head[0] == token:
            out.append(self.head)
            self.head = next(self.it, None)
        return out


def _bots(db: sqlite3.Connection, hours: float) -> set[str]:
    n = db.execute("SELECT COUNT(*) FROM wallets WHERE trades IS NOT NULL").fetchone()[0]
    rows = db.execute("SELECT address, trades FROM wallets WHERE trades IS NOT NULL") if n else db.execute("SELECT wallet, COUNT(*) FROM trades GROUP BY wallet")
    return {w for w, c in rows if c and (c / max(1.0, hours) > 60 or c > 500)}


def _smart_set(db: sqlite3.Connection) -> tuple[dict[str, float], int | None]:
    """Top-decile wallets of the *first half* of the range by stage-2 quality, valid for decisions after that half."""
    try:
        r = db.execute("SELECT range_to, run_ts FROM wallet_stats WHERE label='h1' ORDER BY run_ts DESC LIMIT 1").fetchone()
    except sqlite3.OperationalError:
        return {}, None
    if not r:
        return {}, None
    rows = db.execute("SELECT wallet, quality FROM wallet_stats WHERE label='h1' AND run_ts=? AND trades>=5 AND is_bot=0 AND deployer_linked=0 AND quality IS NOT NULL ORDER BY quality DESC", (r[1],)).fetchall()
    n = max(1, len(rows) // 10) if rows else 0
    return {w: q for w, q in rows[:n]}, int(r[0])


def _exempt(db: sqlite3.Connection) -> dict[str, set[str]]:
    """curve address -> declared snipe-tax-exempt wallets (SnipeTaxExempted logs, when indexed)."""
    out: dict[str, set[str]] = defaultdict(set)
    for curve, t1 in db.execute("SELECT address, topic1 FROM logs WHERE kind='snipe_tax_exempted' AND topic1 IS NOT NULL"):
        out[curve].add(chain.addr_from_topic(t1))
    return out


def _wid(w: str) -> int:
    return int(w[-12:], 16)


def coin_rows(
    token: str,
    trades: list[tuple],
    seqs: list[tuple],
    *,
    launch: dict[str, Any] | None,
    intel: dict[str, Any] | None,
    grad_ts: int | None,
    qdec: dict[str, int],
    bots: set[str],
    smart: dict[str, float],
    smart_from: int | None,
    exempt: set[str],
    range_hi: int,
    run_id: int,
    range_lo: int = 0,
) -> tuple[list[tuple], list[tuple[int, int, int]]]:
    """All coin_minutes rows of one coin plus the rotation outcomes (known_at, wallet_id, hit) for pass B.

    trades: (token, ts, side, token_amount, quote_amount, quote_token, wallet, venue, fee_raw, tax_raw) sorted by ts.
    seqs: (buy_token, buy_ts, wallet, sell_token) sorted by buy_ts."""
    if not trades:
        return [], []
    quote = trades[-1][5] or chain.NATIVE
    dec = qdec.get(quote, 18)
    scale = 10.0 ** (-dec)
    pscale = 10.0 ** (18 - dec)  # raw quote / raw token -> quote units per token
    is_eth = quote == chain.NATIVE
    launch_ts = launch.get("ts") if launch else None
    threshold = launch.get("threshold") if launch else None
    deployer = launch.get("deployer") if launch else None
    first_ts = trades[0][1]
    fees_known = any(t[8] is not None for t in trades[:5])
    tax_hint = intel.get("creator_tax_bps") if intel else None

    # ---- per-trade arrays ----
    ts_a: list[int] = []
    side_a: list[str] = []
    ta_a: list[float] = []
    qa_a: list[float] = []
    w_a: list[str] = []
    price_a: list[float] = []
    for _tok, ts, side, ta, qa, _q, w, venue, _f, _t in trades:
        tai = float(ta) if ta else 0.0
        qai = float(qa) if qa else 0.0
        ts_a.append(ts)
        side_a.append(side)
        ta_a.append(tai)
        qa_a.append(qai)
        w_a.append(w)
        price_a.append((qai / tai) * pscale if tai > 0 and qai > 0 else 0.0)
    n = len(trades)

    # ---- curve reconstruction (ETH-quoted, curve venue) ----
    path = curve_path([(t[1], t[2], t[3], t[4], t[6], t[7], t[8], t[9]) for t in trades], launch_ts=launch_ts, deployer=deployer, grad_ts=grad_ts, tax_hint=tax_hint) if is_eth else None
    recon = path["recon"] if path else "none"
    tax_bps = path["tax_bps"] if path else (tax_hint if tax_hint is not None else DEFAULT_TAX_BPS)
    spot_a: list[float] = path["spot"] if path else [0.0] * n
    q_a: list[int] = path["q"] if path else [0] * n
    t_a: list[int] = path["t"] if path else [0] * n
    # venue tells the stage when the graduation predates the range (no row in `graduations`)
    v4_from: int | None = None
    for i in range(n):
        if trades[i][7] != "curve":
            v4_from = ts_a[i]
            break

    # ---- per-minute series ----
    minutes: list[int] = []
    per_min: dict[int, list[int]] = defaultdict(list)
    for i, ts in enumerate(ts_a):
        m = ts // 60
        if not minutes or minutes[-1] != m:
            minutes.append(m)
        per_min[m].append(i)
    pmed: list[float] = []
    for m in minutes:
        ps = [price_a[i] for i in per_min[m] if price_a[i] > 0]
        pmed.append(sorted(ps)[len(ps) // 2] if ps else (pmed[-1] if pmed else 0.0))  # upper median, as research.backtest

    def price_at(m: int) -> float | None:
        i = bisect.bisect_right(minutes, m) - 1
        return pmed[i] if i >= 0 and pmed[i] > 0 else None

    # ---- rotation inflow per minute (bots excluded) ----
    seq_ts = [s[1] for s in seqs if s[2] not in bots]
    seq_w = [s[2] for s in seqs if s[2] not in bots]
    seq_src = [s[3] for s in seqs if s[2] not in bots]

    # ---- launch features ----
    li = intel or {}
    li_cols = (
        1 if li.get("observed") else (0 if li else None),
        li.get("dev_buy_share"), li.get("bundle_n"), li.get("creator_tax_bps"), li.get("deployer_prior_launches_30d"), li.get("deployer_graduation_rate"),
        (None if li.get("launch_farm") is None else int(bool(li.get("launch_farm")))), (None if li.get("socials_present") is None else int(bool(li.get("socials_present")))),
        li.get("first_buyers_5s"), li.get("taxed_snipers_3s"),
    )
    snipe_zero = li.get("snipe_tax_zero_ts") or ((launch_ts + chain.SNIPE_TAX_SECONDS) if launch_ts else None)

    rows: list[tuple] = []
    outcomes: list[tuple[int, int, int]] = []
    balances: dict[str, float] = {}
    holders = 0
    j_bal = 0  # trades folded into balances so far
    i_lo10 = i_lo60 = i_lo5 = 0
    for mi, m in enumerate(minutes):
        end = (m + 1) * 60
        idx = per_min[m]
        last = idx[-1]
        # fold balances up to the end of this minute
        while j_bal <= last:
            w = w_a[j_bal]
            b = balances.get(w, 0.0)
            nb = b + ta_a[j_bal] if side_a[j_bal] == "buy" else b - ta_a[j_bal]
            if b <= 0 < nb:
                holders += 1
            elif nb <= 0 < b:
                holders -= 1
            balances[w] = nb
            j_bal += 1
        while i_lo10 <= last and ts_a[i_lo10] <= end - 600:
            i_lo10 += 1
        while i_lo60 <= last and ts_a[i_lo60] <= end - 3600:
            i_lo60 += 1
        while i_lo5 <= last and ts_a[i_lo5] <= end - 300:
            i_lo5 += 1
        win10 = range(i_lo10, last + 1)
        fs = flow_stats(((ts_a[i], side_a[i], ta_a[i], qa_a[i], w_a[i]) for i in win10), scale)
        buyers_1h = {w_a[i] for i in range(i_lo60, last + 1) if side_a[i] == "buy"}
        vol_1h = sum(qa_a[i] for i in range(i_lo60, last + 1)) * scale
        p0 = pmed[mi] if pmed[mi] > 0 else None
        p5 = price_at(m - 5)
        p10 = price_at(m - 10)
        mom5 = (p0 / p5 - 1) if p0 and p5 else None
        mom10 = (p0 / p10 - 1) if p0 and p10 else None
        ps5 = [price_a[i] for i in range(i_lo5, last + 1) if price_a[i] > 0]
        range5 = (max(ps5) / min(ps5) - 1) if len(ps5) >= 2 and min(ps5) > 0 else (0.0 if ps5 else None)
        smart_b = smart_s = None
        if smart and smart_from is not None and end > smart_from:
            smart_b = len({w_a[i] for i in win10 if side_a[i] == "buy" and w_a[i] in smart})
            smart_s = len({w_a[i] for i in win10 if side_a[i] == "sell" and w_a[i] in smart})
        insider_sells = sum(1 for i in win10 if side_a[i] == "sell" and (w_a[i] == deployer or w_a[i] in exempt))
        dev_sold = sum(qa_a[i] for i in win10 if side_a[i] == "sell" and w_a[i] == deployer) * scale
        # reserves at the end of the minute
        spot = q_v = t_r = None
        prog = None
        if spot_a[last] > 0:
            spot, q_v, t_r = spot_a[last], q_a[last] * 1e-18, t_a[last] * 1e-18
            prog = max(0.0, min(1.0, (q_a[last] - PHANTOM) / threshold)) if threshold else None
        prog5 = None
        if prog is not None:
            k5 = bisect.bisect_right(minutes, m - 5) - 1
            if k5 >= 0:
                l5 = per_min[minutes[k5]][-1]
                if q_a[l5] > 0 and threshold:
                    prog5 = prog - max(0.0, min(1.0, (q_a[l5] - PHANTOM) / threshold))
        stage = "graduated" if (grad_ts and grad_ts <= end) or (v4_from is not None and v4_from <= end) else "curve"
        # age: from the launch; without a launch row, from the first trade unless the coin was already trading when
        # the range starts (then the age is unknown, not "a few minutes")
        age = (end - launch_ts) if launch_ts else ((end - first_ts) if first_ts > range_lo + 120 else None)
        # rotation inflow
        s_hi = bisect.bisect_right(seq_ts, end)
        s_lo10 = bisect.bisect_right(seq_ts, end - 600)
        s_lo30 = bisect.bisect_right(seq_ts, end - 1800)
        w10 = {seq_w[k] for k in range(s_lo10, s_hi)}
        wprev = {seq_w[k] for k in range(s_lo30, s_lo10)}
        src10 = {seq_src[k] for k in range(s_lo10, s_hi)}
        prev_rate = len(wprev) / 2.0
        inflow = len(w10)
        accel = (inflow / prev_rate) if prev_rate > 0 else float(inflow)
        smart_in = smart_in_q = None
        if smart and smart_from is not None and end > smart_from:
            hits = [smart[w] for w in w10 if w in smart]
            smart_in = len(hits)
            smart_in_q = (sum(hits) / len(hits)) if hits else None
        # labels (strictly after the decision instant)
        labels: dict[str, Any] = {}
        for H in HORIZONS:
            lo = bisect.bisect_right(minutes, m)
            hi = bisect.bisect_right(minutes, m + H)
            ok = (m + H) * 60 <= range_hi
            labels[f"horizon_{H}"] = int(ok)
            if p0 and ok:
                seg = pmed[lo:hi]
                mx = max(seg) if seg else p0
                labels[f"gain_{H}"] = mx / p0 - 1
                if H == 30:
                    mn = min(seg) if seg else p0
                    labels["dd_30"] = mn / p0 - 1
                    labels["t_peak_30"] = (minutes[lo + seg.index(mx)] - m) if seg else 0
                    pe = price_at(m + 30)
                    labels["ret_30"] = (pe / p0 - 1) if pe else None
                if H == 60:
                    pe = price_at(m + 60)
                    labels["ret_60"] = (pe / p0 - 1) if pe else None
            else:
                labels[f"gain_{H}"] = None
        grad30 = int(bool(grad_ts and end < grad_ts <= end + 1800)) if labels["horizon_30"] else None
        grad60 = int(bool(grad_ts and end < grad_ts <= end + 3600)) if labels["horizon_60"] else None
        runner = None
        if labels["horizon_30"] and (labels.get("gain_30") is not None or grad30):
            runner = int((labels.get("gain_30") or 0.0) >= 1.0 or bool(grad30))
        if runner is not None and inflow:
            for k in range(s_lo10, s_hi):
                if seq_ts[k] // 60 == m:
                    outcomes.append((end + 1800, _wid(seq_w[k]), runner))
        rows.append((
            run_id, token, m, end,
            quote, dec, int(is_eth), stage, launch_ts, age, (end - snipe_zero) if snipe_zero else None,
            p0, spot, q_v, t_r, prog, prog5, recon if spot is not None else "none", tax_bps, int(fees_known),
            fs["buys_10m"], fs["sells_10m"], fs["buyers_10m"], fs["sellers_10m"], fs["quote_in_10m"], fs["quote_out_10m"], fs["vol_10m"],
            fs["sell_ratio_10m"], fs["out_in_10m"], fs["top3_share_10m"], holders, last + 1 - i_lo60, len(buyers_1h), vol_1h,
            mom5, mom10, range5,
            smart_b, smart_s, insider_sells, dev_sold,
            inflow, prev_rate, accel, len(src10), s_hi - s_lo10, (inflow / fs["buyers_10m"]) if fs["buyers_10m"] else None,
            None, None, smart_in, smart_in_q, json.dumps(sorted(_wid(w) for w in w10)) if w10 else None,
            *li_cols,
            labels.get("gain_5"), labels.get("gain_15"), labels.get("gain_30"), labels.get("gain_60"), labels.get("dd_30"), labels.get("t_peak_30"), labels.get("ret_30"), labels.get("ret_60"),
            grad30, grad60, runner, labels["horizon_30"], labels["horizon_60"],
        ))
    return rows, outcomes


def build(db_path: str, *, window_s: int = 1800, features_db: str | None = None, limit_coins: int | None = None, log=print) -> dict[str, Any]:
    t0 = time.time()
    store = Store(db_path)
    launch_intel.ensure_table(store.db)
    out = Store(features_db) if features_db else store
    out.db.executescript(SCHEMA)
    q = store.db.execute
    lo, hi = q("SELECT MIN(ts), MAX(ts) FROM trades WHERE ts>0").fetchone()
    if lo is None:
        return {"rows": 0, "coins": 0, "note": "no trades"}
    hours = max(1.0, (hi - lo) / 3600)
    run_id = int(time.time())
    out.db.execute("INSERT INTO coin_minutes_runs(run_id, db, started, range_from, range_to, params) VALUES(?,?,?,?,?,?)", (run_id, db_path, t0, lo, hi, json.dumps({"window_s": window_s})))
    out.commit()
    qdec = {r[0]: r[1] for r in q("SELECT address, decimals FROM quotes")}
    qdec.setdefault(chain.NATIVE, 18)
    launches = {r[0]: {"ts": r[1], "deployer": r[2], "threshold": int(r[3]) if r[3] else None, "pair_token": r[4]} for r in q("SELECT token, ts, deployer, threshold, pair_token FROM launches")}
    grads = {r[0]: r[1] for r in q("SELECT token, ts FROM graduations WHERE ts IS NOT NULL")}
    intel_all: dict[str, dict[str, Any]] = {}
    cols = ",".join(launch_intel.API_FIELDS)
    for r in q(f"SELECT token, {cols} FROM launch_intel"):
        intel_all[r[0]] = dict(zip(launch_intel.API_FIELDS, r[1:]))
    bots = _bots(store.db, hours)
    smart, smart_from = _smart_set(store.db)
    exempt_by_curve = _exempt(store.db)
    curve_of = {r[1]: r[0] for r in q("SELECT curve, token FROM curves")}
    log(f"range {lo}-{hi} ({hours:.1f} h), {len(launches):,} launches, {len(intel_all):,} launch_intel rows, {len(bots):,} bot wallets, smart set {len(smart):,} (valid after {smart_from}), exempt lists {len(exempt_by_curve):,}")

    # streaming connections separate from the writer
    rdb = sqlite3.connect(db_path)
    rdb.execute("PRAGMA cache_size=-262144")
    sdb = sqlite3.connect(db_path)
    seq_stream = _Peek(_sequences_by_token(sdb, window_s))
    ins = f"INSERT OR REPLACE INTO coin_minutes({','.join(COLUMNS)}) VALUES({','.join('?' * len(COLUMNS))})"
    buf: list[tuple] = []
    all_outcomes: list[tuple[int, int, int]] = []
    n_rows = n_coins = n_trades = 0
    recon_count: dict[str, int] = defaultdict(int)
    cur_tok: str | None = None
    group: list[tuple] = []
    last_log = time.time()

    def flush_coin(tok: str, trs: list[tuple]) -> None:
        nonlocal n_rows, n_coins
        seqs = seq_stream.take_token(tok)
        la = launches.get(tok)
        rows, outs = coin_rows(tok, trs, seqs, launch=la, intel=intel_all.get(tok), grad_ts=grads.get(tok), qdec=qdec, bots=bots, smart=smart, smart_from=smart_from, exempt=exempt_by_curve.get(curve_of.get(tok, ""), set()), range_hi=hi, run_id=run_id, range_lo=lo)
        if rows:
            recon_count[rows[-1][17]] += 1
        buf.extend(rows)
        all_outcomes.extend(outs)
        n_rows += len(rows)
        n_coins += 1
        if len(buf) >= 20_000:
            out.db.executemany(ins, buf)
            out.commit()
            buf.clear()

    for tr in _trades_by_token(rdb):
        n_trades += 1
        if tr[0] != cur_tok:
            if cur_tok is not None:
                flush_coin(cur_tok, group)
                if limit_coins and n_coins >= limit_coins:
                    group = []
                    cur_tok = None
                    break
            cur_tok = tr[0]
            group = []
        group.append(tr)
        if time.time() - last_log > 30:
            log(f"  pass A: {n_trades:,} trades, {n_coins:,} coins, {n_rows:,} rows, {time.time() - t0:.0f} s")
            last_log = time.time()
    if cur_tok is not None and group:
        flush_coin(cur_tok, group)
    if buf:
        out.db.executemany(ins, buf)
        out.commit()
        buf.clear()
    tA = time.time() - t0
    log(f"pass A done: {n_trades:,} trades, {n_coins:,} coins, {n_rows:,} rows in {tA:.0f} s; recon {dict(recon_count)}")

    # ---- pass B: walk-forward wallet quality of the inflow wallets ----
    tB0 = time.time()
    all_outcomes.sort()
    hits: dict[int, list[int]] = {}
    k = 0
    upd: list[tuple] = []
    n_q = 0
    for rowid, end, wj in out.db.execute("SELECT rowid, ts, inflow_wallets FROM coin_minutes WHERE run_id=? AND inflow_wallets IS NOT NULL ORDER BY ts", (run_id,)).fetchall():
        while k < len(all_outcomes) and all_outcomes[k][0] <= end:
            _, wid, hit = all_outcomes[k]
            h = hits.get(wid)
            if h is None:
                hits[wid] = [1, hit]
            else:
                h[0] += 1
                h[1] += hit
            k += 1
        ws = json.loads(wj)
        qs = []
        for wid in ws:
            h = hits.get(wid)
            if h:
                qs.append((h[1] + 1) / (h[0] + 2))
        if qs:
            upd.append((sum(qs) / len(qs), len(qs) / len(ws), rowid))
            n_q += 1
        else:
            upd.append((None, 0.0, rowid))
        if len(upd) >= 20_000:
            out.db.executemany("UPDATE coin_minutes SET quality=?, quality_cov=? WHERE rowid=?", upd)
            upd.clear()
    if upd:
        out.db.executemany("UPDATE coin_minutes SET quality=?, quality_cov=? WHERE rowid=?", upd)
    out.commit()
    tB = time.time() - tB0
    stats = {"trades": n_trades, "coins": n_coins, "rows": n_rows, "rotation_outcomes": len(all_outcomes), "rows_with_quality": n_q, "recon": dict(recon_count), "pass_a_s": round(tA, 1), "pass_b_s": round(tB, 1), "total_s": round(time.time() - t0, 1), "range_from": lo, "range_to": hi, "bots": len(bots), "smart_wallets": len(smart), "smart_from": smart_from}
    out.db.execute("UPDATE coin_minutes_runs SET finished=?, trades=?, coins=?, rows=?, stats=? WHERE run_id=?", (time.time(), n_trades, n_coins, n_rows, json.dumps(stats), run_id))
    out.commit()
    log(f"pass B done: quality on {n_q:,} of the inflow rows in {tB:.0f} s; total {time.time() - t0:.0f} s")
    stats["run_id"] = run_id
    return stats


def latest_run(db: sqlite3.Connection) -> dict[str, Any] | None:
    try:
        r = db.execute("SELECT run_id, range_from, range_to, rows, stats FROM coin_minutes_runs WHERE finished IS NOT NULL ORDER BY run_id DESC LIMIT 1").fetchone()
    except sqlite3.OperationalError:
        return None
    if not r:
        return None
    return {"run_id": r[0], "range_from": r[1], "range_to": r[2], "rows": r[3], "stats": json.loads(r[4] or "{}")}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="stampede edge-features", description="per-coin-minute feature table (coin_minutes) for the runner model")
    ap.add_argument("--db", required=True)
    ap.add_argument("--window", type=int, default=1800, help="sequence pairing window (seconds) to read rotation inflow from")
    ap.add_argument("--features-db", default=None, help="write coin_minutes into this sqlite instead of --db")
    ap.add_argument("--limit-coins", type=int, default=None, help="debug: stop after N coins")
    a = ap.parse_args(argv)
    stats = build(a.db, window_s=a.window, features_db=a.features_db, limit_coins=a.limit_coins)
    print(json.dumps(stats, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
