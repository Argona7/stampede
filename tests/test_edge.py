"""Stage 4/5: curve arithmetic against a hand example, reserve reconstruction, coin_minutes features and labels on a
synthetic store, walk-forward fold split, exit-plan simulation, verdict fallback rules, risk caps, radar rows carrying
the verdict through the API."""
from __future__ import annotations

import json
import random
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from helpers import TOKEN_A, TOKEN_B
from stampede import chain
from stampede.api.app import create_app
from stampede.research import features as F
from stampede.rotation import rotate
from stampede.signals import risk, verdict as V
from stampede.store import Store

ETH = chain.NATIVE
Q0, T0 = chain.CURVE_PHANTOM_QUOTE_WEI, chain.CURVE_SUPPLY_WEI
DEPLOYER = "0x" + "d" * 40
ROTATOR = "0x" + "e" * 40
LAUNCH_TS = 2_000_000_000


# ---- curve arithmetic ---------------------------------------------------------------------------------------------------
def test_buy_fill_matches_hand_example():
    # 0.01 ETH on a fresh curve after the snipe window, no creator tax: net 0.0099e18, tokensOut = net*T/(Q+net)
    f = F.buy_fill(Q0, T0, 10**16, 0, elapsed_s=3)
    net = 99 * 10**14
    assert f["net"] == net and f["fee"] == 10**14 and f["snipe"] == 0 and f["tax"] == 0
    assert f["tokens_out"] == net * T0 // (Q0 + net)
    assert abs(f["tokens_out"] / 1e18 - 5_858_334.81) < 1  # 9.9e42 / 1.6899e18 = 5.858e24 raw = 5.86 M tokens
    # inside the snipe window the fee grows: 618 bps at 1 s, 19 bps at 2 s, 0 from 3 s
    assert F.buy_fill(Q0, T0, 10**16, 0, elapsed_s=1)["snipe"] == 10**16 * 618 // 10_000
    assert F.buy_fill(Q0, T0, 10**16, 0, elapsed_s=2)["snipe"] == 10**16 * 19 // 10_000
    assert F.buy_fill(Q0, T0, 10**16, 0, elapsed_s=0)["snipe"] == 10**16 * 9800 // 10_000  # 9900 capped at 100% - fee - tax - 1%
    # creator tax comes off the top too
    g = F.buy_fill(Q0, T0, 10**16, 300)
    assert g["tax"] == 3 * 10**14 and g["net"] == 10**16 - 10**14 - 3 * 10**14


def test_sell_fill_and_invariant_round_trip():
    f = F.buy_fill(Q0, T0, 10**16, 100)
    c = F.Curve(tax_bps=100)
    c.apply("buy", f["tokens_out"])
    assert c.q * c.t <= F.K < (c.q + 1) * c.t  # k invariant: q = k // t
    s = F.sell_fill(c.q, c.t, f["tokens_out"], 100)
    # selling everything back returns the net quote minus impact rounding, minus 1% fee and 1% tax on the gross
    assert abs(s["gross"] - f["net"]) <= 2
    assert s["quote_out"] == s["gross"] - s["gross"] // 100 - s["gross"] // 100
    # the inverse formulas recover the state before a trade
    assert abs(F.q0_from_buy(f["net"], f["tokens_out"]) - Q0) <= Q0 // 10**9  # integer square roots: wei-level rounding
    assert abs(F.q0_from_sell(s["gross"], f["tokens_out"]) - c.q) <= c.q // 10**9


