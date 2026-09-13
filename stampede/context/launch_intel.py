"""Launch intelligence: who launched a PONS v2 coin and how, measured from indexed data only.

    .venv/bin/python -m stampede launch-intel --db /tmp/bf-test.sqlite --out docs/RESEARCH-LAUNCHES.md

Per launch (table `launch_intel`, created here): the dev buy (launch transaction or deployer buys in the first
5 s) as a share of supply, the creator tax, the declared snipe-tax-exempt bundle (behavioural proxy: buyers inside
the 3-second snipe window who paid no snipe tax; exact count when SnipeTaxExempted logs are indexed), the snipers
who did pay the tax, the deployer's track record over the previous 30 days, a launch-farm flag, declared socials,
and the timestamp from which an entry is no longer snipe-taxed.

Everything is computed from `launches`, `graduations`, `trades`, `blocks`, `logs` and `launch_meta`; the batch path
makes no RPC call. Three passes: deployer history (every launch in the store, one streaming query), trade features
(one indexed range query per launch inside the trade range, bounded to the observation horizon), farm grouping.
The report then measures the runner rate (>= 2x the first untaxed entry within the horizon, or graduation) by
feature bucket, one row per coin, in the style of docs/RESEARCH-RUNNERS.md.
"""
from __future__ import annotations

import argparse
import bisect
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import chain
from ..store import Store

SCHEMA = """
CREATE TABLE IF NOT EXISTS launch_intel (
  token TEXT PRIMARY KEY, deployer TEXT, pair_token TEXT, pair_symbol TEXT, launch_block INTEGER, launch_ts INTEGER,
  launch_ts_exact INTEGER, launch_tx TEXT, observed INTEGER,
  dev_buy_share REAL, dev_buy_quote REAL, dev_buy_quote_raw TEXT, dev_buy_in_launch_tx INTEGER, creator_tax_bps INTEGER,
  bundle_n INTEGER, bundle_share REAL, exempt_declared_n INTEGER, first_buyers_5s INTEGER, taxed_snipers_3s INTEGER,
  snipe_tax_paid_quote REAL, trades_h INTEGER,
  deployer_prior_launches_30d INTEGER, deployer_prior_graduations_30d INTEGER, deployer_graduation_rate REAL,
  deployer_first_launch_block INTEGER, launch_farm INTEGER, farm_group_n INTEGER, socials_present INTEGER,
  snipe_window_s INTEGER, snipe_tax_zero_ts INTEGER, computed_at REAL);
CREATE INDEX IF NOT EXISTS launch_intel_deployer ON launch_intel(deployer);
"""

SNIPE_WINDOW_S = chain.SNIPE_TAX_SECONDS  # 3 s on every curve launched so far (each curve snapshots its own value)
DEV_BUY_S = 5  # deployer buys this close to the launch count as the dev buy
FIRST_BUYERS_S = 5
FARM_WINDOW_S = 30 * 60
FARM_MIN_LAUNCHES = 3
YOUNG_DEPLOYER_S = 24 * 3600
DEPLOYER_HISTORY_S = 30 * 86400
TAX_ROUND_BPS = 5
MAX_CREATOR_TAX_BPS = 1000
BLOCKS_PER_S_DEFAULT = 10.0  # Robinhood Chain: ~100 ms blocks (measured per store when the range allows)
API_FIELDS = (
    "observed", "dev_buy_share", "dev_buy_quote", "pair_symbol", "dev_buy_in_launch_tx", "creator_tax_bps", "bundle_n", "bundle_share",
    "exempt_declared_n", "first_buyers_5s", "taxed_snipers_3s", "snipe_tax_paid_quote", "deployer_prior_launches_30d",
    "deployer_prior_graduations_30d", "deployer_graduation_rate", "launch_farm", "farm_group_n", "socials_present", "snipe_window_s", "snipe_tax_zero_ts",
)


def ensure_table(db) -> None:
    db.executescript(SCHEMA)


# ---- protocol arithmetic ---------------------------------------------------------------------------------------------
def snipe_tax_bps(elapsed_s: int, start_bps: int = chain.SNIPE_TAX_START_BPS, window_s: int = SNIPE_WINDOW_S, fee_bps: int = chain.CURVE_FEE_BPS, creator_tax_bps: int = 0) -> int:
    """currentSnipeTaxBps = startBps >> (elapsed * 14 // window), capped so fee + tax + snipe stay under 100% - 1%."""
    if elapsed_s < 0:
        elapsed_s = 0
    shift = elapsed_s * 14 // max(1, window_s)
    bps = start_bps >> shift if shift < 64 else 0
    cap = 10_000 - fee_bps - creator_tax_bps - 100
    return max(0, min(bps, cap))


def creator_tax_bps_of(side: str, quote_raw: int, fee_raw: int, tax_raw: int) -> int | None:
    """Creator tax rate from one curve trade: tax / quoteIn on buys, tax / gross quote (quoteOut + fee + tax) on sells.
    Rounded to 5 bps; None when the ratio is not a plausible rate (bad row, two-sided tx)."""
    base = quote_raw if side == "buy" else quote_raw + fee_raw + tax_raw
    if base <= 0 or tax_raw < 0:
        return None
    bps = tax_raw * 10_000 / base
    r = int(round(bps / TAX_ROUND_BPS) * TAX_ROUND_BPS)
    if r > MAX_CREATOR_TAX_BPS or abs(bps - r) > TAX_ROUND_BPS:
        return None
    return r


def price_of(quote_raw: int, token_raw: int) -> float | None:
    return quote_raw / token_raw if quote_raw > 0 and token_raw > 0 else None


