"""Risk engine (stage 5): position size, exit-now triggers, daily stop and a plain-English exit plan.

Pure functions; the caller (engine, API, TUI) keeps the state (bankroll, open positions, today's PnL) and passes it in.
Sizes are in quote units (ETH for the simulated policy). Defaults live in `risk-config.json` next to this file and can
be overridden there without touching code.

Size = min(hard caps, fractional Kelly) × volatility scale:
- Kelly f* = (p·b − (1 − p)) / b with b = mean win / |mean loss| of the exit policy measured on the train alerts
  (`payoff` in edge-config.json), then `kelly_fraction` of it (half-Kelly by default) times the bankroll;
- hard caps: `cap_per_trade_quote` (0.02 ETH), `cap_bankroll_frac` of the bankroll (2%), `max_concurrent` open positions;
- volatility: a coin whose 5-minute range is above `vol_ref_range_5m` gets scaled down in proportion, never below
  `vol_min_scale`;
- daily stop: once the day's realized PnL is at or below −`daily_stop_frac` of the bankroll there are no new entries.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

CONFIG_PATH = Path(__file__).resolve().parent / "risk-config.json"
DEFAULTS: dict[str, Any] = {
    "bankroll_quote": 1.0,
    "cap_per_trade_quote": 0.02,
    "cap_bankroll_frac": 0.02,
    "max_concurrent": 3,
    "daily_stop_frac": 0.05,
    "kelly_fraction": 0.5,
    "vol_ref_range_5m": 0.30,
    "vol_min_scale": 0.25,
    "min_trade_quote": 0.005,
    "exit_now": {"sell_ratio_10m": 2.0, "out_in_10m": 1.5, "min_sells_10m": 4, "progress_drop_5m": -0.05, "graduation_progress": 0.97},
}
_cache: dict[str, dict[str, Any]] = {}


def load_config(path: Path | str | None = None) -> dict[str, Any]:
    p = Path(path) if path else CONFIG_PATH
    key = str(p)
    if key in _cache:
        return _cache[key]
    cfg = json.loads(json.dumps(DEFAULTS))
    if p.exists():
        user = json.loads(p.read_text())
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    _cache[key] = cfg
    return cfg


def kelly_fraction(p: float, win: float, loss: float) -> float:
    """Kelly fraction of the bankroll for a bet won with probability p paying `win` per unit and losing `|loss|`."""
    loss = abs(loss)
    if win <= 0 or loss <= 0 or p <= 0:
        return 0.0
    b = win / loss
    f = (p * b - (1 - p)) / b
    return max(0.0, min(1.0, f))


def size(p: float | None, payoff: dict[str, Any] | None, cfg: dict[str, Any] | None = None, *, range_5m: float | None = None, open_positions: int = 0, day_pnl_quote: float = 0.0, bankroll: float | None = None) -> dict[str, Any]:
    """Position size for one entry. `payoff`: {win_mean, loss_mean, size_eth} of the exit policy (edge-config.json)."""
    cfg = cfg or load_config()
    bank = bankroll if bankroll is not None else float(cfg["bankroll_quote"])
    reasons: list[str] = []
    out: dict[str, Any] = {"quote": 0.0, "allowed": False, "kelly": 0.0, "kelly_quote": 0.0, "vol_scale": 1.0, "capped_by": None, "reasons": reasons, "bankroll": bank}
    if daily_stop_hit(day_pnl_quote, cfg, bank):
        reasons.append(f"daily stop: today {day_pnl_quote:+.4f} ≤ −{cfg['daily_stop_frac']:.0%} of {bank:.3f} → no new entries")
        out["capped_by"] = "daily_stop"
        return out
    if open_positions >= int(cfg["max_concurrent"]):
        reasons.append(f"{open_positions} positions open ≥ max {cfg['max_concurrent']} → no new entries")
        out["capped_by"] = "max_concurrent"
        return out
    if p is None or p <= 0:
        reasons.append("no probability → no size")
        return out
    win = float((payoff or {}).get("win_mean") or 0.0)
    loss = float((payoff or {}).get("loss_mean") or 0.0)
    ref = float((payoff or {}).get("size_eth") or cfg["cap_per_trade_quote"])
    # payoffs are measured per `ref` quote of stake; express them per unit staked for Kelly
    k = kelly_fraction(p, win / ref if ref else 0.0, loss / ref if ref else 0.0)
    kq = k * float(cfg["kelly_fraction"]) * bank
    out["kelly"], out["kelly_quote"] = round(k, 4), kq
    caps = {"cap_per_trade": float(cfg["cap_per_trade_quote"]), "cap_bankroll_frac": float(cfg["cap_bankroll_frac"]) * bank, "kelly": kq}
    capped_by = min(caps, key=caps.get)
    q = caps[capped_by]
    scale = 1.0
    if range_5m is not None and range_5m > float(cfg["vol_ref_range_5m"]) > 0:
        scale = max(float(cfg["vol_min_scale"]), float(cfg["vol_ref_range_5m"]) / range_5m)
        reasons.append(f"5-min range {range_5m:.0%} > {cfg['vol_ref_range_5m']:.0%} → size × {scale:.2f}")
    q *= scale
    out["vol_scale"] = round(scale, 3)
    out["capped_by"] = capped_by
    if k <= 0:
        reasons.append(f"Kelly ≤ 0 at p={p:.2f} (win {win:+.4f} / loss {loss:+.4f} per {ref} stake) → no edge to size")
        out["quote"] = 0.0
        return out
    reasons.append(f"Kelly {k:.2f} × {cfg['kelly_fraction']} × bankroll {bank:.3f} = {kq:.4f}; caps {cfg['cap_per_trade_quote']} per trade, {cfg['cap_bankroll_frac']:.0%} of bankroll → {capped_by}")
    if q < float(cfg["min_trade_quote"]):
        reasons.append(f"size {q:.4f} below the minimum {cfg['min_trade_quote']} → skip")
        out["quote"] = 0.0
        return out
    out["quote"] = round(q, 6)
    out["allowed"] = True
    return out


def daily_stop_hit(day_pnl_quote: float, cfg: dict[str, Any] | None = None, bankroll: float | None = None) -> bool:
    cfg = cfg or load_config()
    bank = bankroll if bankroll is not None else float(cfg["bankroll_quote"])
    return day_pnl_quote <= -float(cfg["daily_stop_frac"]) * bank


def exit_now(features: dict[str, Any], cfg: dict[str, Any] | None = None) -> list[str]:
    """Reasons to leave a position immediately, from the coin's current features (all optional)."""
    cfg = cfg or load_config()
    x = cfg["exit_now"]
    out: list[str] = []
    sr, oi, sells = features.get("sell_ratio_10m"), features.get("out_in_10m"), features.get("sells_10m")
    if sr is not None and oi is not None and sells is not None and sr >= x["sell_ratio_10m"] and oi >= x["out_in_10m"] and sells >= x["min_sells_10m"]:
        out.append(f"sell pressure: {sells} sells, {sr:.1f}× the buys, {oi:.1f}× the quote in (10 min)")
    if (features.get("insider_sells_10m") or 0) > 0 or (features.get("dev_sold_10m") or 0) > 0:
        out.append("deployer / tax-exempt wallet selling (10 min)")
    p5 = features.get("progress_5m")
    if p5 is not None and p5 <= x["progress_drop_5m"]:
        out.append(f"liquidity drop: curve reserve −{abs(p5):.0%} of the threshold in 5 min")
    prog = features.get("progress")
    if prog is not None and prog >= x["graduation_progress"] and features.get("stage", "curve") == "curve":
        out.append(f"graduation at {prog:.0%}: curve sells stop at completion; sell before it or hold through to the pool")
    if features.get("stage") == "graduated":
        out.append("graduated: the plan was written for the curve")
    return out


