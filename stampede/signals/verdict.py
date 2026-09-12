"""Verdict (stage 4): ENTER / WAIT / AVOID for one coin from its radar-row features.

    verdict(features) -> {action, p_2x_30m, p_minus50_30m, ev_per_trade_quote, entry_window, exit_plan, size, reasons}

`features` uses the names of `research.features.MODEL_FEATURES` (the radar row carries the same names; missing keys
are unknown). The probability comes from the walk-forward model pickle when one exists (`edge-config.json` names it,
else the newest `data/models/edge-*.pkl`, else `STAMPEDE_EDGE_MODEL`), otherwise from the calibrated rule cells
(runner rate per inflow / age / quality / momentum bucket measured on the train folds) that ship in
`stampede/signals/edge-config.json`. The exit plan and the payoff come from the same config; the size from
`signals.risk`. Pure and cheap: no model < 1 ms, with the model a few ms (use `verdicts()` for many rows at once).
"""
from __future__ import annotations

import glob
import json
import math
import os
import pickle
from pathlib import Path
from typing import Any

from . import risk

# The model is called for a handful of rows every ~100 ms inside the realtime engine. libomp's default wait policy keeps
# its worker threads spinning for 200 ms after every parallel region, which burned a whole core in the live engine
# (docs/TRACK-RECORD.md run notes); passive waiting costs nothing here because the batched call runs single-threaded anyway.
os.environ.setdefault("OMP_WAIT_POLICY", "PASSIVE")
os.environ.setdefault("KMP_BLOCKTIME", "0")

CONFIG_PATH = Path(__file__).resolve().parent / "edge-config.json"
ENTER, WAIT, AVOID = "ENTER", "WAIT", "AVOID"
RULE_BUCKETS: dict[str, list[tuple[float, float]]] = {
    "inflow": [(0, 0), (1, 2), (3, 7), (8, 19), (20, 39), (40, 10**9)],
    "age": [(0, 900), (900, 3600), (3600, 4 * 3600), (4 * 3600, 10**12)],
    "quality": [(-1.0, -0.5), (-0.5, 0.35), (0.35, 0.5), (0.5, 2.0)],  # -1 = unknown
    "mom": [(-10.0, -0.2), (-0.2, 0.2), (0.2, 1.0), (1.0, 1e9)],
}
DEFAULT_PAYOFF = {"runner_mean": 0.012, "other_mean": -0.006, "size_eth": 0.02}
DEFAULT_EXIT = {"tp": [[0.8, 0.5]], "trail": 0.25, "sl": 0.3, "time_s": 1800, "inflow_dies": False, "smart_exit": False}
_config_cache: dict[str, dict[str, Any]] = {}
_model_cache: dict[str, Any] = {}


# ---- config / model ---------------------------------------------------------------------------------------------------
def load_config(path: Path | str | None = None) -> dict[str, Any]:
    p = Path(path) if path else CONFIG_PATH
    key = str(p)
    if key not in _config_cache:
        _config_cache[key] = json.loads(p.read_text()) if p.exists() else {}
    return _config_cache[key]


def model_path(config: dict[str, Any] | None = None) -> Path | None:
    env = os.environ.get("STAMPEDE_EDGE_MODEL")
    if env:
        return Path(env) if Path(env).exists() else None
    cfg = config if config is not None else load_config()
    mf = cfg.get("model_file")
    if mf and Path(mf).exists():
        return Path(mf)
    files = sorted(glob.glob("data/models/edge-*.pkl"))
    return Path(files[-1]) if files else None


