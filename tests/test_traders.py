"""Trader intelligence: FIFO PnL with fees on synthetic trades, buyer rank, sniper / exempt flags, exit quality and
rug facts with observed horizons, walk-forward split, copy-test arithmetic on a hand-computed curve, the API."""
import argparse
import math

from fastapi.testclient import TestClient
from helpers import TOKEN_A, TOKEN_B, TOKEN_C
from stampede import chain
from stampede.api.app import create_app
from stampede.research import traders
from stampede.store import Store

T0 = 999_960  # minute-aligned
DEPLOYER = "0x00000000000000000000000000000000000000d0"
W1 = "0x0000000000000000000000000000000000000001"
W2 = "0x0000000000000000000000000000000000000002"
W3 = "0x0000000000000000000000000000000000000003"
WB = "0x000000000000000000000000000000000000000b"
E = 10**18
NATIVE = chain.NATIVE


def seed(store: Store, rows, block0: int = 100):
    """rows: (ts, block, token, wallet, side, token_amount, quote_amount, fee, tax, snipe[, venue])."""
    out = []
    for i, r in enumerate(rows, 1):
        ts, block, tok, w, side, ta, qa, fee, tax, snipe = r[:10]
        venue = r[10] if len(r) > 10 else "curve"
        out.append((f"0x{block0 + i:064x}", block, ts, 1, tok, w, side, str(ta), NATIVE, str(qa), venue, "[]", "transfer_net", "[]", str(fee), str(tax), str(snipe)))
    store.db.executemany("INSERT INTO trades(tx_hash,block,ts,ts_exact,token,wallet,side,token_amount,quote_token,quote_amount,venue,swap_logs,attribution,flags,fee_raw,tax_raw,snipe_raw) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", out)
    store.db.executemany("INSERT OR IGNORE INTO tokens(address,symbol,name,source,curve,first_seen_block) VALUES(?,?,?,?,?,?)", [(TOKEN_A, "AAA", "A coin", "curve", None, None), (TOKEN_B, "BBB", "", "curve", None, None), (TOKEN_C, "CCC", "", "curve", None, None)])
    store.commit()


def build(path) -> Store:
    s = Store(path)
    # launch of A at block 100 = T0 (launches.ts is NULL as in backfilled stores; the time comes from `blocks`)
    s.db.execute("INSERT INTO launches(token,curve,deployer,pair_token,threshold,launch_config,block,ts,tx_hash,source) VALUES(?,?,?,?,?,?,?,?,?,?)", (TOKEN_A, "0xca", DEPLOYER, NATIVE, str(chain.CURVE_GRADUATION_THRESHOLD_WEI), 0, 100, None, "0xl", "ingest"))
    s.upsert_blocks([(100, T0, 1), (101, T0 + 1, 1), (110, T0 + 5000, 1)])
    snipe = 6_180_000_000_000_000
    rows = [
        (T0, 100, TOKEN_A, DEPLOYER, "buy", 3000 * E, 3 * 10**16, 3 * 10**14, 3 * 10**14, 0),  # launch-and-buy, no snipe tax
        (T0 + 1, 101, TOKEN_A, W1, "buy", 1000 * E, 10**17, 10**15 + snipe, 10**15, snipe),  # 1 s after launch: sniper
        (T0 + 5, 102, TOKEN_A, W2, "buy", 250 * E, 5 * 10**16, 5 * 10**14, 5 * 10**14, 0),
        (T0 + 100, 103, TOKEN_A, W1, "buy", 500 * E, 10**17, 10**15, 10**15, 0),
        (T0 + 200, 104, TOKEN_A, W1, "sell", 1200 * E, 3 * 10**17, 303 * 10**13, 303 * 10**13, 0),
        (T0 + 260, 105, TOKEN_A, W2, "buy", 250 * E, 10**17, 10**15, 10**15, 0),  # price 4e-4: the top after W1's first exit
        (T0 + 300, 106, TOKEN_A, W1, "sell", 300 * E, 9 * 10**16, 9 * 10**14, 9 * 10**14, 0),  # closes W1's position
        (T0 + 400, 107, TOKEN_A, W2, "sell", 500 * E, 10**16, 10**14, 10**14, 0),  # -95%: the rug after W1's exit
        (T0 + 5000, 110, TOKEN_A, W3, "buy", 100 * E, 10**16, 10**14, 10**14, 0),  # second half only, still open
        # a wallet that buys two coins in the same block, three times: the multi-coin-block bot rule
        (T0 + 1000, 108, TOKEN_B, WB, "buy", E, 10**15, 10**13, 0, 0),
        (T0 + 1000, 108, TOKEN_C, WB, "buy", E, 10**15, 10**13, 0, 0),
        (T0 + 1100, 109, TOKEN_B, WB, "buy", E, 10**15, 10**13, 0, 0),
        (T0 + 1100, 109, TOKEN_C, WB, "buy", E, 10**15, 10**13, 0, 0),
        (T0 + 1200, 110, TOKEN_B, WB, "buy", E, 10**15, 10**13, 0, 0),
        (T0 + 1200, 110, TOKEN_C, WB, "buy", E, 10**15, 10**13, 0, 0),
    ]
    seed(s, rows)
    s.set_meta("sample_from_block", 100)
    s.set_meta("sample_to_block", 110)
    s.set_meta("sample_label", "traders fixture")
    return s