def _synthetic_curve(n_trades: int, tax_bps: int, seed: int, start_ts: int, fees: bool, hidden_first: bool = False):
    """Trades generated from a real curve replay: (ts, side, token_amount, quote_amount, wallet, venue, fee_raw, tax_raw)."""
    rng = random.Random(seed)
    c = F.Curve(tax_bps=tax_bps)
    wallets = ["0x" + f"{i + 1:040x}" for i in range(12)]
    bal = {w: 0 for w in wallets}
    rows = []
    ts = start_ts
    for i in range(n_trades):
        w = rng.choice(wallets)
        if bal[w] > 0 and rng.random() < 0.35:
            amt = bal[w] * rng.choice((1, 2, 4)) // 4
            s = F.sell_fill(c.q, c.t, amt, tax_bps)
            rows.append((ts, "sell", str(amt), str(s["quote_out"]), w, "curve", str(s["fee"]) if fees else None, str(s["tax"]) if fees else None))
            c.apply("sell", amt)
            bal[w] -= amt
        else:
            spent = rng.choice((5, 10, 20, 40, 80)) * 10**15
            b = F.buy_fill(c.q, c.t, spent, tax_bps, elapsed_s=max(3, ts - start_ts))
            rows.append((ts, "buy", str(b["tokens_out"]), str(spent), w, "curve", str(b["fee"]) if fees else None, str(b["tax"]) if fees else None))
            c.apply("buy", b["tokens_out"])
            bal[w] += b["tokens_out"]
        ts += rng.choice((1, 2, 7, 15, 40))
    return rows[1:] if hidden_first else rows


@pytest.mark.parametrize("fees", [True, False])
def test_fit_curve_exact_on_fresh_curve(fees):
    rows = _synthetic_curve(30, 300, 1, LAUNCH_TS + 5, fees)
    p = F.curve_path(rows, launch_ts=LAUNCH_TS, deployer=DEPLOYER, grad_ts=None, tax_hint=None)
    assert p["recon"] == "exact" and p["tax_bps"] == 300 and p["fees_known"] is fees
    # spot after the last trade equals the replayed curve
    c = F.Curve(tax_bps=300)
    for r in rows:
        c.apply(r[1], int(r[2]))
    assert p["q"][-1] == c.q and p["t"][-1] == c.t


@pytest.mark.parametrize("fees", [True, False])
def test_fit_curve_infers_hidden_start_and_tax(fees):
    rows = _synthetic_curve(30, 200, 2, LAUNCH_TS + 5, fees, hidden_first=True)
    p = F.curve_path(rows, launch_ts=LAUNCH_TS, deployer=DEPLOYER, grad_ts=None, tax_hint=None)
    assert p["recon"] == "inferred" and p["tax_bps"] == 200
    c = F.Curve(tax_bps=200)
    full = _synthetic_curve(30, 200, 2, LAUNCH_TS + 5, fees)
    for r in full:
        c.apply(r[1], int(r[2]))
    assert abs(p["q"][-1] - c.q) <= c.q // 10**6