def _median(xs: list[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    return s[len(s) // 2]


def _quantile(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    return s[min(len(s) - 1, int(q * len(s)))]


def _int(v: Any) -> int:
    try:
        return int(v) if v is not None else 0
    except (TypeError, ValueError):
        return int(float(v))


# ---- block clock -----------------------------------------------------------------------------------------------------
class BlockClock:
    """block -> (timestamp, exact) from the `blocks` table, two primary-key lookups per block, linear in between.
    Beyond 600 blocks (~1 min) from the nearest exact block the time is unknown."""

    MAX_GAP = 600

    def __init__(self, db):
        self.db = db
        self.cache: dict[int, tuple[int | None, int]] = {}

    def __call__(self, block: int | None) -> tuple[int | None, int]:
        if block is None:
            return None, 0
        hit = self.cache.get(block)
        if hit is not None:
            return hit
        if len(self.cache) > 300_000:
            self.cache.clear()
        q = self.db.execute
        lo = q("SELECT number, timestamp FROM blocks WHERE number<=? AND exact=1 AND timestamp>0 ORDER BY number DESC LIMIT 1", (block,)).fetchone()
        if lo and lo[0] == block:
            out: tuple[int | None, int] = (int(lo[1]), 1)
        else:
            hi = q("SELECT number, timestamp FROM blocks WHERE number>=? AND exact=1 AND timestamp>0 ORDER BY number ASC LIMIT 1", (block,)).fetchone()
            if lo and hi and hi[0] - lo[0] <= self.MAX_GAP:
                out = (round(lo[1] + (hi[1] - lo[1]) * (block - lo[0]) / (hi[0] - lo[0])), 0)
            elif lo and block - lo[0] <= self.MAX_GAP:
                out = (round(lo[1] + (block - lo[0]) / BLOCKS_PER_S_DEFAULT), 0)
            elif hi and hi[0] - block <= self.MAX_GAP:
                out = (round(hi[1] - (hi[0] - block) / BLOCKS_PER_S_DEFAULT), 0)
            else:
                out = (None, 0)
        self.cache[block] = out
        return out


def trade_block_range(store: Store) -> tuple[int, int] | None:
    """Blocks the trades cover: the backfill / ingest sample bounds, else measured (full scan on big stores)."""
    lo, hi = store.get_meta("sample_from_block"), store.get_meta("sample_to_block")
    if lo is not None and hi is not None:
        return int(lo), int(hi)
    r = store.db.execute("SELECT MIN(block), MAX(block) FROM trades").fetchone()
    return (int(r[0]), int(r[1])) if r and r[0] is not None else None


def pair_symbols(store: Store) -> dict[str, str]:
    out = {a: q["symbol"] for a, q in store.quotes().items() if q.get("symbol")}
    out.setdefault(chain.NATIVE, "ETH")
    return out


def pair_decimals(store: Store) -> dict[str, int]:
    out = {a: int(q["decimals"]) for a, q in store.quotes().items() if q.get("decimals") is not None}
    out.setdefault(chain.NATIVE, 18)
    return out


# ---- pass 1: deployer history for every launch ----------------------------------------------------------------------
def deployer_pass(store: Store, clock: BlockClock, lo: int | None, hi: int | None, rate: float, now: float, progress=None) -> dict[str, int]:
    """Every launch in the store: prior launches / graduations of its deployer in the previous 30 days (by block
    distance), the deployer's first launch, the resolved launch time, and whether the launch is inside the trade range."""
    q = store.db.execute
    ensure_table(store.db)
    W = int(DEPLOYER_HISTORY_S * rate)
    grad_block = {r[0]: int(r[1]) for r in q("SELECT token, block FROM graduations WHERE block IS NOT NULL")}
    syms = pair_symbols(store)
    rows: list[tuple] = []
    n = obs = 0

    def flush():
        store.db.executemany(
            "INSERT OR REPLACE INTO launch_intel(token,deployer,pair_token,pair_symbol,launch_block,launch_ts,launch_ts_exact,launch_tx,observed,"
            "deployer_prior_launches_30d,deployer_prior_graduations_30d,deployer_graduation_rate,deployer_first_launch_block,snipe_window_s,snipe_tax_zero_ts,computed_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        rows.clear()

    def emit(group: list[tuple[int, str, int | None, str | None, str | None]]):
        nonlocal n, obs
        group.sort()
        blocks = [g[0] for g in group]
        grads = [(grad_block[g[1]], g[0]) for g in group if g[1] in grad_block]  # (graduation block, launch block)
        first = blocks[0]
        for i, (block, token, ts0, tx, pair) in enumerate(group):
            j = bisect.bisect_left(blocks, block - W)
            prior = max(0, i - j)
            prior_grads = sum(1 for gb, lb in grads if gb < block and block - W <= lb < block) if prior else 0
            ts, exact = (int(ts0), 1) if ts0 else ((clock(block)) if (lo is not None and lo <= block <= hi) else (None, 0))
            observed = int(lo is not None and lo <= block <= hi and ts is not None)
            obs += observed
            rows.append((token, dep, pair, syms.get(pair or "", None) or (f"{pair[:6]}…{pair[-4:]}" if pair else None), block, ts, exact, tx, observed, prior, prior_grads, (prior_grads / prior) if prior else None, first, SNIPE_WINDOW_S, (ts + SNIPE_WINDOW_S) if ts is not None else None, now))
            n += 1
        if len(rows) >= 5000:
            flush()
            if progress and n % 100_000 < 5000:
                progress(f"  deployer pass: {n:,} launches")

    dep: str | None = None
    group: list[tuple[int, str, int | None, str | None, str | None]] = []
    # Read the launches through a separate read-only connection: iterating a cursor on the writing connection while
    # flush() inserts fails with SQLITE_BUSY_SNAPSHOT as soon as another process (the live engine) commits in between.
    import sqlite3 as _sqlite3

    reader = _sqlite3.connect(f"file:{store.path}?mode=ro", uri=True) if str(store.path) != ":memory:" else None
    sel = "SELECT token, deployer, block, ts, tx_hash, pair_token FROM launches WHERE block IS NOT NULL ORDER BY deployer, block, token"
    launches_iter = reader.execute(sel) if reader is not None else store.db.execute(sel).fetchall()
    for token, deployer, block, ts0, tx, pair in launches_iter:
        if deployer != dep and group:
            emit(group)
            group = []
        dep = deployer
        group.append((int(block), token, ts0, tx, pair))
    if group:
        emit(group)
    flush()
    store.commit()
    if reader is not None:
        reader.close()
    return {"launches": n, "observed": obs, "history_window_blocks": W}


# ---- pass 2: trade features per observed launch ---------------------------------------------------------------------
def launch_features(rows: list[tuple], deployer: str, launch_ts: int, launch_block: int, launch_tx: str | None, horizon_s: int, window_s: int = SNIPE_WINDOW_S) -> dict[str, Any]:
    """Features of one launch from its early trades. rows: (ts, block, tx_hash, wallet, side, token_amount, quote_amount,
    fee_raw, tax_raw, snipe_raw, venue) ordered by ts. Also returns what the backtest needs (entry, price path)."""
    dev_tokens = dev_quote = 0
    dev_in_tx = 0
    bundle: dict[str, int] = {}
    first_buyers: set[str] = set()
    taxed: dict[str, int] = {}
    snipe_paid = 0
    tax_bps: int | None = None
    tax_seen_zero = False
    taxed_buys: list[tuple[int, int, int]] = []  # (elapsed, snipe_raw, quote_raw)
    p0: float | None = None  # price paid in the launch block (dev buy or first trade at elapsed 0)
    entry_ts: int | None = None
    entry_price: float | None = None
    per_minute: dict[int, list[float]] = defaultdict(list)
    n_trades = 0
    snipe_known = False  # stores from before the fee columns carry NULLs: bundle / sniper counts are then unknown, not 0
    end_ts = launch_ts + horizon_s
    for ts, block, tx, wallet, side, tok_s, q_s, fee_s, tax_s, snipe_s, venue in rows:
        ts = int(ts)
        if ts > end_ts:
            break
        n_trades += 1
        in_launch_tx = bool(launch_tx) and tx == launch_tx
        elapsed = 0 if (in_launch_tx or block == launch_block) else max(0, ts - launch_ts)
        tok, quote = _int(tok_s), _int(q_s)
        fee = _int(fee_s)
        tax = None if tax_s is None else _int(tax_s)
        snipe = None if snipe_s is None else _int(snipe_s)
        snipe_known = snipe_known or snipe is not None
        price = price_of(quote, tok)
        if venue == "curve" and tax_bps is None and quote > 0 and tax is not None:
            if tax > 0:
                tax_bps = creator_tax_bps_of(side, quote, fee, tax)
            else:
                tax_seen_zero = True
        if side == "buy":
            if in_launch_tx or (wallet == deployer and elapsed <= DEV_BUY_S):
                dev_tokens += tok
                dev_quote += quote
                dev_in_tx = dev_in_tx or int(in_launch_tx)
            if elapsed <= FIRST_BUYERS_S:
                first_buyers.add(wallet)
            if snipe is not None and snipe > 0:
                taxed[wallet] = taxed.get(wallet, 0) + snipe
                snipe_paid += snipe
                taxed_buys.append((elapsed, snipe, quote))
            elif snipe == 0 and elapsed < window_s and wallet != deployer and not in_launch_tx:
                bundle[wallet] = bundle.get(wallet, 0) + tok
        if price is None:
            continue
        if elapsed == 0 and p0 is None:
            p0 = price
        if entry_ts is None and elapsed >= window_s:
            entry_ts, entry_price = ts, price
        elif entry_ts is not None and ts > entry_ts:
            per_minute[ts // 60].append(price)
    if tax_bps is None and tax_seen_zero:
        tax_bps = 0
    mins = sorted(per_minute)
    medians = [(_median(per_minute[m]) or 0.0) for m in mins]
    max_after = max(medians) if medians else None
    end_price = medians[-1] if medians else None
    supply = chain.CURVE_SUPPLY_WEI
    snipe_ok = snipe_known or n_trades == 0  # no trades at all: an empty window is a real 0
    return {
        "dev_buy_share": dev_tokens / supply,
        "dev_buy_quote_raw": dev_quote,
        "dev_buy_in_launch_tx": dev_in_tx,
        "creator_tax_bps": tax_bps,
        "bundle_n": len(bundle) if snipe_ok else None,
        "bundle_share": (sum(bundle.values()) / supply) if snipe_ok else None,
        "first_buyers_5s": len(first_buyers),
        "taxed_snipers_3s": len(taxed) if snipe_ok else None,
        "snipe_tax_paid_raw": snipe_paid if snipe_ok else None,
        "taxed_buys": taxed_buys,
        "trades_h": n_trades,
        "p0": p0,
        "entry_ts": entry_ts,
        "entry_price": entry_price,
        "max_after": max_after,
        "end_price": end_price,
    }


def trades_pass(store: Store, clock: BlockClock, hi_ts: int | None, horizon_s: int, gain: float, socials: dict[str, int], exempt_n: dict[str, int], progress=None, limit: int | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """One indexed range query per observed launch; writes the trade-derived columns, returns the backtest records."""
    q = store.db.execute
    dec = pair_decimals(store)
    grad_block = {r[0]: int(r[1]) for r in q("SELECT token, block FROM graduations WHERE block IS NOT NULL")}
    launches = q("SELECT token, deployer, launch_block, launch_ts, launch_tx, pair_token FROM launch_intel WHERE observed=1 ORDER BY launch_block").fetchall()
    if limit:
        launches = launches[:limit]
    recs: list[dict[str, Any]] = []
    upd: list[tuple] = []
    rows_read = 0
    t0 = time.time()
    for i, (tok, dep, lb, lts, ltx, pair) in enumerate(launches, 1):
        lts = int(lts)
        rows = q(
            "SELECT ts, block, tx_hash, wallet, side, token_amount, quote_amount, fee_raw, tax_raw, snipe_raw, venue FROM trades WHERE token=? AND ts>=? AND ts<=? ORDER BY ts, id",
            (tok, lts - 2, lts + horizon_s),
        ).fetchall()
        rows_read += len(rows)
        f = launch_features(rows, dep, lts, int(lb), ltx, horizon_s)
        d = dec.get(pair or "")  # None: pair token decimals unknown in this store -> quote amounts stay NULL (shares are exact anyway)
        soc = socials.get(tok)
        ex = exempt_n.get(ltx or "")
        in_units = (lambda raw: (raw / 10**d) if raw is not None else None) if d is not None else (lambda raw: None)
        upd.append((f["dev_buy_share"], in_units(f["dev_buy_quote_raw"]), str(f["dev_buy_quote_raw"]), f["dev_buy_in_launch_tx"], f["creator_tax_bps"], f["bundle_n"], f["bundle_share"], ex, f["first_buyers_5s"], f["taxed_snipers_3s"], in_units(f["snipe_tax_paid_raw"]), f["trades_h"], soc, tok))
        gb = grad_block.get(tok)
        gts = clock(gb)[0] if gb is not None else None
        graduated = bool(gts is not None and lts < gts <= lts + horizon_s)
        complete = hi_ts is not None and lts + horizon_s <= hi_ts
        max_gain = end_gain = None
        if f["entry_price"]:
            max_gain = (f["max_after"] / f["entry_price"] - 1) if f["max_after"] else 0.0
            end_gain = (f["end_price"] / f["entry_price"] - 1) if f["end_price"] else None
        recs.append({
            "tok": tok, "launch_ts": lts, "pair": pair, "dec": d, "dev_buy_share": f["dev_buy_share"], "dev_buy_quote": in_units(f["dev_buy_quote_raw"]), "bundle_n": f["bundle_n"],
            "creator_tax_bps": f["creator_tax_bps"], "taxed_snipers_3s": f["taxed_snipers_3s"], "first_buyers_5s": f["first_buyers_5s"], "socials": soc, "trades_h": f["trades_h"],
            "taxed_buys": f["taxed_buys"], "p0": f["p0"], "entry_ts": f["entry_ts"], "entry_price": f["entry_price"], "max_gain": max_gain, "end_gain": end_gain,
            "graduated": graduated, "complete": complete, "runner": (bool(max_gain is not None and max_gain >= gain) or graduated) if f["entry_price"] else None,
        })
        if len(upd) >= 1000:
            _write_features(store, upd)
            if progress:
                el = time.time() - t0
                progress(f"  trades pass: {i:,}/{len(launches):,} launches, {rows_read:,} trade rows, {el:.0f} s")
    _write_features(store, upd)
    store.commit()
    # deployer history joins the records after the fact (cheap: one query)
    dep_info = {r[0]: r[1:] for r in q("SELECT token, deployer_prior_launches_30d, deployer_graduation_rate, deployer_first_launch_block, launch_block FROM launch_intel WHERE observed=1")}
    for r in recs:
        di = dep_info.get(r["tok"])
        r["dep_prior"], r["dep_rate"] = (di[0], di[1]) if di else (None, None)
    return recs, {"observed_launches": len(launches), "trade_rows_read": rows_read, "seconds": round(time.time() - t0, 1)}


def _write_features(store: Store, upd: list[tuple]) -> None:
    if not upd:
        return
    store.db.executemany(
        "UPDATE launch_intel SET dev_buy_share=?, dev_buy_quote=?, dev_buy_quote_raw=?, dev_buy_in_launch_tx=?, creator_tax_bps=?, bundle_n=?, bundle_share=?, exempt_declared_n=?, "
        "first_buyers_5s=?, taxed_snipers_3s=?, snipe_tax_paid_quote=?, trades_h=?, socials_present=? WHERE token=?",
        upd,
    )
    upd.clear()


# ---- pass 3: launch farms ------------------------------------------------------------------------------------------------
def farm_pass(store: Store, rate: float) -> dict[str, int]:
    """launch_farm = 1 when a launch comes from a deployer whose first launch is < 24 h old AND at least 3 launches
    (this one included) within +-30 min share its fingerprint (pair token, creator tax, exact dev-buy quote amount)
    and also come from < 24 h-old deployers. Launches without a dev buy have no fingerprint and are never flagged
    by this rule (their deployer history still is)."""
    q = store.db.execute
    young = int(YOUNG_DEPLOYER_S * rate)
    win = int(FARM_WINDOW_S * rate)
    q("UPDATE launch_intel SET launch_farm=0, farm_group_n=NULL WHERE observed=1")
    groups: dict[tuple, list[tuple[int, str]]] = defaultdict(list)
    for tok, block, first, pair, tax, dq in q("SELECT token, launch_block, deployer_first_launch_block, pair_token, creator_tax_bps, dev_buy_quote_raw FROM launch_intel WHERE observed=1 AND creator_tax_bps IS NOT NULL AND dev_buy_quote_raw IS NOT NULL AND dev_buy_quote_raw NOT IN ('0','')"):
        if first is None or block - first >= young:
            continue
        groups[(pair, tax, dq)].append((int(block), tok))
    flagged: list[tuple[int, int, str]] = []
    n_farm = 0
    for members in groups.values():
        members.sort()
        blocks = [m[0] for m in members]
        for b, tok in members:
            n = bisect.bisect_right(blocks, b + win) - bisect.bisect_left(blocks, b - win)
            farm = int(n >= FARM_MIN_LAUNCHES)
            n_farm += farm
            flagged.append((farm, n, tok))
    store.db.executemany("UPDATE launch_intel SET launch_farm=?, farm_group_n=? WHERE token=?", flagged)
    store.commit()
    return {"fingerprinted": len(flagged), "launch_farm": n_farm, "young_blocks": young, "window_blocks": win}


# ---- orchestration -------------------------------------------------------------------------------------------------------
def load_socials(store: Store) -> dict[str, int]:
    """socials_present from the cached launch calldata parse (launch_meta): 1 / 0; tokens without a row stay unknown."""
    return {r[0]: int(bool(r[1] or r[2] or r[3])) for r in store.db.execute("SELECT token, twitter, telegram, website FROM launch_meta")}


def exempt_counts(store: Store) -> dict[str, int]:
    """launch tx -> distinct SnipeTaxExempted accounts other than the deployer (only when the logs are indexed)."""
    dep_by_tx = {r[0]: r[1] for r in store.db.execute("SELECT tx_hash, deployer FROM launches WHERE tx_hash IN (SELECT DISTINCT tx_hash FROM logs WHERE kind='snipe_tax_exempted')")}
    if not dep_by_tx:
        return {}
    acc: dict[str, set[str]] = defaultdict(set)
    for tx, t1 in store.db.execute("SELECT tx_hash, topic1 FROM logs WHERE kind='snipe_tax_exempted' AND topic1 IS NOT NULL"):
        a = chain.addr_from_topic(t1)
        if a != dep_by_tx.get(tx):
            acc[tx].add(a)
    return {tx: len(acc.get(tx, ())) for tx in dep_by_tx}


def fetch_socials(store: Store, rpc, max_n: int, progress=None) -> int:
    """Optional, bounded: parse declared socials from the launch calldata of observed launches without a cached row."""
    from .pons import launch_socials

    todo = [r[0] for r in store.db.execute("SELECT token FROM launch_intel WHERE observed=1 AND token NOT IN (SELECT token FROM launch_meta) ORDER BY launch_block LIMIT ?", (max_n,))]
    n = 0
    for tok in todo:
        if launch_socials(store, rpc, tok) is not None:
            n += 1
    if progress:
        progress(f"  socials: fetched {n} of {len(todo)} launch transactions")
    return n


def compute(store: Store, horizon_s: int = 3600, gain: float = 1.0, socials_max: int = 0, rpc=None, progress=None, limit: int | None = None) -> dict[str, Any]:
    t0 = time.time()
    ensure_table(store.db)
    rng = trade_block_range(store)
    clock = BlockClock(store.db)
    lo, hi = rng if rng else (None, None)
    lo_ts, hi_ts = (clock(lo)[0], clock(hi)[0]) if rng else (None, None)
    rate = BLOCKS_PER_S_DEFAULT
    if lo_ts and hi_ts and hi_ts - lo_ts >= 60 and hi > lo:
        rate = (hi - lo) / (hi_ts - lo_ts)
    now = time.time()
    st: dict[str, Any] = {"trade_blocks": [lo, hi], "trade_ts": [lo_ts, hi_ts], "blocks_per_s": round(rate, 3), "horizon_s": horizon_s, "gain": gain}
    st["deployer_pass"] = deployer_pass(store, clock, lo, hi, rate, now, progress)
    st["deployer_pass"]["seconds"] = round(time.time() - t0, 1)
    if progress:
        progress(f"deployer pass: {st['deployer_pass']}")
    if socials_max and rpc is not None:
        st["socials_fetched"] = fetch_socials(store, rpc, socials_max, progress)
    socials = load_socials(store)
    exempt = exempt_counts(store)
    st["exempt_logs_launches"] = len(exempt)
    t1 = time.time()
    recs, tp = trades_pass(store, clock, hi_ts, horizon_s, gain, socials, exempt, progress, limit)
    st["trades_pass"] = tp
    if progress:
        progress(f"trades pass: {tp}")
    st["farm_pass"] = farm_pass(store, rate)
    farm = {r[0]: r[1] for r in store.db.execute("SELECT token, launch_farm FROM launch_intel WHERE observed=1")}
    for r in recs:
        r["farm"] = farm.get(r["tok"])
    st["farm_pass"]["seconds"] = round(time.time() - t1 - tp["seconds"], 1)
    st["seconds"] = round(time.time() - t0, 1)
    st["records"] = recs
    return st


# ---- report ---------------------------------------------------------------------------------------------------------------
def pct(x: float | None, d: int = 1) -> str:
    return "—" if x is None else f"{100 * x:.{d}f}%"


def report(store: Store, st: dict[str, Any], db_label: str) -> str:
    recs: list[dict[str, Any]] = st["records"]
    H = st["horizon_s"]
    gain = st["gain"]
    q = store.db.execute
    n_launches = q("SELECT COUNT(*) FROM launch_intel").fetchone()[0]
    n_obs = q("SELECT COUNT(*) FROM launch_intel WHERE observed=1").fetchone()[0]
    n_traded = q("SELECT COUNT(*) FROM launch_intel WHERE trades_h>0").fetchone()[0]
    with_entry = [r for r in recs if r["entry_price"]]
    sample = [r for r in with_entry if r["complete"]]
    base_rate = (sum(r["runner"] for r in sample) / len(sample)) if sample else None
    lo_ts, hi_ts = st["trade_ts"]
    span_h = ((hi_ts - lo_ts) / 3600) if lo_ts and hi_ts else 0.0

    def fmt_ts(ts: int | None, f: str = "%Y-%m-%d %H:%M") -> str:
        return datetime.fromtimestamp(ts, timezone.utc).strftime(f) if ts else "?"

    def table(name: str, groups: list[tuple[str, list[dict[str, Any]]]]) -> list[str]:
        lines = [f"### {name}", "", "| bucket | coins | runner rate | lift vs base | median max gain | median gain at +H | graduated |", "|---|---|---|---|---|---|---|"]
        for label, rows in groups:
            if not rows:
                lines.append(f"| {label} | 0 | — | — | — | — | — |")
                continue
            rate = sum(r["runner"] for r in rows) / len(rows)
            ends = [r["end_gain"] for r in rows if r.get("end_gain") is not None]
            lift = f"{rate / base_rate:.2f}×" if base_rate else "—"
            lines.append(f"| {label} | {len(rows)} | {pct(rate)} | {lift} | {pct(_median([r['max_gain'] or 0.0 for r in rows]))} | {pct(_median(ends))} | {sum(r['graduated'] for r in rows)} |")
        lines.append("")
        return lines

    def grp(bins):
        return [(lbl, [r for r in sample if f(r)]) for lbl, f in bins]

    dev_bins = [("no dev buy", lambda r: r["dev_buy_share"] == 0), ("0–1%", lambda r: 0 < r["dev_buy_share"] <= 0.01), ("1–3%", lambda r: 0.01 < r["dev_buy_share"] <= 0.03), ("3–5%", lambda r: 0.03 < r["dev_buy_share"] <= 0.05), ("5–10%", lambda r: 0.05 < r["dev_buy_share"] <= 0.10), ("> 10%", lambda r: r["dev_buy_share"] > 0.10)]
    bundle_bins = [("unknown (store without snipe-tax columns)", lambda r: r["bundle_n"] is None), ("0", lambda r: r["bundle_n"] == 0), ("1–2", lambda r: r["bundle_n"] is not None and 1 <= r["bundle_n"] <= 2), ("3–5", lambda r: r["bundle_n"] is not None and 3 <= r["bundle_n"] <= 5), ("6+", lambda r: r["bundle_n"] is not None and r["bundle_n"] >= 6)]
    tax_bins = [("unknown (no curve trade with a readable ratio)", lambda r: r["creator_tax_bps"] is None), ("0 bps", lambda r: r["creator_tax_bps"] == 0), ("5–100 bps", lambda r: r["creator_tax_bps"] is not None and 0 < r["creator_tax_bps"] <= 100), ("105–300 bps", lambda r: r["creator_tax_bps"] is not None and 100 < r["creator_tax_bps"] <= 300), ("305–600 bps", lambda r: r["creator_tax_bps"] is not None and 300 < r["creator_tax_bps"] <= 600), ("605–1000 bps", lambda r: r["creator_tax_bps"] is not None and r["creator_tax_bps"] > 600)]
    dep_bins = [("no prior launch in 30 d", lambda r: not r["dep_prior"]), ("prior launches, none graduated", lambda r: r["dep_prior"] and r["dep_rate"] == 0), ("0–10% graduated", lambda r: r["dep_prior"] and r["dep_rate"] is not None and 0 < r["dep_rate"] <= 0.10), ("10–30%", lambda r: r["dep_prior"] and r["dep_rate"] is not None and 0.10 < r["dep_rate"] <= 0.30), ("> 30%", lambda r: r["dep_prior"] and r["dep_rate"] is not None and r["dep_rate"] > 0.30)]
    prior_bins = [("0", lambda r: not r["dep_prior"]), ("1–5", lambda r: r["dep_prior"] and r["dep_prior"] <= 5), ("6–50", lambda r: r["dep_prior"] and 5 < r["dep_prior"] <= 50), ("51+", lambda r: r["dep_prior"] and r["dep_prior"] > 50)]
    farm_bins = [("launch farm: yes", lambda r: r.get("farm") == 1), ("launch farm: no", lambda r: r.get("farm") == 0)]
    soc_bins = [("socials declared: yes", lambda r: r["socials"] == 1), ("socials declared: no", lambda r: r["socials"] == 0), ("unknown (launch calldata not parsed)", lambda r: r["socials"] is None)]
    sniper_bins = [("unknown", lambda r: r["taxed_snipers_3s"] is None), ("0 taxed snipers", lambda r: r["taxed_snipers_3s"] == 0), ("1–2", lambda r: r["taxed_snipers_3s"] is not None and 1 <= r["taxed_snipers_3s"] <= 2), ("3+", lambda r: r["taxed_snipers_3s"] is not None and r["taxed_snipers_3s"] >= 3)]
    clean = [r for r in sample if r["bundle_n"] is not None and r["bundle_n"] <= 2 and r["dev_buy_share"] <= 0.05 and r.get("farm") != 1]
    known = [r for r in sample if r["bundle_n"] is not None]
    combos = [("clean launch (bundle ≤ 2, dev buy ≤ 5%, not a farm)", clean), ("… & deployer graduated before (rate > 0)", [r for r in clean if r["dep_rate"]]), ("… & creator tax ≤ 300 bps", [r for r in clean if r["creator_tax_bps"] is not None and r["creator_tax_bps"] <= 300]), ("bundle ≥ 3 or dev buy > 5% or farm", [r for r in known if r not in clean]), ("bundle unknown (store without snipe-tax columns)", [r for r in sample if r["bundle_n"] is None])]

    # snipe-tax cost table
    by_el: dict[int, dict[str, Any]] = {e: {"buys": 0, "bps": [], "eth": 0.0} for e in range(SNIPE_WINDOW_S)}
    for r in recs:
        for el, snipe, quote in r["taxed_buys"]:
            b = by_el.setdefault(min(el, SNIPE_WINDOW_S - 1), {"buys": 0, "bps": [], "eth": 0.0})
            b["buys"] += 1
            if quote > 0:
                b["bps"].append(snipe * 10_000 / quote)
            if r["pair"] == chain.NATIVE:
                b["eth"] += snipe / 1e18
    ratios = [r["entry_price"] / r["p0"] for r in with_entry if r["p0"] and r["entry_price"]]
    wait_s = [r["entry_ts"] - r["launch_ts"] for r in with_entry if r["entry_ts"] is not None]

    # feature distribution over observed launches (not only those with an entry)
    obs = q("SELECT COUNT(*), SUM(dev_buy_share>0), SUM(bundle_n>0), SUM(taxed_snipers_3s>0), SUM(launch_farm=1), SUM(trades_h>0), SUM(creator_tax_bps IS NOT NULL), SUM(socials_present IS NOT NULL), SUM(socials_present=1), SUM(exempt_declared_n IS NOT NULL) FROM launch_intel WHERE observed=1").fetchone()
    dev_shares = [r[0] for r in q("SELECT dev_buy_share FROM launch_intel WHERE observed=1 AND dev_buy_share>0")]
    tax_dist = q("SELECT creator_tax_bps, COUNT(*) FROM launch_intel WHERE observed=1 AND creator_tax_bps IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 6").fetchall()
    dep_prior = [r[0] for r in q("SELECT deployer_prior_launches_30d FROM launch_intel WHERE observed=1")]
    young = q("SELECT SUM(launch_block - deployer_first_launch_block < ?) FROM launch_intel WHERE observed=1", (st["farm_pass"]["young_blocks"],)).fetchone()[0]

    md: list[str] = []
    md.append("# Launch quality and runners: what the launch itself says about a PONS coin")
    md.append("")
    md.append(f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} by `stampede launch-intel` from `{db_label}`.")
    md.append("")
    md.append("## Setup")
    md.append("")
    md.append(f"- Data: {q('SELECT COUNT(*) FROM trades').fetchone()[0]:,} trades over {span_h:.1f} h ({fmt_ts(lo_ts)}–{fmt_ts(hi_ts, '%H:%M')} UTC, blocks {st['trade_blocks'][0]:,}–{st['trade_blocks'][1]:,}, measured {st['blocks_per_s']:.2f} blocks/s); {n_launches:,} launches in the store (lifecycle logs reach 30 days back so deployer history is complete for the trade range); {q('SELECT COUNT(*) FROM graduations').fetchone()[0]:,} graduations.")
    md.append(f"- Observed launches: {n_obs:,} launched inside the trade range with a resolvable launch time; {n_traded:,} of them had at least one trade within the horizon. Launches from before the range have deployer history only; their dev buy / bundle / snipe columns stay NULL (unknown, not 0).")
    md.append(f"- Entry: the first trade at ≥ {SNIPE_WINDOW_S} s after the launch, i.e. the first moment a buy is not snipe-taxed. {len(with_entry):,} observed launches had such a trade; {len(sample):,} of them also have the full {H // 60}-min horizon inside the data and form the sample below.")
    md.append(f"- Outcome 'runner': the minute-median price reaches ≥ {1 + gain:.0f}× the entry price within {H // 60} min of the launch, or the coin graduates within that window. Base rate over the sample: **{pct(base_rate, 2)}** of {len(sample):,} coins.")
    md.append("- Features are per launch, one row per coin, all knowable at launch time except the bundle proxy (needs the 3-second window to close) — nothing looks past the entry.")
    md.append("")
    md.append("## Definitions")
    md.append("")
    md.append(f"- `dev_buy_share`: tokens bought in the launch transaction (`launchAndBuy`) or by the deployer within {DEV_BUY_S} s, over the 1e9 supply. `dev_buy_quote` is what was paid, in pair-token units — NULL when the store does not know the pair token's decimals (`quotes`; ETH always does, `--resolve-quotes` fills the rest with two `eth_call`s per pair token, not per launch).")
    md.append("- `creator_tax_bps`: creator tax read from the first curve trade with a readable ratio (tax / quoteIn on buys, tax / (quoteOut + fee + tax) on sells), rounded to 5 bps; 0 when the first curve trades carry no tax.")
    md.append(f"- `bundle_n` / `bundle_share`: distinct wallets other than the deployer that bought inside the {SNIPE_WINDOW_S}-second snipe window and paid **no** snipe tax — the behavioural proxy for the declared exemption list (`SnipeTaxExempted`, up to 32 wallets). `exempt_declared_n` is the exact count from indexed `SnipeTaxExempted` logs when a store has them (research stores built before 2026-09-12 do not; the ingest paths now keep them).")
    md.append(f"- `taxed_snipers_3s`: distinct wallets that bought inside the window and paid the snipe tax (`SnipeTaxCharged`, part of `CurveBuy.fee`); `snipe_tax_paid_quote` is the sum they paid. `first_buyers_5s`: distinct buyers in the first {FIRST_BUYERS_S} s, deployer included.")
    md.append(f"- `deployer_prior_launches_30d` / `deployer_prior_graduations_30d` / `deployer_graduation_rate`: launches by the same deployer in the {DEPLOYER_HISTORY_S // 86400} days before this one (block distance at the measured block rate, {st['deployer_pass']['history_window_blocks']:,} blocks), how many of those had graduated before this launch, and the ratio (NULL without prior launches). 'Deployer' is the `TokenLaunched.deployer` topic — the caller of the factory, which can be a contract (e.g. Multicall3).")
    md.append(f"- `launch_farm`: 1 when the deployer's first launch is < {YOUNG_DEPLOYER_S // 3600} h old **and** at least {FARM_MIN_LAUNCHES} launches within ±{FARM_WINDOW_S // 60} min (this one included) share the fingerprint (pair token, `creator_tax_bps`, exact dev-buy quote amount) **and** all come from < {YOUNG_DEPLOYER_S // 3600} h-old deployers. `farm_group_n` is the size of that group. Launches without a dev buy have no fingerprint and are never flagged by this rule; a deployer first seen before the 30-day lifecycle window looks 'young' on its first launch inside it (known limitation).")
    md.append("- `socials_present`: 1 / 0 from the socials parsed out of the launch calldata (`launch_meta`, filled by the coin drawer's Refresh context or `--socials N`); NULL = not parsed (never fetched in the batch for every launch).")
    md.append(f"- `snipe_tax_zero_ts`: launch timestamp + the curve's snipe window ({SNIPE_WINDOW_S} s on every curve launched so far; the batch path does not read per-curve values). The tax is `9900 >> (elapsed × 14 // 3)` bps of quote-in: {snipe_tax_bps(0)} (capped at 10000 − fee − creator tax − 100) at 0 s, {snipe_tax_bps(1)} at 1 s, {snipe_tax_bps(2)} at 2 s, 0 from 3 s. `elapsed` uses `block.timestamp`, which has 1-second granularity, so the zero-tax time is a lower bound: the first block whose timestamp is ≥ launch + 3 s.")
    md.append("")
    md.append("## What the observed launches look like")
    md.append("")
    md.append("| measure | value |")
    md.append("|---|---|")
    if obs and obs[0]:
        n0 = obs[0]
        md.append(f"| observed launches | {n0:,} |")
        md.append(f"| with any trade within the horizon | {obs[5] or 0:,} ({pct((obs[5] or 0) / n0)}) |")
        md.append(f"| with a dev buy | {obs[1] or 0:,} ({pct((obs[1] or 0) / n0)}) · median share {pct(_median(dev_shares), 2)} · p90 {pct(_quantile(dev_shares, 0.9), 2)} |")
        md.append(f"| with an untaxed bundle in the snipe window (proxy) | {obs[2] or 0:,} ({pct((obs[2] or 0) / n0)}) |")
        md.append(f"| with taxed snipers in the window | {obs[3] or 0:,} ({pct((obs[3] or 0) / n0)}) |")
        md.append(f"| creator tax readable | {obs[6] or 0:,} · most common: {', '.join(f'{int(t)} bps ×{c}' for t, c in tax_dist) or '—'} |")
        md.append(f"| deployer first seen < 24 h before the launch | {young or 0:,} ({pct((young or 0) / n0)}) · median prior launches in 30 d: {int(_median(dep_prior) or 0)} · p90 {int(_quantile(dep_prior, 0.9) or 0)} |")
        md.append(f"| launch farm (rule above) | {obs[4] or 0:,} ({pct((obs[4] or 0) / n0)}) of {st['farm_pass']['fingerprinted']:,} fingerprinted |")
        md.append(f"| socials parsed / declared | {obs[7] or 0:,} / {obs[8] or 0:,} |")
        md.append(f"| exact exemption logs available | {obs[9] or 0:,} launches |")
    md.append("")
    md.append("## Results: runner rate by launch feature (one row per coin)")
    md.append("")
    if not sample:
        md.append(f"_No coin has both an untaxed entry and a full {H // 60}-min horizon inside this range; the tables are empty. Re-run with a shorter `--horizon` on short ranges, or on the 14-day store._")
        md.append("")
    md += table("By dev buy (share of supply bought in the launch tx or by the deployer within 5 s)", grp(dev_bins))
    md += table("By untaxed bundle size in the snipe window (proxy for declared exemptions)", grp(bundle_bins))
    md += table("By creator tax", grp(tax_bins))
    md += table("By the deployer's graduation rate over the previous 30 days", grp(dep_bins))
    md += table("By the deployer's prior launches in 30 days", grp(prior_bins))
    md += table("By launch farm flag", grp(farm_bins))
    md += table("By declared socials", grp(soc_bins))
    md += table("By taxed snipers in the window (someone paid to be first)", grp(sniper_bins))
    md += table("Combined rules (the `clean_launch` radar preset)", combos)
    md.append("## Entry at t = 3 s versus t = 0 s: what the snipe tax costs and what waiting costs")
    md.append("")
    md.append("| elapsed since launch | tax by formula (bps of quote-in) | taxed buys observed | median tax paid (bps) | total paid, ETH-quoted launches |")
    md.append("|---|---|---|---|---|")
    for e in range(SNIPE_WINDOW_S):
        b = by_el.get(e, {"buys": 0, "bps": [], "eth": 0.0})
        med = _median(b["bps"])
        md.append(f"| {e} s | {snipe_tax_bps(e)} | {b['buys']:,} | {f'{med:.0f}' if med is not None else '—'} | {b['eth']:.4f} ETH |")
    md.append(f"| ≥ {SNIPE_WINDOW_S} s | 0 | — | — | — |")
    md.append("")
    md.append(f"- Formula: `9900 >> (elapsed × 14 // 3)`, capped at `10000 − 100 (curve fee) − creator tax − 100`; the buy at 0 s keeps ~1% of its quote. Observed medians below the formula at a given second are two-sided or multi-event rows and the 1-second timestamp rounding, not a different rate.")
    if ratios:
        md.append(f"- Waiting for the tax to reach zero: the first untaxed trade comes a median {int(_median(wait_s) or 0)} s after the launch (p90 {int(_quantile(wait_s, 0.9) or 0)} s) at a median **{_median(ratios):.2f}×** the launch-block price (p25 {_quantile(ratios, 0.25):.2f}×, p75 {_quantile(ratios, 0.75):.2f}×; {len(ratios):,} launches with both prices). Paying 99% tax at 0 s means receiving ~1% of the tokens the same quote buys 3 s later — every observed ratio is far below 100×, so the untaxed entry is the cheaper one.")
    md.append("")
    md.append("## Reading this honestly")
    md.append("")
    md.append("- One row per coin; buckets with a handful of coins are noise. Lifts are versus the base rate of *observed launches with an untaxed entry*, which is not the base rate of the rotation backtest (RESEARCH-RUNNERS.md measures coin-minutes with inflow).")
    md.append("- 'Runner' is a price path on the curve; nobody earned it. Fees (1%), the creator tax and the price impact of the entry itself are ignored, and the entry is the first trade after the window, whoever made it.")
    md.append("- The bundle is a behavioural proxy: an untaxed buyer inside the window is either on the declared exemption list, the creator fee recipient, or a wallet whose buy landed in a block stamped ≥ 3 s after a launch stamped earlier than it happened. Exact counts need the `SnipeTaxExempted` logs, which the ingest paths keep from now on.")
    md.append(f"- Deployer history counts launches by the same `deployer` address; farms that rotate wallets show up as young deployers instead. The 30-day window is measured in blocks at the store's block rate ({st['blocks_per_s']:.2f}/s).")
    md.append(f"- The sample is {span_h:.1f} hours of one chain on one launchpad ({len(sample):,} coins with a complete horizon). Treat these as first measurements; the numbers are recomputed whenever this command runs on a new range.")
    md.append("")
    md.append("## Exact commands")
    md.append("")
    md.append("```sh")
    md.append(f".venv/bin/python -m stampede launch-intel --db {db_label} --horizon {H} --gain {gain} --out docs/RESEARCH-LAUNCHES.md")
    md.append("```")
    md.append("")
    tp = st["trades_pass"]
    md.append(f"Compute time {st['seconds']:.0f} s: deployer pass {st['deployer_pass']['seconds']:.1f} s over {st['deployer_pass']['launches']:,} launches, trades pass {tp['seconds']:.1f} s over {tp['observed_launches']:,} observed launches ({tp['trade_rows_read']:,} trade rows read), farm pass {st['farm_pass']['seconds']:.1f} s. No RPC call in the batch path.")
    if tp["trade_rows_read"] and tp["seconds"] >= 0.05:
        rows_per_s = tp["trade_rows_read"] / tp["seconds"]
        per_launch_ms = 1000 * tp["seconds"] / max(1, tp["observed_launches"])
        md.append("")
        md.append(f"Extrapolation at this run's rate ({rows_per_s:,.0f} trade rows/s, {per_launch_ms:.2f} ms per observed launch, one indexed range query each): a 14-day store with 40,000,000 trades of which ~60% fall in the first hour after their launch, and ~180,000 observed launches, needs about {(0.6 * 40_000_000 / rows_per_s) / 60:.0f} min for the rows plus {180_000 * per_launch_ms / 1000 / 60:.1f} min of per-launch overhead; the deployer pass is linear in launches ({st['deployer_pass']['seconds']:.1f} s here). Short stores overstate the per-row cost (fixed costs dominate).")
    return "\n".join(md)


# ---- API / live helpers ----------------------------------------------------------------------------------------------------
def intel_for(store: Store, tokens: list[str] | set[str]) -> dict[str, dict[str, Any]]:
    """launch_intel rows for many coins at once, as the `launch` sub-object the radar and the coin card show."""
    toks = list(tokens)
    if not toks:
        return {}
    ensure_table(store.db)
    cols = ",".join(API_FIELDS)
    out: dict[str, dict[str, Any]] = {}
    for i in range(0, len(toks), 400):
        chunk = toks[i : i + 400]
        ph = ",".join("?" * len(chunk))
        for r in store.db.execute(f"SELECT token, {cols} FROM launch_intel WHERE token IN ({ph})", chunk):
            d = dict(zip(API_FIELDS, r[1:]))
            d["observed"] = bool(d["observed"])
            d["launch_farm"] = None if d["launch_farm"] is None else bool(d["launch_farm"])
            d["socials_present"] = None if d["socials_present"] is None else bool(d["socials_present"])
            d["dev_buy_in_launch_tx"] = None if d["dev_buy_in_launch_tx"] is None else bool(d["dev_buy_in_launch_tx"])
            d["source"] = "indexed trades + lifecycle logs; bundle is the behavioural proxy unless exempt_declared_n is set"
            out[r[0]] = d
    return out


def is_clean(li: dict[str, Any] | None, bundle_max: int | None, dev_buy_max: float | None, exclude_farm: bool) -> bool:
    """The clean_launch filter: unknown features do not pass (n/a is not 0)."""
    if bundle_max is None and dev_buy_max is None and not exclude_farm:
        return True
    if not li or not li.get("observed"):
        return False
    if bundle_max is not None and (li.get("bundle_n") is None or li["bundle_n"] > bundle_max):
        return False
    if dev_buy_max is not None and (li.get("dev_buy_share") is None or li["dev_buy_share"] > dev_buy_max):
        return False
    if exclude_farm and li.get("launch_farm") is not False:
        return False
    return True


def fetch_exempt(store: Store, rpc, token: str) -> int | None:
    """Live path: read the launch tx receipt, keep its SnipeTaxExempted logs in `logs`, return the distinct declared
    accounts other than the deployer (and store it as exempt_declared_n when the launch has an intel row)."""
    from .pons import launch_of

    la = launch_of(store, token)
    if not la or not la.get("tx_hash"):
        return None
    accts: set[str] = set()
    cached = store.db.execute("SELECT topic1 FROM logs WHERE tx_hash=? AND kind='snipe_tax_exempted'", (la["tx_hash"],)).fetchall()
    if cached:  # already indexed (ingest / backfill / an earlier refresh): no RPC
        accts = {chain.addr_from_topic(t[0]) for t in cached if t[0]} - {la.get("deployer")}
        ensure_table(store.db)
        store.db.execute("UPDATE launch_intel SET exempt_declared_n=? WHERE token=?", (len(accts), token))
        store.commit()
        return len(accts)
    try:
        rc = rpc.get_receipt(la["tx_hash"])
    except Exception:  # noqa: BLE001
        return None
    if not rc or not rc.get("logs"):
        return None
    rows = []
    for l in rc["logs"]:
        tp = l.get("topics") or []
        if not tp or tp[0] != chain.T_SNIPE_TAX_EXEMPTED:
            continue
        rows.append((l["transactionHash"], int(l["logIndex"], 16), int(l["blockNumber"], 16), l["address"].lower(), tp[0], tp[1] if len(tp) > 1 else None, None, None, l.get("data") or "0x", "snipe_tax_exempted", "alchemy_on_demand"))
        if len(tp) > 1:
            a = chain.addr_from_topic(tp[1])
            if a != la.get("deployer"):
                accts.add(a)
    store.insert_logs(rows)
    ensure_table(store.db)
    store.db.execute("UPDATE launch_intel SET exempt_declared_n=? WHERE token=?", (len(accts), token))
    store.commit()
    return len(accts)


# ---- CLI --------------------------------------------------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="stampede launch-intel", description=__doc__.split("\n\n")[0])
    ap.add_argument("--db", required=True, help="store to read and to write launch_intel into")
    ap.add_argument("--out", default="", help="write the markdown report here (default: stdout)")
    ap.add_argument("--horizon", type=int, default=3600, help="observation window after the launch, seconds")
    ap.add_argument("--gain", type=float, default=1.0, help="runner = price multiple minus one within the horizon: 1.0 = 2x")
    ap.add_argument("--socials", type=int, default=0, help="parse declared socials from up to N launch transactions of observed launches (needs ALCHEMY_KEY); default 0 = none")
    ap.add_argument("--resolve-quotes", action="store_true", help="symbol()/decimals() of the pair tokens missing from `quotes` (two eth_calls per pair token, needs ALCHEMY_KEY)")
    ap.add_argument("--limit", type=int, default=None, help="debug: only the first N observed launches in the trades pass")
    ap.add_argument("--no-report", action="store_true")
    a = ap.parse_args(argv)
    store = Store(a.db)
    store.db.execute("PRAGMA cache_size=-262144")
    rpc = None
    if a.socials or a.resolve_quotes:
        from ..rpc import Rpc

        rpc = Rpc()
        if not rpc.alchemy_url:
            print("--socials / --resolve-quotes need ALCHEMY_KEY in .env; skipping the RPC steps", file=sys.stderr)
            rpc = None
    if a.resolve_quotes and rpc is not None:
        from ..ingest import Ingest

        n_q = Ingest(store, rpc, None).resolve_quotes()
        print(f"quotes: {n_q} pair tokens resolved", file=sys.stderr)
    run_id = store.start_run("launch_intel", {"db": a.db, "horizon": a.horizon, "gain": a.gain, "socials": a.socials, "resolve_quotes": a.resolve_quotes})
    st = compute(store, a.horizon, a.gain, a.socials, rpc, progress=lambda m: print(m, file=sys.stderr, flush=True), limit=a.limit)
    stats = {k: v for k, v in st.items() if k != "records"}
    store.finish_run(run_id, stats)
    print(f"launch_intel: {st['deployer_pass']['launches']:,} launches, {st['deployer_pass']['observed']:,} observed, {st['farm_pass']['launch_farm']:,} launch farms, {st['seconds']:.1f} s", file=sys.stderr)
    if not a.no_report:
        text = report(store, st, a.db)
        if a.out:
            Path(a.out).write_text(text)
            print(f"written {a.out}", file=sys.stderr)
        else:
            print(text)
    store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
