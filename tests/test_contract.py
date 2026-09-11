"""Shared clock, event stream and counting semantics (the contract both surfaces rely on)."""
import time

import pytest
from helpers import TOKEN_A, TOKEN_B, TOKEN_C
from stampede.api import queries
from stampede.api.session import SessionClock, SessionError
from stampede.normalize import Interp
from stampede.rotation import rotate
from stampede.store import Store

W = 1800


def seed(store: Store, rows):
    store.db.executemany(
        "INSERT INTO trades(tx_hash,block,ts,ts_exact,token,wallet,side,token_amount,quote_token,quote_amount,venue,swap_logs,attribution,flags) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(f"0x{i:064x}", i, ts, 1, tok, w, side, "1", None, "0", "curve", "[]", "transfer_net", "[]") for i, (ts, tok, w, side) in enumerate(rows, 1)],
    )
    store.db.executemany("INSERT OR IGNORE INTO tokens(address,symbol,name,source,curve,first_seen_block) VALUES(?,?,?,?,?,?)", [(TOKEN_A, "AAA", "", "curve", None, 1), (TOKEN_B, "BBB", "", "curve", None, 1), (TOKEN_C, "CCC", "", "curve", None, 1)])
    store.commit()


# ---- counts ----
def test_one_wallet_two_sequences_counts_one_wallet(tmp_path):
    s = Store(tmp_path / "c.sqlite")
    seed(s, [(1000, TOKEN_A, "0xw1", "sell"), (1100, TOKEN_B, "0xw1", "buy"), (1200, TOKEN_B, "0xw1", "buy")])
    rotate(s, W)
    g = queries.graph(s, W, None, None, 1, 50)
    e = g["edges"][0]
    assert (e["wallets_main"], e["sequences"]) == (1, 2)
    assert g["totals"]["sequences_in_range"] == 2 and g["totals"]["wallets_in_range"] == 1


def test_node_unique_wallets_do_not_double_count_across_edges(tmp_path):
    """w1 rotates A->C and B->C: node C has an edge-sum of 2 but exactly 1 unique inbound wallet."""
    s = Store(tmp_path / "c.sqlite")
    seed(s, [(1000, TOKEN_A, "0xw1", "sell"), (1000, TOKEN_B, "0xw1", "sell"), (1300, TOKEN_C, "0xw1", "buy"), (5000, TOKEN_A, "0xw2", "sell"), (5100, TOKEN_C, "0xw2", "buy"), (9000, TOKEN_B, "0xw3", "sell"), (9100, TOKEN_C, "0xw3", "buy")])
    rotate(s, W)
    g = queries.graph(s, W, None, None, 1, 50)
    c = next(n for n in g["nodes"] if n["address"] == TOKEN_C)
    # w1's two sells make its C-buy ambiguous (not in main weight); w2 (A->C) and w3 (B->C) are clean
    assert c["in_edge_wallet_sum"] == 2 and c["in_unique_wallets"] == 2
    s.db.execute("DELETE FROM trades WHERE wallet='0xw1' AND token=?", (TOKEN_B,))
    s.commit()
    rotate(s, W)
    g = queries.graph(s, W, None, None, 1, 50)
    c = next(n for n in g["nodes"] if n["address"] == TOKEN_C)
    # now w1 is clean on A->C: A->C has 2 wallets, B->C has 1: sum 3, unique 3 (w1, w2, w3)
    assert c["in_edge_wallet_sum"] == 3 and c["in_unique_wallets"] == 3
    # an address that is on two different edges into C counts once:
    seed(s, [(20000, TOKEN_B, "0xw2", "sell"), (20100, TOKEN_C, "0xw2", "buy")])
    rotate(s, W)
    g = queries.graph(s, W, None, None, 1, 50)
    c = next(n for n in g["nodes"] if n["address"] == TOKEN_C)
    assert c["in_edge_wallet_sum"] == 4 and c["in_unique_wallets"] == 3


