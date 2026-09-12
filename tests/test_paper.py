"""Stage 7: the paper ledger (fills at the next block's reserves vs the hand formula; TP / trail / stop / time / trigger /
inflow-dies / graduation exits; equity and statistics with the bootstrap CI; risk caps and the 30-min dedupe of the
engine's edge_enter alerts; persistence through WriteBatch.sql), and the /api/paper + /api/track-record endpoints."""
from __future__ import annotations

import json

from fastapi.testclient import TestClient
from helpers import CURVE_A, TOKEN_A, TOKEN_B, TOKEN_C, WALLET_1, WALLET_2, curve_buy, curve_sell, transfer
from stampede import chain
from stampede.api import radar as radar_mod
from stampede.api.app import create_app
from stampede.engine import paper as P
from stampede.engine.runner import Writer
from stampede.engine.state import CurveReserve, EngineState, WriteBatch
from stampede.store import Store
from test_engine import block, seeded_store

Q0, T0 = chain.CURVE_PHANTOM_QUOTE_WEI, chain.CURVE_SUPPLY_WEI
ETH = chain.NATIVE
SIZE = 2 * 10**16  # 0.02 ETH
ENTER_VERDICT = {"action": "ENTER", "p_2x_30m": 0.61, "p_minus50_30m": 0.2, "ev_per_trade_quote": 0.004, "size": {"quote": 0.02, "allowed": True, "capped_by": "cap_per_trade"}, "exit_plan": {"text": ["sell 50% at +100%", "then trail 25% below the high", "stop at −30%", "out after 45 min whatever the price"]}, "reasons": ["p(≥2× in 30 min) 61% from rules"], "source": "rules"}


def ledger(**kw) -> P.PaperLedger:
    return P.PaperLedger(None, config={"exit_plan": {"tp": [[1.0, 0.5]], "trail": 0.25, "sl": 0.3, "time_s": 2700, "inflow_dies": True}, "payoff": {"runner_mean": 0.0186, "other_mean": -0.0024, "size_eth": 0.02}}, **kw)


def fresh_reserve(tax_bps: int = 100, observed_buy: int = 10**17) -> CurveReserve:
    rs = CurveReserve(TOKEN_A, ETH)
    fee, tax = observed_buy * 100 // 10_000, observed_buy * tax_bps // 10_000
    rs.apply_buy(observed_buy, rs.tokens_out(observed_buy - fee - tax), fee, tax, block=1, ts=1)
    return rs


def price_up(rs: CurveReserve, factor: float, block: int, ts: int) -> None:
    """Observed buys until the spot price is `factor` × the current one (constant product: Q·T = k)."""
    target = rs.price() * factor
    while rs.price() < target:
        q = 5 * 10**16
        fee, tax = q // 100, q * (rs.tax_bps or 0) // 10_000
        rs.apply_buy(q, rs.tokens_out(q - fee - tax), fee, tax, block=block, ts=ts)


def price_down(rs: CurveReserve, factor: float, block: int, ts: int) -> None:
    target = rs.price() * factor
    while rs.price() > target:
        t = 10**23
        gross = rs.quote_out(t)
        fee, tax = gross // 100, gross * (rs.tax_bps or 0) // 10_000
        rs.apply_sell(t, gross - fee - tax, fee, tax, block=block, ts=ts)


def run_block(L: P.PaperLedger, clock: int, blk: int, reserves: dict, rows: dict | None = None, launches: dict | None = None, grads: dict | None = None, pool_px=None, wb=None):
    return L.on_block(clock, blk, reserves, rows or {}, launches or {}, grads or {}, lambda t: pool_px, lambda t: "AAA", wb)


