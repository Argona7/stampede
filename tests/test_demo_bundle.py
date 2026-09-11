"""The bundled demo: export the fixed sample out of a store, pack/unpack it, and boot the replay server on it
with no API key in the environment."""
import json
import os

from fastapi.testclient import TestClient
from helpers import TOKEN_A, TOKEN_B
from stampede import demo
from stampede.api.app import create_app
from stampede.rotation import rotate
from stampede.store import Store

W1 = "0x1111111111111111111111111111111111111111"
W2 = "0x2222222222222222222222222222222222222222"


def build_src(path):
    s = Store(path)
    rows = [(1000, TOKEN_A, W1, "sell"), (1100, TOKEN_B, W1, "buy"), (1300, TOKEN_A, W2, "sell"), (1400, TOKEN_B, W2, "buy")]
    # one trade outside the sample bounds (a "live tail" row) that must not be exported
    rows.append((9000, TOKEN_B, W2, "buy"))
    s.db.executemany(
        "INSERT INTO trades(tx_hash,block,ts,ts_exact,token,wallet,side,token_amount,quote_token,quote_amount,venue,swap_logs,attribution,flags) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(f"0x{i:064x}", 100 + i if ts < 9000 else 900, ts, 1, tok, w, side, "1000000000000000000", "0x0000000000000000000000000000000000000000", "1000", "curve", "[]", "transfer_net", "[]") for i, (ts, tok, w, side) in enumerate(rows, 1)],
    )
    s.db.executemany("INSERT INTO tokens(address,symbol,name,source,curve,first_seen_block) VALUES(?,?,?,?,?,?)", [(TOKEN_A, "AAA", "", "curve", None, 1), (TOKEN_B, "BBB", "", "curve", None, 1)])
    s.db.executemany("INSERT INTO wallets(address,is_contract,trades) VALUES(?,?,?)", [(W1, 0, 2), (W2, 0, 3), ("0x3333333333333333333333333333333333333333", 1, 0)])
    s.upsert_blocks([(101, 1000, 1), (104, 1400, 1), (900, 9000, 1)])
    s.set_meta("sample_from_block", 101)
    s.set_meta("sample_to_block", 104)
    s.set_meta("sample_label", "fixture")
    s.set_meta("live_cursor", 900)
    s.commit()
    rotate(s, 1800)
    rotate(s, 300)
    # a long ambiguous-style candidates blob that the export must compact
    s.db.execute("UPDATE sequences SET candidates=? WHERE window_s=1800 AND wallet=?", (json.dumps({"sold_in_window": ["0x" + f"{i:040x}" for i in range(20)], "sold_in_window_n": 20, "bought_between": [TOKEN_A]}), W2))
    s.commit()
    s.close()


def test_export_pack_unpack_and_boot_without_keys(tmp_path, monkeypatch):
    src = tmp_path / "src.sqlite"
    build_src(src)
    out = tmp_path / "demo.sqlite"
    info = demo.export_demo(src, out, windows=(1800,))
    assert info["rows"]["trades"] == 4, info  # the live-tail row stayed behind
    assert info["rows"]["sequences"] == 2
    assert info["rows"]["wallets"] == 2  # only wallets referenced by exported trades
    assert not out.with_name("demo.sqlite-wal").exists()  # single file after VACUUM

    d = Store(out)
    try:
        assert d.get_meta("sample_label") == "fixture"
        assert d.get_meta("live_cursor") is None
        assert d.get_meta("demo_bundle")["rows"]["trades"] == 4
        cand = json.loads(d.db.execute("SELECT candidates FROM sequences WHERE wallet=?", (W2,)).fetchone()[0])
        assert cand["sold_in_window"] == [] and cand["sold_in_window_n"] == 20 and cand["bought_between"] == [TOKEN_A]
    finally:
        d.close()

    bundle = tmp_path / "bundle" / "stampede-demo.sqlite.xz"
    demo.pack(out, bundle)
    dest = tmp_path / "unpacked.sqlite"
    assert demo.ensure_demo_db(bundle=bundle, dest=dest) == dest
    assert dest.stat().st_size == out.stat().st_size
    # a second call keeps the unpacked copy, refresh re-unpacks it
    assert demo.ensure_demo_db(bundle=bundle, dest=dest) == dest
    assert demo.ensure_demo_db(bundle=bundle, dest=dest, refresh=True) == dest

    for k in ("ALCHEMY_KEY", "TWITTERAPI_KEY", "HYPERSYNC_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    app = create_app(mode="replay", db=dest, speed=20.0, context_policy="off")
    with TestClient(app) as tc:
        st = tc.get("/api/status").json()
        assert st["mode"] == "replay" and st["sample"]["trades"] == 4 and st["store"]["trades"] == 4
        assert st["session"]["from_ts"] == 1000 and st["session"]["to_ts"] == 1400
        assert st["context_worker"]["context_enabled"] is False
        tc.post("/api/session", json={"action": "seek", "ts": 1400})
        ev = tc.get("/api/events?backfill_s=600").json()
        assert ev["kind"] == "history" and {e["wallet"] for e in ev["events"]} == {W1, W2}
        radar = tc.get("/api/radar?preset=all").json()
        assert any(r["address"] == TOKEN_B for r in radar["rows"])
        edge = tc.get(f"/api/edge/{TOKEN_A}/{TOKEN_B}").json()
        assert edge["wallets_main"] == 2 and len(edge["sequences"]) == 2


def test_missing_bundle_is_a_clear_error(tmp_path, monkeypatch):
    monkeypatch.delenv("STAMPEDE_DEMO_URL", raising=False)
    import pytest

    with pytest.raises(SystemExit) as e:
        demo.ensure_demo_db(bundle=tmp_path / "none.xz", dest=tmp_path / "x.sqlite")
    assert "demo bundle not found" in str(e.value)
    assert os.environ.get("STAMPEDE_DEMO_URL") is None