def args(db, **kw) -> argparse.Namespace:
    a = traders.build_parser().parse_args(["--db", str(db)])
    for k, v in kw.items():
        setattr(a, k, v)
    return a


def test_fifo_pnl_with_fees_ranks_flags_and_horizons(tmp_path):
    db = tmp_path / "t.sqlite"
    s = build(db)
    res = traders.run(s, args(db, min_trades=1), progress=lambda m: None)
    full = res["stats"]["full"]
    w1 = full[W1]
    # FIFO: lot1 = 0.1 ETH + fee (1% + snipe) + tax; lot2 = 0.1 + 1% + 1%. Sell 1200 of 1500 tokens takes lot1 and 2/5 of lot2.
    cost1 = 10**17 + 10**15 + 6_180_000_000_000_000 + 10**15
    cost2 = 10**17 + 2 * 10**15
    realized1 = 3 * 10**17 - cost1 - cost2 * 2 // 5
    realized2 = 9 * 10**16 - cost2 * 3 // 5
    assert math.isclose(w1["realized_eth"], (realized1 + realized2) / 1e18, rel_tol=1e-9)
    assert w1["positions_closed"] == 1 and w1["positions_open"] == 0 and w1["wins"] == 1 and w1["win_rate"] == 1.0
    assert w1["unrealized_eth"] == 0.0 and math.isclose(w1["cost_eth"], (cost1 + cost2) / 1e18)
    assert math.isclose(w1["fees_eth"], (7_180_000_000_000_000 + 10**15 + 2 * 10**15 + 2 * 303 * 10**13 + 2 * 9 * 10**14) / 1e18)
    assert math.isclose(w1["roi"], (realized1 + realized2) / (cost1 + cost2))
    assert w1["median_hold_s"] == 299  # first buy T0+1 -> closing sell T0+300
    # buyer rank since launch: deployer 1, W1 2, W2 3, W3 4; sniper = 1 s after launch with snipe tax paid
    assert w1["buyer_rank_median"] == 2 and full[W2]["buyer_rank_median"] == 3 and full[W3]["buyer_rank_median"] == 4
    assert w1["sniper_share"] == 1.0 and full[W2]["sniper_share"] == 0.0
    # exit quality: sell 1 at 2.5e-4 vs the 4e-4 top within 15 min = 0.625; sell 2 at 3e-4 above every later price = 1.0; proceeds-weighted
    assert math.isclose(w1["exit_quality"], (0.625 * 3e17 + 1.0 * 9e16) / 3.9e17, rel_tol=1e-9)
    # the coin fell 93% within 60 min after W1's exit -> rug avoided
    assert w1["rug_avoid"] == 1.0
    # W2 bought the top and sold into the rug: a loss, and its exit has no observed 15-min horizon problem (trades follow)
    w2 = full[W2]
    assert w2["realized_eth"] < 0 and w2["wins"] == 0 and w2["win_rate"] == 0.0
    # W3: one open position marked at the last price (1e-4 = its own entry): unrealized = -fees; horizon past the end -> nothing claimed
    w3 = full[W3]
    assert w3["positions_open"] == 1 and math.isclose(w3["unrealized_eth"], -(2 * 10**14) / 1e18, rel_tol=1e-9)
    assert w3["exit_quality"] is None and w3["rug_avoid"] is None
    # deployer: launch-and-buy without snipe tax while W1 paid it one second later -> exempt + deployer tags; rank 1
    d = full[DEPLOYER]
    assert d["deployer_linked"] == 1 and "deployer" in d["tags"] and "snipe_exempt" in d["tags"] and d["buyer_rank_median"] == 1
    # bot by same-block multi-coin buys (3 blocks with buys of 2 coins)
    assert full[WB]["is_bot"] == 1 and full[WB]["multi_coin_blocks"] == 3 and "bot" in full[WB]["tags"]
    assert not full[W1]["is_bot"]
    # quality: W1 1 closed win, exit q ~0.71, rug avoided, roi_realized clipped
    q = traders.quality_score(1, 1, w1["roi_realized"], w1["exit_quality"], 1.0)
    assert w1["quality"] == q and 0.5 < q < 0.7
    # tables written: full / h1 / h2 stats, positions for wallets with >= 2 trades, wallet_scores bot flag without a score
    labels = dict(s.db.execute("SELECT label, COUNT(*) FROM wallet_stats GROUP BY label").fetchall())
    assert labels["full"] == 5 and labels["h1"] == 4 and labels["h2"] == 1  # W3 trades only in the second half
    pos = s.db.execute("SELECT wallet, closed, buys, sells, pnl_quote, exit_quality, rug_after, buyer_rank, sniper FROM wallet_positions WHERE wallet=?", (W1,)).fetchone()
    assert pos[1] == 1 and pos[2] == 2 and pos[3] == 2 and math.isclose(pos[4], (realized1 + realized2) / 1e18) and pos[6] == 1 and pos[7] == 2 and pos[8] == 1
    assert s.db.execute("SELECT COUNT(*) FROM wallet_positions WHERE wallet=?", (W3,)).fetchone()[0] == 0  # single trade: stats only
    assert s.db.execute("SELECT is_bot, score FROM wallet_scores WHERE wallet=?", (WB,)).fetchone() == (1, None)
    assert "## Walk-forward" in res["report"] and "Copy-test" in res["report"]