# ---- fills -------------------------------------------------------------------------------------------------------------
def test_entry_fills_at_the_next_blocks_reserves_against_the_hand_formula():
    L = ledger()
    rs = fresh_reserve(tax_bps=250)  # the curve's own events say 2.5 % creator tax
    assert rs.tax_bps == 250
    res = {CURVE_A: rs}
    assert L.on_enter(TOKEN_A, "AAA", CURVE_A, ENTER_VERDICT, {"range_5m": 0.1}, clock=1000, block=100) is None
    assert len(L.pending) == 1 and L.pending[0].size_wei == SIZE
    # the decision block itself never fills
    assert run_block(L, 1000, 100, res) == [] and L.pending
    # another trader buys in block 101 before us: our fill sees *those* reserves
    q = 3 * 10**17
    fee, tax = q // 100, q * 250 // 10_000
    rs.apply_buy(q, rs.tokens_out(q - fee - tax), fee, tax, block=101, ts=1001)
    Q, T = rs.phantom + rs.quote, rs.tokens
    spot = rs.price()
    ev = run_block(L, 1001, 101, res)
    assert [e["kind"] for e in ev] == ["opened"] and not L.pending and len(L.open) == 1
    pos = next(iter(L.open.values()))
    fee_w, tax_w = SIZE * 100 // 10_000, SIZE * 250 // 10_000
    net = SIZE - fee_w - tax_w
    assert (pos.fee_wei, pos.tax_wei, pos.snipe_wei) == (fee_w, tax_w, 0) and pos.tax_source == "curve events"
    assert pos.tokens == net * T // (Q + net) == rs.tokens_out(net)  # tokensOut = net·T/(Q+net) on the block-101 reserves
    assert pos.entry_px == SIZE / pos.tokens and pos.opened_block == 101 and pos.alert_block == 100 and pos.opened_ts == 1001
    assert pos.impact_bps > 0 and abs((net / pos.tokens) / spot - 1 - pos.impact_bps / 10_000) < 1e-9
    # the first mark is what a sell would return now: gross − 1 % − tax, below the entry (fees + impact both ways)
    gross = pos.tokens * (rs.phantom + rs.quote) // (rs.tokens + pos.tokens)
    assert pos.mark_wei == gross - gross // 100 - gross * 250 // 10_000 and pos.ret < 0 and -0.10 < pos.ret  # 2 × (1 % + 2.5 %) plus the round-trip impact of 0.02 on ~2.1 ETH
    d = ev[0]
    assert d["size_quote"] == 0.02 and d["fills"][0]["side"] == "buy" and d["fills"][0]["reason"] == "entry" and d["plan"]["tp"] == [[1.0, 0.5]] and d["p_2x_30m"] == 0.61
    # inside the snipe window the fill pays the snipe tax too; without curve events the tax falls back to the launch intel
    L2 = ledger()
    rs2 = CurveReserve(TOKEN_B, ETH)
    L2.on_enter(TOKEN_B, "BBB", "0xcurve", ENTER_VERDICT, {}, clock=2000, block=10, tax_hint=300)
    ev2 = run_block(L2, 2001, 11, {"0xcurve": rs2}, launches={TOKEN_B: {"ts": 2000}})
    p2 = next(iter(L2.open.values()))
    assert ev2[0]["kind"] == "opened" and p2.tax_source == "launch intel" and p2.tax_bps == 300 and p2.snipe_wei == SIZE * 618 // 10_000  # 618 bps at 1 s
    # the size is the risk engine's from the coin's features: a 60 % 5-min range halves the 0.02 cap, and the verdict's own
    # (smaller) size is never exceeded
    L4 = ledger()
    L4.on_enter(TOKEN_A, "AAA", CURVE_A, ENTER_VERDICT, {"range_5m": 0.6}, clock=1, block=1)
    assert L4.pending[0].size_wei == 10**16
    L4.on_enter(TOKEN_B, "BBB", "0xcb", {**ENTER_VERDICT, "size": {"quote": 0.0155, "allowed": True}}, {}, clock=1, block=1)
    assert L4.pending[1].size_wei == 155 * 10**14
    # an unknown reserve (snapshot pending) or a non-ETH pair never fills silently
    L3 = ledger()
    L3.on_enter(TOKEN_C, "CCC", "0xc", ENTER_VERDICT, {}, clock=1, block=1)
    ev3 = run_block(L3, 2, 2, {"0xc": CurveReserve(TOKEN_C, ETH, complete=False)})
    assert ev3[0]["kind"] == "skipped" and "snapshot" in ev3[0]["reason"] and not L3.open


