"""Realtime engine: block assembly (out-of-order logs, grace, deferral, gaps + backfill), curve reserves against the
PONS economics, incremental sequences == rotate(), the SSE ring with Last-Event-ID, /api/perf. No network."""
from __future__ import annotations

import json
import queue
import time

from fastapi.testclient import TestClient
from helpers import CURVE_A, CURVE_C, POOL_B, TOKEN_A, TOKEN_B, TOKEN_C, WALLET_1, WALLET_2, curve_buy, curve_sell, transfer, v4_swap
from stampede import chain
from stampede.api.app import create_app
from stampede.engine import feed as feed_mod
from stampede.engine.bus import Bus, Histogram
from stampede.engine.feed import BACKFILL_TOPICS, Block, BlockAssembler, PonsBloom, bloom_mask, make_backfill_fn, parse_log
from stampede.engine.runner import Engine, Writer
from stampede.engine.state import CurveReserve, EngineState
from stampede.rotation import rotate
from stampede.store import Store

WALLET_3 = "0x0000000000000000000000000000000000003333"
ALL_ONES = "0x" + "ff" * 256
ZERO_BLOOM = "0x" + "00" * 256


# ---- frame builders ---------------------------------------------------------------------------------------------------
def head(n: int, ts: int = 1_700_000_000, bloom: str = ALL_ONES) -> dict:
    return {"number": hex(n), "timestamp": hex(ts), "hash": "0x" + f"{n:064x}", "logsBloom": bloom}


def rpc_log(n: int, tx: str, li: int, topic0: str, address: str, txi: int = 0, topics: list[str] | None = None, data: str = "0x" + "00" * 128) -> dict:
    return {"logIndex": hex(li), "address": address, "topics": [topic0] + (topics or []), "data": data, "blockNumber": hex(n), "transactionHash": tx, "transactionIndex": hex(txi)}


def tagged(l: dict, n: int, tx: str, txi: int = 0) -> dict:
    """helpers' store-shaped log -> the feed's log dict."""
    return {**l, "block": n, "tx_hash": tx, "tx_index": txi, "removed": False}


def block(n: int, ts: int, txs: list[tuple[str, list[dict]]], source: str = "live") -> Block:
    logs = [tagged(l, n, tx, i) for i, (tx, ls) in enumerate(txs) for l in ls]
    logs.sort(key=lambda l: l["log_index"])
    t = time.time()
    return Block(n, ts, "0x" + f"{n:064x}", logs, t, t, t, source)


# ---- assembly -------------------------------------------------------------------------------------------------------
def test_assembler_releases_in_order_with_out_of_order_logs_and_grace():
    asm = BlockAssembler(pons_pool=lambda pid: False, bloom=PonsBloom())
    t = 1000.0
    # block 10 has a curve buy in tx A whose transfers arrive out of order on the transfer connection
    asm.on_head(head(10), t)
    asm.on_log(parse_log(rpc_log(10, "0xa", 5, chain.T_CURVE_BUY, CURVE_A, 2)), "fast", t + 0.002)
    asm.on_log(parse_log(rpc_log(10, "0xa", 7, chain.T_TRANSFER, TOKEN_A, 2, ["0x" + "0" * 64, "0x" + "0" * 64])), "bulk", t + 0.010)
    asm.on_log(parse_log(rpc_log(10, "0xa", 6, chain.T_TRANSFER, TOKEN_A, 2, ["0x" + "0" * 64, "0x" + "0" * 64])), "bulk", t + 0.012)
    assert asm.poll(t + 0.02) == []  # header in, but neither side has passed the block yet
    asm.on_head(head(11), t + 0.1)  # fast boundary
    assert asm.poll(t + 0.11) == []  # a PONS swap: still waits for the transfer stream
    asm.on_log(parse_log(rpc_log(11, "0xb", 0, chain.T_TRANSFER, TOKEN_B, 0, ["0x" + "0" * 64, "0x" + "0" * 64])), "bulk", t + 0.15)
    out = asm.poll(t + 0.151)
    assert [b.number for b in out] == [10]
    assert [l["log_index"] for l in out[0].logs] == [5, 6, 7] and out[0].flags == set() and out[0].source == "live"
    assert out[0].t_head_received == t and out[0].t_logs_complete == t + 0.151 and out[0].ts == 1_700_000_000
    # block 11 has no PONS swap; the bloom says "maybe": released after the 250 ms grace with the flag, not before
    assert asm.poll(t + 0.1 + 0.24) == []
    out = asm.poll(t + 0.1 + 0.26)
    assert [(b.number, sorted(b.flags)) for b in out] == [(11, ["fast_grace"])]
    # a header whose bloom cannot contain PONS activity is released at once
    asm.on_head(head(12, bloom=ZERO_BLOOM), t + 0.4)
    out = asm.poll(t + 0.4)
    assert [(b.number, sorted(b.flags)) for b in out] == [(12, ["bloom_skip"])]
    assert asm.stats["released"] == 3 and asm.stats["fast_grace"] == 1