def test_strict_fees_skip_unknown_rows_and_estimate_fills_them(tmp_path):
    db = tmp_path / "f.sqlite"
    s = build(db)
    s.db.execute("UPDATE trades SET fee_raw=NULL, tax_raw=NULL, snipe_raw=NULL WHERE wallet=?", (W2,))
    s.commit()
    res = traders.run(s, args(db, min_trades=1, no_write=True), progress=lambda m: None)
    strict = res["stats"]["full"][W2]
    assert strict["fees_unknown"] == 3 and strict["positions_closed"] == 0 and strict["realized_eth"] == 0.0 and "fees_unknown" in strict["tags"]
    # --no-write: the report still comes from tables (attached in-memory scratch database), the store itself stays untouched
    assert "scratch database only" in res["report"] and "| 1 | `0x00000000…" in res["report"]
    assert s.db.execute("SELECT COUNT(*) FROM sqlite_master WHERE name IN ('wallet_stats','wallet_positions')").fetchone()[0] == 0
    est = traders.run(s, args(db, min_trades=1, no_write=True, fees="estimate"), progress=lambda m: None)["stats"]["full"][W2]
    assert est["positions_closed"] == 1 and "fees_estimated" in est["tags"]
    # estimate: 1% base fee on every leg, creator tax unknown -> cost 1.5e17 * 1.01, proceeds 1e16 net
    assert math.isclose(est["realized_eth"], (10**16 - (5 * 10**16 + 5 * 10**14) - (10**17 + 10**15)) / 1e18, rel_tol=1e-9)


