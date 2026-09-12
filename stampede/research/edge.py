"""Is there an edge? Walk-forward runner model, alert precision, exit-policy search and a simulated policy with the
exact PONS curve math (stage 4 of the STAMPEDE programme).

    stampede edge --db data/research-14d.sqlite --train-days 7 --test-days 7 --out docs/RESEARCH-EDGE.md

Inputs: `coin_minutes` (stampede edge-features; built here when missing) and the store's trades for the simulator.
Everything is out-of-sample: the range is cut into folds (days; on a store shorter than the requested days the fold is
the range divided by train + test and the report says so), the model for fold d is trained on folds < d only, the
alert threshold is chosen on the train folds, the exit policy is chosen on the train folds' alerts and then applied
unchanged to the test folds. Targets, thresholds, fees and every assumption are written into the report and into
`stampede/signals/edge-config.json`, which the verdict uses at runtime (with the model pickle when present).
"""
from __future__ import annotations

import argparse
import bisect
import json
import math
import pickle
import random
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import chain
from ..context import launch_intel
from ..signals.verdict import RULE_BUCKETS, rule_cell
from ..store import Store
from . import features as F

SIZE_ETH = 0.02
SIZE_WEI = int(SIZE_ETH * 10**18)
LABEL_COLS = ["gain_5", "gain_15", "gain_30", "gain_60", "dd_30", "t_peak_30", "ret_30", "ret_60", "grad_30", "grad_60", "runner_30", "horizon_30", "horizon_60"]
META_COLS = ["token", "minute", "ts", "quote_token", "stage", "spot", "q_virtual", "t_reserve", "recon", "est_tax_bps", "fees_known", "price", "launch_ts", "inflow_wallets"]
DEDUPE_S = 1800  # one alert per coin per 30 min, as the context worker does
CONFIG_PATH = Path(__file__).resolve().parent.parent / "signals" / "edge-config.json"
MODEL_DIR = Path("data") / "models"

# exit-policy grid searched on the TRAIN alerts only (tp: (gain, sell share); trail: from the TP fill on, or from entry
# when there is no TP; sl: stop; time_s: time exit; inflow_dies / smart_exit: minute triggers)
GRID = {
    "tp": [None, (0.5, 0.5), (0.8, 0.5), (1.0, 0.5), (0.8, 1.0)],
    "trail": [None, 0.25, 0.35],
    "sl": [0.3, 0.4],
    "time_s": [1200, 1800, 2700],
    "inflow_dies": [False, True],
    "smart_exit": [False, True],
}
NAMED = {
    "hold_30": {"tp": None, "trail": None, "sl": None, "time_s": 1800, "inflow_dies": False, "smart_exit": False},
    "tp80_half_trail25_sl30_t30": {"tp": (0.8, 0.5), "trail": 0.25, "sl": 0.3, "time_s": 1800, "inflow_dies": False, "smart_exit": False},
    "sl30_t30": {"tp": None, "trail": None, "sl": 0.3, "time_s": 1800, "inflow_dies": False, "smart_exit": False},
}


def pct(x: float | None, d: int = 1) -> str:
    return "—" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{100 * x:.{d}f}%"


def fnum(x: float | None, d: int = 4) -> str:
    return "—" if x is None else f"{x:.{d}f}"


def utc(ts: int | float | None) -> str:
    return "—" if ts is None else datetime.fromtimestamp(int(ts), timezone.utc).strftime("%Y-%m-%d %H:%M")


# ---- data -----------------------------------------------------------------------------------------------------------
class Table:
    """coin_minutes in column arrays (numpy), loaded in chunks."""

    def __init__(self, db: sqlite3.Connection, run_id: int, feature_cols: list[str]):
        import numpy as np

        self.features = feature_cols
        cols = feature_cols + LABEL_COLS + META_COLS
        n = db.execute("SELECT COUNT(*) FROM coin_minutes WHERE run_id=?", (run_id,)).fetchone()[0]
        self.n = n
        self.X = np.full((n, len(feature_cols)), np.nan, dtype=np.float32)
        self.L = np.full((n, len(LABEL_COLS)), np.nan, dtype=np.float64)
        self.meta: dict[str, list] = {c: [None] * n for c in META_COLS}
        cur = db.cursor()
        cur.arraysize = 20_000
        cur.execute(f"SELECT {','.join(cols)} FROM coin_minutes WHERE run_id=? ORDER BY ts, token", (run_id,))
        nf, nl = len(feature_cols), len(LABEL_COLS)
        i = 0
        while True:
            rows = cur.fetchmany(20_000)
            if not rows:
                break
            arr = np.array([[np.nan if v is None else float(v) for v in r[: nf + nl]] for r in rows], dtype=np.float64)
            self.X[i : i + len(rows)] = arr[:, :nf]
            self.L[i : i + len(rows)] = arr[:, nf:]
            for j, r in enumerate(rows):
                for k, c in enumerate(META_COLS):
                    self.meta[c][i + j] = r[nf + nl + k]
            i += len(rows)
        self.ts = np.array([t or 0 for t in self.meta["ts"]], dtype=np.int64)
        self.token = self.meta["token"]

    def label(self, name: str):
        return self.L[:, LABEL_COLS.index(name)]

    def col(self, name: str):
        return self.X[:, self.features.index(name)]


def rules_score(t: Table):
    """The current radar score (stage 1 calibration) on every row, for the rules baseline."""
    import numpy as np

    from ..api import radar

    inflow, accel, breadth, quality, age, mom10, smart = (t.col(c) for c in ("inflow_10m", "accel", "breadth", "quality", "age_s", "mom_10m", "smart_inflow"))
    out = np.zeros(t.n, dtype=np.float64)
    for i in range(t.n):
        q = None if np.isnan(quality[i]) else float(quality[i])
        a = None if np.isnan(age[i]) else int(age[i])
        chg = None if np.isnan(mom10[i]) else float(mom10[i]) * 100
        sm = None if np.isnan(smart[i]) else int(smart[i])
        out[i] = radar.score(int(inflow[i]) if not np.isnan(inflow[i]) else 0, float(accel[i]) if not np.isnan(accel[i]) else 0.0, int(breadth[i]) if not np.isnan(breadth[i]) else 0, q, None, a, t.meta["stage"][i] or "curve", chg, smart_inflow=sm)["score"]
    return out


def under_radar_mask(t: Table):
    import numpy as np

    inflow, age, mom10 = t.col("inflow_10m"), t.col("age_s"), t.col("mom_10m")
    score = rules_score(t)
    ok_age = ~np.isnan(age) & (age < 3600)
    ok_mom = np.isnan(mom10) | (mom10 < 1.0)
    return (inflow >= 8) & (inflow <= 40) & ok_age & ok_mom & (score >= 60), score


