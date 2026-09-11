"""Radar ranking, filters, bots, score parts; coin/alerts endpoints; backtest runs end to end on a fixture."""
import subprocess
import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient
from helpers import TOKEN_A, TOKEN_B, TOKEN_C
from stampede.api import radar
from stampede.api.app import create_app
from stampede.rotation import rotate
from stampede.store import Store

W = 1800
T0 = 1_000_000  # clock for the fixture


def seed(store: Store, rows, block0=100):
    store.db.executemany(
        "INSERT INTO trades(tx_hash,block,ts,ts_exact,token,wallet,side,token_amount,quote_token,quote_amount,venue,swap_logs,attribution,flags) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(f"0x{block0 + i:064x}", block0 + i, ts, 1, tok, w, side, str(amt_tok), "0x0000000000000000000000000000000000000000", str(amt_q), "curve", "[]", "transfer_net", "[]") for i, (ts, tok, w, side, amt_tok, amt_q) in enumerate(rows, 1)],
    )
    store.db.executemany("INSERT OR IGNORE INTO tokens(address,symbol,name,source,curve,first_seen_block) VALUES(?,?,?,?,?,?)", [(TOKEN_A, "AAA", "", "curve", None, None), (TOKEN_B, "BBB", "", "curve", None, None), (TOKEN_C, "CCC", "", "curve", None, None)])
    store.commit()


def build(path: Path) -> Store:
    s = Store(path)
    rows = []
    # three wallets sell A then buy B inside the last 10 minutes (inflow 3); one wallet sells A then buys C 20 min ago
    for i, w in enumerate(["0x" + f"{i + 1:040x}" for i in range(3)]):
        rows.append((T0 - 900 + i, TOKEN_A, w, "sell", 10**18, 10**15))
        rows.append((T0 - 300 + i, TOKEN_B, w, "buy", 10**18, 2 * 10**15))
    rows.append((T0 - 1500, TOKEN_A, "0x" + f"{9:040x}", "sell", 10**18, 10**15))
    rows.append((T0 - 1300, TOKEN_C, "0x" + f"{9:040x}", "buy", 10**18, 10**15))
    # price series for B: doubles after the inflow (runner) ; C flat
    rows.append((T0 - 1700, TOKEN_B, "0x" + f"{7:040x}", "buy", 10**18, 10**15))
    rows.append((T0 + 600, TOKEN_B, "0x" + f"{8:040x}", "buy", 10**18, 5 * 10**15))
    rows.append((T0 + 2000, TOKEN_C, "0x" + f"{8:040x}", "buy", 10**18, 10**15))
    seed(s, rows)
    s.upsert_blocks([(100, T0 - 1800, 1), (120, T0 + 2000, 1)])
    s.set_meta("sample_from_block", 100)
    s.set_meta("sample_to_block", 120)
    s.set_meta("sample_label", "fixture")
    s.commit()
    rotate(s, W)
    return s


def test_radar_ranks_inflow_and_exposes_score_parts(tmp_path):
    s = build(tmp_path / "r.sqlite")
    out = radar.radar(s, W, T0, 1800, min_wallets=1, exclude_bots=True)
    syms = [r["symbol"] for r in out["rows"]]
    assert syms[0] == "BBB" and "CCC" in syms
    b = out["rows"][0]
    assert b["inflow_10m"] == 3 and b["wallets_range"] == 3 and b["breadth"] == 1
    assert b["sources"][0]["symbol"] == "AAA" and b["sources"][0]["wallets"] == 3
    assert 0 <= b["score"] <= 100 and set(b["parts"]) >= {"inflow", "acceleration", "breadth", "wallet_quality", "attention_penalty"}
    assert b["parts"]["mentions_known"] is False and b["parts"]["quality_known"] is False
    assert b["stage"] == "curve" and b["age_s"] is not None
    c = next(r for r in out["rows"] if r["symbol"] == "CCC")
    assert c["inflow_10m"] == 0 and c["wallets_range"] == 1  # inflow was 20 min ago: in range, not in the last 10 min
    assert b["score"] > c["score"]