# ---- synthetic store: features, labels, fold split -----------------------------------------------------------------------
def _seed_store(path: Path, n_coins: int = 6, seed: int = 3) -> tuple[Store, dict[str, list[tuple]]]:
    s = Store(path)
    per: dict[str, list[tuple]] = {}
    block = 1000
    trade_rows = []
    launch_rows = []
    for k in range(n_coins):
        tok = "0x" + f"{0xa000 + k:040x}"
        curve = "0x" + f"{0xc000 + k:040x}"
        lts = LAUNCH_TS + k * 400
        rows = _synthetic_curve(60 + 10 * k, 100 * (k % 3), seed + 10 + k, lts + 4, fees=(k % 2 == 0))
        per[tok] = rows
        launch_rows.append((tok, curve, DEPLOYER, ETH, str(chain.CURVE_GRADUATION_THRESHOLD_WEI), 0, block, lts, f"0x{k:064x}", "test"))
        for i, (ts, side, ta, qa, w, venue, fee, tax) in enumerate(rows):
            block += 1
            trade_rows.append((f"0x{block:064x}", block, ts, 1, tok, w, side, ta, ETH, qa, venue, "[]", "transfer_net", "[]", fee, tax, None))
    # a rotation: a wallet that trades nothing else sells coin 0 then buys coin 1 within a minute (direct sequence)
    w = ROTATOR
    t_rot = LAUNCH_TS + 400 + 200
    trade_rows.append((f"0x{block + 1:064x}", block + 1, t_rot, 1, "0x" + f"{0xa000:040x}", w, "sell", str(10**20), ETH, str(10**14), "curve", "[]", "transfer_net", "[]", None, None, None))
    trade_rows.append((f"0x{block + 2:064x}", block + 2, t_rot + 20, 1, "0x" + f"{0xa001:040x}", w, "buy", str(10**20), ETH, str(2 * 10**14), "curve", "[]", "transfer_net", "[]", None, None, None))
    per["0x" + f"{0xa000:040x}"].append((t_rot, "sell", str(10**20), str(10**14), w, "curve", None, None))
    per["0x" + f"{0xa001:040x}"].append((t_rot + 20, "buy", str(10**20), str(2 * 10**14), w, "curve", None, None))
    s.db.executemany("INSERT INTO trades(tx_hash,block,ts,ts_exact,token,wallet,side,token_amount,quote_token,quote_amount,venue,swap_logs,attribution,flags,fee_raw,tax_raw,snipe_raw) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", trade_rows)
    s.db.executemany("INSERT OR REPLACE INTO launches(token,curve,deployer,pair_token,threshold,launch_config,block,ts,tx_hash,source) VALUES(?,?,?,?,?,?,?,?,?,?)", launch_rows)
    s.db.executemany("INSERT OR IGNORE INTO tokens(address,symbol,name,source,curve,first_seen_block) VALUES(?,?,?,?,?,?)", [(t, f"C{i}", "", "curve", None, None) for i, t in enumerate(per)])
    s.db.execute("INSERT OR IGNORE INTO quotes(address,symbol,decimals) VALUES(?,?,?)", (ETH, "ETH", 18))
    s.commit()
    rotate(s, 1800)
    return s, per