# ---- calibrated rules (the verdict's fallback without a model; the cell definition lives in signals.verdict) --------
def _nn(v: float) -> float | None:
    return None if v != v else float(v)


def _cell_of(t: Table, i: int) -> str:
    return rule_cell(_nn(t.col("inflow_10m")[i]), _nn(t.col("age_s")[i]), _nn(t.col("quality")[i]), _nn(t.col("mom_10m")[i]))


def fit_rules(t: Table, idx, y, base: float) -> dict[str, dict[str, float]]:
    """Rate per (inflow, age, quality, momentum) cell on the given rows, shrunk towards the base rate (weight 10)."""
    cells: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    inflow, age, q, mom = t.col("inflow_10m"), t.col("age_s"), t.col("quality"), t.col("mom_10m")
    for i in idx:
        c = cells[rule_cell(_nn(inflow[i]), _nn(age[i]), _nn(q[i]), _nn(mom[i]))]
        c[0] += 1
        c[1] += int(y[i])
    return {k: {"n": n, "p": (h + 10 * base) / (n + 10)} for k, (n, h) in cells.items()}


def rules_p(t: Table, table: dict[str, dict[str, float]], base: float):
    import numpy as np

    inflow, age, q, mom = t.col("inflow_10m"), t.col("age_s"), t.col("quality"), t.col("mom_10m")
    out = np.full(t.n, base)
    for i in range(t.n):
        c = table.get(rule_cell(_nn(inflow[i]), _nn(age[i]), _nn(q[i]), _nn(mom[i])))
        if c:
            out[i] = c["p"]
    return out


# ---- model ----------------------------------------------------------------------------------------------------------
def make_model(seed: int):
    from sklearn.ensemble import HistGradientBoostingClassifier

    return HistGradientBoostingClassifier(learning_rate=0.06, max_iter=200, max_leaf_nodes=15, min_samples_leaf=40, l2_regularization=2.0, early_stopping=False, random_state=seed)


def fit_matrix(X):
    """Training matrix: a feature that is missing on every training row (e.g. smart-wallet features before the first
    half is known) is set to 0 there; the binner rejects all-NaN columns, and a tree never splits on a constant."""
    import numpy as np

    Xf = np.array(X, dtype=np.float32, copy=True)
    allnan = np.isnan(Xf).all(axis=0)
    Xf[:, allnan] = 0.0
    return Xf


def walk_forward(t: Table, fold: Any, folds: list[int], train_mask, targets: dict[str, Any], seed: int, log) -> dict[str, Any]:
    """p for every row of every fold in `folds`, from a model trained on the rows of earlier folds only."""
    import numpy as np

    out = {k: np.full(t.n, np.nan) for k in targets}
    for d in folds:
        fit_idx = np.where(train_mask & (fold < d))[0]
        pred_idx = np.where(fold == d)[0]
        if len(pred_idx) == 0:
            continue
        for k, y in targets.items():
            yk = y[fit_idx]
            if len(fit_idx) < 200 or yk.sum() < 10 or yk.sum() == len(yk):
                continue
            m = make_model(seed)
            m.fit(fit_matrix(t.X[fit_idx]), yk)
            out[k][pred_idx] = m.predict_proba(t.X[pred_idx])[:, 1]
        log(f"  fold {d}: trained on {len(fit_idx):,} rows, scored {len(pred_idx):,}")
    return out


# ---- alerts ----------------------------------------------------------------------------------------------------------
def select_alerts(t: Table, p, cand, idx_pool, threshold: float) -> list[int]:
    """Causal rule: alert when p >= threshold on a candidate row; one alert per coin per 30 min. Rows in time order."""
    last: dict[str, int] = {}
    out: list[int] = []
    for i in idx_pool:
        if not cand[i] or p[i] != p[i] or p[i] < threshold:
            continue
        tok, ts = t.token[i], int(t.ts[i])
        if tok in last and ts - last[tok] < DEDUPE_S:
            continue
        last[tok] = ts
        out.append(i)
    return out


def threshold_for_rate(t: Table, p, cand, idx_pool, per_hour: float) -> float:
    """Largest threshold whose causal alert count on the pool is >= per_hour * hours."""
    import numpy as np

    ps = np.array([p[i] for i in idx_pool if cand[i] and p[i] == p[i]])
    if len(ps) == 0:
        return 1.0
    hours = max(1e-9, (t.ts[idx_pool].max() - t.ts[idx_pool].min()) / 3600) if len(idx_pool) else 1.0
    want = per_hour * hours
    for qtl in np.linspace(0.999, 0.5, 120):
        thr = float(np.quantile(ps, qtl))
        if len(select_alerts(t, p, cand, idx_pool, thr)) >= want:
            return thr
    return float(np.quantile(ps, 0.5))