def load_model(path: Path | str | None = None, config: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """{'features': [...], 'models': {'p2x': clf, 'p50': clf, 'pdd50': clf}} or None when no pickle is usable."""
    p = Path(path) if path else model_path(config)
    if p is None:
        return None
    key = str(p)
    if key in _model_cache:
        return _model_cache[key]
    try:
        with open(p, "rb") as fh:
            m = pickle.load(fh)
        if not isinstance(m, dict) or "models" not in m or "features" not in m:
            m = None
    except Exception:  # noqa: BLE001 - a stale pickle must never break the radar
        m = None
    _model_cache[key] = m
    return m


def reset_caches() -> None:
    _config_cache.clear()
    _model_cache.clear()


# ---- rules fallback ---------------------------------------------------------------------------------------------------
def _bucket(v: float, edges: list[tuple[float, float]]) -> int:
    for i, (lo, hi) in enumerate(edges):
        if lo <= v < hi or (i == len(edges) - 1 and v >= lo):
            return i
    return 0


def _num(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def rule_cell(inflow: float | None, age: float | None, quality: float | None, mom: float | None, buckets: dict[str, Any] | None = None) -> str:
    b = buckets or RULE_BUCKETS
    return "%d/%d/%d/%d" % (
        _bucket(inflow if inflow is not None else 0.0, b["inflow"]),
        _bucket(age if age is not None else 10**11, b["age"]),
        _bucket(quality if quality is not None else -1.0, b["quality"]),
        _bucket(mom if mom is not None else 0.0, b["mom"]),
    )


def rules_p(features: dict[str, Any], config: dict[str, Any], key: str = "rules_calibration") -> tuple[float, str]:
    """Calibrated cell rate for the features (train-fold runner rate of the cell), and the cell key."""
    rc = config.get(key) or {}
    cells = rc.get("cells") or {}
    base = (config.get("base_rates") or {}).get("train_all") or 0.05
    cell = rule_cell(_num(features.get("inflow_10m")), _num(features.get("age_s")), _num(features.get("quality")), _num(features.get("mom_10m")), rc.get("buckets"))
    c = cells.get(cell)
    return (float(c["p"]) if c else float(base)), cell


# ---- verdict ----------------------------------------------------------------------------------------------------------
def feature_vector(features: dict[str, Any], names: list[str]) -> list[float]:
    out = []
    for n in names:
        v = features.get(n)
        if isinstance(v, bool):
            v = float(v)
        f = _num(v)
        out.append(float("nan") if f is None else f)
    return out


def _hard_avoid(f: dict[str, Any], config: dict[str, Any]) -> list[str]:
    cand = config.get("candidate") or {}
    out: list[str] = []
    if f.get("stage") not in (None, "curve"):
        out.append(f"stage {f.get('stage')}: the plan is for the curve")
    age = _num(f.get("age_s"))
    if age is not None and age > float(cand.get("age_max_s") or 4 * 3600):
        out.append(f"age {int(age // 60)} min > {int(cand.get('age_max_s') or 14400) // 60} min: older coins fall below the base rate")
    ssz = _num(f.get("since_snipe_zero_s"))
    if ssz is not None and ssz < 0:
        out.append("inside the snipe-tax window")
    if f.get("launch_farm") in (1, True):
        out.append("launch farm")
    if (_num(f.get("insider_sells_10m")) or 0) > 0 or (_num(f.get("dev_sold_10m")) or 0) > 0:
        out.append("deployer / exempt wallet sold in the last 10 min")
    if f.get("is_eth") is False or (f.get("quote_symbol") and f.get("quote_symbol") != "ETH" and f.get("is_eth") is None):
        out.append(f"quote {f.get('quote_symbol') or 'non-ETH'}: the policy was measured on ETH-quoted curves")
    return out


def _reasons(f: dict[str, Any], p2x: float, p50: float | None, pdd: float | None, source: str, cell: str | None, config: dict[str, Any]) -> list[str]:
    r = [f"p(≥2× in 30 min) {p2x:.0%} from {source}" + (f" cell {cell}" if cell else "")]
    if pdd is not None:
        r.append(f"p(−50% in 30 min) {pdd:.0%}")
    inflow = _num(f.get("inflow_10m"))
    if inflow is not None:
        acc = _num(f.get("accel"))
        r.append(f"inflow {int(inflow)} wallets / 10 min" + (f", ×{acc:.1f} vs before" if acc is not None and acc > 0 else ""))
    q = _num(f.get("quality"))
    if q is not None:
        r.append(f"rotation-wallet quality {q:.2f}")
    sm = _num(f.get("smart_inflow"))
    if sm:
        r.append(f"{int(sm)} top-decile trader(s) among the inflow")
    age = _num(f.get("age_s"))
    if age is not None:
        r.append(f"age {int(age // 60)} min")
    sr = _num(f.get("sell_ratio_10m"))
    if sr is not None and sr >= 1.0 and (_num(f.get("sells_10m")) or 0) >= 3:
        r.append(f"sells {sr:.1f}× the buys (10 min)")
    dev = _num(f.get("dev_buy_share"))
    if dev is not None and dev > 0.05:
        r.append(f"dev bought {dev:.0%} of supply")
    return r


def verdict(features: dict[str, Any], model: dict[str, Any] | None | bool = None, config: dict[str, Any] | None = None, *, open_positions: int = 0, day_pnl_quote: float = 0.0, bankroll: float | None = None) -> dict[str, Any]:
    """Pure verdict for one coin. `model=None` loads the default pickle when one exists; `model=False` forces the rules."""
    return verdicts([features], model, config, open_positions=open_positions, day_pnl_quote=day_pnl_quote, bankroll=bankroll)[0]


def verdicts(rows: list[dict[str, Any]], model: dict[str, Any] | None | bool = None, config: dict[str, Any] | None = None, *, open_positions: int = 0, day_pnl_quote: float = 0.0, bankroll: float | None = None) -> list[dict[str, Any]]:
    """Verdicts for many coins with one model call per target."""
    cfg = config if config is not None else load_config()
    m = None if model is False else (model if isinstance(model, dict) else load_model(config=cfg))
    p2 = p5 = pd = None
    source = "rules"
    if m is not None and rows:
        try:
            import numpy as np
            from threadpoolctl import threadpool_limits

            X = np.array([feature_vector(r, m["features"]) for r in rows], dtype=np.float32)
            # one row through a boosted ensemble is ~0.4 ms single-threaded and ~14 ms with the OpenMP pool warm-up
            with threadpool_limits(limits=1 if len(rows) < 500 else None):
                p2 = m["models"]["p2x"].predict_proba(X)[:, 1]
                p5 = m["models"]["p50"].predict_proba(X)[:, 1] if "p50" in m["models"] else None
                pd = m["models"]["pdd50"].predict_proba(X)[:, 1] if "pdd50" in m["models"] else None
            source = "model"
        except Exception:  # noqa: BLE001 - fall back to the rules rather than fail the radar
            p2 = p5 = pd = None
            source = "rules"
    thr = (cfg.get("thresholds") or {})
    p_enter = float(thr.get("p_enter") or 0.5)
    p_wait = float(thr.get("p_wait") or (p_enter * 0.6))
    payoff = cfg.get("payoff") or DEFAULT_PAYOFF
    run_mean = float(payoff.get("runner_mean", DEFAULT_PAYOFF["runner_mean"]))
    oth_mean = float(payoff.get("other_mean", DEFAULT_PAYOFF["other_mean"]))
    ref = float(payoff.get("size_eth") or 0.02)
    plan = cfg.get("exit_plan") or DEFAULT_EXIT
    lead = ((cfg.get("metrics") or {}).get("test") or {}).get("model_causal", {}).get("lead_median_min")
    rcfg = risk.load_config()
    out: list[dict[str, Any]] = []
    for i, f in enumerate(rows):
        cell = None
        if p2 is not None:
            p2x, p50, pdd = float(p2[i]), (float(p5[i]) if p5 is not None else None), (float(pd[i]) if pd is not None else None)
        else:
            p2x, cell = rules_p(f, cfg)
            pdd_c, _ = rules_p(f, cfg, "rules_calibration_dd") if cfg.get("rules_calibration_dd") else (None, None)
            p50, pdd = None, pdd_c
        ev_ref = p2x * run_mean + (1 - p2x) * oth_mean  # per `ref` quote of stake
        sz = risk.size(p2x, {"win_mean": run_mean, "loss_mean": oth_mean, "size_eth": ref}, rcfg, range_5m=_num(f.get("range_5m")), open_positions=open_positions, day_pnl_quote=day_pnl_quote, bankroll=bankroll)
        ev = ev_ref * (sz["quote"] / ref if ref and sz["quote"] else 1.0)
        avoid = _hard_avoid(f, cfg)
        exit_now = risk.exit_now(f, rcfg)
        reasons = _reasons(f, p2x, p50, pdd, source, cell, cfg)
        if avoid or exit_now:
            action = AVOID
            reasons = avoid + exit_now + reasons
        elif pdd is not None and pdd >= 0.5 and p2x < 0.6:
            action = AVOID
            reasons = [f"p(−50% in 30 min) {pdd:.0%} ≥ 50%"] + reasons
        elif p2x >= p_enter and ev_ref > 0:
            if sz["allowed"]:
                action = ENTER
            else:
                action = WAIT
                reasons = sz["reasons"] + reasons
        elif p2x >= p_wait:
            action = WAIT
            reasons = [f"p below the entry threshold {p_enter:.0%}"] + reasons
        else:
            action = WAIT
            reasons = ["no signal"] + reasons
        triggers = ["sell pressure spike", "deployer / exempt wallet selling", "liquidity drop", "graduation"]
        if plan.get("inflow_dies"):
            triggers.append("rotation inflow stops for 5 min")
        if plan.get("smart_exit"):
            triggers.append("top-decile wallets selling, none buying")
        exit_plan = {"tp": plan.get("tp") or [], "sl": plan.get("sl"), "trail": plan.get("trail"), "time_exit_s": plan.get("time_s") or plan.get("time_exit_s") or 1800, "triggers": triggers}
        exit_plan["text"] = risk.plan_text(exit_plan, sz if action == ENTER else None, f.get("quote_symbol") or "ETH")
        out.append({
            "action": action,
            "p_2x_30m": round(p2x, 4),
            "p_50_30m": round(p50, 4) if p50 is not None else None,
            "p_minus50_30m": round(pdd, 4) if pdd is not None else None,
            "ev_per_trade_quote": round(ev, 6),
            "ev_ref_quote": round(ev_ref, 6),
            "entry_window": {"valid_s": 120, "note": f"act within 2 min of the decision minute; the alert led the peak by a median of {int(lead)} min on the test folds" if lead else "act within 2 min of the decision minute"},
            "exit_plan": exit_plan,
            "size": {"quote": sz["quote"], "allowed": sz["allowed"], "capped_by": sz["capped_by"], "kelly": sz["kelly"], "vol_scale": sz["vol_scale"]},
            "reasons": reasons[:8],
            "source": source,
            "thresholds": {"p_enter": p_enter, "p_wait": p_wait},
        })
    return out