# ---- exits ---------------------------------------------------------------------------------------------------------------
def open_position(L: P.PaperLedger, rs: CurveReserve, clock: int = 1000, blk: int = 100, tok: str = TOKEN_A) -> P.Position:
    L.on_enter(tok, "AAA", CURVE_A, ENTER_VERDICT, {}, clock=clock, block=blk)
    ev = run_block(L, clock + 1, blk + 1, {CURVE_A: rs})
    assert ev and ev[0]["kind"] == "opened"
    return next(p for p in L.open.values() if p.token == tok)


def test_take_profit_then_trailing_stop():
    L = ledger()
    rs = fresh_reserve()
    wb = WriteBatch()
    pos = open_position(L, rs)
    price_up(rs, 2.3, 102, 1010)  # +130 % spot: the mark (net of impact and fees) clears +100 %
    ev = run_block(L, 1010, 102, {CURVE_A: rs}, rows={TOKEN_A: {"inflow_10m": 3}}, wb=wb)
    assert [e["kind"] for e in ev] == ["fill"] and pos.tp_done and pos.tokens_remaining == pos.tokens - pos.tokens // 2
    assert pos.fills[-1]["reason"] == "tp" and pos.fills[-1]["side"] == "sell" and pos.proceeds_wei > SIZE // 2  # half the tokens already returned more than half the stake
    assert pos.status == "open" and pos.ret > 1.0
    peak = pos.peak_px
    price_down(rs, 0.9, 103, 1020)  # -10 % from the high: inside the 25 % trail
    ev = run_block(L, 1020, 103, {CURVE_A: rs}, rows={TOKEN_A: {"inflow_10m": 3}}, wb=wb)
    assert pos.status == "open" and pos.peak_px == peak
    price_down(rs, 0.8, 104, 1030)  # now ~28 % below the high: trailing stop
    ev = run_block(L, 1030, 104, {CURVE_A: rs}, rows={TOKEN_A: {"inflow_10m": 3}}, wb=wb)
    assert [e["kind"] for e in ev] == ["closed"] and pos.status == "closed" and pos.exit_reason == "trail" and pos.tokens_remaining == 0
    assert pos.pnl_quote > 0 and ev[0]["pnl_quote"] == pos.pnl_quote and pos.hold_s == 29
    assert L.realized_wei == pos.proceeds_wei - SIZE and len(L.closed) == 1 and not L.open
    # everything the writer needs went into the batch: the position row twice (open, close), three fills, one equity row
    sqls = [s for s, _ in wb.sql]
    assert sqls.count(P.POSITION_INSERT) == 1 and sqls.count(P.FILL_INSERT) == 2 and sqls.count(P.EQUITY_INSERT) == 1 and any("UPDATE paper_positions SET mark_px" in s for s in sqls)


