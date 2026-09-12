"""Launch intelligence (stage 3): dev buy, bundle proxy, creator tax from ratios, deployer history, launch-farm rule,
snipe-tax zero time; the radar `launch` object and the clean_launch preset; the live exempt-set fetch."""
from __future__ import annotations

from fastapi.testclient import TestClient
from stampede import chain
from stampede.api import radar
from stampede.api.app import create_app
from stampede.context import launch_intel as li
from stampede.rotation import rotate
from stampede.store import Store

T0 = 1_700_000_000
ETH = chain.NATIVE
E18 = 10**18
SUPPLY = chain.CURVE_SUPPLY_WEI


def addr(i: int) -> str:
    return "0x" + f"{i:040x}"


TOKEN_X = addr(0xA0)  # the coin the rotating wallets come from
A, B, G, P = addr(0xA1), addr(0xB1), addr(0xC1), addr(0xD1)
F = [addr(0xF1), addr(0xF2), addr(0xF3), addr(0xF4)]
D1, D2, D6, DP = addr(0x1001), addr(0x1002), addr(0x1006), addr(0x100D)
DF = [addr(0x2001), addr(0x2002), addr(0x2003), addr(0x2004)]
W1, W2, W3 = addr(0x3001), addr(0x3002), addr(0x3003)
R = [addr(0x4001), addr(0x4002), addr(0x4003), addr(0x4004)]