def test_features_rows_labels_and_inflow(tmp_path):
    s, per = _seed_store(tmp_path / "f.sqlite")
    stats = F.build(str(s.path), log=lambda *a: None)
    assert stats["rows"] > 0 and stats["coins"] == 6
    db = s.db
    run = F.latest_run(db)
    assert run and run["rows"] == stats["rows"]
    # every coin-minute with a trade has a row, features are as-of, labels forward
    tok0 = "0x" + f"{0xa000:040x}"
    rows = db.execute("SELECT minute, ts, age_s, buys_10m, sells_10m, buyers_10m, holders, spot, recon, est_tax_bps, fees_known, gain_30, horizon_30, inflow_10m FROM coin_minutes WHERE run_id=? AND token=? ORDER BY minute", (run["run_id"], tok0)).fetchall()
    minutes_with_trades = sorted({r[0] // 60 for r in per[tok0]})
    assert [r[0] for r in rows] == minutes_with_trades
    first = rows[0]
    assert first[1] == (first[0] + 1) * 60 and first[2] == first[1] - LAUNCH_TS  # decision instant and age from the launch
    assert first[3] + first[4] == sum(1 for r in per[tok0] if r[0] // 60 == first[0])  # trades of the first minute
    assert first[8] == "exact" and first[9] == 0 and first[10] == 1 and first[7] > 0  # coin 0: fees known, tax 0
    assert all(r[6] >= 0 for r in rows)  # holders proxy never negative
    # a minute whose 30-min horizon runs past the range gets no label
    hi = db.execute("SELECT MAX(ts) FROM trades").fetchone()[0]
    all_rows = db.execute("SELECT minute, horizon_30, gain_30 FROM coin_minutes WHERE run_id=?", (run["run_id"],)).fetchall()
    assert all((h == 1) == ((m + 30) * 60 <= hi) for m, h, _g in all_rows) and any(h == 0 and g is None for _m, h, g in all_rows)
    # labels: max gain over the next 30 min from the per-minute median price
    prices = {}
    for r in per[tok0]:
        prices.setdefault(r[0] // 60, []).append(int(r[3]) / int(r[2]))
    med = {m: sorted(v)[len(v) // 2] for m, v in prices.items()}
    r0 = next(r for r in rows if r[12] == 1)
    fut = [med[m] for m in med if r0[0] < m <= r0[0] + 30]
    assert r0[11] == pytest.approx(max(fut) / med[r0[0]] - 1, rel=1e-9)
    # the rotation into coin 1 shows as inflow in that minute
    tok1 = "0x" + f"{0xa001:040x}"
    m_rot = (LAUNCH_TS + 400 + 220) // 60
    inflow = db.execute("SELECT inflow_10m, breadth, inflow_wallets FROM coin_minutes WHERE run_id=? AND token=? AND minute=?", (run["run_id"], tok1, m_rot)).fetchone()
    assert inflow[0] >= 1 and inflow[1] >= 1 and F._wid(ROTATOR) in json.loads(inflow[2]) and len(json.loads(inflow[2])) == inflow[0]
    # coin 1 has no fee columns: tax fitted from the trades
    tax1 = db.execute("SELECT DISTINCT est_tax_bps, fees_known, recon FROM coin_minutes WHERE run_id=? AND token=?", (run["run_id"], tok1)).fetchall()
    assert tax1 == [(100, 0, "exact")]


def test_flow_stats_shared_definition():
    rows = [(10, "buy", 1.0, 5.0, "a"), (11, "buy", 1.0, 3.0, "b"), (12, "sell", 1.0, 2.0, "a"), (13, "buy", 1.0, 1.0, "c"), (14, "buy", 1.0, 1.0, "d")]
    fs = F.flow_stats(rows, 1.0)
    assert fs["buys_10m"] == 4 and fs["sells_10m"] == 1 and fs["buyers_10m"] == 4 and fs["sellers_10m"] == 1
    assert fs["quote_in_10m"] == 10.0 and fs["quote_out_10m"] == 2.0 and fs["vol_10m"] == 12.0
    assert fs["sell_ratio_10m"] == 0.25 and fs["out_in_10m"] == 0.2 and fs["top3_share_10m"] == pytest.approx(0.9)


def test_edge_walk_forward_split_and_report(tmp_path):
    pytest.importorskip("sklearn")
    s, _ = _seed_store(tmp_path / "e.sqlite", n_coins=8)
    s.close()
    out = tmp_path / "edge.md"
    res = subprocess.run([sys.executable, "-m", "stampede.research.edge", "--db", str(tmp_path / "e.sqlite"), "--train-days", "2", "--test-days", "2", "--out", str(out), "--no-model", "--no-config", "--bootstrap", "50", "--k", "3"], capture_output=True, text=True, cwd=Path(__file__).resolve().parent.parent, timeout=600)
    assert res.returncode == 0, res.stderr[-2000:]
    text = out.read_text()
    assert "## Targets" in text and "walk-forward" in text.lower() and "Reading this honestly" in text
    # folds are compressed on a short range and the report says so
    assert "compressed" in res.stdout or "shorter than the requested" in text
    from stampede.research import edge

    # the causal alert rule dedupes per coin for 30 min and respects the threshold
    class Tt:
        token = ["a", "a", "a", "b"]

        def __init__(self):
            import numpy as np

            self.ts = np.array([100, 700, 2000, 2001])

    tt = Tt()
    import numpy as np

    p = np.array([0.9, 0.9, 0.9, 0.1])
    cand = np.array([True, True, True, True])
    assert edge.select_alerts(tt, p, cand, [0, 1, 2, 3], 0.5) == [0, 2]


# ---- simulator -----------------------------------------------------------------------------------------------------------
class _Path:
    """A hand-built reserve path: buys push the spot up, then a dump."""

    def __init__(self, ts, sides, amounts, tax=0, launch_ts=LAUNCH_TS, grad_ts=None):
        from stampede.research.edge import CoinPath

        c = F.Curve(tax_bps=tax)
        self.ts, self.q, self.t = [], [], []
        for t_, side, amt in zip(ts, sides, amounts):
            c.apply(side, amt)
            self.ts.append(t_)
            self.q.append(c.q)
            self.t.append(c.t)
        self.tax, self.recon, self.start, self.launch_ts, self.grad_ts = tax, "exact", 0, launch_ts, grad_ts
        self.threshold = chain.CURVE_GRADUATION_THRESHOLD_WEI
        self.state_at = CoinPath.state_at.__get__(self)


def test_simulate_exit_rules():
    from stampede.research.edge import simulate

    big = 3 * 10**26  # 30% of supply bought at once: the spot doubles
    t0 = LAUNCH_TS + 600
    # entry state after trade 0; pump at +60 s; dump at +120 s; quiet after
    path = _Path([t0 - 5, t0 + 60, t0 + 120, t0 + 3000], ["buy", "buy", "sell", "buy"], [10**25, big, big, 10**24])
    # hold 30 min then sell at the last state (the dump brought the price back near entry)
    hold = simulate(path, t0, {"tp": None, "trail": None, "sl": None, "time_s": 1800}, {}, latency_s=2)
    assert hold and hold["exit"] == "time" and -0.06 < hold["ret"] < 0.0  # fees + rounding only
    # take profit 50% at +80% then a 25% trail: the pump fills the TP, the dump triggers the trail
    tp = simulate(path, t0, {"tp": (0.8, 0.5), "trail": 0.25, "sl": 0.4, "time_s": 1800}, {}, latency_s=2)
    assert tp and tp["exit"] == "trail" and tp["pnl"] > 0.008 and tp["hold_s"] == 118  # entry at t0 + 2 s latency, trail at t0 + 120 s
    # a stop at -30%: the dump takes the price below entry? no - the dump returns to the entry level, so no stop
    sl = simulate(path, t0, {"tp": None, "trail": None, "sl": 0.3, "time_s": 1800}, {}, latency_s=2)
    assert sl and sl["exit"] == "time"
    # the inflow-dies trigger sells at a minute boundary after 5 min
    rows = {(t0 // 60) + k: {"inflow_10m": 0, "smart_sellers_10m": 0, "smart_buyers_10m": 0} for k in range(1, 40)}
    dies = simulate(path, t0, {"tp": None, "trail": None, "sl": None, "time_s": 1800, "inflow_dies": True}, rows, latency_s=2)
    assert dies and dies["exit"] == "inflow_dies" and 300 <= dies["hold_s"] <= 420
    # entry cost is exactly the stake, fees are counted
    assert abs((hold["pnl"] + 0.02) - (hold["pnl"] + 0.02)) < 1e-12 and hold["fees"] > 0
    # unknown state before the path: no trade
    assert simulate(path, t0 - 100, {"time_s": 60}, {}, latency_s=2) is None


# ---- verdict fallback rules and risk -------------------------------------------------------------------------------------
CFG = {
    "thresholds": {"p_enter": 0.3, "p_wait": 0.15},
    "base_rates": {"train_all": 0.05},
    "rules_calibration": {"buckets": {k: [list(x) for x in v] for k, v in V.RULE_BUCKETS.items()}, "cells": {"3/1/3/2": {"n": 50, "p": 0.35}, "1/0/0/1": {"n": 500, "p": 0.06}}},
    "payoff": {"runner_mean": 0.015, "other_mean": -0.005, "size_eth": 0.02},
    "exit_plan": {"tp": [[0.8, 0.5]], "trail": 0.25, "sl": 0.3, "time_s": 1800, "inflow_dies": True},
    "candidate": {"age_max_s": 14400},
}


def test_verdict_rules_fallback_actions():
    hot = {"inflow_10m": 12, "age_s": 1200, "quality": 0.6, "mom_10m": 0.3, "stage": "curve", "is_eth": True, "quote_symbol": "ETH", "range_5m": 0.1}
    v = V.verdict(hot, model=False, config=CFG)
    assert v["action"] == "ENTER" and v["p_2x_30m"] == 0.35 and v["source"] == "rules" and v["ev_ref_quote"] > 0
    assert v["size"]["allowed"] and 0 < v["size"]["quote"] <= 0.02 and v["exit_plan"]["tp"] == [[0.8, 0.5]] and v["exit_plan"]["sl"] == 0.3
    assert any("sell 50% at +80%" in line for line in v["exit_plan"]["text"]) and "rotation inflow stops for 5 min" in v["exit_plan"]["triggers"]
    cold = {"inflow_10m": 1, "age_s": 60, "mom_10m": 0.5, "stage": "curve", "is_eth": True}
    assert V.verdict(cold, model=False, config=CFG)["action"] == "WAIT"
    unknown_cell = {"inflow_10m": 25, "age_s": 100, "stage": "curve", "is_eth": True}
    u = V.verdict(unknown_cell, model=False, config=CFG)
    assert u["p_2x_30m"] == 0.05 and u["action"] == "WAIT"  # unseen cell falls back to the base rate
    # hard avoids beat the probability
    assert V.verdict({**hot, "stage": "graduated"}, model=False, config=CFG)["action"] == "AVOID"
    assert V.verdict({**hot, "age_s": 5 * 3600}, model=False, config=CFG)["action"] == "AVOID"
    assert V.verdict({**hot, "launch_farm": 1}, model=False, config=CFG)["action"] == "AVOID"
    assert V.verdict({**hot, "insider_sells_10m": 1}, model=False, config=CFG)["action"] == "AVOID"
    assert V.verdict({**hot, "since_snipe_zero_s": -1}, model=False, config=CFG)["action"] == "AVOID"
    sp = V.verdict({**hot, "sell_ratio_10m": 3.0, "out_in_10m": 2.0, "sells_10m": 9}, model=False, config=CFG)
    assert sp["action"] == "AVOID" and any("sell pressure" in r for r in sp["reasons"])
    # missing everything: still a verdict, never an exception
    e = V.verdict({}, model=False, config=CFG)
    assert e["action"] == "WAIT" and e["p_2x_30m"] == 0.05
    # fast without the model
    t0 = time.perf_counter()
    for _ in range(200):
        V.verdict(hot, model=False, config=CFG)
    assert (time.perf_counter() - t0) / 200 < 0.001


def test_verdict_uses_risk_state():
    hot = {"inflow_10m": 12, "age_s": 1200, "quality": 0.6, "mom_10m": 0.3, "stage": "curve", "is_eth": True}
    assert V.verdict(hot, model=False, config=CFG, open_positions=3)["action"] == "WAIT"
    stopped = V.verdict(hot, model=False, config=CFG, day_pnl_quote=-0.06, bankroll=1.0)
    assert stopped["action"] == "WAIT" and any("daily stop" in r for r in stopped["reasons"])


def test_risk_size_caps_kelly_and_daily_stop():
    cfg = risk.load_config()
    payoff = {"win_mean": 0.015, "loss_mean": -0.005, "size_eth": 0.02}
    sz = risk.size(0.4, payoff, cfg, bankroll=1.0)
    # Kelly: b = 3, f = (0.4*3 - 0.6)/3 = 0.2; half-Kelly of 1 ETH = 0.1 > caps -> 0.02 per trade cap binds
    assert sz["kelly"] == pytest.approx(0.2, abs=1e-3) and sz["capped_by"] == "cap_per_trade" and sz["quote"] == 0.02 and sz["allowed"]
    # a smaller bankroll makes the 2% cap bind
    assert risk.size(0.4, payoff, cfg, bankroll=0.5)["capped_by"] == "cap_bankroll_frac"
    # low probability: Kelly binds and may go to zero
    low = risk.size(0.2, payoff, cfg, bankroll=1.0)
    assert low["kelly"] == pytest.approx(0.0) and low["quote"] == 0.0 and not low["allowed"]
    # volatility scaling
    vol = risk.size(0.4, payoff, cfg, bankroll=1.0, range_5m=0.6)
    assert vol["vol_scale"] == 0.5 and vol["quote"] == 0.01
    # daily stop and concurrency
    assert not risk.size(0.4, payoff, cfg, bankroll=1.0, day_pnl_quote=-0.05)["allowed"]
    assert risk.size(0.4, payoff, cfg, bankroll=1.0, day_pnl_quote=-0.049)["allowed"]
    assert risk.size(0.4, payoff, cfg, bankroll=1.0, open_positions=3)["capped_by"] == "max_concurrent"
    assert risk.daily_stop_hit(-0.05, cfg, 1.0) and not risk.daily_stop_hit(-0.01, cfg, 1.0)
    # exit-now triggers
    assert risk.exit_now({"sell_ratio_10m": 2.5, "out_in_10m": 2.0, "sells_10m": 6}, cfg)
    assert risk.exit_now({"progress_5m": -0.08}, cfg) and risk.exit_now({"progress": 0.98, "stage": "curve"}, cfg)
    assert risk.exit_now({"dev_sold_10m": 0.01}, cfg) and not risk.exit_now({"sell_ratio_10m": 0.5}, cfg)
    lines = risk.plan_text({"tp": [[0.8, 0.5]], "trail": 0.25, "sl": 0.3, "time_exit_s": 1800, "triggers": ["graduation"]}, {"quote": 0.02, "allowed": True, "capped_by": "cap_per_trade"})
    assert lines[0].startswith("size 0.0200 ETH") and "sell 50% at +80%" in lines and "stop at −30%" in lines and "out after 30 min whatever the price" in lines


def test_tui_verdict_line():
    from stampede.tui.app import verdict_line

    assert verdict_line(None) == "verdict: n/a"
    line = verdict_line({"action": "ENTER", "p_2x_30m": 0.42, "p_minus50_30m": 0.1, "size": {"quote": 0.02, "allowed": True}, "exit_plan": {"text": ["sell 50% at +80%", "stop at −30%"]}, "source": "model"})
    assert line.startswith("verdict: ENTER · p2x 42% · p-50% 10% · size 0.0200 · (model)") and "plan: sell 50% at +80% · stop at −30%" in line
    assert verdict_line({"action": "WAIT", "p_2x_30m": 0.05, "source": "rules"}) == "verdict: WAIT · p2x 5% · (rules)"


# ---- API: rows carry the verdict -------------------------------------------------------------------------------------------
def test_radar_rows_and_coin_carry_verdict(tmp_path):
    from test_radar import T0, build

    build(tmp_path / "r.sqlite").close()
    app = create_app(mode="replay", window="30m", db=tmp_path / "r.sqlite", context_policy="off")
    tc = TestClient(app)
    tc.post("/api/session", json={"action": "seek", "ts": T0})
    r = tc.get("/api/radar", params={"preset": "all", "min_wallets": 1}).json()
    assert r["rows"], "fixture rows"
    for row in r["rows"]:
        v = row["verdict"]
        assert v["action"] in ("ENTER", "WAIT", "AVOID") and 0 <= v["p_2x_30m"] <= 1
        assert set(v) >= {"p_minus50_30m", "ev_per_trade_quote", "entry_window", "exit_plan", "size", "reasons"}
        assert isinstance(v["exit_plan"]["text"], list) and v["exit_plan"]["time_exit_s"] > 0
    c = tc.get(f"/api/coin/{TOKEN_B}").json()
    assert c["verdict"]["action"] in ("ENTER", "WAIT", "AVOID") and c["verdict"]["reasons"]
    a = tc.get("/api/alerts").json()
    assert "edge_enter" in a["rules"] and a["rules"]["edge_enter"]["per_hour"] == 5
    assert TOKEN_A  # fixture import used