def test_stop_loss_time_exit_inflow_dies_and_exit_now_trigger():
    # stop: the mark falls 30 % below the entry price (which includes the fees)
    L = ledger()
    rs = fresh_reserve()
    pos = open_position(L, rs)
    price_down(rs, 0.8, 102, 1005)
    assert run_block(L, 1005, 102, {CURVE_A: rs}, rows={TOKEN_A: {"inflow_10m": 2}}) == [] or pos.status == "open"
    price_down(rs, 0.85, 103, 1006)
    ev = run_block(L, 1006, 103, {CURVE_A: rs}, rows={TOKEN_A: {"inflow_10m": 2}})
    assert pos.status == "closed" and pos.exit_reason == "stop" and pos.pnl_quote < -0.005 and ev[-1]["kind"] == "closed"
    # time exit at 45 min whatever the price (inflow keeps the inflow-dies rule quiet)
    L = ledger()
    rs = fresh_reserve()
    pos = open_position(L, rs)
    for t in (1300, 2000, 3000, 3700):
        ev = run_block(L, t, 100 + t, {CURVE_A: rs}, rows={TOKEN_A: {"inflow_10m": 2}})
        assert pos.status == "open", t
    ev = run_block(L, 1001 + 2700, 5000, {CURVE_A: rs}, rows={TOKEN_A: {"inflow_10m": 2}})
    assert pos.status == "closed" and pos.exit_reason == "time" and pos.closed_ts == 3701 and -0.01 < pos.pnl_quote < 0
    # inflow dies: no rotation inflow in 10 min once the trade is 5 min old (the row vanished from the radar counts as 0)
    L = ledger()
    rs = fresh_reserve()
    pos = open_position(L, rs)
    assert run_block(L, 1200, 200, {CURVE_A: rs}, rows={}) == [] or pos.status == "open"  # 199 s in: too early
    ev = run_block(L, 1301, 300, {CURVE_A: rs}, rows={})
    assert pos.status == "closed" and pos.exit_reason == "inflow_dies"
    # exit-now trigger of the risk engine from the coin's features: sell pressure
    L = ledger()
    rs = fresh_reserve()
    pos = open_position(L, rs)
    L.features[TOKEN_A] = {"sell_ratio_10m": 3.0, "out_in_10m": 2.0, "sells_10m": 6, "stage": "curve"}
    ev = run_block(L, 1010, 110, {CURVE_A: rs}, rows={TOKEN_A: {"inflow_10m": 4}})
    assert pos.status == "closed" and pos.exit_reason == "trigger: sell pressure" and ev[-1]["exit_reason"].startswith("trigger")
    # liquidity drop from the ledger's own progress history (progress_5m)
    L = ledger()
    rs = fresh_reserve(observed_buy=10**18)
    pos = open_position(L, rs)
    price_down(rs, 0.75, 120, 1002)  # a 25 % price drop on a 1 ETH curve pulls the reserve well below −5 % of the threshold
    ev = run_block(L, 1310, 130, {CURVE_A: rs}, rows={TOKEN_A: {"inflow_10m": 4}})
    assert pos.status == "closed" and pos.exit_reason in ("trigger: liquidity drop", "stop")


def test_graduation_closes_at_the_pool_price_with_fee_and_tax():
    L = ledger()
    rs = fresh_reserve()
    pos = open_position(L, rs)
    rs.graduated = True
    pool_px = pos.entry_px * 3  # the observed pool trades at 3× our entry
    ev = run_block(L, 1500, 200, {CURVE_A: rs}, rows={TOKEN_A: {"inflow_10m": 1}}, pool_px=pool_px)
    assert pos.status == "closed" and pos.exit_reason == "graduation" and pos.fills[-1]["venue"] == "pool"
    gross = int(pos.tokens * pool_px)
    assert pos.proceeds_wei == gross - gross // 100 - gross * 100 // 10_000 and pos.pnl_quote > 0.03
    assert ev[-1]["kind"] == "closed" and ev[-1]["exit_reason"] == "graduation"


# ---- statistics ------------------------------------------------------------------------------------------------------------
def closed_dict(i: int, tok: str, pnl: float, opened: int, hold: int = 600, usd: float | None = None) -> dict:
    return {"id": i, "token": tok, "symbol": tok[-3:], "status": "closed", "opened_ts": opened, "closed_ts": opened + hold, "hold_s": hold, "size_quote": 0.02, "pnl_quote": pnl, "pnl_usd": usd, "exit_reason": "stop" if pnl < 0 else "trail", "fee_quote": 0.0002, "tax_quote": 0.0002, "snipe_quote": 0.0, "exit_fees_quote": 0.0003, "tokens": "10", "tokens_remaining": "0"}