def test_walk_forward_correlations_and_bootstrap():
    def row(w, pnl, q, trades=6, bot=0):
        return {"wallet": w, "trades": trades, "pnl_eth": pnl, "realized_eth": pnl, "pnl_usd": None, "quality": q, "roi": pnl, "is_bot": bot, "deployer_linked": 0}

    h1 = {f"w{i}": row(f"w{i}", i / 10, 0.5 + i / 100) for i in range(12)}
    h2 = {f"w{i}": row(f"w{i}", i / 20, 0.5 + i / 200) for i in range(12)}  # same order: perfect rank persistence
    h1["bot"] = row("bot", 9.0, 0.99, bot=1)
    h2["bot"] = row("bot", 9.0, 0.99, bot=1)
    wf = traders.walk_forward(h1, h2, min_trades=5, top_n=5)
    assert wf["common"] == 13 and wf["eligible"] == 12
    assert wf["spearman"]["pnl_h1_vs_pnl_h2"] == 1.0 and wf["spearman"]["quality_h1_vs_pnl_h2"] == 1.0
    top = wf["top_by_quality"]
    assert top["n"] == 5 and set(wf["top_wallets"]) == {"w11", "w10", "w9", "w8", "w7"} and "bot" not in wf["top_wallets"]
    assert math.isclose(top["mean"], sum(i / 20 for i in range(7, 12)) / 5)
    lo, hi = top["ci95"]
    assert lo <= top["mean"] <= hi and lo >= 7 / 20 and hi <= 11 / 20
    assert traders.spearman([1.0, 2.0], [1.0, 2.0]) is None  # too few
    assert traders.spearman([1, 2, 3, 4], [1, 3, 2, 4]) == 0.8


def test_curve_arithmetic_matches_hand_numbers():
    rq, rt = chain.CURVE_PHANTOM_QUOTE_WEI, chain.CURVE_SUPPLY_WEI
    order = 2 * 10**16  # 0.02 ETH
    tokens, q_net, fees = traders.curve_buy(rq, rt, order, 0.0)
    assert q_net == int(order / 1.01) and fees == order - q_net
    # constant product: tokens = S * q_net / (R + q_net)
    assert math.isclose(tokens, rt * q_net / (rq + q_net), rel_tol=1e-9)
    assert math.isclose(tokens / 1e18, 11_649_600, rel_tol=1e-3)  # ~11.65M tokens for 0.02 ETH on a fresh curve
    # selling them straight back returns q_net minus rounding, then 1% fee: a -1.99% round trip
    net, gross = traders.curve_sell(rq + q_net, rt - tokens, tokens, 0.0)
    assert abs(gross - q_net) <= 2 and math.isclose(net, gross * 0.99, rel_tol=1e-9)
    assert math.isclose((net - order) / order, -(1 - 0.99 / 1.01), rel_tol=1e-6)


