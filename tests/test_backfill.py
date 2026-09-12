"""HyperSync backfill: lifecycle logs become curves/tokens/pools/launches, joined transactions become trades with exact
timestamps, the cursor makes it resumable. A fake client stands in for the network."""
import asyncio
from types import SimpleNamespace as NS

from helpers import CURVE_A, TOKEN_A, TOKEN_B, WALLET_1, WALLET_2, curve_buy, curve_sell, topic_addr, transfer, w
from stampede import chain
from stampede.research import backfill
from stampede.store import Store

DEPLOYER = "0x00000000000000000000000000000000000000d1"
CURVE_B = "0x000000000000000000000000000000000000cb02"


class FakeHS:
    """Just enough of the `hypersync` module surface for query construction."""

    class LogField:
        BLOCK_NUMBER = TRANSACTION_HASH = LOG_INDEX = ADDRESS = DATA = TOPIC0 = TOPIC1 = TOPIC2 = TOPIC3 = "f"

    class BlockField:
        NUMBER = TIMESTAMP = "f"

    class TransactionField:
        HASH = BLOCK_NUMBER = FROM = TO = "f"

    class JoinMode:
        JOIN_ALL = "JoinAll"
        JOIN_NOTHING = "JoinNothing"

    @staticmethod
    def FieldSelection(log=None, block=None, transaction=None):
        return NS(log=log, block=block, transaction=transaction)

    @staticmethod
    def LogSelection(address=None, topics=None):
        return NS(address=address, topics=topics)

    @staticmethod
    def Query(**kw):
        return NS(**kw)

    @staticmethod
    def StreamConfig(**kw):
        return NS(**kw)


def hs_log(d, block, tx):
    return NS(block_number=block, transaction_hash=tx, log_index=d["log_index"], address=d["address"], data=d["data"], topics=[d["topic0"], d.get("topic1"), d.get("topic2"), d.get("topic3")])


def launched(token, curve, deployer, block, tx, li=0):
    data = "0x" + chain.NATIVE[2:].rjust(64, "0") + w(0) + w(chain.CURVE_GRADUATION_THRESHOLD_WEI)
    return NS(block_number=block, transaction_hash=tx, log_index=li, address=chain.PONS_V2_FACTORY, data=data, topics=[chain.T_TOKEN_LAUNCHED, topic_addr(token), topic_addr(curve), topic_addr(deployer)])


class FakeRx:
    def __init__(self, responses):
        self.responses = list(responses)

    async def recv(self):
        return self.responses.pop(0) if self.responses else None


class FakeClient:
    def __init__(self):
        self.queries = []

    async def stream(self, query, config):
        self.queries.append(query)
        if query.join_mode == "JoinNothing":  # lifecycle pass
            return FakeRx([NS(next_block=1001, data=NS(logs=[launched(TOKEN_A, CURVE_A, DEPLOYER, 1000, "0xl1"), launched(TOKEN_B, CURVE_B, DEPLOYER, 1000, "0xl2", 1)], blocks=[NS(number=1000, timestamp="0x5f5e100")], transactions=[]))])
        # trades pass: one buy tx and, in the next response, a sell tx of the same wallet
        buy_logs = [transfer(TOKEN_A, CURVE_A, WALLET_1, 10**24, 1), curve_buy(CURVE_A, WALLET_1, WALLET_1, 10**16, 10**24, 2)]
        buy_logs[1]["data"] = "0x" + w(10**16) + w(10**24) + w(10**14) + w(0)
        sell_logs = [transfer(TOKEN_A, WALLET_1, CURVE_A, 10**24, 1), curve_sell(CURVE_A, WALLET_1, WALLET_1, 10**24, 2 * 10**16, 2)]
        r1 = NS(next_block=1101, data=NS(logs=[hs_log(l, 1100, "0xb1") for l in buy_logs], blocks=[NS(number=1100, timestamp=100_000_100)], transactions=[NS(hash="0xb1", block_number=1100, from_=WALLET_1, to=CURVE_A)]))
        r2 = NS(next_block=1301, data=NS(logs=[hs_log(l, 1300, "0xs1") for l in sell_logs], blocks=[NS(number=1300, timestamp="0x5f5e1c8")], transactions=[NS(hash="0xs1", block_number=1300, from_=WALLET_1, to=CURVE_A)]))
        return FakeRx([r1, r2])

    async def get_height(self):
        return 2000