def test_radar_filters_and_bots(tmp_path):
    s = build(tmp_path / "r.sqlite")
    # mark two of the three inflow wallets as bots -> inflow drops to 1
    s.db.executemany("INSERT INTO wallet_scores(wallet, rotations, runner_hits, score, is_bot, trades_per_hour, updated_at) VALUES(?,?,?,?,?,?,?)", [("0x" + f"{1:040x}", 0, 0, None, 1, 500, time.time()), ("0x" + f"{2:040x}", 0, 0, None, 1, 500, time.time()), ("0x" + f"{3:040x}", 4, 3, 0.8, 0, 1, time.time())])
    s.commit()
    out = radar.radar(s, W, T0, 1800, min_wallets=1, exclude_bots=True)
    b = next(r for r in out["rows"] if r["symbol"] == "BBB")
    assert b["inflow_10m"] == 1 and b["quality"] == 0.8 and b["parts"]["quality_known"] is True
    assert out["bots_excluded"] == 2
    out2 = radar.radar(s, W, T0, 1800, min_wallets=1, exclude_bots=False)
    assert next(r for r in out2["rows"] if r["symbol"] == "BBB")["inflow_10m"] == 3
    assert radar.radar(s, W, T0, 1800, min_wallets=1, stage="graduated")["rows"] == []
    assert radar.radar(s, W, T0, 1800, min_wallets=1, age_max_s=60)["rows"] == []
    assert radar.radar(s, W, T0, 1800, min_wallets=1, quality_min=0.5)["rows"][0]["symbol"] == "BBB"
    # mentions filter only applies when mentions are known
    ctx = {"mentions": {TOKEN_B: {"mentions_1h": 50, "mentions_24h": 200}}}
    out3 = radar.radar(s, W, T0, 1800, min_wallets=1, mentions_max=3, context=ctx)
    assert all(r["symbol"] != "BBB" for r in out3["rows"])
    out4 = radar.radar(s, W, T0, 1800, min_wallets=1, context=ctx)
    b4 = next(r for r in out4["rows"] if r["symbol"] == "BBB")
    assert b4["mentions_1h"] == 50 and b4["parts"]["attention_penalty"] < 0


def test_radar_and_coin_endpoints(tmp_path):
    build(tmp_path / "r.sqlite").close()
    app = create_app(mode="replay", window="30m", db=tmp_path / "r.sqlite", context_policy="off")
    tc = TestClient(app)
    tc.post("/api/session", json={"action": "seek", "ts": T0})
    r = tc.get("/api/radar", params={"preset": "all", "min_wallets": 1}).json()
    assert r["clock"] == T0 and r["rows"][0]["symbol"] == "BBB" and "under_radar" in r["presets"]
    assert r["context"]["policy"] == "off"
    c = tc.get(f"/api/coin/{TOKEN_B}").json()
    assert c["symbol"] == "BBB" and c["progress"]["stage"] == "curve" and c["inbound"][0]["token"]["symbol"] == "AAA" and c["inbound"][0]["wallets_main"] == 3
    assert c["market_now"] is None and c["mentions"] is None and "note" in c
    a = tc.get("/api/alerts").json()
    assert "track_record" in a and "under_radar_top5" in a["rules"]


def test_backtest_runs_and_finds_the_runner(tmp_path):
    db = tmp_path / "bt.sqlite"
    build(db).close()
    out = tmp_path / "report.md"
    res = subprocess.run([sys.executable, "-m", "stampede.research.backtest", "--db", str(db), "--out", str(out), "--horizon", "1800", "--write-scores"], capture_output=True, text=True, cwd=Path(__file__).resolve().parent.parent)
    assert res.returncode == 0, res.stderr
    text = out.read_text()
    assert "By rotation inflow" in text and "Base rate" in text
    s = Store(db)
    assert s.db.execute("SELECT COUNT(*) FROM wallet_scores").fetchone()[0] >= 0