def test_assembler_defers_transactions_with_incomplete_transfers():
    asm = BlockAssembler(pons_pool=lambda pid: False, bloom=PonsBloom())
    t = 2000.0
    z = ["0x" + "0" * 64, "0x" + "0" * 64]
    asm.on_head(head(20), t)
    asm.on_log(parse_log(rpc_log(20, "0xa1", 1, chain.T_CURVE_BUY, CURVE_A, 1)), "fast", t + 0.001)
    asm.on_log(parse_log(rpc_log(20, "0xa5", 9, chain.T_CURVE_SELL, CURVE_C, 5)), "fast", t + 0.001)
    asm.on_log(parse_log(rpc_log(20, "0xa1", 2, chain.T_TRANSFER, TOKEN_A, 1, z)), "bulk", t + 0.02)
    asm.on_log(parse_log(rpc_log(20, "0xa3", 6, chain.T_TRANSFER, TOKEN_B, 3, z)), "bulk", t + 0.03)  # transfer stream reached tx index 3
    asm.on_head(head(21), t + 0.1)
    assert asm.poll(t + 0.9) == []  # transfers of tx index 5 may still be streaming: held until the cap
    out = asm.poll(t + 1.01)
    assert [b.number for b in out] == [20, 21] and "bulk_cap" in out[0].flags and out[0].deferred_txs == 1 and "fast_grace" in out[1].flags
    assert {l["tx_hash"] for l in out[0].logs} == {"0xa1", "0xa3"}  # tx a5 is not attributed from a partial set
    # its transfers arrive late, then the transfer stream passes the block -> one fragment with the complete tx
    asm.on_log(parse_log(rpc_log(20, "0xa5", 10, chain.T_TRANSFER, TOKEN_C, 5, z)), "bulk", t + 1.2)
    asm.on_log(parse_log(rpc_log(21, "0xb", 0, chain.T_TRANSFER, TOKEN_B, 0, z)), "bulk", t + 1.3)
    out = asm.poll(t + 1.31)
    frags = [b for b in out if b.source == "late"]
    assert len(frags) == 1 and frags[0].number == 20 and [l["log_index"] for l in frags[0].logs] == [9, 10] and "deferred" in frags[0].flags
    assert asm.stats["deferred_txs"] == 1 and asm.stats["fragments"] == 1


def test_gap_detection_and_backfill_fill_the_hole_in_order():
    asm = BlockAssembler(pons_pool=lambda pid: False, bloom=PonsBloom())
    t = 3000.0
    asm.on_head(head(30, bloom=ZERO_BLOOM), t)
    asm.on_head(head(31, bloom=ZERO_BLOOM), t + 0.1)
    assert [b.number for b in asm.poll(t + 0.1)] == [30, 31]
    asm.on_head(head(35, bloom=ZERO_BLOOM), t + 0.5)  # 32..34 never came
    assert asm.take_gap_requests() == [(32, 34)] and asm.gaps[-1]["reason"] == "missing heads"
    assert asm.poll(t + 0.6) == []  # strict order: 35 waits behind the gap
    calls: list[tuple[int, int, list]] = []

    class FakeRpc:
        def get_blocks(self, numbers):
            return {n: {"timestamp": hex(1_700_000_000 + n), "hash": "0x" + f"{n:064x}"} for n in numbers}

        def get_logs_parallel(self, lo, hi, topics=None, workers=4):
            calls.append((lo, hi, topics))
            logs = [rpc_log(33, "0xd", 1, chain.T_CURVE_SELL, CURVE_C, 0), rpc_log(33, "0xd", 2, chain.T_TRANSFER, TOKEN_C, 0, ["0x" + "0" * 64, "0x" + "0" * 64]), rpc_log(34, "0xe", 0, chain.T_V4_SWAP, "0x" + "ee" * 20)]
            return logs, []

    got = make_backfill_fn(FakeRpc())(32, 34)
    assert calls == [(32, 34, BACKFILL_TOPICS)] and sorted(got) == [32, 33, 34]
    assert [l["log_index"] for l in got[33][2]] == [1, 2] and got[34][2] == []  # a Swap from another v4 fork is not a PONS log
    for n, (ts, h, logs) in got.items():
        asm.on_backfill(n, ts, h, logs, t + 1.0)
    out = asm.poll(t + 1.0)
    assert [(b.number, b.source) for b in out] == [(32, "backfill"), (33, "backfill"), (34, "backfill"), (35, "live")]
    assert out[1].ts == 1_700_000_033 and len(out[1].logs) == 2
    assert asm.stats["gaps"] == 1 and asm.stats["gap_blocks"] == 3 and asm.stats.get("gap_unfilled_blocks", 0) == 0