def test_stats_expectancy_profit_factor_drawdown_hours_and_bootstrap_ci():
    base = 1_789_171_200  # 2026-09-12 00:00 UTC
    closed = [closed_dict(1, TOKEN_A, 0.010, base + 100, usd=40.0), closed_dict(2, TOKEN_B, -0.006, base + 200, usd=-24.0), closed_dict(3, TOKEN_A, -0.004, base + 3700), closed_dict(4, TOKEN_C, 0.020, base + 7300, usd=80.0)]
    st = P.stats(closed, [{"size_quote": 0.02, "tokens": "10", "tokens_remaining": "10", "unrealized_quote": -0.001}], skipped=2)
    assert st["trades"] == 4 and st["coins"] == 3 and st["wins"] == 2 and st["hit_rate"] == 0.5
    assert abs(st["expectancy_quote"] - 0.005) < 1e-12 and abs(st["expectancy_pct"] - 0.25) < 1e-9 and abs(st["total_quote"] - 0.02) < 1e-12
    assert st["usd_known"] == 3 and abs(st["expectancy_usd"] - 32.0) < 1e-9 and st["total_usd"] is None  # USD only when every trade has a rate
    assert abs(st["profit_factor"] - 3.0) < 1e-9 and abs(st["max_drawdown_quote"] - (-0.010)) < 1e-12  # 0.010 → 0.004 → 0.000: drawdown 0.010
    assert st["exits"] == {"trail": 2, "stop": 2} and st["median_hold_s"] == 600 and abs(st["fees_quote"] - 4 * 0.0007) < 1e-12
    assert [h["hour"] for h in st["per_hour"]] == ["2026-09-12 00", "2026-09-12 01", "2026-09-12 02"] and st["per_hour"][0]["n"] == 2 and abs(st["per_hour"][0]["pnl"] - 0.004) < 1e-12
    assert [h["hour"] for h in st["by_hour_of_day"]] == [0, 1, 2]
    lo, hi = st["ci95_quote"]
    assert lo <= st["expectancy_quote"] <= hi and lo < hi and st["open"] == 1 and st["exposure_quote"] == 0.02 and st["unrealized_quote"] == -0.001 and st["skipped_by_risk"] == 2
    assert st["simulated"] is True and "simulated" in st["note"]
    # deterministic, resamples coins: identical input gives the identical interval; one coin has no interval
    assert P.bootstrap_ci([[0.01, -0.004], [-0.006], [0.02]]) == (lo, hi) and P.bootstrap_ci([[0.01, 0.02]]) is None
    assert P.stats([])["trades"] == 0 and P.stats([])["hit_rate"] is None and P.stats([])["ci95_quote"] is None and P.max_drawdown([1.0, -0.5, -0.7, 2.0]) == -1.2


# ---- risk caps, dedupe --------------------------------------------------------------------------------------------------------
def test_risk_caps_skip_the_fourth_position_and_a_coin_already_open():
    L = ledger()
    reserves = {}
    for i, tok in enumerate((TOKEN_A, TOKEN_B, TOKEN_C)):
        rs = fresh_reserve()
        reserves[f"0xc{i}"] = rs
        assert L.on_enter(tok, tok[-3:], f"0xc{i}", ENTER_VERDICT, {}, clock=1000 + i, block=100 + i) is None
    ev = run_block(L, 1010, 110, reserves)
    assert sorted(e["kind"] for e in ev) == ["opened"] * 3 and len(L.open) == 3
    skipped = L.on_enter("0x" + "9" * 40, "NINE", "0xc9", ENTER_VERDICT, {}, clock=1011, block=111)
    assert skipped["kind"] == "skipped" and skipped["capped_by"] == "max_concurrent" and L.stats["skipped_by_risk"] == 1
    again = L.on_enter(TOKEN_A, "AAA", "0xc0", ENTER_VERDICT, {}, clock=1012, block=112)
    assert again["kind"] == "skipped" and "already open" in again["reason"]
    # the daily stop blocks new entries once today's realized pnl is at or below −5 % of the bankroll
    L2 = ledger()
    rs = fresh_reserve()
    pos = open_position(L2, rs)
    pos.status, pos.closed_ts, pos.proceeds_wei = "closed", 1500, 0  # a total loss of 0.02 on a 1.0 bankroll is 2 %: not stopped yet
    L2.closed.append(pos)
    L2.open.clear()
    assert L2.on_enter(TOKEN_B, "BBB", "0xcb", ENTER_VERDICT, {}, clock=1600, block=160) is None
    L2.pending.clear()
    for k in range(3):
        L2.closed.append(P.Position(id=10 + k, token=TOKEN_C, symbol="C", curve="0xcc", quote_token=ETH, alert_ts=1, alert_block=1, opened_ts=1700, opened_block=1, size_wei=SIZE, tokens=1, tokens_remaining=0, entry_px=1.0, spot_px_before=None, impact_bps=None, fee_wei=0, tax_wei=0, snipe_wei=0, tax_bps=100, tax_source="t", p_2x_30m=0.5, ev_quote=0.0, plan={}, status="closed", closed_ts=1800, proceeds_wei=0))
    stopped = L2.on_enter(TOKEN_B, "BBB", "0xcb", ENTER_VERDICT, {}, clock=1900, block=190)
    assert stopped["kind"] == "skipped" and stopped["capped_by"] == "daily_stop"