def test_copy_test_on_a_hand_built_curve(tmp_path):
    db = tmp_path / "c.sqlite"
    s = Store(db)
    lo = T0
    s.db.execute("INSERT INTO launches(token,curve,deployer,pair_token,threshold,launch_config,block,ts,tx_hash,source) VALUES(?,?,?,?,?,?,?,?,?,?)", (TOKEN_C, "0xcc", DEPLOYER, NATIVE, str(chain.CURVE_GRADUATION_THRESHOLD_WEI), 0, 100, lo, "0xl", "ingest"))
    rq, rt = chain.CURVE_PHANTOM_QUOTE_WEI, chain.CURVE_SUPPLY_WEI
    q_l = 10**17
    tok_l = rt - (rq * rt) // (rq + q_l)  # leader buys 0.1 ETH worth on the fresh curve
    rq1, rt1 = rq + q_l, rt - tok_l
    gross = rq1 - (rq1 * rt1) // (rt1 + tok_l)  # leader sells everything back 60 s later
    rows = [
        (lo, 100, TOKEN_C, "0x00000000000000000000000000000000000000aa", "buy", tok_l, q_l, q_l // 100, 0, 0),
        (lo + 60, 101, TOKEN_C, "0x00000000000000000000000000000000000000aa", "sell", tok_l, gross - gross // 100, gross // 100, 0, 0),
        (lo + 4000, 102, TOKEN_C, W3, "buy", 10 * E, 10**15, 10**13, 0, 0),
    ]
    seed(s, rows)
    s.upsert_blocks([(100, lo, 1), (102, lo + 4000, 1)])
    s.commit()
    launches = traders.Launches(s)
    ct = traders.copy_test(s, {10: ["0x00000000000000000000000000000000000000aa"]}, lo, lo + 4000, launches, order_wei=2 * 10**16, hold_s=1800, history_from=lo)
    r = ct["by_k"][10]
    assert ct["events"] == 1 and r["n"] == 1 and r["leader_sold"] == 1 and r["curve_method"] == 1
    # hand computation: we buy after the leader (state rq1, rt1: the leader's 0.1 ETH lifted the price ~12%) with our own
    # impact, and sell after the leader's exit (the curve is back at the fresh state plus our own lot). The copier bought the
    # leader's pump and sells at the pre-pump price: about -11% of price plus two 1% fees and our own impact.
    tokens, q_net, _ = traders.curve_buy(rq1, rt1, 2 * 10**16, 0.0)
    rq2, rt2 = rq1 - gross, rt1 + tok_l
    net, _ = traders.curve_sell(rq2 + q_net, rt2 - tokens, tokens, 0.0)
    assert math.isclose(r["mean_ret"], (net - 2 * 10**16) / 2e16, rel_tol=1e-9)
    pump = (rq1 / rt1) / (rq / rt) - 1  # the leader's price move we buy into (+12.3%); unwinding it costs 1/(1+pump)-1 = -10.9%
    assert math.isclose(pump, 0.1226, rel_tol=1e-2)
    assert (1 / (1 + pump) - 1) - 0.035 < r["mean_ret"] < (1 / (1 + pump) - 1) - 0.015 and r["hit_rate"] == 0.0
    assert r["median_impact_pct"] is not None and 1 < r["median_impact_pct"] < 3  # effective vs marginal price: the 1% fee plus ~1.1% slippage of 0.02 ETH on a 1.78 ETH reserve
    assert math.isclose(r["total_pnl_eth"], (net - 2 * 10**16) / 1e18, rel_tol=1e-9)
    # a leader who bought at the fresh state and never sells: the timed exit at +30 min marks against the same curve, so only the
    # fees and our own impact are lost
    s.db.execute("DELETE FROM trades WHERE side='sell'")
    s.commit()
    ct2 = traders.copy_test(s, {10: ["0x00000000000000000000000000000000000000aa"]}, lo, lo + 4000, launches, order_wei=2 * 10**16, hold_s=1800, history_from=lo)
    r2 = ct2["by_k"][10]
    assert r2["n"] == 1 and r2["leader_sold"] == 0 and math.isclose(r2["mean_ret"], -(1 - 0.99 / 1.01), rel_tol=1e-4)  # the fee round trip only


def test_traders_api_and_wallet_card(tmp_path):
    db = tmp_path / "a.sqlite"
    s = build(db)
    traders.run(s, args(db, min_trades=1), progress=lambda m: None)
    s.close()
    app = create_app(mode="replay", window="30m", db=db, context_policy="off")
    tc = TestClient(app)
    top = tc.get("/api/traders", params={"preset": "top", "min_trades": 1}).json()
    # total PnL marks the deployer's unsold 3000 tokens at the last price (+0.27 ETH unrealized); by realized PnL W1 leads
    assert top["preset"] == "top" and top["total"] == 5 and top["rows"][0]["wallet"] == DEPLOYER and top["rows"][0]["unrealized_eth"] > 0.2
    assert set(top["presets"]) == {"top", "smart", "snipers", "bots"} and top["range"]["from_ts"] == T0
    real = tc.get("/api/traders", params={"preset": "top", "min_trades": 1, "sort": "realized"}).json()
    assert real["rows"][0]["wallet"] == W1 and real["rows"][0]["realized_eth"] > 0
    r0 = real["rows"][0]
    assert r0["short"].startswith("0x0000") and r0["explorer"].endswith(W1) and r0["last_ts"] == T0 + 300 and r0["tags"] == ["sniper"]
    assert r0["pnl_usd"] is None  # no fx rates in this store: unknown, not 0
    smart = tc.get("/api/traders", params={"preset": "smart", "min_trades": 1}).json()
    assert all(not r["is_bot"] and not r["deployer_linked"] for r in smart["rows"]) and DEPLOYER not in {r["wallet"] for r in smart["rows"]}
    bots = tc.get("/api/traders", params={"preset": "bots"}).json()
    assert [r["wallet"] for r in bots["rows"]] == [WB]
    snip = tc.get("/api/traders", params={"preset": "snipers", "min_trades": 1}).json()
    assert W1 in {r["wallet"] for r in snip["rows"]} and W2 not in {r["wallet"] for r in snip["rows"]}
    assert [r["wallet"] for r in tc.get("/api/traders", params={"preset": "top", "min_trades": 1, "min_win_rate": 0.9}).json()["rows"]] == [W1]
    assert [r["wallet"] for r in tc.get("/api/traders", params={"preset": "top", "min_trades": 1, "active_within_s": 10, "clock": T0 + 305}).json()["rows"]] == [W1]
    assert [r["wallet"] for r in tc.get("/api/traders", params={"preset": "top", "min_trades": 1, "min_roi": 5.0}).json()["rows"]] == [DEPLOYER]  # +880% on the unsold launch bag
    assert tc.get("/api/traders", params={"preset": "top", "min_trades": 1, "min_roi": 20.0}).json()["rows"] == []
    card = tc.get(f"/api/wallet/{W1}").json()
    assert card["address"] == W1 and card["stats"]["positions_closed"] == 1 and card["stats"]["tags"] == ["sniper"]
    assert len(card["positions"]) == 1 and card["positions"][0]["closed"] is True and card["positions"][0]["token"]["symbol"] == "AAA" and card["positions"][0]["entry_tx_url"].startswith("https://")
    assert len(card["trades"]) == 4 and card["trades"][0]["ts"] >= card["trades"][-1]["ts"] and card["trades"][0]["tx_url"].startswith("https://")
    assert card["coins"][0]["symbol"] == "AAA" and card["coins"][0]["trades"] == 4
    assert "copy_test" in card and "note" in card
    unknown = tc.get("/api/wallet/0x00000000000000000000000000000000000000ff").json()
    assert unknown["stats"] is None and unknown["trades"] == [] and unknown["positions"] == []
    look = tc.get("/api/traders/lookup", params={"wallets": f"{W1},{WB},0x00000000000000000000000000000000000000ff"}).json()
    assert look["wallets"][W1]["quality"] > 0.5 and look["wallets"][WB]["is_bot"] is True and "0x00000000000000000000000000000000000000ff" not in look["wallets"]
    assert look["top_decile_min_quality"] is None and look["wallets"][W1]["smart"] is False  # nobody has the 5 trades the smart set needs


def test_smart_inflow_feeds_the_radar_score(tmp_path, monkeypatch):
    from stampede.api import radar, traders_api

    db = tmp_path / "s.sqlite"
    s = build(db)
    assert traders_api.smart_inflow(s, {W1, W2}) is None  # no wallet_stats yet: unknown, not 0
    assert radar.score(5, 2.0, 2, None, None, 600, "curve")["parts"]["smart_known"] is False
    traders.run(s, args(db, min_trades=1), progress=lambda m: None)
    monkeypatch.setattr(traders_api, "SMART_MIN_TRADES", 3)
    traders_api._smart_cache.clear()
    top, thr = traders_api.smart_set(s)
    assert list(top) == [W1] and thr == top[W1]  # W1 (4 trades, quality > 0.5) is the top decile of the two eligible wallets
    si = traders_api.smart_inflow(s, {W1, W2, WB})
    assert si == {"count": 1, "mean_quality": round(top[W1], 3), "threshold": thr}
    plain = radar.score(5, 2.0, 2, None, None, 600, "curve")
    smart = radar.score(5, 2.0, 2, None, None, 600, "curve", smart_inflow=1)
    assert smart["parts"]["smart_inflow"] == 3.0 and smart["parts"]["smart_known"] is True and smart["score"] == plain["score"] + 3.0
    assert radar.score(5, 2.0, 2, None, None, 600, "curve", smart_inflow=7)["parts"]["smart_inflow"] == 9.0  # capped
