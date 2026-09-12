"""RPC backfill: chunk planning and adaptive spans, the endpoint pool's pacing / penalty box / rotation, transfer
filtering by swap transaction, timestamp flags, resume from the shared cursor - all against a fake JSON-RPC transport."""
from __future__ import annotations

import json
from collections import Counter

import pytest
from helpers import CURVE_A, POOL_B, TOKEN_A, TOKEN_B, WALLET_1, WALLET_2, curve_buy, curve_sell, make_ctx, topic_addr, transfer, v4_swap, w
from stampede import chain
from stampede.research import backfill_rpc as br
from stampede.store import Store

DEPLOYER = "0x00000000000000000000000000000000000000d1"
OTHER_HOOK = "0x0000000000000000000000000000000000000ff1"
OTHER_PM = "0x0000000000000000000000000000000000000ff2"


def rpc_log(d: dict, block: int, tx: str, ts: int | None = None) -> dict:
    """helpers' store-shaped log dict -> JSON-RPC log object (hex numbers, topics list, optional blockTimestamp)."""
    topics = [t for t in (d["topic0"], d.get("topic1"), d.get("topic2"), d.get("topic3")) if t]
    out = {"address": d["address"], "topics": topics, "data": d["data"], "blockNumber": hex(block), "transactionHash": tx, "logIndex": hex(d["log_index"]), "blockHash": "0x" + "00" * 32, "transactionIndex": "0x0", "removed": False}
    if ts is not None:
        out["blockTimestamp"] = hex(ts)
    return out


def launched(token, curve, deployer, block, tx, li=0, ts=None):
    data = "0x" + chain.NATIVE[2:].rjust(64, "0") + w(0) + w(chain.CURVE_GRADUATION_THRESHOLD_WEI)
    return rpc_log({"log_index": li, "address": chain.PONS_V2_FACTORY, "topic0": chain.T_TOKEN_LAUNCHED, "topic1": topic_addr(token), "topic2": topic_addr(curve), "topic3": topic_addr(deployer), "data": data}, block, tx, ts)


def hook_fee(pool_id, fee, tax, li, address=chain.PONS_V2_HOOK):
    return {"log_index": li, "address": address, "topic0": chain.T_HOOK_FEE, "topic1": pool_id, "topic2": None, "topic3": None, "data": "0x" + w(0) + w(fee) + w(tax), "kind": "hook_fee"}