def plan_text(exit_plan: dict[str, Any], sz: dict[str, Any] | None = None, quote_symbol: str = "ETH") -> list[str]:
    """The exit plan as instructions a person can follow by hand."""
    lines: list[str] = []
    if sz is not None:
        lines.append(f"size {sz['quote']:.4f} {quote_symbol} ({sz.get('capped_by') or 'n/a'})" if sz.get("allowed") else f"no entry: {'; '.join(sz.get('reasons') or ['not allowed'])}")
    tps = exit_plan.get("tp") or []
    if tps and isinstance(tps[0], (int, float)):
        tps = [tps]
    for gain, share in tps:
        lines.append(f"sell {share:.0%} at +{gain:.0%}")
    if exit_plan.get("trail") is not None:
        lines.append(f"then trail {exit_plan['trail']:.0%} below the high{' from entry' if not tps else ''}")
    if exit_plan.get("sl") is not None:
        lines.append(f"stop at −{exit_plan['sl']:.0%}")
    if exit_plan.get("time_exit_s") or exit_plan.get("time_s"):
        lines.append(f"out after {int(exit_plan.get('time_exit_s') or exit_plan.get('time_s')) // 60} min whatever the price")
    trig = exit_plan.get("triggers") or []
    if trig:
        lines.append("exit now on: " + ", ".join(str(t) for t in trig))
    return lines