def test_engine_edge_enter_alerts_dedupe_per_coin_and_cap_per_hour(tmp_path, monkeypatch):
    store = seeded_store(tmp_path / "d.sqlite")
    st = EngineState(store, paper=False)
    rows = {}
    for i in range(7):
        tok = f"0x{i:040x}"
        rows[tok] = {"address": tok, "symbol": f"T{i}", "score": 70.0 - i, "inflow_10m": 9, "mentions_1h": None, "price_quote": 1e-9, "sources": [], "accel": 1.0, "breadth": 1, "age_s": 100, "stage": "curve", "progress": 0.1, "rank": i + 1, "verdict": {**ENTER_VERDICT, "p_2x_30m": 0.9 - i / 100}}
    st.rows = rows
    st.ranked = list(rows.values())
    wb = WriteBatch()
    fired = st._edge_alerts(clock=10_000, block=1, wb=wb)
    assert len(fired) == 5 and [f["detail"]["rank"] for f in fired] == [1, 2, 3, 4, 5] and all(f["rule"] == "edge_enter" for f in fired)
    assert len(wb.alerts) == 5 and wb.alerts[0][5] == "edge_enter" and json.loads(wb.alerts[0][10])["size_quote"] == 0.02 and len(st.alert_pending) == 5
    assert st._edge_alerts(clock=10_060, block=2, wb=wb) == []  # the hour's budget is spent
    st.edge_times.clear()
    muted = st._edge_alerts(clock=11_000, block=3, wb=WriteBatch())
    assert {f["symbol"] for f in muted} == {"T5", "T6"}  # the five alerted coins stay muted for 30 min; the two that never fired may
    st.edge_times.clear()
    later = st._edge_alerts(clock=10_000 + 1801, block=4, wb=WriteBatch())
    assert {f["token"] for f in later} == {f["token"] for f in fired}  # after 30 min the same coins can alert again (T5/T6 are now the muted ones)
    # persisted alerts feed the dedupe memory of a restarted engine (only clocks inside the last 30 min of wall time count)
    Writer(tmp_path / "d.sqlite").flush(store, wb)
    assert store.db.execute("SELECT COUNT(*) FROM alerts WHERE rule='edge_enter'").fetchone()[0] == 5
    st2 = EngineState(store, paper=False)
    assert st2.edge_recent == {} and len(st2.alert_pending) == 0  # clock 10_000 is decades old in wall time
    store.close()


# ---- end to end through the engine state + persistence + API ------------------------------------------------------------------
def launch_log(token: str, curve: str, deployer: str) -> dict:
    return {"log_index": 0, "address": chain.PONS_V2_FACTORY, "topic0": chain.T_TOKEN_LAUNCHED, "topic1": "0x" + token[2:].rjust(64, "0"), "topic2": "0x" + curve[2:].rjust(64, "0"), "topic3": "0x" + deployer[2:].rjust(64, "0"), "data": "0x" + chain.NATIVE[2:].rjust(64, "0") + f"{0:064x}" + f"{chain.CURVE_GRADUATION_THRESHOLD_WEI:064x}", "kind": "token_launched"}