def test_backfill_writes_universe_trades_and_cursor(tmp_path, monkeypatch):
    monkeypatch.setattr(backfill, "make_client", lambda hs, token=None: FakeClient())
    store = Store(tmp_path / "r.sqlite")
    res = asyncio.run(backfill.run(store, 1000, 1300, FakeHS, resume=False, progress=lambda m: None))
    assert res["lifecycle"]["launches"] == 2
    assert res["sync_lifecycle"]["launches"] == 2
    assert store.curves()[CURVE_A]["token"] == TOKEN_A and TOKEN_B in store.tokens()
    tr = store.db.execute("SELECT side, ts, ts_exact, fee_raw, wallet FROM trades ORDER BY block").fetchall()
    assert tr == [("buy", 100_000_100, 1, str(10**14), WALLET_1), ("sell", 100_000_200, 1, "0", WALLET_1)]
    assert store.db.execute('SELECT "from", "to" FROM txs WHERE hash=?', ("0xb1",)).fetchone() == (WALLET_1, CURVE_A)
    assert store.db.execute("SELECT trades FROM wallets WHERE address=?", (WALLET_1,)).fetchone() == (2,)
    assert store.get_meta("backfill_cursor") == 1301 and store.get_meta("sample_from_block") == 1000
    assert res["trades"]["trades"] == 2 and res["trades"]["txs"] == 2
    # resume: both passes are complete, nothing is re-fetched
    res2 = asyncio.run(backfill.run(store, 1000, 1300, FakeHS, resume=True, progress=lambda m: None))
    assert "lifecycle" not in res2 and "trades" not in res2
    store.close()


def test_trades_query_batches_pool_ids_and_joins_everything():
    bf = backfill.Backfill(Store(":memory:"), FakeClient(), FakeHS, progress=lambda m: None)
    q = bf.trades_query(10, 20, [f"0x{i:064x}" for i in range(backfill.POOL_ID_BATCH + 1)])
    assert q.join_mode == "JoinAll" and q.to_block == 21
    swaps = [s for s in q.logs if s.address == [chain.V4_POOL_MANAGER]]
    assert len(swaps) == 2 and swaps[0].topics[0] == [chain.T_V4_SWAP] and len(swaps[0].topics[1]) == backfill.POOL_ID_BATCH
    assert q.logs[0].topics == [[chain.T_CURVE_BUY, chain.T_CURVE_SELL, chain.T_SNIPE_TAX, chain.T_CURVE_REFUND]]


def test_pool_row_sorts_currencies():
    pr = {"topic1": "0x" + "ab" * 32, "data": "0x" + TOKEN_B[2:].rjust(64, "0") + chain.NATIVE[2:].rjust(64, "0") + WALLET_2[2:].rjust(64, "0"), "block": 5, "tx_hash": "0xp"}
    row = backfill.pool_row(pr)
    assert row[1] == chain.NATIVE and row[2] == TOKEN_B and row[5] == chain.PONS_V2_HOOK


def test_resilient_stream_reopens_after_a_stall(monkeypatch):
    """A HyperSync stream that stops delivering (DNS blip, dropped connection) never raises; the wrapper times out and
    re-opens the stream from the last next_block instead of hanging forever."""
    monkeypatch.setattr(backfill, "RECV_TIMEOUT_S", 0.05)
    opened = []

    real_sleep = asyncio.sleep

    class StallRx:
        async def recv(self):
            await real_sleep(10)

    class Client:
        async def stream(self, query, config):
            opened.append(query.from_block)
            if len(opened) == 1:
                return StallRx()
            return FakeRx([NS(next_block=1201, data=NS(logs=[], blocks=[], transactions=[])), NS(next_block=1301, data=NS(logs=[], blocks=[], transactions=[]))])

    async def collect():
        out = []
        async for res in backfill.resilient_stream(lambda: Client(), lambda a, b: NS(from_block=a, to_block=b + 1), 1000, 1300, FakeHS, 2, progress=lambda m: None):
            out.append(int(res.next_block))
        return out

    monkeypatch.setattr(asyncio, "sleep", lambda s: _fast_sleep(s))
    assert asyncio.run(collect()) == [1201, 1301]
    assert opened == [1000, 1000]  # re-opened from the same cursor because nothing had been delivered yet


async def _fast_sleep(_s):
    return None