def top_k_per_hour(t: Table, p, cand, idx_pool, k: int) -> list[int]:
    """Non-causal reference: within each clock hour, the k highest-p candidate rows (one per coin)."""
    by_hour: dict[int, list[int]] = defaultdict(list)
    for i in idx_pool:
        if cand[i] and p[i] == p[i]:
            by_hour[int(t.ts[i]) // 3600].append(i)
    out: list[int] = []
    for h, rows in by_hour.items():
        rows.sort(key=lambda i: -p[i])
        seen: set[str] = set()
        for i in rows:
            if t.token[i] in seen:
                continue
            seen.add(t.token[i])
            out.append(i)
            if len(seen) >= k:
                break
    out.sort(key=lambda i: int(t.ts[i]))
    return out


def alert_metrics(t: Table, alerts: list[int], base: float, idx_pool) -> dict[str, Any]:
    import numpy as np

    y2 = t.label("runner_30")
    g30 = t.label("gain_30")
    g60 = t.label("gain_60")
    tpk = t.label("t_peak_30")
    hours = max(1e-9, (t.ts[idx_pool].max() - t.ts[idx_pool].min()) / 3600) if len(idx_pool) else 1.0
    hits = [i for i in alerts if y2[i] == 1]
    prec = len(hits) / len(alerts) if alerts else None
    lead = float(np.median([tpk[i] for i in hits])) if hits else None
    # catch rate: coins with a 3x within 60 min ahead of some test row; caught when an alert fired while that was still ahead
    three: dict[str, list[int]] = defaultdict(list)
    for i in idx_pool:
        if g60[i] == g60[i] and g60[i] >= 2.0:
            three[t.token[i]].append(int(t.ts[i]))
    caught = 0
    for tok, tss in three.items():
        lo, hi = min(tss), max(tss)
        if any(t.token[i] == tok and lo <= int(t.ts[i]) <= hi for i in alerts):
            caught += 1
    return {
        "alerts": len(alerts),
        "coins": len({t.token[i] for i in alerts}),
        "per_hour": len(alerts) / hours,
        "precision_2x": prec,
        "precision_50": (sum(1 for i in alerts if g30[i] >= 0.5) / len(alerts)) if alerts else None,
        "lift": (prec / base) if prec is not None and base > 0 else None,
        "lead_median_min": lead,
        "lead_ge_2_share": (sum(1 for i in hits if tpk[i] >= 2) / len(hits)) if hits else None,
        "catch_3x": (caught / len(three)) if three else None,
        "catch_n": len(three),
        "caught": caught,
        "median_gain_30": float(np.median([g30[i] for i in alerts])) if alerts else None,
        "median_dd_30": float(np.median([t.label("dd_30")[i] for i in alerts])) if alerts else None,
    }


# ---- simulator -------------------------------------------------------------------------------------------------------
class CoinPath:
    """Reserve state after every curve trade of one coin (from the store), for the exit simulation."""

    def __init__(self, store: Store, token: str, launch: dict[str, Any] | None, intel: dict[str, Any] | None, grad_ts: int | None):
        rows = store.db.execute("SELECT ts, side, token_amount, quote_amount, wallet, venue, fee_raw, tax_raw FROM trades WHERE token=? AND ts>0 ORDER BY ts, id", (token,)).fetchall()
        self.ts = [r[0] for r in rows]
        p = F.curve_path(rows, launch_ts=launch.get("ts") if launch else None, deployer=launch.get("deployer") if launch else None, grad_ts=grad_ts, tax_hint=intel.get("creator_tax_bps") if intel else None)
        self.q, self.t, self.tax, self.recon, self.start = p["q"], p["t"], p["tax_bps"], p["recon"], p["start"]
        self.launch_ts = launch.get("ts") if launch else None
        self.grad_ts = grad_ts
        self.threshold = (launch.get("threshold") if launch else None) or chain.CURVE_GRADUATION_THRESHOLD_WEI

    def state_at(self, ts: int) -> tuple[int, int, int] | None:
        """(index, Q, T) after the last curve trade at or before ts; None when the state is not reconstructed there."""
        i = bisect.bisect_right(self.ts, ts) - 1
        while i >= 0 and self.q[i] == 0:
            if self.start is not None and i < self.start:
                return None
            i -= 1
        if i < 0 or self.q[i] == 0:
            return None
        return i, self.q[i], self.t[i]


def simulate(path: CoinPath, alert_ts: int, policy: dict[str, Any], minute_rows: dict[int, dict[str, Any]], latency_s: int = 2, size_wei: int = SIZE_WEI, range_hi: int | None = None) -> dict[str, Any] | None:
    """One trade of `size_wei` entered right after the alert, exits by the policy, fills by the curve math. Returns
    None when the entry state is unknown. pnl in quote units (ETH)."""
    entry_ts = alert_ts + latency_s
    st = path.state_at(entry_ts)
    if st is None:
        return None
    i0, q, t = st
    if path.grad_ts and entry_ts >= path.grad_ts:
        return None
    elapsed = (entry_ts - path.launch_ts) if path.launch_ts else None
    fill = F.buy_fill(q, t, size_wei, path.tax, elapsed)
    tokens = fill["tokens_out"]
    if tokens <= 0:
        return None
    cost = size_wei / 1e18
    entry_px = cost / tokens
    remaining = tokens
    proceeds = 0.0
    fees = (fill["fee"] + fill["snipe"] + fill["tax"]) / 1e18
    tp = policy.get("tp")
    trail = policy.get("trail")
    sl = policy.get("sl")
    time_s = policy.get("time_s") or 1800
    deadline = entry_ts + time_s
    tp_done = tp is None
    peak_px = entry_px
    trail_on = trail is not None and tp is None
    exit_reason = None
    exit_ts = None
    mark_px = entry_px

    def sell(frac: float, qq: int, tt: int, ts: int) -> None:
        nonlocal remaining, proceeds, fees
        amt = int(remaining * frac) if frac < 1 else remaining
        if amt <= 0:
            return
        f = F.sell_fill(qq, tt, amt, path.tax)
        proceeds += f["quote_out"] / 1e18
        fees += (f["fee"] + f["tax"]) / 1e18
        remaining -= amt

    i = i0 + 1
    n = len(path.ts)
    next_minute = (entry_ts // 60 + 1) * 60
    last_q, last_t = q, t
    while remaining > 0:
        # minute-boundary triggers first when the next trade is beyond the boundary
        if next_minute <= deadline and (i >= n or path.ts[i] > next_minute):
            if next_minute > (range_hi or 10**12):
                break
            row = minute_rows.get(next_minute // 60 - 1)  # the row whose decision instant is this boundary
            if row is not None:
                if policy.get("inflow_dies") and row.get("inflow_10m") == 0 and next_minute - entry_ts >= 300:
                    sell(1.0, last_q, last_t, next_minute)
                    exit_reason, exit_ts = "inflow_dies", next_minute
                    break
                if policy.get("smart_exit") and (row.get("smart_sellers_10m") or 0) >= 2 and (row.get("smart_buyers_10m") or 0) == 0:
                    sell(1.0, last_q, last_t, next_minute)
                    exit_reason, exit_ts = "smart_exit", next_minute
                    break
            next_minute += 60
            continue
        if i >= n or path.ts[i] > deadline or path.q[i] == 0:
            break
        if path.grad_ts and path.ts[i] >= path.grad_ts:
            break
        last_q, last_t = path.q[i], path.t[i]
        mark = F.sell_fill(last_q, last_t, remaining, path.tax)["quote_out"] / 1e18
        mark_px = mark / remaining if remaining else 0.0
        if mark_px > peak_px:
            peak_px = mark_px
        ret = mark_px / entry_px - 1
        if sl is not None and ret <= -sl:
            sell(1.0, last_q, last_t, path.ts[i])
            exit_reason, exit_ts = "stop", path.ts[i]
            break
        if not tp_done and ret >= tp[0]:
            sell(tp[1], last_q, last_t, path.ts[i])
            tp_done = True
            trail_on = trail is not None
            peak_px = mark_px
            if remaining <= 0:
                exit_reason, exit_ts = "tp", path.ts[i]
                break
        elif trail_on and mark_px <= peak_px * (1 - trail):
            sell(1.0, last_q, last_t, path.ts[i])
            exit_reason, exit_ts = "trail", path.ts[i]
            break
        i += 1
    if remaining > 0:
        # time exit (or graduation / end of data) at the last known state
        if range_hi is not None and deadline > range_hi:
            return None  # the horizon is not observed
        sell(1.0, last_q, last_t, deadline)
        exit_reason = exit_reason or ("graduation" if path.grad_ts and deadline >= path.grad_ts else "time")
        exit_ts = exit_ts or deadline
    return {"pnl": proceeds - cost, "ret": proceeds / cost - 1, "fees": fees, "exit": exit_reason, "hold_s": (exit_ts or deadline) - entry_ts, "entry_px": entry_px, "tokens": tokens}


def policy_key(p: dict[str, Any]) -> str:
    tp = p.get("tp")
    return f"tp={'none' if not tp else f'{int(tp[0] * 100)}%x{int(tp[1] * 100)}%'} trail={p.get('trail') if p.get('trail') is not None else 'none'} sl={p.get('sl') if p.get('sl') is not None else 'none'} time={(p.get('time_s') or 0) // 60}m inflow_dies={'on' if p.get('inflow_dies') else 'off'} smart_exit={'on' if p.get('smart_exit') else 'off'}"


def grid_policies() -> list[dict[str, Any]]:
    out = []
    for tp in GRID["tp"]:
        for trail in GRID["trail"]:
            for sl in GRID["sl"]:
                for time_s in GRID["time_s"]:
                    for inf in GRID["inflow_dies"]:
                        for sm in GRID["smart_exit"]:
                            out.append({"tp": tp, "trail": trail, "sl": sl, "time_s": time_s, "inflow_dies": inf, "smart_exit": sm})
    return out


def policy_stats(results: list[tuple[str, dict[str, Any]]], n_boot: int = 0, seed: int = 7) -> dict[str, Any]:
    """results: (coin, sim result). Expectancy, hit rate, drawdown of the sequential equity curve, bootstrap CI by coin."""
    if not results:
        return {"n": 0}
    pnl = [r["pnl"] for _, r in results]
    n = len(pnl)
    mean = sum(pnl) / n
    eq = 0.0
    peak = 0.0
    mdd = 0.0
    for x in pnl:
        eq += x
        peak = max(peak, eq)
        mdd = min(mdd, eq - peak)
    out: dict[str, Any] = {
        "n": n,
        "coins": len({c for c, _ in results}),
        "expectancy": mean,
        "expectancy_pct": mean / SIZE_ETH,
        "hit_rate": sum(1 for x in pnl if x > 0) / n,
        "total": sum(pnl),
        "max_drawdown": mdd,
        "median_ret": sorted(r["ret"] for _, r in results)[n // 2],
        "fees_total": sum(r["fees"] for _, r in results),
        "exits": dict(sorted(((k, sum(1 for _, r in results if r["exit"] == k)) for k in {r["exit"] for _, r in results}), key=lambda x: -x[1])),
        "hold_median_s": sorted(r["hold_s"] for _, r in results)[n // 2],
    }
    if n_boot:
        rng = random.Random(seed)
        by_coin: dict[str, list[float]] = defaultdict(list)
        for c, r in results:
            by_coin[c].append(r["pnl"])
        coins = list(by_coin)
        means = []
        for _ in range(n_boot):
            s = [x for c in (coins[rng.randrange(len(coins))] for _ in coins) for x in by_coin[c]]
            means.append(sum(s) / len(s))
        means.sort()
        out["ci95"] = (means[int(0.025 * n_boot)], means[min(n_boot - 1, int(0.975 * n_boot))])
    return out


# ---- report ---------------------------------------------------------------------------------------------------------
def md_table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    return [f"| {' | '.join(headers)} |", f"|{'---|' * len(headers)}"] + [f"| {' | '.join(str(c) for c in r)} |" for r in rows] + [""]


def run(a) -> dict[str, Any]:
    import numpy as np

    t0 = time.time()
    log = print
    store = Store(a.db)
    launch_intel.ensure_table(store.db)
    fdb = Store(a.features_db).db if a.features_db else store.db
    run = F.latest_run(fdb)
    if run is None or a.rebuild:
        log("coin_minutes missing: building features first")
        F.build(a.db, features_db=a.features_db, log=log)
        run = F.latest_run(fdb)
    assert run is not None
    lo, hi = run["range_from"], run["range_to"]
    span = hi - lo
    fold_s = a.fold_s
    n_folds = a.train_days + a.test_days
    compressed = False
    if span < n_folds * fold_s * 0.9:
        fold_s = span / n_folds
        compressed = True
    log(f"loading coin_minutes run {run['run_id']}: {run['rows']:,} rows, range {utc(lo)}–{utc(hi)} UTC, fold {fold_s / 60:.1f} min{' (compressed)' if compressed else ''}")
    t = Table(fdb, run["run_id"], F.MODEL_FEATURES)
    fold = np.minimum(((t.ts - lo) / fold_s).astype(int), n_folds - 1)
    train_folds = list(range(a.train_days))
    test_folds = list(range(a.train_days, n_folds))
    y2 = np.nan_to_num(t.label("runner_30"), nan=0.0)
    y50 = np.nan_to_num((t.label("gain_30") >= 0.5).astype(float), nan=0.0)
    ydd = np.nan_to_num((t.label("dd_30") <= -0.5).astype(float), nan=0.0)
    labeled = t.label("horizon_30") == 1
    stage = np.array([s == "curve" for s in t.meta["stage"]])
    is_eth = np.array([q == chain.NATIVE for q in t.meta["quote_token"]])
    recon_ok = np.array([r in ("exact", "inferred", "approx") for r in t.meta["recon"]])
    age = t.col("age_s")
    inflow = t.col("inflow_10m")
    train_mask = labeled & stage
    cand = labeled & stage & is_eth & recon_ok & ~np.isnan(age) & (age <= a.age_max_s) & (age >= a.min_age_s) & (inflow >= a.min_inflow)
    in_train = np.isin(fold, train_folds)
    in_test = np.isin(fold, test_folds)
    base_all_test = float(y2[labeled & in_test].mean()) if (labeled & in_test).any() else float("nan")
    base_curve_test = float(y2[labeled & stage & in_test].mean()) if (labeled & stage & in_test).any() else float("nan")
    base_cand_test = float(y2[cand & in_test].mean()) if (cand & in_test).any() else float("nan")
    base_all_train = float(y2[labeled & in_train].mean()) if (labeled & in_train).any() else float("nan")
    log(f"rows {t.n:,}; labeled {int(labeled.sum()):,}; curve {int((labeled & stage).sum()):,}; candidates {int(cand.sum()):,} (ETH, curve, reconstructed, age ≤ {a.age_max_s // 3600} h, inflow ≥ {a.min_inflow}); test base rate {pct(base_all_test, 2)}")

    # ---- walk-forward model over every fold >= 1 ----
    targets = {"p2x": y2, "p50": y50, "pdd50": ydd}
    log("walk-forward model")
    wf = walk_forward(t, fold, list(range(1, n_folds)), train_mask, targets, a.seed, log)
    p = wf["p2x"]
    idx_train = np.where(in_train & (fold >= 1))[0]
    idx_test = np.where(in_test)[0]
    idx_train = idx_train[np.argsort(t.ts[idx_train], kind="stable")]
    idx_test = idx_test[np.argsort(t.ts[idx_test], kind="stable")]

    # ---- rules baselines ----
    ur_mask, score = under_radar_mask(t)
    ur_test = select_alerts(t, np.where(ur_mask, 1.0, 0.0), cand, idx_test, 1.0)
    rules_tab = fit_rules(t, np.where(in_train & labeled & stage)[0], y2, base_all_train if base_all_train == base_all_train else 0.05)
    prules = rules_p(t, rules_tab, base_all_train if base_all_train == base_all_train else 0.05)
    thr_rules = threshold_for_rate(t, prules, cand, idx_train, a.k)
    rules_test = select_alerts(t, prules, cand, idx_test, thr_rules)
    score_thr = threshold_for_rate(t, score, cand, idx_train, a.k)
    score_test = select_alerts(t, score, cand, idx_test, score_thr)

    # ---- model alerts: threshold chosen on the train folds (out-of-sample p there too) ----
    thr = threshold_for_rate(t, p, cand, idx_train, a.k)
    model_train = select_alerts(t, p, cand, idx_train, thr)
    model_test = select_alerts(t, p, cand, idx_test, thr)
    topk_test = top_k_per_hour(t, p, cand, idx_test, a.k)
    metrics = {
        "model_causal": alert_metrics(t, model_test, base_all_test, idx_test),
        "model_topk": alert_metrics(t, topk_test, base_all_test, idx_test),
        "rules_calibrated": alert_metrics(t, rules_test, base_all_test, idx_test),
        "radar_score": alert_metrics(t, score_test, base_all_test, idx_test),
        "under_radar": alert_metrics(t, ur_test, base_all_test, idx_test),
    }
    # precision at several alert rates (test), same causal rule
    rate_rows = []
    for per_hour in (1, 2, 3, 5, 8, 12):
        th = threshold_for_rate(t, p, cand, idx_train, per_hour)
        al = select_alerts(t, p, cand, idx_test, th)
        m = alert_metrics(t, al, base_all_test, idx_test)
        rate_rows.append([per_hour, f"{th:.3f}", m["alerts"], f"{m['per_hour']:.1f}", pct(m["precision_2x"]), (f"{m['lift']:.1f}×" if m["lift"] else "—"), fnum(m["lead_median_min"], 0), pct(m["catch_3x"])])

    # ---- calibration (test rows, candidates) ----
    calib_rows = []
    pt = p[idx_test]
    ct = cand[idx_test]
    yt = y2[idx_test]
    ok = ct & (pt == pt)
    if ok.sum() > 20:
        qs = np.quantile(pt[ok], np.linspace(0, 1, 11))
        for i in range(10):
            sel = ok & (pt >= qs[i]) & (pt <= qs[i + 1] if i == 9 else pt < qs[i + 1])
            if sel.sum():
                calib_rows.append([f"{qs[i]:.3f}–{qs[i + 1]:.3f}", int(sel.sum()), pct(float(pt[sel].mean())), pct(float(yt[sel].mean()))])

    # ---- final train-fold model for the artefact + importance on the test rows ----
    fit_idx = np.where(train_mask & in_train)[0]
    models: dict[str, Any] = {}
    importance: list[tuple[str, float]] = []
    if len(fit_idx) >= 200 and y2[fit_idx].sum() >= 10:
        Xf = fit_matrix(t.X[fit_idx])
        for k, y in targets.items():
            m = make_model(a.seed)
            m.fit(Xf, y[fit_idx])
            models[k] = m
        try:
            from sklearn.inspection import permutation_importance

            te = np.where(cand & in_test)[0]
            if len(te) > 5000:
                te = np.random.default_rng(a.seed).choice(te, 5000, replace=False)
            if len(te) >= 200 and y2[te].sum() >= 5:
                pi = permutation_importance(models["p2x"], t.X[te], y2[te], scoring="average_precision", n_repeats=3, random_state=a.seed)
                importance = sorted(zip(t.features, pi.importances_mean), key=lambda x: -x[1])
        except Exception as e:  # noqa: BLE001
            log(f"importance skipped: {e}")

    # ---- exit policy search on TRAIN alerts, evaluation on TEST alerts ----
    launches = {r[0]: {"ts": r[1], "deployer": r[2], "threshold": int(r[3]) if r[3] else None} for r in store.db.execute("SELECT token, ts, deployer, threshold FROM launches")}
    grads = {r[0]: r[1] for r in store.db.execute("SELECT token, ts FROM graduations WHERE ts IS NOT NULL")}
    need = {t.token[i] for i in model_train + model_test + ur_test}
    intel_all = launch_intel.intel_for(store, need)
    paths: dict[str, CoinPath] = {}
    minute_rows: dict[str, dict[int, dict[str, Any]]] = defaultdict(dict)
    cols = ("inflow_10m", "smart_sellers_10m", "smart_buyers_10m")

    def path_of(tok: str) -> CoinPath:
        if tok not in paths:
            paths[tok] = CoinPath(store, tok, launches.get(tok), intel_all.get(tok) or launch_intel.intel_for(store, {tok}).get(tok), grads.get(tok))
            for r in fdb.execute(f"SELECT minute, {','.join(cols)} FROM coin_minutes WHERE run_id=? AND token=?", (run["run_id"], tok)):
                minute_rows[tok][r[0]] = dict(zip(cols, r[1:]))
        return paths[tok]

    sim_index: dict[int, dict[str, Any]] = {}

    def sim_all(alerts: list[int], policy: dict[str, Any], keep: bool = False) -> list[tuple[str, dict[str, Any]]]:
        out = []
        for i in alerts:
            tok = t.token[i]
            r = simulate(path_of(tok), int(t.ts[i]), policy, minute_rows[tok], a.latency_s, SIZE_WEI, hi)
            if r is not None:
                out.append((tok, r))
                if keep:
                    sim_index[i] = r
        return out

    min_trades = min(a.min_policy_trades, max(5, len(model_train) // 2))
    log(f"exit-policy grid: {len(grid_policies())} policies on {len(model_train)} train alerts (min {min_trades} simulated trades)")
    grid_res = []
    for pol in grid_policies():
        st = policy_stats(sim_all(model_train, pol))
        if st["n"] >= min_trades:
            grid_res.append((st["expectancy"], pol, st))
    grid_res.sort(key=lambda x: -x[0])
    chosen = grid_res[0][1] if grid_res else NAMED["tp80_half_trail25_sl30_t30"]
    chosen_train = grid_res[0][2] if grid_res else policy_stats(sim_all(model_train, chosen))
    test_res = sim_all(model_test, chosen, keep=True)
    chosen_test = policy_stats(test_res, n_boot=a.bootstrap, seed=a.seed)
    named_test = {name: policy_stats(sim_all(model_test, pol), n_boot=a.bootstrap, seed=a.seed) for name, pol in NAMED.items()}
    ur_policy_test = policy_stats(sim_all(ur_test, chosen), n_boot=a.bootstrap, seed=a.seed)

    # payoff of the chosen policy on the train alerts, conditional on the label the model predicts (for the verdict's EV)
    tr_keep: dict[int, dict[str, Any]] = {}
    for i in model_train:
        r = simulate(path_of(t.token[i]), int(t.ts[i]), chosen, minute_rows[t.token[i]], a.latency_s, SIZE_WEI, hi)
        if r is not None:
            tr_keep[i] = r
    run_pnl = [r["pnl"] for i, r in tr_keep.items() if y2[i] == 1]
    oth_pnl = [r["pnl"] for i, r in tr_keep.items() if y2[i] == 0]
    payoff = {
        "runner_mean": (sum(run_pnl) / len(run_pnl)) if run_pnl else 0.012,
        "other_mean": (sum(oth_pnl) / len(oth_pnl)) if oth_pnl else -0.006,
        "runner_n": len(run_pnl), "other_n": len(oth_pnl), "size_eth": SIZE_ETH,
        "note": "mean simulated pnl per 0.02 ETH trade of the chosen policy on the train alerts, split by whether the coin did reach 2x within 30 min; defaults when a side has no trades",
    }
    rules_dd = fit_rules(t, np.where(in_train & labeled & stage)[0], ydd, float(ydd[in_train & labeled & stage].mean()) if (in_train & labeled & stage).any() else 0.1)

    # ---- per-alert / per-coin tables (test) ----
    g30, dd30, tpk = t.label("gain_30"), t.label("dd_30"), t.label("t_peak_30")
    from ..api import queries

    labels = {k: (v["symbol"] if v.get("symbol") not in (None, "", "?") else v.get("short") or k[:10]) for k, v in queries.token_labels(store, need).items()} if need else {}
    sim_by_alert = sim_index
    alert_rows = []
    for i in model_test:
        r = sim_by_alert.get(i)
        alert_rows.append([utc(t.ts[i])[-5:], labels.get(t.token[i]) or t.token[i][:10], f"{p[i]:.3f}", int(inflow[i]), f"{int(age[i]) // 60} min", pct(float(g30[i])), pct(float(dd30[i])), int(tpk[i]) if tpk[i] == tpk[i] else "—", int(y2[i]), (f"{r['pnl']:+.4f} ({r['exit']})" if r else "n/a")])
    first_alert: dict[str, int] = {}
    for i in model_test:
        first_alert.setdefault(t.token[i], i)
    coin_rows = []
    for tok, i in first_alert.items():
        r = sim_by_alert.get(i)
        coin_rows.append([utc(t.ts[i])[-5:], labels.get(tok) or tok[:10], f"{p[i]:.3f}", int(inflow[i]), pct(float(g30[i])), pct(float(t.label('gain_60')[i])) if t.label("gain_60")[i] == t.label("gain_60")[i] else "—", int(y2[i]), (f"{r['pnl']:+.4f}" if r else "n/a")])
    fc_hits = sum(1 for tok, i in first_alert.items() if y2[i] == 1)

    # ---- artefacts ----
    created = datetime.now(timezone.utc)
    model_path = None
    if models and not a.no_model:
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        model_path = MODEL_DIR / f"edge-{created:%Y%m%d}.pkl"
        with open(model_path, "wb") as fh:
            pickle.dump({"version": 1, "created": created.isoformat(), "db": a.db, "features": t.features, "models": models, "sklearn": __import__("sklearn").__version__}, fh)
    mc = metrics["model_causal"]
    config = {
        "version": 1,
        "created": created.isoformat(),
        "db": a.db,
        "range": [lo, hi],
        "fold_s": fold_s,
        "train_folds": a.train_days,
        "test_folds": a.test_days,
        "features": t.features,
        "model_file": str(model_path) if model_path else None,
        "targets": {"p2x": "max gain over the next 30 min >= 100% or graduation within 30 min", "p50": "max gain 30 min >= 50%", "pdd50": "max drawdown 30 min <= -50%"},
        "thresholds": {"p_enter": thr, "alerts_per_hour_target": a.k, "p_wait": max(0.0, thr * 0.6)},
        "candidate": {"stage": "curve", "quote": "ETH", "age_max_s": a.age_max_s, "min_age_s": a.min_age_s, "min_inflow_10m": a.min_inflow},
        "base_rates": {"test_all": base_all_test, "test_curve": base_curve_test, "test_candidates": base_cand_test, "train_all": base_all_train},
        "rules_calibration": {"buckets": RULE_BUCKETS, "cells": rules_tab, "threshold": thr_rules, "note": "runner rate per inflow/age/quality/momentum cell on the train folds, shrunk to the base rate with weight 10"},
        "rules_calibration_dd": {"buckets": RULE_BUCKETS, "cells": rules_dd, "note": "rate of a -50% drawdown within 30 min per cell, same buckets"},
        "exit_plan": {**chosen, "tp": [list(chosen["tp"])] if chosen.get("tp") else [], "key": policy_key(chosen)},
        "payoff": payoff,
        "fees": {"curve_fee_bps": F.FEE_BPS, "creator_tax": "per coin from fee columns, else fitted from the first trades, else 100 bps (estimated)", "snipe_tax": "9900 >> (elapsed*14//3) bps inside the first 3 s", "impact": "constant-product fill on the reconstructed reserves", "latency_s": a.latency_s, "size_eth": SIZE_ETH},
        "metrics": {"test": metrics, "policy_test": {k: v for k, v in chosen_test.items() if k != "exits"} | {"exits": chosen_test.get("exits")}, "policy_train": {k: v for k, v in chosen_train.items()}},
        "importance": importance[:20],
        "note": "Out-of-sample numbers from stampede edge; recomputed whenever it runs. Not a return anyone earned.",
    }
    if not a.no_config:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(config, indent=1, default=float))

    # ---- report ----
    md: list[str] = []
    md.append("# Is there an edge? Walk-forward runner model, alert precision and a simulated exit policy")
    md.append("")
    md.append(f"Generated {created:%Y-%m-%d %H:%M} UTC by `stampede edge` from `{a.db}` (coin_minutes run {run['run_id']}).")
    md.append("")
    md.append("## Targets (the programme goal, measured here)")
    md.append("")
    targets_rows = [
        ["precision of the alerts (≥ 2× within 30 min)", "≥ 30%", pct(mc["precision_2x"]), "yes" if (mc["precision_2x"] or 0) >= 0.30 else "no"],
        ["lift over the coin-minute base rate", "≥ 5×", f"{mc['lift']:.1f}×" if mc["lift"] else "—", "yes" if (mc["lift"] or 0) >= 5 else "no"],
        ["alert precedes the peak (median minutes, hits)", "≥ 2 min", fnum(mc["lead_median_min"], 0), "yes" if (mc["lead_median_min"] or 0) >= 2 else "no"],
        ["policy expectancy per 0.02 ETH trade (test)", "> 0 with CI lower bound > 0", f"{chosen_test.get('expectancy', 0):+.4f} ETH, CI [{chosen_test.get('ci95', (0, 0))[0]:+.4f}, {chosen_test.get('ci95', (0, 0))[1]:+.4f}]" if chosen_test.get("n") else "no trades", "yes" if chosen_test.get("n") and chosen_test.get("ci95", (0,))[0] > 0 else "no"],
        ["catch rate of coins that did ≥ 3× within 60 min", "reported", f"{pct(mc['catch_3x'])} ({mc['caught']} of {mc['catch_n']})", "—"],
    ]
    md += md_table(["target", "bar", "measured (test folds)", "met"], targets_rows)
    md.append("## Setup")
    md.append("")
    md.append(f"- Data: {run['rows']:,} coin-minutes with a trade, {utc(lo)}–{utc(hi)} UTC ({span / 3600:.1f} h). Labels use the per-minute median trade price in quote units; a coin-minute counts when its 30-min horizon is inside the range ({int(labeled.sum()):,} rows).")
    md.append(f"- Folds: {n_folds} × {fold_s / 60:.0f} min{' — the store is shorter than the requested ' + str(a.train_days) + ' + ' + str(a.test_days) + ' days, so a fold is the range divided by ' + str(n_folds) + '; on the 14-day store a fold is one day' if compressed else ' (days)'}. Train folds 0–{a.train_days - 1}, test folds {a.train_days}–{n_folds - 1}. Every fold ≥ 1 is scored by a model trained on earlier folds only (expanding window); the alert threshold and the exit policy are chosen on the train folds and applied unchanged to the test folds.")
    md.append(f"- Model: `HistGradientBoostingClassifier` (200 iterations, 15 leaves, lr 0.06) on {len(t.features)} features (missing values native), trained on curve-stage rows of every quote asset. Target: max gain ≥ 100% within 30 min or graduation within 30 min; secondary models for ≥ 50% and for a −50% drawdown.")
    md.append(f"- Candidates for alerts: curve stage, ETH-quoted, reconstructed reserves, age {a.min_age_s} s – {a.age_max_s // 3600} h, rotation inflow ≥ {a.min_inflow} wallet in 10 min ({int((cand & in_test).sum()):,} test rows). Alert rule: p ≥ {thr:.3f} (the threshold that gave {a.k} alerts/hour on the train folds), one alert per coin per 30 min.")
    md.append(f"- Base rates on the test folds: all coin-minutes {pct(base_all_test, 2)}, curve-stage {pct(base_curve_test, 2)}, candidates {pct(base_cand_test, 2)}. Lift is against the first.")
    md.append(f"- Fees in the simulation: 1% curve fee, creator tax per coin ({'from fee columns' if any(t.meta['fees_known']) else 'fitted from the first trades, else 100 bps — estimated, this store has no fee columns'}), snipe tax when the entry is < 3 s after launch, price impact of 0.02 ETH by the constant-product fill on the reconstructed reserves, {a.latency_s} s entry latency. Exits at graduation are approximated by the last curve state.")
    md.append("")
    md.append("## Alerts on the test folds")
    md.append("")
    rows = []
    for name, m in metrics.items():
        rows.append([name, m["alerts"], m["coins"], f"{m['per_hour']:.1f}", pct(m["precision_2x"]), pct(m["precision_50"]), (f"{m['lift']:.1f}×" if m["lift"] else "—"), fnum(m["lead_median_min"], 0), pct(m["lead_ge_2_share"]), pct(m["catch_3x"]), pct(m["median_gain_30"]), pct(m["median_dd_30"])])
    md += md_table(["rule", "alerts", "coins", "/h", "≥ 2× in 30 min", "≥ +50%", "lift", "lead (min)", "lead ≥ 2 min", "catch 3×/60", "median max gain", "median drawdown"], rows)
    md.append("`model_causal` is the deployable rule (threshold fixed on train). `model_topk` picks the k best rows of each clock hour with hindsight inside the hour and is an upper bound, not a rule. `rules_calibrated` is the no-model fallback the verdict uses (cell table below). `radar_score` thresholds the stage-1 score the same way. `under_radar` is the live alert rule.")
    md.append("")
    md.append("### Precision at other alert rates (model, causal thresholds from the train folds)")
    md.append("")
    md += md_table(["alerts/h wanted", "threshold p", "test alerts", "test /h", "≥ 2×", "lift", "lead (min)", "catch 3×"], rate_rows)
    md.append("### Calibration on the test candidates (deciles of p)")
    md.append("")
    md += md_table(["p range", "rows", "mean p", "realized ≥ 2×"], calib_rows) if calib_rows else ["(too few scored candidates)", ""]
    md.append("### Feature importance (permutation, average precision, test candidates)")
    md.append("")
    md += md_table(["feature", "Δ average precision"], [[f, f"{v:+.4f}"] for f, v in importance[:15]]) if importance else ["(not computed)", ""]
    md.append("## Exit policy")
    md.append("")
    md.append(f"Grid of {len(grid_policies())} policies simulated on the {len(model_train)} train-fold alerts (TP ladder × trailing stop × stop loss × time exit × inflow-dies trigger × smart-wallets-exit trigger); chosen by expectancy with ≥ {min_trades} simulated trades{' (default policy kept: too few train alerts)' if not grid_res else ''}. Then applied unchanged to the test alerts.")
    md.append("")
    md.append(f"Chosen: `{policy_key(chosen)}`.")
    md.append("")
    top_rows = [[policy_key(pol), st["n"], f"{st['expectancy']:+.4f}", pct(st["hit_rate"]), f"{st['max_drawdown']:+.3f}"] for _, pol, st in grid_res[:8]]
    md += md_table(["policy (train alerts)", "trades", "expectancy ETH", "hit rate", "max DD ETH"], top_rows) if top_rows else ["(no policy reached the minimum number of trades on the train alerts)", ""]

    def stat_row(name: str, st: dict[str, Any]) -> list[Any]:
        if not st.get("n"):
            return [name, 0, "—", "—", "—", "—", "—", "—"]
        ci = st.get("ci95")
        return [name, st["n"], f"{st['expectancy']:+.4f} ({st['expectancy_pct']:+.1%})", (f"[{ci[0]:+.4f}, {ci[1]:+.4f}]" if ci else "—"), pct(st["hit_rate"]), f"{st['total']:+.3f}", f"{st['max_drawdown']:+.3f}", ", ".join(f"{k} {v}" for k, v in st["exits"].items())]

    md.append("### Test folds (0.02 ETH per trade, sequential equity curve, bootstrap by coin × %d)" % a.bootstrap)
    md.append("")
    md += md_table(["policy on the test alerts", "trades", "expectancy per trade", "95% CI (mean)", "hit rate", "total ETH", "max DD ETH", "exits"], [stat_row("chosen (model alerts)", chosen_test)] + [stat_row(f"{n} (model alerts)", st) for n, st in named_test.items()] + [stat_row("chosen (under_radar alerts)", ur_policy_test)])
    md.append("## Alerts, one row per alert (test folds)")
    md.append("")
    md += md_table(["UTC", "coin", "p", "inflow", "age", "max gain 30", "drawdown 30", "peak (min)", "≥ 2×", "sim pnl ETH (exit)"], alert_rows[:60])
    if len(alert_rows) > 60:
        md.append(f"… {len(alert_rows) - 60} more.")
        md.append("")
    md.append(f"## One row per coin (first alert; {len(first_alert)} coins, {fc_hits} reached 2× within 30 min = {pct(fc_hits / len(first_alert)) if first_alert else '—'})")
    md.append("")
    md += md_table(["UTC", "coin", "p", "inflow", "max gain 30", "max gain 60", "≥ 2×", "sim pnl ETH"], coin_rows[:60])
    md.append("## Reading this honestly")
    md.append("")
    md.append(f"- {'The range is ' + f'{span / 3600:.1f} hours, so the folds are ' + f'{fold_s / 60:.0f}-minute slices, not days; the model for early folds saw minutes of data. Treat every number here as a pipeline check; the 14-day store is the measurement.' if compressed else 'Folds are days; the model for day d saw days < d only.'}")
    md.append(f"- Alerts on the test folds: {mc['alerts']} on {mc['coins']} coins. With a base rate of {pct(base_all_test, 2)} and {mc['alerts']} alerts the 95% binomial interval of the precision is roughly ±{pct(1.96 * math.sqrt(max(1e-9, (mc['precision_2x'] or 0.1) * (1 - (mc['precision_2x'] or 0.1)) / max(1, mc['alerts']))))}; the bootstrap CI of the policy is wide for the same reason.")
    md.append("- Signal minutes are autocorrelated (a coin stays a candidate while its inflow lasts); the per-coin table is the fairer count and the bootstrap resamples coins, not alerts.")
    md.append("- The simulation buys the coin nobody else was buying at that second: the observed trades are replayed unchanged, our 0.02 ETH moves only our own fills. Sells at graduation are approximated by the last curve state; snipe-window entries pay the snipe tax; MEV, failed transactions and gas are not modelled.")
    md.append("- Wallet features are walk-forward: rotation quality uses outcomes known at the decision minute; stage-2 smart-wallet features come from the first half of the range and are unknown (missing) before it.")
    md.append("- 'Runner' is a price path; nobody earned these numbers. If the edge is not in the table, it is not there — the numbers are recomputed whenever this command runs.")
    md.append("")
    md.append("## Commands")
    md.append("")
    md.append("```")
    md.append(f"stampede edge-features --db {a.db}")
    md.append(f"stampede edge --db {a.db} --train-days {a.train_days} --test-days {a.test_days} --out {a.out or 'docs/RESEARCH-EDGE.md'}")
    md.append("# 14-day store, once data/backfill-14d.log ends with READY:")
    md.append("stampede fx --db data/research-14d.sqlite --days 16")
    md.append("stampede launch-intel --db data/research-14d.sqlite --horizon 3600 --resolve-quotes --out docs/RESEARCH-LAUNCHES.md")
    md.append("stampede traders --db data/research-14d.sqlite --fees strict --out docs/RESEARCH-TRADERS.md")
    md.append("stampede edge-features --db data/research-14d.sqlite")
    md.append("stampede edge --db data/research-14d.sqlite --train-days 7 --test-days 7 --out docs/RESEARCH-EDGE.md")
    md.append("```")
    md.append("")
    md.append(f"Artefacts: model `{model_path or 'not written'}` (gitignored), config `{CONFIG_PATH.relative_to(CONFIG_PATH.parents[2]) if not a.no_config else 'not written'}`. Compute time {time.time() - t0:.0f} s (features {run['stats'].get('total_s', '?')} s).")
    text = "\n".join(md)
    if a.out:
        Path(a.out).write_text(text)
        log(f"written {a.out}")
    else:
        print(text)
    if fdb is not store.db:
        fdb.close()
    store.close()
    return config


def build_parser(ap: argparse.ArgumentParser) -> argparse.ArgumentParser:
    ap.add_argument("--db", required=True)
    ap.add_argument("--train-days", type=int, default=7)
    ap.add_argument("--test-days", type=int, default=7)
    ap.add_argument("--fold-s", type=int, default=86400, help="fold length in seconds (a day); compressed automatically on a shorter store")
    ap.add_argument("--out", default="", help="markdown report (default: stdout)")
    ap.add_argument("--features-db", default=None, help="sqlite holding coin_minutes when not inside --db")
    ap.add_argument("--rebuild", action="store_true", help="rebuild coin_minutes even if a run exists")
    ap.add_argument("--k", type=float, default=5.0, help="alerts per hour to calibrate the threshold on the train folds")
    ap.add_argument("--age-max-s", type=int, default=4 * 3600)
    ap.add_argument("--min-age-s", type=int, default=60, help="candidate rows need this age at the decision instant (60 = not the launch minute)")
    ap.add_argument("--min-inflow", type=int, default=1, help="candidate rows need at least this rotation inflow (10 min)")
    ap.add_argument("--latency-s", type=int, default=2, help="seconds between the decision instant and the simulated fill")
    ap.add_argument("--bootstrap", type=int, default=1000)
    ap.add_argument("--min-policy-trades", type=int, default=20)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--no-model", action="store_true", help="do not write the model pickle")
    ap.add_argument("--no-config", action="store_true", help="do not write stampede/signals/edge-config.json")
    return ap


def main(argv: list[str] | None = None) -> int:
    a = build_parser(argparse.ArgumentParser(prog="stampede edge", description=__doc__.split("\n\n")[0])).parse_args(argv)
    run(a)
    return 0


if __name__ == "__main__":
    sys.exit(main())
