"""Both surfaces read one server: the TUI's numbers for a pair equal the web API's numbers at the same
session clock, and clock control from either side is visible to the other."""
import asyncio
import os

os.environ.pop("NO_COLOR", None)
os.environ.setdefault("TEXTUAL_COLOR_SYSTEM", "truecolor")

from fastapi.testclient import TestClient  # noqa: E402
from helpers import TOKEN_A, TOKEN_B, until  # noqa: E402
from stampede.api.app import create_app  # noqa: E402
from stampede.rotation import rotate  # noqa: E402
from stampede.store import Store  # noqa: E402
from stampede.tui.app import StampedeTUI  # noqa: E402
from stampede.tui.client import ApiClient, ApiError  # noqa: E402

W1 = "0x1111111111111111111111111111111111111111"


def build_db(path):
    s = Store(path)
    rows = [(1000, TOKEN_A, W1, "sell"), (1100, TOKEN_B, W1, "buy"), (1200, TOKEN_B, W1, "buy"), (1300, TOKEN_A, "0x2222222222222222222222222222222222222222", "sell"), (1400, TOKEN_B, "0x2222222222222222222222222222222222222222", "buy")]
    s.db.executemany(
        "INSERT INTO trades(tx_hash,block,ts,ts_exact,token,wallet,side,token_amount,quote_token,quote_amount,venue,swap_logs,attribution,flags) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(f"0x{i:064x}", 100 + i, ts, 1, tok, w, side, "1000000000000000000", "0x0000000000000000000000000000000000000000", "1000", "curve", "[]", "transfer_net", "[]") for i, (ts, tok, w, side) in enumerate(rows, 1)],
    )
    s.db.executemany("INSERT INTO tokens(address,symbol,name,source,curve,first_seen_block) VALUES(?,?,?,?,?,?)", [(TOKEN_A, "AAA", "", "curve", None, 1), (TOKEN_B, "BBB", "", "curve", None, 1)])
    s.upsert_blocks([(101, 1000, 1), (105, 1400, 1)])
    s.set_meta("sample_from_block", 101)
    s.set_meta("sample_to_block", 105)
    s.set_meta("sample_label", "fixture")
    s.commit()
    rotate(s, 1800)
    s.close()


class InProcessApi(ApiClient):
    """The TUI client routed through FastAPI's TestClient (no sockets)."""

    def __init__(self, tc: TestClient):
        super().__init__("http://test")
        self.tc = tc

    def _get(self, path, params=None):
        r = self.tc.get(path, params={k: v for k, v in (params or {}).items() if v is not None})
        if r.status_code >= 400:
            raise ApiError(f"HTTP {r.status_code}")
        return r.json()

    def _post(self, path, body):
        r = self.tc.post(path, json=body)
        if r.status_code >= 400:
            raise ApiError(f"HTTP {r.status_code}")
        return r.json()


def test_tui_and_web_api_agree_on_the_same_session(tmp_path):
    db = tmp_path / "s.sqlite"
    build_db(db)
    app = create_app(mode="replay", window="30m", db=db)
    tc = TestClient(app)
    sess = tc.get("/api/session").json()
    assert sess["mode"] == "replay" and sess["controls"]
    # web-side control: seek to the end of the sample
    seek = tc.post("/api/session", json={"action": "seek", "ts": 1400}).json()
    assert seek["clock_ts"] == 1400 and seek["id"] == sess["id"]
    # web numbers for the pair at this clock
    web_edge = tc.get(f"/api/edge/{TOKEN_A}/{TOKEN_B}", params={"window": "1800s", "from": 1400 - 1800, "to": 1400, "exact": 0}).json()
    assert web_edge["wallets_main"] == 2 and web_edge["sequences_total"] == 3
    ev = tc.get("/api/events", params={"window": "1800s", "backfill_s": 1800}).json()
    assert ev["session"]["id"] == sess["id"] and ev["count"] == 3 and ev["kind"] == "history"
    status = tc.get("/api/status").json()
    assert status["session"]["id"] == sess["id"]
    assert status["sample"]["trades"] == 5 and status["store"]["trades"] == 5 and status["store"]["unknown_time_trades"] == 0

    async def tui_side():
        tui = StampedeTUI(InProcessApi(tc), poll_s=0.3)
        async with tui.run_test(size=(180, 50)) as pilot:
            await until(lambda: tui.session is not None and tui.query_one("#feed").row_count == 3)
            await pilot.pause()
            assert tui.session["id"] == sess["id"] and tui.session["clock_ts"] == 1400
            await pilot.press("enter")
            await until(lambda: tui.edge_doc is not None)
            await pilot.pause()
            txt = str(tui.query_one("#detailbody").render())
            assert "2 WALLETS" in txt and "3 sequences" in txt  # same as web_edge
            # TUI-side control is visible to the web API
            await pilot.press("left")  # seek -60 s
            await until(lambda: tc.get("/api/session").json()["clock_ts"] == 1340)

    asyncio.run(tui_side())