def test_stalled_log_subscriptions_are_detected_and_the_window_is_refetched():
    """Heads keep flowing but no PONS log arrives for 60 blocks whose bloom expects them: the assembler reports the stall;
    the reconnect registers the whole empty window (not just the last block) for the HTTP backfill."""
    asm = BlockAssembler(pons_pool=lambda pid: False, bloom=PonsBloom())
    t = 7000.0
    for i, n in enumerate(range(500, 530)):  # healthy: every other block has a curve log
        asm.on_head(head(n), t + i * 0.1)
        if i % 2 == 0:
            asm.on_log(parse_log(rpc_log(n, f"0x{n:x}", 1, chain.T_CURVE_SELL, CURVE_A, 0)), "fast", t + i * 0.1 + 0.001)
            asm.on_log(parse_log(rpc_log(n, f"0x{n:x}", 2, chain.T_TRANSFER, TOKEN_A, 0, ["0x" + "0" * 64, "0x" + "0" * 64])), "bulk", t + i * 0.1 + 0.02)
            asm.on_log(parse_log(rpc_log(n + 1, "0xb", 0, chain.T_TRANSFER, TOKEN_B, 0, ["0x" + "0" * 64, "0x" + "0" * 64])), "bulk", t + i * 0.1 + 0.09)
        asm.poll(t + i * 0.1 + 0.095)
    assert not asm.fast_logs_dead and asm.fast_empty_since in (None, 529)
    for i, n in enumerate(range(530, 600)):  # the log subscriptions die: heads only, blooms still say "maybe"
        asm.on_head(head(n), t + 3 + i * 0.1)
        asm.poll(t + 3 + i * 0.1 + 0.05)
    assert asm.fast_logs_dead and asm.fast_empty_since == 529 or asm.fast_empty_since == 530
    dead_from = asm.fast_empty_since
    asm.on_connected("fast")  # the feed reconnected
    asm.on_head(head(600), t + 11)
    req = asm.take_gap_requests()
    assert req == [(dead_from, 599)] and asm.gaps[-1]["reason"] == "fast (re)connect" and not asm.fast_logs_dead
    assert all(asm.late[n].wait_backfill for n in range(dead_from, 599)) and asm.stats["gap_blocks"] == 599 - dead_from + 1
    # a healthy bulk stream is 0-2 blocks behind the heads; 50 behind means it stalled (here it stopped at 529 too)
    assert asm.bulk_dead
    asm.bulk_max = 598
    assert not asm.bulk_dead


def test_bloom_filter_has_no_false_negatives():
    pb = PonsBloom([POOL_B])
    b = bloom_mask(bytes.fromhex(chain.V4_POOL_MANAGER[2:])) | bloom_mask(bytes.fromhex(chain.T_V4_SWAP[2:])) | bloom_mask(bytes.fromhex(POOL_B[2:]))
    assert pb.expect_fast("0x" + f"{b:0512x}") is True
    other_pool = bloom_mask(bytes.fromhex(chain.V4_POOL_MANAGER[2:])) | bloom_mask(bytes.fromhex(chain.T_V4_SWAP[2:])) | bloom_mask(bytes.fromhex("ab" * 32))
    assert pb.expect_fast("0x" + f"{other_pool:0512x}") is False  # a swap on a pool the engine does not follow
    assert pb.expect_fast("0x" + f"{bloom_mask(bytes.fromhex(chain.T_CURVE_BUY[2:])):0512x}") is True
    assert pb.expect_fast(ZERO_BLOOM) is False and pb.expect_fast(None) is True