def buy_log(curve: str, token: str, wallet: str, quote_in: int, tokens_out: int, li: int = 0) -> list[dict]:
    b = curve_buy(curve, wallet, wallet, quote_in, tokens_out, li)
    b["data"] = "0x" + f"{quote_in:064x}" + f"{tokens_out:064x}" + f"{quote_in // 100:064x}" + f"{quote_in // 100:064x}"
    return [b, transfer(token, curve, wallet, tokens_out, li + 1)]


def test_engine_state_opens_a_paper_position_at_the_next_block_and_persists_it(tmp_path, monkeypatch):
    db = tmp_path / "p.sqlite"
    store = seeded_store(db)
    new_token, new_curve = "0x" + "d4" * 20, "0x" + "cd" * 20

    def fake_attach(rows):
        for r in rows:
            r.pop("_vf", None)
            r["verdict"] = dict(ENTER_VERDICT) if r["address"] == new_token else {"action": "WAIT", "p_2x_30m": 0.1}

    monkeypatch.setattr(radar_mod, "attach_verdicts", fake_attach)
    st = EngineState(store)
    assert st.paper is not None
    # block 200: launch of the new coin with a first buy (complete reserve from its first trade)
    q1 = 10**17
    rs_tmp = CurveReserve(new_token, ETH)
    t1 = rs_tmp.tokens_out(q1 - 2 * (q1 // 100))
    st.apply_block(block(200, 2000, [("0xl", [launch_log(new_token, new_curve, WALLET_2)] + [{**l, "log_index": l["log_index"] + 1} for l in buy_log(new_curve, new_token, WALLET_2, q1, t1)])]))
    rs = st.reserves[new_curve]
    assert rs.complete and rs.tax_bps == 100
    # blocks 201-202: two wallets sell AAA then buy the new coin -> rotation inflow -> a radar row -> ENTER -> edge_enter alert
    st.apply_block(block(201, 2010, [("0xs1", [transfer(TOKEN_A, WALLET_1, CURVE_A, 10**18, 0), curve_sell(CURVE_A, WALLET_1, WALLET_1, 10**18, 10**15, 1)]), ("0xs2", [transfer(TOKEN_A, WALLET_2, CURVE_A, 10**18, 0), curve_sell(CURVE_A, WALLET_2, WALLET_2, 10**18, 10**15, 1)])]))
    q2 = 5 * 10**16
    t2 = rs.tokens_out(q2 - 2 * (q2 // 100))
    b1 = buy_log(new_curve, new_token, WALLET_1, q2, t2)
    rs_after = CurveReserve(new_token, ETH, quote=rs.quote + q2 - 2 * (q2 // 100), tokens=rs.tokens - t2)
    t3 = rs_after.tokens_out(q2 - 2 * (q2 // 100))
    b2 = buy_log(new_curve, new_token, WALLET_2, q2, t3)
    events, wb, _ = st.apply_block(block(202, 2020, [("0xb1", b1), ("0xb2", b2)]))  # two rotating wallets: the row is ranked
    fired = [d for t, d in events if t == "alert" and d.get("kind") == "fired" and d["rule"] == "edge_enter"]
    assert len(fired) == 1 and fired[0]["token"] == new_token and fired[0]["detail"]["size_quote"] == 0.02 and fired[0]["detail"]["engine"] == "wss"
    assert len(st.paper.pending) == 1 and not [d for t, d in events if t == "position"]  # queued: the fill waits for the next block
    assert any(r[5] == "edge_enter" for r in wb.alerts)
    Writer(db).flush(store, wb)
    # block 203: the fill happens at these reserves
    Q, T = rs.phantom + rs.quote, rs.tokens
    events, wb, _ = st.apply_block(block(203, 2030, []))
    pos_ev = [d for t, d in events if t == "position"]
    assert len(pos_ev) == 1 and pos_ev[0]["kind"] == "opened" and pos_ev[0]["token"] == new_token and pos_ev[0]["size_quote"] == 0.02
    pos = next(iter(st.paper.open.values()))
    net = SIZE - SIZE // 100 - SIZE // 100
    assert pos.tokens == net * T // (Q + net) and pos.opened_block == 203 and pos.alert_block == 202 and pos.tax_source == "curve events"
    Writer(db).flush(store, wb)
    assert store.db.execute("SELECT COUNT(*) FROM paper_positions WHERE status='open'").fetchone()[0] == 1 and store.db.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 1
    # marks are persisted (throttled) and a restarted ledger picks the open position up
    events, wb, _ = st.apply_block(block(204, 2040, []))
    Writer(db).flush(store, wb)
    st2 = EngineState(store)
    assert len(st2.paper.open) == 1 and next(iter(st2.paper.open.values())).tokens == pos.tokens and st2.paper.next_id == pos.id + 1
    snap = P.read_snapshot(store)
    assert snap["simulated"] and len(snap["positions"]["open"]) == 1 and snap["stats"]["open"] == 1 and snap["stats"]["trades"] == 0
    assert st.summary()["paper"]["open"] == 1
    store.close()


def test_api_paper_and_track_record_endpoints(tmp_path):
    db = tmp_path / "api.sqlite"
    store = seeded_store(db)
    P.ensure_tables(store.db)
    L = ledger()
    rs = fresh_reserve()
    wb = WriteBatch()
    L.on_enter(TOKEN_A, "AAA", CURVE_A, ENTER_VERDICT, {}, clock=1000, block=100)
    run_block(L, 1001, 101, {CURVE_A: rs}, wb=wb)
    price_down(rs, 0.6, 102, 1100)
    run_block(L, 1100, 102, {CURVE_A: rs}, rows={TOKEN_A: {"inflow_10m": 2}}, wb=wb)
    assert L.closed and L.closed[0].exit_reason == "stop"
    store.db.execute("INSERT INTO alerts(created_ts, clock_ts, mode, token, symbol, rule, score, inflow, mentions_1h, price, detail, outcome_30m) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (1000, 1000, "fixture", TOKEN_A, "AAA", "edge_enter", 70.0, 9, None, 1e-9, json.dumps({"p_2x_30m": 0.61, "size_quote": 0.02, "age_s": 100, "engine": "wss"}), -40.0))
    store.commit()
    Writer(db).flush(store, wb)
    store.close()
    app = create_app(mode="fixture", db=db, context_policy="off")
    tc = TestClient(app)
    d = tc.get("/api/paper").json()
    assert d["simulated"] is True and d["source"] == "tables" and d["mode"] == "fixture"
    assert d["positions"]["open"] == [] and len(d["positions"]["closed"]) == 1 and d["positions"]["closed"][0]["exit_reason"] == "stop" and d["positions"]["closed"][0]["fills"][0]["side"] == "buy"
    assert d["stats"]["trades"] == 1 and d["stats"]["hit_rate"] == 0.0 and d["stats"]["expectancy_quote"] < 0 and d["stats"]["ci95_quote"] is None and d["config"]["size_cap_quote"] == 0.02
    assert d["equity"] and d["equity"][-1]["realized_quote"] == d["stats"]["total_quote"]
    tr = tc.get("/api/track-record").json()
    assert tr["simulated"] is True and tr["perf"] is None and tr["mode"] == "fixture" and tr["paper"]["stats"]["trades"] == 1
    assert tr["alerts"]["by_rule"]["edge_enter"]["fired"] == 1 and tr["alerts"]["by_rule"]["edge_enter"]["with_outcome_30m"] == 1 and tr["alerts"]["recent"][0]["p_2x_30m"] == 0.61
    from stampede import track_record

    md = track_record.render_markdown(tr)
    assert "simulated" in md.lower() and "| `edge_enter` | 1 |" in md and "stop 1" in md and "Regenerate" in md
    # the CLI writes the same report
    out = tmp_path / "TR.md"
    from stampede.cli import main

    assert main(["track-record", "--db", str(db), "--out", str(out), "--mode", "fixture"]) == 0
    assert out.exists() and "Live paper run: track record" in out.read_text()