class Fixture:
    """Blocks run at exactly 1 block/s from block 1000 = T0, so block distance == seconds (readable tests)."""

    def __init__(self, path):
        self.s = Store(path)
        self.n = 0
        self.trades: list[tuple] = []
        self.launches: list[tuple] = []
        self.grads: list[tuple] = []

    def ts(self, block: int) -> int:
        return T0 + (block - 1000)

    def launch(self, token: str, deployer: str, block: int, tx: str | None = None, pair: str = ETH) -> str:
        tx = tx or f"0xl{token[-4:]}{block:08x}".ljust(66, "0")
        self.launches.append((token, addr(0xCC00 + len(self.launches)), deployer, pair, str(chain.CURVE_GRADUATION_THRESHOLD_WEI), 0, block, None, tx, "test"))
        return tx

    def graduate(self, token: str, block: int) -> None:
        self.grads.append((token, "pool", block, None, f"0xg{block:08x}".ljust(66, "0"), ETH, None))

    def trade(self, token: str, wallet: str, side: str, block: int, tokens: int, quote: int, fee: int = 0, tax: int = 0, snipe: int = 0, tx: str | None = None, venue: str = "curve", quote_token: str = ETH) -> None:
        self.n += 1
        tx = tx or f"0xt{self.n:08x}".ljust(66, "0")
        self.trades.append((tx, block, self.ts(block), 1, token, wallet, side, str(tokens), quote_token, str(quote), venue, "[]", "transfer_net", "[]", str(fee), str(tax), str(snipe)))

    def buy(self, token, wallet, block, quote, tokens, tax_bps=0, snipe_bps=0, tx=None):
        tax = quote * tax_bps // 10_000
        snipe = quote * snipe_bps // 10_000
        self.trade(token, wallet, "buy", block, tokens, quote, fee=quote // 100 + snipe, tax=tax, snipe=snipe, tx=tx)

    def commit(self, lo=1000, hi=5000) -> Store:
        s = self.s
        s.upsert_blocks((b, self.ts(b), 1) for b in range(lo, hi + 1, 1))
        s.db.executemany("INSERT OR REPLACE INTO launches(token,curve,deployer,pair_token,threshold,launch_config,block,ts,tx_hash,source) VALUES(?,?,?,?,?,?,?,?,?,?)", self.launches)
        s.db.executemany("INSERT OR REPLACE INTO graduations(token,stage,block,ts,tx_hash,quote_token,creator) VALUES(?,?,?,?,?,?,?)", self.grads)
        s.db.executemany("INSERT INTO trades(tx_hash,block,ts,ts_exact,token,wallet,side,token_amount,quote_token,quote_amount,venue,swap_logs,attribution,flags,fee_raw,tax_raw,snipe_raw) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", self.trades)
        s.db.executemany("INSERT OR IGNORE INTO tokens(address,symbol,name,source,curve,first_seen_block) VALUES(?,?,?,?,?,?)", [(t, sym, "", "curve", None, None) for t, sym in ((A, "AAA"), (B, "BBB"), (G, "GGG"), (P, "PPP"), (TOKEN_X, "XXX"), *((f, f"F{i}") for i, f in enumerate(F)))])
        s.set_meta("sample_from_block", lo)
        s.set_meta("sample_to_block", hi)
        s.set_meta("sample_label", "launch intel fixture")
        s.commit()
        return s


def build(path) -> Store:
    fx = Fixture(path)
    # A: launchAndBuy dev buy of 2% for 0.03 ETH at 300 bps creator tax; a taxed sniper at +1 s, an exempt bundle wallet
    # at +2 s, the first untaxed entry at +3 s at 2x the dev price, then a 5x within the hour (runner)
    tx_a = fx.launch(A, D1, 1000)
    fx.buy(A, D1, 1000, quote=3 * 10**16, tokens=SUPPLY // 50, tax_bps=300, tx=tx_a)
    fx.buy(A, W1, 1001, quote=10**16, tokens=SUPPLY // 200, tax_bps=300, snipe_bps=618)
    fx.buy(A, W2, 1002, quote=10**16, tokens=SUPPLY // 100, tax_bps=300, snipe_bps=0)
    fx.buy(A, W3, 1003, quote=3 * 10**16, tokens=SUPPLY // 100, tax_bps=300)  # price = 3e-9 per token... 2x the dev price
    fx.buy(A, W3, 1600, quote=15 * 10**16, tokens=SUPPLY // 100, tax_bps=300)  # 5x the entry price
    # B: no launch-tx buy; the deployer buys 1% four seconds later (counts as dev buy); 3 exempt wallets at +1 s;
    # creator tax only readable from a sell (200 bps of gross); no untaxed entry until +10 s, then flat
    fx.launch(B, D2, 1500)
    fx.buy(B, DF[3], 1501, quote=10**16, tokens=SUPPLY // 300, tax_bps=200, snipe_bps=0)
    fx.buy(B, W1, 1501, quote=10**16, tokens=SUPPLY // 300, tax_bps=200, snipe_bps=0)
    fx.buy(B, W2, 1501, quote=10**16, tokens=SUPPLY // 300, tax_bps=200, snipe_bps=0)
    fx.buy(B, D2, 1504, quote=2 * 10**16, tokens=SUPPLY // 100, tax_bps=200)
    gross = 5 * 10**15
    fx.trade(B, W3, "sell", 1510, tokens=SUPPLY // 400, quote=gross - gross // 100 - gross // 50, fee=gross // 100, tax=gross // 50)
    fx.buy(B, W3, 1700, quote=6 * 10**15, tokens=SUPPLY // 400, tax_bps=200)
    # farm: F1..F3 by fresh deployers within 30 min, identical fingerprint (0.05 ETH, 100 bps); F4 identical but 40 min later
    for i, blk in enumerate((2000, 2100, 2200, 4500)):
        tx = fx.launch(F[i], DF[i], blk)
        fx.buy(F[i], DF[i], blk, quote=5 * 10**16, tokens=SUPPLY // 40, tax_bps=100, tx=tx)
        fx.buy(F[i], W1, blk + 5, quote=10**16, tokens=SUPPLY // 200, tax_bps=100)
    # G: deployer D6 launched three coins before (one graduated before G, one after G) -> prior 3, graduated 1
    for blk in (1100, 1200, 1300):
        fx.launch(addr(0xE000 + blk), D6, blk)
    fx.graduate(addr(0xE000 + 1100), 1250)
    fx.graduate(addr(0xE000 + 1200), 1450)
    tx_g = fx.launch(G, D6, 1400)
    fx.buy(G, D6, 1400, quote=10**16, tokens=SUPPLY // 100, tax_bps=0, tx=tx_g)
    fx.buy(G, W2, 1405, quote=10**16, tokens=SUPPLY // 100, tax_bps=0)
    # P: launched before the indexed range, trades inside it -> deployer record only
    fx.launch(P, DP, 500)
    fx.buy(P, W1, 1005, quote=10**16, tokens=SUPPLY // 100, tax_bps=100)
    # rotation inflow for the radar: two wallets sell X then buy A; two sell X then buy F1 (a farm coin)
    for i, w in enumerate(R[:2]):
        fx.buy(TOKEN_X, w, 900 + i, quote=10**15, tokens=10**18)
        fx.trade(TOKEN_X, w, "sell", 3000 + i, tokens=10**18, quote=10**15)
        fx.buy(A, w, 3100 + i, quote=10**15, tokens=10**24)
    for i, w in enumerate(R[2:]):
        fx.buy(TOKEN_X, w, 950 + i, quote=10**15, tokens=10**18)
        fx.trade(TOKEN_X, w, "sell", 3200 + i, tokens=10**18, quote=10**15)
        fx.buy(F[0], w, 3300 + i, quote=10**15, tokens=10**24)
    s = fx.commit(lo=900, hi=5000)
    rotate(s, 1800)
    return s


def intel(s: Store, token: str) -> dict:
    return li.intel_for(s, [token])[token]


def test_protocol_arithmetic():
    assert [li.snipe_tax_bps(e) for e in range(5)] == [9800, 618, 19, 0, 0]
    assert li.snipe_tax_bps(0, creator_tax_bps=300) == 9500  # capped at 10000 - fee - tax - 100
    q = 15 * 10**15
    assert li.creator_tax_bps_of("buy", q, q // 100, q // 100) == 100
    assert li.creator_tax_bps_of("buy", q, 0, q * 175 // 10_000) == 175
    gross = 3 * 10**15
    assert li.creator_tax_bps_of("sell", gross - gross // 100 - gross // 400, gross // 100, gross // 400) == 25
    assert li.creator_tax_bps_of("buy", q, 0, q) is None  # 100% is not a creator tax
    assert li.creator_tax_bps_of("buy", 0, 0, 5) is None


def test_features_per_launch(tmp_path):
    s = build(tmp_path / "li.sqlite")
    st = li.compute(s, horizon_s=1800, gain=1.0)
    assert st["deployer_pass"]["launches"] == 11 and st["deployer_pass"]["observed"] == 10  # P predates the range
    assert abs(st["blocks_per_s"] - 1.0) < 1e-6
    a = intel(s, A)
    assert a["observed"] is True and a["dev_buy_in_launch_tx"] is True
    assert abs(a["dev_buy_share"] - 0.02) < 1e-12 and abs(a["dev_buy_quote"] - 0.03) < 1e-12 and a["pair_symbol"] == "ETH"
    assert a["creator_tax_bps"] == 300
    assert a["bundle_n"] == 1 and abs(a["bundle_share"] - 0.01) < 1e-12  # W2: untaxed inside the window; W1 paid, W3 came after it
    assert a["taxed_snipers_3s"] == 1 and abs(a["snipe_tax_paid_quote"] - 0.0618 * 0.01) < 1e-12
    assert a["first_buyers_5s"] == 4 and a["snipe_tax_zero_ts"] == T0 + 3 and a["snipe_window_s"] == 3
    assert a["deployer_prior_launches_30d"] == 0 and a["deployer_graduation_rate"] is None and a["launch_farm"] is False
    assert a["exempt_declared_n"] is None and a["socials_present"] is None  # no logs, no calldata parse: unknown, not 0
    b = intel(s, B)
    assert b["dev_buy_in_launch_tx"] is False and abs(b["dev_buy_share"] - 0.01) < 1e-12  # the deployer's buy 4 s later
    assert b["bundle_n"] == 3 and b["creator_tax_bps"] == 200  # the buys carry no readable... they do: 200 bps on quote-in
    assert b["taxed_snipers_3s"] == 0
    g = intel(s, G)
    assert (g["deployer_prior_launches_30d"], g["deployer_prior_graduations_30d"]) == (3, 1) and abs(g["deployer_graduation_rate"] - 1 / 3) < 1e-9
    assert g["creator_tax_bps"] == 0  # first curve trades carry no tax: the rate is zero, not unknown
    p = intel(s, P)
    assert p["observed"] is False and p["dev_buy_share"] is None and p["bundle_n"] is None and p["launch_farm"] is None
    assert p["deployer_prior_launches_30d"] == 0  # deployer history is still computed for launches before the range


def test_launch_farm_rule(tmp_path):
    s = build(tmp_path / "li.sqlite")
    li.compute(s, horizon_s=1800)
    rows = {f: intel(s, f) for f in F}
    assert all(rows[f]["launch_farm"] is True and rows[f]["farm_group_n"] == 3 for f in F[:3])
    assert rows[F[3]]["launch_farm"] is False and rows[F[3]]["farm_group_n"] == 1  # same fingerprint, 40 min away
    a = intel(s, A)
    assert a["launch_farm"] is False  # unique fingerprint


def test_backtest_records_and_report(tmp_path):
    s = build(tmp_path / "li.sqlite")
    st = li.compute(s, horizon_s=1800, gain=1.0)
    rec = {r["tok"]: r for r in st["records"]}
    a = rec[A]
    assert a["entry_ts"] == T0 + 3 and a["complete"] is True and a["runner"] is True and abs(a["max_gain"] - 4.0) < 1e-9
    assert abs(a["p0"] * 2 - a["entry_price"]) < 1e-18  # the untaxed entry paid twice the dev price
    b = rec[B]
    assert b["entry_ts"] == T0 + 504 and b["runner"] is False  # the deployer's own buy at +4 s is the first untaxed trade; +20% at most afterwards
    assert abs(b["max_gain"] - 0.2) < 1e-9
    assert rec[F[3]]["complete"] is False  # horizon runs past the end of the data: dropped from the tables
    text = li.report(s, st, "fixture.sqlite")
    for needle in ("## Definitions", "By dev buy", "By launch farm flag", "Entry at t = 3 s versus t = 0 s", "| 1 s | 618 |", "clean launch", "Exact commands"):
        assert needle in text, needle


def test_compute_is_idempotent_and_bounded_by_limit(tmp_path):
    s = build(tmp_path / "li.sqlite")
    li.compute(s, horizon_s=1800)
    first = s.db.execute("SELECT * FROM launch_intel ORDER BY token").fetchall()
    li.compute(s, horizon_s=1800)
    again = s.db.execute("SELECT * FROM launch_intel ORDER BY token").fetchall()
    assert [r[:-1] for r in first] == [r[:-1] for r in again]  # everything but computed_at
    st = li.compute(s, horizon_s=1800, limit=2)
    assert st["trades_pass"]["observed_launches"] == 2


def test_radar_rows_carry_launch_and_clean_preset_filters(tmp_path):
    s = build(tmp_path / "li.sqlite")
    clock = T0 + 2400
    out = radar.radar(s, 1800, clock, 1800, min_wallets=1)
    rows = {r["symbol"]: r for r in out["rows"]}
    assert rows["AAA"]["launch"] is None  # launch intel not computed yet: n/a, and the clean preset passes nobody
    assert radar.radar(s, 1800, clock, 1800, min_wallets=1, **{k: v for k, v in radar.PRESETS["clean_launch"].items() if k != "label"})["rows"] == []
    li.compute(s, horizon_s=1800)
    out = radar.radar(s, 1800, clock, 1800, min_wallets=1)
    rows = {r["symbol"]: r for r in out["rows"]}
    assert {"AAA", "F0"} <= set(rows)
    assert rows["AAA"]["launch"]["bundle_n"] == 1 and rows["AAA"]["launch"]["launch_farm"] is False and rows["AAA"]["launch"]["snipe_tax_zero_ts"] == T0 + 3
    assert rows["F0"]["launch"]["launch_farm"] is True
    clean = radar.radar(s, 1800, clock, 1800, min_wallets=1, **{k: v for k, v in radar.PRESETS["clean_launch"].items() if k != "label"})
    assert [r["symbol"] for r in clean["rows"]] == ["AAA"]
    assert [r["symbol"] for r in radar.radar(s, 1800, clock, 1800, min_wallets=1, bundle_max=0)["rows"]] == ["F0"]  # AAA has one bundle wallet; F0 none (farm, but exclude_farm is off)
    assert "launch" in radar.SCORE_NOTES and "clean_launch" in radar.PRESETS


def test_api_returns_launch_object_and_preset(tmp_path):
    s = build(tmp_path / "li.sqlite")
    li.compute(s, horizon_s=1800)
    s.close()
    app = create_app(mode="replay", window="30m", db=tmp_path / "li.sqlite", context_policy="off")
    tc = TestClient(app)
    tc.post("/api/session", json={"action": "seek", "ts": T0 + 2400})
    r = tc.get("/api/radar", params={"preset": "all", "min_wallets": 1}).json()
    assert "clean_launch" in r["presets"]
    a = next(x for x in r["rows"] if x["symbol"] == "AAA")
    assert abs(a["launch"]["dev_buy_share"] - 0.02) < 1e-12 and a["launch"]["creator_tax_bps"] == 300 and a["launch"]["observed"] is True
    c = tc.get("/api/radar", params={"preset": "clean_launch", "min_wallets": 1}).json()
    assert [x["symbol"] for x in c["rows"]] == ["AAA"] and c["preset"] == "clean_launch"
    coin = tc.get(f"/api/coin/{A}").json()
    assert coin["launch"]["deployer"] == D1 and coin["launch"]["intel"]["bundle_n"] == 1 and coin["launch"]["intel"]["snipe_tax_zero_ts"] == T0 + 3
    coin_p = tc.get(f"/api/coin/{P}").json()
    assert coin_p["launch"]["intel"]["observed"] is False and coin_p["launch"]["intel"]["dev_buy_share"] is None


def test_fetch_exempt_reads_receipt_once_and_counts_declared_wallets(tmp_path):
    s = build(tmp_path / "li.sqlite")
    li.compute(s, horizon_s=1800)
    tx = s.db.execute("SELECT tx_hash FROM launches WHERE token=?", (A,)).fetchone()[0]

    def t(a: str) -> str:
        return "0x" + a[2:].rjust(64, "0")

    class FakeRpc:
        calls = 0

        def get_receipt(self, h):
            self.calls += 1
            assert h == tx
            logs = [(chain.T_SNIPE_TAX_EXEMPTED, [t(D1)]), (chain.T_SNIPE_TAX_EXEMPTED, [t(D1)]), (chain.T_SNIPE_TAX_EXEMPTED, [t(W2)]), (chain.T_SNIPE_TAX_EXEMPTED, [t(W2)]), (chain.T_SNIPE_TAX_EXEMPTED, [t(W3)]), (chain.T_TOKEN_LAUNCHED, [t(A), t(addr(1)), t(D1)])]
            return {"logs": [{"transactionHash": tx, "logIndex": hex(i), "blockNumber": hex(1000), "address": addr(0xCC00), "topics": [t0, *rest], "data": "0x"} for i, (t0, rest) in enumerate(logs)]}

    rpc = FakeRpc()
    assert li.fetch_exempt(s, rpc, A) == 2  # W2 and W3; the deployer and duplicates do not count
    assert intel(s, A)["exempt_declared_n"] == 2
    assert s.db.execute("SELECT COUNT(*) FROM logs WHERE kind='snipe_tax_exempted' AND tx_hash=?", (tx,)).fetchone()[0] == 5
    assert li.fetch_exempt(s, rpc, A) == 2 and rpc.calls == 1  # second call answered from the indexed logs
    # a recompute keeps the exact count from the logs
    li.compute(s, horizon_s=1800)
    assert intel(s, A)["exempt_declared_n"] == 2


def test_tui_launch_line():
    from stampede.tui.app import launch_line

    assert launch_line(None).startswith("launch: n/a")
    assert "before the indexed range" in launch_line({"observed": False, "deployer_prior_launches_30d": 4})
    line = launch_line({"observed": True, "dev_buy_share": 0.02, "bundle_n": 1, "exempt_declared_n": None, "creator_tax_bps": 300, "deployer_prior_launches_30d": 3, "deployer_graduation_rate": 1 / 3, "launch_farm": False, "snipe_tax_zero_ts": T0 + 3, "snipe_window_s": 3})
    assert line == "launch: dev 2.00% · bundle 1 · tax 300 bps · dep grad 33% of 3 · farm no · tax-free from 22:13:23 UTC (+3s)"