# ---- reserves ---------------------------------------------------------------------------------------------------------
def test_reserve_reconstruction_follows_the_curve_economics():
    rs = CurveReserve(TOKEN_A, chain.NATIVE)
    Q, T = chain.CURVE_PHANTOM_QUOTE_WEI, chain.CURVE_SUPPLY_WEI
    quote_in = 10**16  # 0.01 ETH at t=0: 1% fee, 1% creator tax and the 99% snipe tax (inside `fee`) on the remainder
    tax = quote_in * chain.CURVE_FEE_BPS // 10000
    fee_base = quote_in * chain.CURVE_FEE_BPS // 10000
    snipe = (quote_in - fee_base - tax) * chain.SNIPE_TAX_START_BPS // 10000
    fee = fee_base + snipe
    net = quote_in - fee - tax
    out = rs.tokens_out(net)
    assert out == net * T // (Q + net) and out > 0
    rs.apply_buy(quote_in, out, fee, tax, block=1, ts=1)
    assert rs.quote == net and rs.tokens == T - out and rs.buys == 1
    assert rs.progress() == net / chain.CURVE_GRADUATION_THRESHOLD_WEI and abs(rs.price() - (Q + net) / (T - out)) < 1e-12
    # a later buy without snipe tax
    q2 = 10**17
    fee2 = tax2 = q2 // 100
    net2 = q2 - fee2 - tax2
    out2 = rs.tokens_out(net2)
    assert out2 == net2 * rs.tokens // (Q + rs.quote + net2)
    rs.apply_buy(q2, out2, fee2, tax2, block=2, ts=2)
    assert rs.quote == net + net2 and rs.tokens == T - out - out2
    # selling everything back: gross = quoteOut + fee + tax leaves the reserve, tokens come back
    gross = rs.quote_out(out2)
    fee3 = tax3 = gross // 100
    rs.apply_sell(out2, gross - fee3 - tax3, fee3, tax3, block=3, ts=3)
    assert rs.tokens == T - out and rs.quote == net + net2 - gross
    # events at or before a chain snapshot are already inside it
    rs2 = CurveReserve(TOKEN_A, chain.NATIVE, complete=False)
    assert rs2.price() is None and rs2.progress() is None and rs2.needs_snapshot
    rs2.snapshot(quote=5 * 10**17, tokens=T // 2, block=100, phantom=Q, threshold=chain.CURVE_GRADUATION_THRESHOLD_WEI)
    rs2.apply_buy(10**16, 1, 0, 0, block=100, ts=1)  # ignored: block <= synced_block
    assert rs2.quote == 5 * 10**17 and rs2.tokens == T // 2 and not rs2.needs_snapshot
    rs2.apply_buy(10**16, 1, 0, 0, block=101, ts=1)
    assert rs2.quote == 5 * 10**17 + 10**16
    # non-ETH launch configs have their own phantom: unknown until snapshotted, progress still works
    rs3 = CurveReserve(TOKEN_C, "0x" + "55" * 20, phantom=None, threshold=8_090_000_000)
    rs3.apply_buy(1_000_000, 10**24, 10_000, 10_000, block=1, ts=1)
    assert rs3.price() is None and rs3.progress() == 980_000 / 8_090_000_000 and rs3.needs_snapshot


# ---- state: trades, incremental sequences, radar rows ---------------------------------------------------------------
def seeded_store(path) -> Store:
    s = Store(path)
    s.upsert_curves([(CURVE_A, TOKEN_A, chain.NATIVE, "test"), (CURVE_C, TOKEN_C, chain.NATIVE, "test")])
    s.upsert_tokens([(TOKEN_A, "AAA", "", "curve", CURVE_A, 1), (TOKEN_B, "BBB", "", "v4_pool", None, 1), (TOKEN_C, "CCC", "", "curve", CURVE_C, 1)])
    s.upsert_pools([(POOL_B, chain.NATIVE, TOKEN_B, 0, 200, chain.PONS_V2_HOOK, None, None)])
    s.db.execute("INSERT INTO launches(token,curve,deployer,pair_token,threshold,launch_config,block,ts,tx_hash,source) VALUES(?,?,?,?,?,?,?,?,?,?)", (TOKEN_B, None, None, chain.NATIVE, str(chain.CURVE_GRADUATION_THRESHOLD_WEI), 0, 90, 900, "0x", "test"))
    s.upsert_blocks([(90, 900, 1), (105, 4000, 1)])
    s.set_meta("sample_from_block", 90)
    s.set_meta("sample_to_block", 105)
    s.set_meta("sample_label", "engine fixture")
    s.commit()
    return s


def sell_tx(curve: str, token: str, wallet: str, li: int = 0, tokens: int = 10**18, quote: int = 10**15) -> list[dict]:
    return [transfer(token, wallet, curve, tokens, li), curve_sell(curve, wallet, wallet, tokens, quote, li + 1)]


def buy_tx(curve: str, token: str, wallet: str, li: int = 0, tokens: int = 10**18, quote: int = 10**15) -> list[dict]:
    return [curve_buy(curve, wallet, wallet, quote, tokens, li), transfer(token, curve, wallet, tokens, li + 1)]


def buy_pool_tx(wallet: str, li: int = 0, tokens: int = 10**18, quote: int = 10**15) -> list[dict]:
    return [v4_swap(POOL_B, chain.UNIVERSAL_ROUTER, -quote, tokens, li), transfer(TOKEN_B, chain.V4_POOL_MANAGER, wallet, tokens, li + 1)]


def test_incremental_sequences_equal_rotate_on_the_same_trades(tmp_path):
    db = tmp_path / "e.sqlite"
    store = seeded_store(db)
    st = EngineState(store, window_s=1800, span_s=1800)
    W = 1800
    blocks = [
        block(100, 1000, [("0x01", sell_tx(CURVE_A, TOKEN_A, WALLET_1)), ("0x02", sell_tx(CURVE_A, TOKEN_A, WALLET_2)), ("0x03", sell_tx(CURVE_C, TOKEN_C, WALLET_2, tokens=2 * 10**18))]),
        block(101, 1100, [("0x04", buy_pool_tx(WALLET_1))]),  # W1: clean A -> B
        block(102, 1200, [("0x05", buy_pool_tx(WALLET_2))]),  # W2: sold A and C in the window -> ambiguous rows
        block(103, 1300, [("0x06", sell_tx(CURVE_A, TOKEN_A, WALLET_3) + buy_tx(CURVE_C, TOKEN_C, WALLET_3, li=2))]),  # W3: direct A -> C in one tx
        block(104, 1400, [("0x07", buy_tx(CURVE_A, TOKEN_A, WALLET_1))]),  # W1 buys A after buying B: another buy between? no sell of B -> no sequence... unless A was sold: A sold at 1000 -> A->A excluded; B never sold -> nothing
        block(105, 4000, [("0x08", buy_pool_tx(WALLET_3))]),  # W3: the sell of A at 1300 is outside the 1800 s window -> nothing
    ]
    all_events, seq_rows, trade_rows = [], [], []
    for b in blocks:
        if b.number == 105:
            # before the clock jumps: B has one main inflow wallet (W1 clean; W2's rows are ambiguous), C one (W3 direct)
            assert st.rows[TOKEN_B]["wallets_range"] == 1 and st.rows[TOKEN_B]["inflow_10m"] == 1 and st.rows[TOKEN_B]["symbol"] == "BBB" and st.rows[TOKEN_B]["sources"][0]["symbol"] == "AAA"
            assert st.rows[TOKEN_C]["wallets_range"] == 1 and st.rows[TOKEN_B]["age_s"] == 1400 - 900 and st.rows[TOKEN_B]["stage"] == "curve"
        events, wb, timing = st.apply_block(b)
        all_events.extend(events)
        seq_rows.extend(wb.sequences)
        trade_rows.extend(wb.trades)
        assert timing["t_end"] >= timing["t_start"]
    assert TOKEN_B not in st.rows and TOKEN_C not in st.rows  # clock 4000: every inflow is older than the 30-min span
    assert any(t == "radar_delta" and TOKEN_B in d["removed"] for t, d in all_events)
    # the same trades through the batch path
    w = Writer(db)
    from stampede.engine.state import WriteBatch

    wb_all = WriteBatch()
    wb_all.trades = trade_rows
    wb_all.blocks = [(b.number, b.ts, 1) for b in blocks]
    w.flush(store, wb_all)
    rotate(store, W)
    batch = {(r[0], r[1], r[2], r[3]) for r in store.db.execute("SELECT wallet, sell_trade, buy_trade, grade FROM sequences WHERE window_s=?", (W,))}
    live = {(r[1], r[4], r[5], r[9]) for r in seq_rows}
    assert live == batch and len(live) == 4, (live, batch)
    grades = sorted((r[9], r[2][-2:], r[3][-2:]) for r in seq_rows)
    assert grades == [("ambiguous", "a1", "b2"), ("ambiguous", "c3", "b2"), ("clean", "a1", "b2"), ("direct", "a1", "c3")]
    # trades match the batch normalizer on ids and content
    assert len(trade_rows) == 9 and store.db.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 9
    types = [t for t, _ in all_events]
    assert types.count("trade") == 9 and types.count("sequence") == 4 and types.count("block") == 6 and "radar_delta" in types
    trade_ev = next(d for t, d in all_events if t == "trade")
    assert trade_ev["symbol"] == "AAA" and trade_ev["side"] == "sell" and trade_ev["wallet"] == WALLET_1 and trade_ev["price_quote"] == 1e-3
    seq_ev = next(d for t, d in all_events if t == "sequence")
    assert seq_ev["grade"] == "clean" and seq_ev["sell_symbol"] == "AAA" and seq_ev["buy_symbol"] == "BBB" and seq_ev["gap_s"] == 100
    store.close()


def test_launch_and_curve_events_maintain_the_registry_and_reserves(tmp_path):
    store = seeded_store(tmp_path / "l.sqlite")
    st = EngineState(store)
    new_token, new_curve = "0x" + "d4" * 20, "0x" + "cd" * 20
    launch = {
        "log_index": 0, "address": chain.PONS_V2_FACTORY, "topic0": chain.T_TOKEN_LAUNCHED, "topic1": "0x" + new_token[2:].rjust(64, "0"), "topic2": "0x" + new_curve[2:].rjust(64, "0"), "topic3": "0x" + WALLET_1[2:].rjust(64, "0"),
        "data": "0x" + chain.NATIVE[2:].rjust(64, "0") + f"{0:064x}" + f"{chain.CURVE_GRADUATION_THRESHOLD_WEI:064x}", "kind": "token_launched",
    }
    quote_in, tokens_out = 10**16, 5 * 10**24
    logs = [launch, curve_buy(new_curve, WALLET_1, WALLET_1, quote_in, tokens_out, 1), transfer(new_token, new_curve, WALLET_1, tokens_out, 2)]
    logs[1]["data"] = "0x" + f"{quote_in:064x}" + f"{tokens_out:064x}" + f"{quote_in // 100:064x}" + f"{quote_in // 100:064x}"
    events, wb, _ = st.apply_block(block(200, 2000, [("0xaa", logs)]))
    assert st.ctx.curves[new_curve] == {"token": new_token, "pair_token": chain.NATIVE} and new_token in st.ctx.universe and new_curve in st.ctx.infra
    assert st.launches[new_token]["ts"] == 2000 and len(wb.launches) == 1 and len(wb.curves) == 1 and wb.tokens[0][0] == new_token
    rs = st.reserves[new_curve]
    assert rs.complete and rs.quote == quote_in - 2 * (quote_in // 100) and rs.tokens == chain.CURVE_SUPPLY_WEI - tokens_out
    tr = [d for t, d in events if t == "trade"]
    assert len(tr) == 1 and tr[0]["token"] == new_token and tr[0]["side"] == "buy" and tr[0]["venue"] == "curve"
    # a curve first seen mid-life waits for a snapshot; a graduation marks it done
    events, wb, _ = st.apply_block(block(201, 2001, [("0xab", buy_tx(CURVE_A, TOKEN_A, WALLET_2))]))
    assert st.reserves[CURVE_A].complete is False and st.reserves[CURVE_A].needs_snapshot
    pool_reg = {"log_index": 0, "address": chain.PONS_V2_HOOK, "topic0": chain.T_POOL_REGISTERED, "topic1": "0x" + "77" * 32, "topic2": None, "topic3": None, "data": "0x" + new_token[2:].rjust(64, "0") + chain.NATIVE[2:].rjust(64, "0") + WALLET_1[2:].rjust(64, "0"), "kind": "pool_registered"}
    events, wb, _ = st.apply_block(block(202, 2002, [("0xac", [pool_reg])]))
    assert st.is_pons_pool("0x" + "77" * 32) and st.graduations[new_token]["stage"] == "pool" and rs.graduated and len(wb.pools) == 1
    store.close()


def test_late_swap_logs_and_parked_transfers_still_yield_the_trade(tmp_path):
    """Under load the node can deliver a PONS log after the next head: the block is already released. The state keeps the
    logs of the last 300 blocks, so the late fragment is normalized together with the on-time transfers; transfers that
    arrive late for a transaction without a swap yet are parked by the assembler and rejoined when the swap log comes."""
    store = seeded_store(tmp_path / "late.sqlite")
    st = EngineState(store)
    asm = BlockAssembler(pons_pool=st.is_pons_pool, bloom=PonsBloom())
    t = 5000.0
    z = ["0x" + WALLET_1[2:].rjust(64, "0"), "0x" + WALLET_1[2:].rjust(64, "0")]
    amt = 10**18
    # block 40: tx X = a curve buy whose swap log is late; its transfer (curve -> wallet) arrives on time on the bulk side
    asm.on_head(head(40), t)
    tr = tagged(transfer(TOKEN_A, CURVE_A, WALLET_1, amt, 3), 40, "0xx", 1)
    asm.on_log(tr, "bulk", t + 0.01)
    asm.on_head(head(41, bloom=ZERO_BLOOM), t + 0.1)
    asm.on_log(parse_log(rpc_log(41, "0xy", 0, chain.T_TRANSFER, TOKEN_B, 0, z)), "bulk", t + 0.12)
    out = asm.poll(t + 0.13)
    assert [b.number for b in out] == [40, 41] and [l["log_index"] for l in out[0].logs] == [3]
    for b in out:
        events, wb, _ = st.apply_block(b)
        assert not [e for e in events if e[0] == "trade"]  # no swap seen yet: the transfer alone is not a trade
    late_swap = tagged(curve_buy(CURVE_A, WALLET_1, WALLET_1, 10**15, amt, 2), 40, "0xx", 1)
    asm.on_log(late_swap, "fast", t + 0.3)
    frags = asm.poll(t + 0.45)
    assert len(frags) == 1 and frags[0].source == "late" and [l["log_index"] for l in frags[0].logs] == [2]
    events, wb, _ = st.apply_block(frags[0])
    trades = [d for typ, d in events if typ == "trade"]
    assert len(trades) == 1 and trades[0]["wallet"] == WALLET_1 and trades[0]["side"] == "buy" and trades[0]["tx"] == "0xx" and len(wb.trades) == 1
    # block 42: tx Y sells on curve C; both its transfer and its swap log are late, the transfer first
    asm.on_head(head(42, bloom=ZERO_BLOOM), t + 0.5)
    asm.on_head(head(43, bloom=ZERO_BLOOM), t + 0.6)
    assert [b.number for b in asm.poll(t + 0.6)] == [42, 43]
    asm.on_log(tagged(transfer(TOKEN_C, WALLET_2, CURVE_C, amt, 7), 42, "0xyy", 2), "bulk", t + 0.7)
    assert asm.stats["late_transfers_parked"] == 1 and asm.poll(t + 0.9) == []
    asm.on_log(tagged(curve_sell(CURVE_C, WALLET_2, WALLET_2, amt, 10**15, 8), 42, "0xyy", 2), "fast", t + 1.0)
    frags = asm.poll(t + 1.15)
    assert len(frags) == 1 and [l["log_index"] for l in frags[0].logs] == [7, 8] and asm.stats["late_transfers_rejoined"] == 1
    events, wb, _ = st.apply_block(frags[0])
    trades = [d for typ, d in events if typ == "trade"]
    assert len(trades) == 1 and trades[0]["wallet"] == WALLET_2 and trades[0]["side"] == "sell" and trades[0]["symbol"] == "CCC"
    store.close()


# ---- SSE stream + perf --------------------------------------------------------------------------------------------------
def parse_sse(text: str) -> list[dict]:
    out = []
    for chunk in text.split("\n\n"):
        if not chunk.strip() or chunk.startswith(":"):
            continue
        frame: dict = {}
        for line in chunk.split("\n"):
            k, _, v = line.partition(":")
            frame[k] = v.strip()
        if "data" in frame:
            frame["json"] = json.loads(frame["data"])
            out.append(frame)
    return out


def test_sse_ring_replays_from_last_event_id(tmp_path):
    db = tmp_path / "s.sqlite"
    seeded_store(db).close()
    app = create_app(mode="fixture", db=db, context_policy="off")
    bus: Bus = app.state.bus
    for i in range(1, 9):
        bus.publish("trade" if i % 2 else "block", 100 + i, {"i": i})
    tc = TestClient(app)
    with tc.stream("GET", "/api/stream", headers={"Last-Event-ID": "5"}, params={"limit": 3}) as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        frames = parse_sse("".join(r.iter_text()))
    assert frames[0]["event"] == "session" and "id" not in frames[0] and frames[0]["json"]["data"]["hello"] is True and frames[0]["json"]["data"]["replay_gap"] is False
    assert [f["id"] for f in frames[1:]] == ["6", "7", "8"] and [f["event"] for f in frames[1:]] == ["block", "trade", "block"]
    assert frames[2]["json"] == {"id": 7, "type": "trade", "ts_emit": frames[2]["json"]["ts_emit"], "block": 107, "data": {"i": 7}}
    # type filter + query-string resume
    with tc.stream("GET", "/api/stream", params={"last_event_id": 0, "types": "trade", "limit": 2}) as r:
        frames = parse_sse("".join(r.iter_text()))
    assert [f["id"] for f in frames[1:]] == ["1", "3"] and all(f["event"] == "trade" for f in frames[1:])
    # an id older than the ring reports the gap
    small = Bus(ring_size=3)
    for i in range(1, 8):
        small.publish("block", i, {})
    replay, gap = small.replay(2)
    assert [e.id for e in replay] == [5, 6, 7] and gap is True
    replay, gap = bus.replay(5)
    assert [e.id for e in replay] == [6, 7, 8] and gap is False and bus.stats()["by_type"] == {"trade": 4, "block": 4}


def test_perf_endpoint_shape(tmp_path):
    db = tmp_path / "p.sqlite"
    seeded_store(db).close()
    app = create_app(mode="fixture", db=db, context_policy="off")
    p = TestClient(app).get("/api/perf").json()
    assert p["engine"] is None and "events" in p and "latency_ms" in p and "note" in p
    eng = Engine(db, bus=app.state.bus)  # not started: the shape must still be complete
    p = eng.perf()
    assert set(p) >= {"engine", "blocks", "latency_ms", "feed", "events", "process", "state", "resolver", "radar_reconcile", "rules"}
    assert set(p["latency_ms"]) >= {"head_to_logs_complete", "logs_to_trades", "trades_to_radar", "block_to_emit", "apply_block"}
    assert set(p["blocks"]) >= {"first", "last", "processed", "gaps_found", "gaps_backfilled_blocks", "gaps_unfilled_blocks", "late_logs", "deferred_txs"}
    assert p["process"]["rss_mb"] is not None and "cpu_percent_avg" in p["process"]
    h = Histogram()
    assert h.summary()["p50"] is None
    for v in range(1, 101):
        h.add(float(v))
    s = h.summary()
    assert (s["p50"], s["p90"], s["p95"], s["p99"], s["max"], s["n"]) == (51.0, 91.0, 96.0, 100.0, 100.0, 100)
    for k, v in p["latency_ms"].items():
        assert set(v) >= {"n", "p50", "p90", "p95", "p99", "max"}, k


def test_feed_fails_over_to_the_second_endpoint_and_reconnects(monkeypatch):
    """The first endpoint refuses, the second accepts the subscriptions and streams two heads before closing:
    blocks come out in order, the failover and the reconnect are counted (no network: `connect` is faked)."""
    import asyncio
    import websockets.asyncio.client as wsc

    class FakeWS:
        latency = 0.05

        def __init__(self, role_frames):
            self.pending: list[dict] = []
            self.frames = list(role_frames)

        async def send(self, msg):
            self.pending.append(json.loads(msg))

        async def recv(self):
            if self.pending:
                req = self.pending.pop(0)
                return json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": f"0xsub{req['id']}"})
            if self.frames:
                await asyncio.sleep(0.01)
                return json.dumps(self.frames.pop(0))
            await asyncio.sleep(0.05)
            raise ConnectionError("closed by the fake node")

    heads = [{"jsonrpc": "2.0", "method": "eth_subscription", "params": {"subscription": "0xsub1", "result": head(n, bloom=ZERO_BLOOM)}} for n in (100, 101)]
    attempts: list[str] = []

    class FakeConnect:
        def __init__(self, url, **kw):
            self.url = url

        async def __aenter__(self):
            attempts.append(self.url)
            if self.url == "wss://a":
                raise OSError("connection refused")
            return FakeWS(heads if len([u for u in attempts if u == "wss://b"]) == 1 else [])  # first fast connect streams; later ones idle

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(wsc, "connect", FakeConnect)
    out: queue.Queue = queue.Queue()
    f = feed_mod.Feed(out, BlockAssembler(bloom=PonsBloom()), None, endpoints=["wss://a", "wss://b"], stable_after_s=30)
    f.start()
    got = []
    deadline = time.time() + 6
    while time.time() < deadline and len(got) < 2:
        try:
            got.append(out.get(timeout=0.5))
        except queue.Empty:
            pass
    f.stop()
    assert [b.number for b in got] == [100, 101] and all("bloom_skip" in b.flags for b in got)
    snap = f.snapshot()
    fast = snap["connections"]["fast"]
    assert attempts[0] == "wss://a" and "wss://b" in attempts and fast["failovers"] >= 1 and fast["errors"] >= 1 and fast["connects"] >= 1
    assert snap["reconnects"] >= 0 and f.first_head == 100 and snap["head_gap_ms"]["n"] == 1


def test_feed_constants_and_parse_log_filters():
    assert set(feed_mod.FAST_SUBSCRIPTIONS) == {"heads", "curve", "swap", "factory", "hook"} and set(feed_mod.BULK_SUBSCRIPTIONS) == {"transfer"}
    assert feed_mod.FAST_SUBSCRIPTIONS["swap"][1]["address"] == chain.V4_POOL_MANAGER and feed_mod.FAST_SUBSCRIPTIONS["factory"][1]["address"] == chain.PONS_V2_FACTORY
    assert "address" not in feed_mod.FAST_SUBSCRIPTIONS["curve"][1] and "address" not in feed_mod.BULK_SUBSCRIPTIONS["transfer"][1]
    assert set(BACKFILL_TOPICS[0]) == {t for sub in list(feed_mod.FAST_SUBSCRIPTIONS.values())[1:] + list(feed_mod.BULK_SUBSCRIPTIONS.values()) for t in sub[1]["topics"][0]}
    assert parse_log(rpc_log(1, "0x1", 0, chain.T_V4_SWAP, "0x" + "ee" * 20)) is None  # Swap from another fork
    assert parse_log(rpc_log(1, "0x1", 0, chain.T_TOKEN_LAUNCHED, WALLET_1)) is None  # TokenLaunched from another factory
    l = parse_log(rpc_log(1, "0xAB", 3, chain.T_CURVE_BUY, CURVE_A.upper(), 2, ["0x" + "1" * 64]))
    assert l["kind"] == "curve_buy" and l["address"] == CURVE_A and l["tx_hash"] == "0xab" and l["tx_index"] == 2 and l["topic1"] == "0x" + "1" * 64 and l["log_index"] == 3
    q: queue.Queue = queue.Queue()
    f = feed_mod.Feed(q, BlockAssembler(), None, endpoints=["wss://a", "wss://b"])
    snap = f.snapshot()
    assert set(snap["connections"]) == {"fast", "bulk"} and snap["reconnects"] == 0 and snap["gaps"] == []