class FakeChain:
    """Answers eth_getLogs / eth_getBlockByNumber from a list of RPC-shaped logs; per-URL span caps and 429 scripts."""

    def __init__(self, logs: list[dict], timestamps: dict[int, int] | None = None):
        self.logs = logs
        self.timestamps = timestamps or {}
        self.max_span: dict[str, int] = {}
        self.rate_limit_first: dict[str, int] = {}  # url -> number of leading calls answered with 429
        self.max_addresses: dict[str, int] = {}
        self.calls: list[tuple[str, str, int, int]] = []  # (url, method, from, to)
        self.n_calls: Counter = Counter()

    def __call__(self, url: str, payload, timeout: float):
        self.n_calls[url] += 1
        if isinstance(payload, list):  # header batch
            self.calls.append((url, "batch", len(payload), 0))
            out = []
            for item in payload:
                n = int(item["params"][0], 16)
                out.append({"jsonrpc": "2.0", "id": item["id"], "result": {"number": hex(n), "timestamp": hex(self.timestamps[n])} if n in self.timestamps else None})
            return 200, {}, out
        if self.rate_limit_first.get(url, 0) > 0:
            self.rate_limit_first[url] -= 1
            self.calls.append((url, "429", 0, 0))
            return 429, {"Retry-After": "7"}, {"jsonrpc": "2.0", "id": 1, "error": {"code": -32029, "message": "rate limit exceeded", "data": {"retry_after_ms": 7000}}}
        assert payload["method"] == "eth_getLogs"
        flt = payload["params"][0]
        fr, to = int(flt["fromBlock"], 16), int(flt["toBlock"], 16)
        self.calls.append((url, "eth_getLogs", fr, to))
        if to - fr + 1 > self.max_span.get(url, 10**9):
            return 200, {}, {"jsonrpc": "2.0", "id": 1, "error": {"code": -32602, "message": "block range exceeds maximum allowed (max=%d)" % self.max_span[url], "data": {"max_blocks": self.max_span[url]}}}
        addrs = flt.get("address")
        if isinstance(addrs, list) and len(addrs) > self.max_addresses.get(url, 10**9):
            return 200, {}, {"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "log query timed out"}}
        if isinstance(addrs, str):
            addrs = [addrs]
        addr_set = {a.lower() for a in addrs} if addrs else None
        t0s = set(flt["topics"][0]) if flt.get("topics") else None
        res = []
        for l in self.logs:
            b = int(l["blockNumber"], 16)
            if not (fr <= b <= to):
                continue
            if addr_set is not None and l["address"].lower() not in addr_set:
                continue
            if t0s is not None and l["topics"][0] not in t0s:
                continue
            res.append(l)
        return 200, {}, {"jsonrpc": "2.0", "id": 1, "result": res}


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def make_pool(fake, eps, progress=lambda m: None):
    clock = FakeClock()
    pool = br.EndpointPool(eps, transport=fake, clock=clock, sleep=clock.sleep, progress=progress)
    # the pool waits on a Condition with a timeout; with the fake clock the wait must advance time instead of blocking
    pool._cv.wait = lambda timeout=None: clock.sleep(timeout or 0.01)  # type: ignore[method-assign]
    return pool, clock


# ---- planning ----------------------------------------------------------------------------------------------------


def test_plan_chunks_covers_range_inclusive_with_short_tail():
    assert br.plan_chunks(1000, 3499, 1000) == [(1000, 1999), (2000, 2999), (3000, 3499)]
    assert br.plan_chunks(5, 5, 1000) == [(5, 5)]
    assert br.plan_chunks(10, 9, 1000) == []
    with pytest.raises(ValueError):
        br.plan_chunks(1, 2, 0)


def test_get_logs_shrinks_the_span_on_range_errors_and_still_covers_the_range():
    logs = [rpc_log(curve_buy(CURVE_A, WALLET_1, WALLET_1, 10**16, 10**24, 1), b, f"0x{b:x}", 100 + b) for b in range(1000, 5000, 500)]
    fake = FakeChain(logs)
    fake.max_span["u1"] = 1000  # the node accepts at most 1,000 blocks per query
    pool, _ = make_pool(fake, [br.Endpoint("e1", "u1", pace=0, span=4000)])
    st = Counter()
    got = pool.get_logs(1000, 4999, {"topics": [[chain.T_CURVE_BUY]]}, st)
    assert len(got) == 8 and {int(l["blockNumber"], 16) for l in got} == set(range(1000, 5000, 500))
    assert pool.eps[0].span == 1000  # 4000 -> 2000 -> 1000, then accepted
    assert st["span_shrinks"] == 2 and pool.eps[0].stats["range"] == 2
    ranges = [(f, t) for _, m, f, t in fake.calls if m == "eth_getLogs"]
    assert ranges[0] == (1000, 4999) and ranges[-1][1] == 4999 and all(t - f + 1 <= 1000 for f, t in ranges[2:])


def test_span_never_shrinks_below_the_floor():
    fake = FakeChain([])
    fake.max_span["u1"] = 10  # pathological node
    pool, _ = make_pool(fake, [br.Endpoint("e1", "u1", pace=0, span=600)])
    with pytest.raises(RuntimeError):
        pool.get_logs(1, 600, {"topics": [[chain.T_CURVE_BUY]]})
    assert pool.eps[0].span == br.MIN_SPAN


# ---- endpoint pool -------------------------------------------------------------------------------------------------


def test_rate_limited_endpoint_is_benched_for_retry_after_and_rotation_continues():
    logs = [rpc_log(curve_buy(CURVE_A, WALLET_1, WALLET_1, 1, 1, 1), 100, "0xa", 5)]
    fake = FakeChain(logs)
    fake.rate_limit_first["u1"] = 1  # first call on e1 answers 429 with Retry-After 7
    e1, e2 = br.Endpoint("e1", "u1", pace=0.1, span=1000), br.Endpoint("e2", "u2", pace=0, span=1000)
    pool, clock = make_pool(fake, [e1, e2])
    t0 = clock()
    got = pool.get_logs(1, 1000, {"topics": [[chain.T_CURVE_BUY]]})
    assert len(got) == 1
    assert e1.stats["rate_limited"] == 1 and e1.next_at >= t0 + 7  # benched exactly as told
    assert e1.cur_pace > e1.pace  # and paced slower afterwards
    assert e2.stats["ok"] == 1  # the other endpoint served the retry immediately
    # while e1 is in the penalty box, acquire() hands out e2 only
    for _ in range(3):
        ep = pool.acquire()
        assert ep is e2
        pool.report(ep, True, 0.1, blocks=1)
    clock.sleep(8)
    names = set()
    for _ in range(4):
        ep = pool.acquire()
        names.add(ep.name)
        pool.report(ep, True, 0.1, blocks=1)
    assert names == {"e1", "e2"}  # back in rotation after the penalty


def test_transport_errors_back_off_exponentially_and_pacing_is_respected():
    calls = []

    def flaky(url, payload, timeout):
        calls.append(url)
        raise ConnectionError("boom")

    e1 = br.Endpoint("e1", "u1", pace=2.0, span=1000)
    pool, clock = make_pool(flaky, [e1])
    ep = pool.acquire()
    with pytest.raises(br.RpcFail) as ei:
        pool.post(ep, {"jsonrpc": "2.0", "id": 1, "method": "eth_chainId", "params": []})
    assert ei.value.kind == "transport" and e1.strikes == 1 and e1.next_at >= clock() + 4.0
    # pacing: a second acquire on a healthy endpoint is granted only after `pace` seconds
    e2 = br.Endpoint("e2", "u2", pace=2.0, span=1000)
    pool2, clock2 = make_pool(lambda *a: (200, {}, {"jsonrpc": "2.0", "id": 1, "result": []}), [e2])
    t = clock2()
    pool2.report(pool2.acquire(), True, 0.0)
    pool2.acquire()
    assert clock2() >= t + 2.0


def test_shared_pacer_spaces_requests_across_pools_and_propagates_penalties(tmp_path):
    """Two pools (two segment workers on one IP) share one pacing file: their requests to the same host are spaced by
    the pace, a 429 seen by one benches both, and a CU bucket meters getLogs calls."""
    clock = FakeClock()
    pacer = br.SharedPacer(tmp_path / "pacer", clock=clock, sleep=clock.sleep)
    ok = lambda url, payload, timeout: (200, {}, {"jsonrpc": "2.0", "id": 1, "result": []})  # noqa: E731
    e1, e2 = br.Endpoint("nf", "https://nf.example/rpc", pace=10.0, span=1000), br.Endpoint("nf", "https://nf.example/rpc", pace=10.0, span=1000)
    p1 = br.EndpointPool([e1], transport=ok, clock=clock, sleep=clock.sleep, pacer=pacer)
    p2 = br.EndpointPool([e2], transport=ok, clock=clock, sleep=clock.sleep, pacer=pacer)
    for p in (p1, p2):
        p._cv.wait = lambda timeout=None: clock.sleep(timeout or 0.01)  # type: ignore[method-assign]
    t0 = clock()
    p1.get_logs(1, 100, {"topics": [[chain.T_CURVE_BUY]]})
    t1 = clock()
    p2.get_logs(1, 100, {"topics": [[chain.T_CURVE_BUY]]})  # the other process must wait for the shared turn
    assert t1 - t0 < 1.0 and clock() - t1 >= 10.0 - 1e-6
    # a 429 on one pool benches the host for everyone
    e1.strikes = 0
    p1.report(e1, False, 0.1, br.RpcFail("rate_limited", "429", retry_after=30.0))
    e1.inflight = 0
    t2 = clock()
    p2.get_logs(1, 100, {"topics": [[chain.T_CURVE_BUY]]})
    assert clock() - t2 >= 30.0 - 1e-6
    # CU bucket: 100 CU/min -> the calls that fit the bucket go out at once, the next one waits for one call's refill
    bucket = br.SharedPacer(tmp_path / "pacer2", clock=clock, sleep=clock.sleep)
    e3 = br.Endpoint("bm", "https://bm.example", pace=0.0, span=1000, cu_per_minute=100)
    p3 = br.EndpointPool([e3], transport=ok, clock=clock, sleep=clock.sleep, pacer=bucket)
    p3._cv.wait = lambda timeout=None: clock.sleep(timeout or 0.01)  # type: ignore[method-assign]
    fit = int(100 // br.GETLOGS_CU)
    t3 = clock()
    for _ in range(fit):
        p3.get_logs(1, 100, {"topics": [[chain.T_CURVE_BUY]]})
    assert clock() - t3 < 1.0
    p3.get_logs(1, 100, {"topics": [[chain.T_CURVE_BUY]]})
    assert clock() - t3 >= br.GETLOGS_CU * 60 / 100 - 1e-6  # one call's worth of refill at 100 CU/min
    # no directory: the pacer is inert
    br.SharedPacer(None).wait_turn(e3, 25.0)


def test_classify_error_kinds():
    assert br.classify_error(200, {}, {"result": []}) is None
    assert br.classify_error(429, {"Retry-After": "15"}, None).retry_after == 15.0
    e = br.classify_error(200, {}, {"error": {"code": -32000, "message": "query exceeds limit of 10000 results"}})
    assert e.kind == "range"
    assert br.classify_error(200, {}, {"error": {"code": -32003, "message": "response too large"}}).kind == "range"
    assert br.classify_error(200, {}, {"error": {"code": -32005, "message": "the network is busy, please try again in a moment"}}).kind == "transport"
    assert br.classify_error(403, {}, None).kind == "forbidden"
    assert br.classify_error(200, {}, {"error": "rate_limited", "message": "Too many requests from this IP (1 per 10s)"}).kind == "rate_limited"
    assert br.classify_error(200, {}, None).kind == "transport"  # HTML challenge page


def test_make_endpoints_accepts_names_and_urls():
    eps = br.make_endpoints(["official", "https://example.org/rpc"])
    assert eps[0].url == chain.PUBLIC_RPC and eps[0].span == br.KNOWN_ENDPOINTS["official"]["span"]
    assert eps[1].name == "example.org"
    assert [e.name for e in br.make_endpoints(None)] == br.DEFAULT_ENDPOINTS


# ---- selection / normalization ---------------------------------------------------------------------------------------


def test_select_swap_class_keeps_what_the_hypersync_query_selected():
    ctx = make_ctx()
    logs = [
        curve_buy(CURVE_A, WALLET_1, WALLET_1, 1, 1, 0) | {"block": 1, "tx_hash": "0xt1"},
        v4_swap(POOL_B, WALLET_2, -1, 1, 1) | {"block": 1, "tx_hash": "0xt2"},
        v4_swap("0x" + "ee" * 32, WALLET_2, -1, 1, 2) | {"block": 1, "tx_hash": "0xt3"},  # pool outside the universe
        v4_swap(POOL_B, WALLET_2, -1, 1, 3) | {"block": 1, "tx_hash": "0xt4", "address": OTHER_PM},  # another v4 fork
        hook_fee(POOL_B, 5, 6, 4) | {"block": 1, "tx_hash": "0xt2"},
        hook_fee(POOL_B, 5, 6, 5, OTHER_HOOK) | {"block": 1, "tx_hash": "0xt5"},
        {"log_index": 6, "address": CURVE_A, "topic0": chain.T_SNIPE_TAX, "topic1": topic_addr(WALLET_1), "topic2": None, "topic3": None, "data": "0x" + w(3), "kind": "snipe_tax", "block": 1, "tx_hash": "0xt1"},
    ]
    st = Counter()
    kept, tokens, txs = br.select_swap_class(logs, ctx, st)
    assert [l["log_index"] for l in kept] == [0, 1, 4, 6]
    assert tokens == {TOKEN_A, TOKEN_B} and txs == {"0xt1", "0xt2"}
    assert st["v4_non_universe_dropped"] == 1 and st["v4_other_manager_dropped"] == 1 and st["hook_fee_other_dropped"] == 1


def test_transfers_only_from_transactions_with_a_swap():
    tr = [transfer(TOKEN_A, CURVE_A, WALLET_1, 5, 1) | {"tx_hash": "0xswap"}, transfer(TOKEN_A, WALLET_1, WALLET_2, 5, 1) | {"tx_hash": "0xplain"}]
    kept = br.transfers_of_swaps(tr, {"0xswap"})
    assert [l["tx_hash"] for l in kept] == ["0xswap"]


def test_rpc_log_dict_reads_hex_fields_and_block_timestamp():
    d = br.rpc_log_dict(rpc_log(curve_sell(CURVE_A, WALLET_1, WALLET_1, 1, 2, 7), 4242, "0xAB", 1_700_000_000))
    assert d["log_index"] == 7 and d["block"] == 4242 and d["tx_hash"] == "0xab" and d["kind"] == "curve_sell" and d["ts"] == 1_700_000_000
    assert d["topic3"] is None
    assert br.rpc_log_dict(rpc_log(curve_sell(CURVE_A, WALLET_1, WALLET_1, 1, 2, 7), 1, "0x1"))["ts"] is None
    assert br.rpc_log_dict(rpc_log(curve_sell(CURVE_A, WALLET_1, WALLET_1, 1, 2, 7), 1, "0x1", 0))["ts"] is None  # the official endpoint's 0x0


def test_resolve_timestamps_exact_from_logs_headers_for_anchors_interpolation_for_the_rest():
    known = {100: 1000, 110: 1001}
    fetched = []

    def headers(nums):
        fetched.append(list(nums))
        return {n: 1000 + (n - 100) // 10 for n in nums if n != 130}  # block 130 stays unknown to the node

    st = Counter()
    out = br.resolve_timestamps({100, 105, 110, 120, 130, 140}, known, headers, st)
    assert out[100] == (1000, 1) and out[110] == (1001, 1)  # from blockTimestamp
    assert fetched == [[105, 120, 130, 140]]  # few missing blocks: every one is asked for
    assert out[105] == (1000, 1) and out[120] == (1002, 1) and out[140] == (1004, 1)  # headers -> exact
    assert out[130] == (1003, 0)  # interpolated between 120 and 140 -> not exact
    assert st["headers_fetched"] == 3 and st["ts_interpolated"] == 1
    # many missing blocks: only every ANCHOR_EVERY-th one (plus both ends) is fetched, the rest interpolated
    many = set(range(1000, 1000 + 40 * br.ANCHOR_EVERY * br.HEADER_BATCH))
    fetched.clear()
    st = Counter()
    out = br.resolve_timestamps(many, {}, lambda nums: (fetched.append(list(nums)) or {n: n for n in nums}), st)
    anchors = set(fetched[0])
    assert len(anchors) < len(many) / 4 and all(v[1] == 1 for k, v in out.items() if k in anchors)
    assert st["ts_interpolated"] == len(many) - len(anchors) and all(v[0] is not None for v in out.values())
    # no header source and no anchors at all: unknown, never a fake value
    out = br.resolve_timestamps({7}, {}, None, Counter())
    assert out[7] == (None, 0)


# ---- end to end with the fake chain ------------------------------------------------------------------------------------


def scenario_logs():
    """Launch of TOKEN_A at block 1000; a buy at 1100 (tx 0xb1) and a sell at 1300 (tx 0xs1) by WALLET_1, plus a plain
    transfer of TOKEN_A at 1200 (tx 0xp1) that must not become a trade; a v4 swap of TOKEN_B by WALLET_2 at 1350 (0xv1)
    where the PoolManager sends the tokens - a wallet only when `infra` is not seeded."""
    buy = curve_buy(CURVE_A, WALLET_1, WALLET_1, 10**16, 10**24, 2)
    buy["data"] = "0x" + w(10**16) + w(10**24) + w(10**14) + w(0)
    logs = [
        launched(TOKEN_A, CURVE_A, DEPLOYER, 1000, "0xl1", ts=100_000_000),
        rpc_log(transfer(TOKEN_A, CURVE_A, WALLET_1, 10**24, 1), 1100, "0xb1", 100_000_100),
        rpc_log(buy, 1100, "0xb1", 100_000_100),
        rpc_log(transfer(TOKEN_A, WALLET_1, WALLET_2, 10**20, 1), 1200, "0xp1", 100_000_150),
        rpc_log(transfer(TOKEN_A, WALLET_1, CURVE_A, 10**24, 1), 1300, "0xs1", 100_000_200),
        rpc_log(curve_sell(CURVE_A, WALLET_1, WALLET_1, 10**24, 2 * 10**16, 2), 1300, "0xs1", 100_000_200),
        rpc_log(transfer(TOKEN_B, chain.V4_POOL_MANAGER, WALLET_2, 5 * 10**21, 1), 1350, "0xv1", 100_000_250),
        rpc_log(v4_swap(POOL_B, chain.UNIVERSAL_ROUTER, -(3 * 10**15), 5 * 10**21, 2), 1350, "0xv1", 100_000_250),
        rpc_log(hook_fee(POOL_B, 3 * 10**13, 10**13, 3), 1350, "0xv1", 100_000_250),
    ]
    return logs


def seed_pool_b(store: Store):
    store.upsert_tokens([(TOKEN_B, "B", "B", "launch", "0x000000000000000000000000000000000000cb02", 900)])
    store.upsert_pools([(POOL_B, chain.NATIVE, TOKEN_B, 0, 200, chain.PONS_V2_HOOK, 950, "0xpr")])
    store.commit()


def test_run_end_to_end_writes_the_same_rows_as_the_hypersync_path(tmp_path):
    fake = FakeChain(scenario_logs())
    pool, _ = make_pool(fake, [br.Endpoint("e1", "u1", pace=0, span=1000), br.Endpoint("e2", "u2", pace=0, span=1000)])
    store = Store(tmp_path / "r.sqlite")
    seed_pool_b(store)
    res = br.run(store, 1000, 1399, pool, chunk=200, workers=2, resume=False, progress=lambda m: None)
    assert res["lifecycle"]["launches"] == 1 and res["sync_lifecycle"]["launches"] == 1 and res["infra_seeded"] == len(chain.KNOWN_INFRA)
    assert store.curves()[CURVE_A]["token"] == TOKEN_A
    tr = store.db.execute("SELECT tx_hash, wallet, side, venue, ts, ts_exact, fee_raw, tax_raw, quote_amount, flags FROM trades ORDER BY block").fetchall()
    assert tr[0] == ("0xb1", WALLET_1, "buy", "curve", 100_000_100, 1, str(10**14), "0", str(10**16), "[]")
    assert tr[1] == ("0xs1", WALLET_1, "sell", "curve", 100_000_200, 1, "0", "0", str(2 * 10**16), "[]")
    # v4: with `infra` seeded the PoolManager is not a wallet, so the swap is one buy with its quote amount and hook fee
    assert tr[2] == ("0xv1", WALLET_2, "buy", "v4", 100_000_250, 1, str(3 * 10**13), str(10**13), str(3 * 10**15), json.dumps(["hook_fee_in_unspecified_currency"]))
    assert len(tr) == 3  # the plain transfer at 1200 is not a trade
    assert store.db.execute("SELECT trades FROM wallets WHERE address=?", (WALLET_1,)).fetchone() == (2,)
    assert store.db.execute("SELECT timestamp, exact FROM blocks WHERE number=1300").fetchone() == (100_000_200, 1)
    assert store.get_meta("backfill_cursor") == 1400 and store.get_meta("backfill_lifecycle_cursor") == 1400
    assert store.db.execute("SELECT COUNT(*) FROM txs").fetchone() == (0,)  # not available over getLogs (documented)
    assert res["trades"]["trades"] == 3 and res["trades"]["chunks"] == 2 and res["trades"]["logs_transfer_kept"] == 3
    # both endpoints took part; every getLogs stayed within the chunk
    assert fake.n_calls["u1"] > 0 and fake.n_calls["u2"] > 0
    assert all(t - f + 1 <= 200 for _, m, f, t in fake.calls if m == "eth_getLogs")
    # resume: both cursors are past `to`, nothing is fetched again
    n = len(fake.calls)
    res2 = br.run(store, 1000, 1399, pool, chunk=200, resume=True, progress=lambda m: None)
    assert "lifecycle" not in res2 and "trades" not in res2 and len(fake.calls) == n
    store.close()


def test_without_infra_seed_the_pool_manager_becomes_a_wallet(tmp_path):
    """Reproduces the rows of the stores backfilled before `seed_infra` existed (equivalence checks against them)."""
    fake = FakeChain(scenario_logs())
    pool, _ = make_pool(fake, [br.Endpoint("e1", "u1", pace=0, span=1000)])
    store = Store(tmp_path / "r.sqlite")
    seed_pool_b(store)
    br.run(store, 1000, 1399, pool, chunk=400, workers=1, resume=False, progress=lambda m: None, infra=False)
    v4 = store.db.execute("SELECT wallet, side, quote_amount FROM trades WHERE venue='v4' ORDER BY side").fetchall()
    assert v4 == [(WALLET_2, "buy", "0"), (chain.V4_POOL_MANAGER, "sell", "0")]
    assert store.db.execute("SELECT COUNT(*) FROM infra").fetchone() == (0,)


def test_resume_starts_at_the_stored_cursor_and_skips_done_chunks(tmp_path):
    fake = FakeChain(scenario_logs())
    pool, _ = make_pool(fake, [br.Endpoint("e1", "u1", pace=0, span=1000)])
    store = Store(tmp_path / "r.sqlite")
    seed_pool_b(store)
    store.upsert_curves([(CURVE_A, TOKEN_A, chain.NATIVE, "token_launched")])
    store.upsert_tokens([(TOKEN_A, "", "", "launch", CURVE_A, 1000)])
    store.set_meta("backfill_lifecycle_cursor", 1400)
    store.set_meta("backfill_cursor", 1250)  # a previous run (either path) stopped here
    res = br.run(store, 1000, 1399, pool, chunk=100, workers=2, resume=True, progress=lambda m: None)
    assert "lifecycle" not in res
    ranges = sorted((f, t) for _, m, f, t in fake.calls if m == "eth_getLogs")
    assert ranges[0][0] == 1250 and all(f >= 1250 for f, _ in ranges)  # nothing before the cursor is fetched
    assert [r[0] for r in store.db.execute("SELECT tx_hash FROM trades ORDER BY block")] == ["0xs1", "0xv1"]  # the buy at 1100 belongs to the done part
    assert store.get_meta("backfill_cursor") == 1400
    # --no-resume ignores the cursor
    fake.calls.clear()
    br.run(store, 1000, 1399, pool, chunk=100, workers=1, resume=False, progress=lambda m: None)
    assert min(f for _, m, f, t in fake.calls if m == "eth_getLogs") == 0  # lifecycle lookback clamps at block 0, trades from 1000
    assert store.db.execute("SELECT COUNT(*) FROM trades").fetchone() == (3,)  # INSERT OR IGNORE: no duplicates


def test_header_fallback_when_logs_carry_no_timestamp(tmp_path):
    logs = [l for l in scenario_logs()]
    for l in logs:
        l.pop("blockTimestamp", None)
    fake = FakeChain(logs, timestamps={1100: 100_000_100, 1300: 100_000_200, 1350: 100_000_250})
    pool, _ = make_pool(fake, [br.Endpoint("e1", "u1", pace=0, span=1000)])
    store = Store(tmp_path / "r.sqlite")
    seed_pool_b(store)
    store.upsert_curves([(CURVE_A, TOKEN_A, chain.NATIVE, "token_launched")])
    store.upsert_tokens([(TOKEN_A, "", "", "launch", CURVE_A, 1000)])
    store.set_meta("backfill_lifecycle_cursor", 1400)
    res = br.run(store, 1000, 1399, pool, chunk=400, workers=1, resume=True, progress=lambda m: None)
    assert res["trades"]["headers_fetched"] == 3
    assert [r for r in store.db.execute("SELECT block, ts, ts_exact FROM trades ORDER BY block")] == [(1100, 100_000_100, 1), (1300, 100_000_200, 1), (1350, 100_000_250, 1)]
    assert any(m == "batch" for _, m, _, _ in fake.calls)


def test_address_list_is_split_when_the_node_rejects_it():
    toks = [f"0x{i:040x}" for i in range(1, 121)]
    logs = [rpc_log(transfer(t, WALLET_1, WALLET_2, 1, i), 10, f"0x{i:x}", 5) for i, t in enumerate(toks)]
    fake = FakeChain(logs)
    fake.max_addresses["u1"] = 70  # the node times out above 70 addresses per query
    pool, _ = make_pool(fake, [br.Endpoint("e1", "u1", pace=0, span=1000, max_addresses=200)])
    st = Counter()
    got = pool.get_logs_by_address(1, 100, toks, [[chain.T_TRANSFER]], st)
    assert len(got) == 120 and len({l["address"] for l in got}) == 120
    assert st["address_splits"] == 1 and pool.eps[0].max_addresses == 60 and pool.eps[0].span == 1000  # the span is untouched
    assert [n for _, m, n, _ in fake.calls if m == "eth_getLogs"] == [1, 1, 1]  # one reject, two halves