# ---- events ----
def test_events_cursor_pagination_loses_nothing(tmp_path):
    s = Store(tmp_path / "e.sqlite")
    rows = []
    for i in range(60):
        rows.append((1000 + i * 10, TOKEN_A, f"0xw{i}", "sell"))
        rows.append((1005 + i * 10, TOKEN_B, f"0xw{i}", "buy"))
    seed(s, rows)
    rotate(s, W)
    hist = queries.events(s, W, until=1000 + 5 + 10 * 9, after=None, limit=500, backfill_s=10_000)
    assert hist["kind"] == "history" and hist["count"] == 10 and not hist["has_more"]
    cur = hist["next_cursor"]
    seen = [e["id"] for e in hist["events"]]
    # 50 more events arrive before the client polls again; page size 20 -> three pages, nothing lost, no dupes
    until = 1005 + 10 * 59
    while True:
        page = queries.events(s, W, until=until, after=cur, limit=20)
        assert page["kind"] == "new"
        seen += [e["id"] for e in page["events"]]
        cur = page["next_cursor"]
        if not page["has_more"]:
            break
    assert len(seen) == 60 and len(set(seen)) == 60
    # polling the same snapshot again yields nothing new
    again = queries.events(s, W, until=until, after=cur, limit=20)
    assert again["count"] == 0 and again["next_cursor"] == cur
    # events are ordered and none is later than `until`
    assert all(e["buy_ts"] <= until for e in page["events"])


def test_events_history_after_seek_is_not_marked_new(tmp_path):
    s = Store(tmp_path / "e.sqlite")
    seed(s, [(1000, TOKEN_A, "0xw1", "sell"), (1100, TOKEN_B, "0xw1", "buy")])
    rotate(s, W)
    h = queries.events(s, W, until=1100, after=None, backfill_s=300)
    assert h["kind"] == "history" and h["count"] == 1 and h["events"][0]["cursor"] == h["next_cursor"]
    empty = queries.events(s, W, until=900, after=None, backfill_s=300)
    assert empty["count"] == 0 and empty["next_cursor"] == "900:0"


# ---- session clock ----
def test_replay_clock_play_pause_seek_speed():
    c = SessionClock("replay", from_ts=1000, to_ts=5000, span_s=1800, speed=10)
    st = c.state()
    assert st["clock_ts"] == 2800 and not st["playing"] and st["controls"]
    c.control("seek", ts=3000)
    assert c.clock() == 3000
    c.control("speed", speed=100)
    c.control("play")
    time.sleep(0.25)
    t = c.clock()
    assert 3020 <= t <= 3040, t  # 0.25 s * 100x
    c.control("pause")
    frozen = c.clock()
    time.sleep(0.1)
    assert c.clock() == frozen
    c.control("seek", ts=4995)
    c.control("play")
    time.sleep(0.15)
    assert c.clock() == 5000 and not c.playing  # stops at the end of the sample
    assert c.state()["at_end"] is True
    c.control("seek", ts=-50)
    assert c.clock() == 1000  # clamped to the sample start


def test_fixture_and_live_refuse_clock_controls():
    f = SessionClock("fixture", 1000, 5000)
    assert f.clock() == 5000
    with pytest.raises(SessionError) as e:
        f.control("play")
    assert e.value.status == 409
    l = SessionClock("live", None, None)
    assert l.clock() is None and l.state()["controls"] is False
    with pytest.raises(SessionError):
        l.control("seek", ts=1)
    # span/window are allowed everywhere
    f.control("span", span_s=300)
    assert f.state()["span_s"] == 300


# ---- unknown time ----
def test_interp_never_invents_1970():
    assert Interp({})(123) == (None, 0)
    it = Interp({1000: 50_000})
    assert it(1000) == (50_000, 1)
    assert it(1100) == (50_010, 0)  # 100 blocks * 0.1 s extrapolated, marked inexact
    assert it(1000 + 601) == (None, 0)  # too far to guess
    assert it(300) == (None, 0)


# ---- names ----
def test_duplicate_tickers_are_disambiguated_by_address(tmp_path):
    s = Store(tmp_path / "n.sqlite")
    seed(s, [(1000, TOKEN_A, "0xw1", "sell"), (1100, TOKEN_B, "0xw1", "buy")])
    s.db.execute("UPDATE tokens SET symbol='MARIO' WHERE address IN (?, ?)", (TOKEN_A, TOKEN_B))
    s.commit()
    rotate(s, W)
    labels = queries.token_labels(s, {TOKEN_A, TOKEN_B, TOKEN_C})
    assert labels[TOKEN_A]["symbol"] == f"MARIO·{TOKEN_A[-4:]}" and labels[TOKEN_B]["symbol"] == f"MARIO·{TOKEN_B[-4:]}"
    assert labels[TOKEN_A]["symbol_raw"] == "MARIO" and labels[TOKEN_C]["symbol"] == "CCC"  # unique tickers stay as they are
    ev = queries.events(s, W, until=1100, after=None, backfill_s=300)["events"][0]
    assert ev["from_symbol"] != ev["to_symbol"]  # never "MARIO -> MARIO" for two different coins
    hits = queries.search(s, "mario")
    assert len(hits) == 2 and len({h["symbol"] for h in hits}) == 2
