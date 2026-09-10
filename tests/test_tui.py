"""The TUI against a fake API: rows, selection stability, evidence counts, seek rebuild, search, quit, resize, error state."""
import asyncio
import os

os.environ.pop("NO_COLOR", None)
os.environ.setdefault("TEXTUAL_COLOR_SYSTEM", "truecolor")

from helpers import TOKEN_A, TOKEN_B, until  # noqa: E402
from stampede.tui.app import StampedeTUI  # noqa: E402
from stampede.tui.client import ApiError  # noqa: E402

W1 = "0x1111111111111111111111111111111111111111"


def ev(i: int, buy_ts: int, wallet: str = W1, a: str = TOKEN_A, b: str = TOKEN_B, grade: str = "clean") -> dict:
    return {
        "id": i,
        "cursor": f"{buy_ts}:{i}",
        "buy_ts": buy_ts,
        "buy_ts_exact": True,
        "sell_ts": buy_ts - 100,
        "wallet": wallet,
        "from": a,
        "to": b,
        "from_symbol": "AAA",
        "to_symbol": "BBB",
        "from_short": "0x0000…00a1",
        "to_short": "0x0000…00b2",
        "grade": grade,
        "gap_s": 100,
        "buy_tx": "0x" + "b" * 64,
        "sell_tx": "0x" + "a" * 64,
    }


class FakeClient:
    def __init__(self):
        self.clock = 2000
        self.playing = False
        self.rev = 0
        self.history = [ev(1, 1500), ev(2, 1900)]
        self.new: list[dict] = []
        self.fail = False
        self.calls: list[str] = []

    def session(self):
        self.calls.append("session")
        if self.fail:
            raise ApiError("ConnectionError: refused")
        return {"id": "S-test", "rev": self.rev, "mode": "replay", "label": "REPLAY 10× · PAUSED", "clock_ts": self.clock, "playing": self.playing, "speed": 10, "span_s": 1800, "window_s": 1800, "from_ts": 0, "to_ts": 9000, "controls": True}

    def status(self):
        return {"chain": {"name": "Robinhood Chain"}, "data": {"age_s": 5}, "live": None}

    def events(self, window_s, until, after, limit=500, backfill_s=1800):
        self.calls.append(f"events:{after}")
        if after is None:
            rows = [e for e in self.history if until - backfill_s < e["buy_ts"] <= until]
            return {"kind": "history", "events": rows, "next_cursor": rows[-1]["cursor"] if rows else f"{until}:0", "has_more": False, "count": len(rows)}
        rows = [e for e in self.new if e["buy_ts"] <= until]
        self.new = [e for e in self.new if e["buy_ts"] > until]
        return {"kind": "new", "events": rows, "next_cursor": rows[-1]["cursor"] if rows else after, "has_more": False, "count": len(rows)}

    def graph(self, window_s, from_ts, to_ts, min_wallets=1, limit=12):
        return {"edges": [{"from": TOKEN_A, "to": TOKEN_B, "wallets_main": 1, "sequences": 2}], "nodes": [{"address": TOKEN_A, "symbol": "AAA"}, {"address": TOKEN_B, "symbol": "BBB"}]}

    def edge(self, a, b, window_s, from_ts, to_ts, limit=40):
        tr = lambda side, ts: {"tx": "0x" + "c" * 64, "block": 1, "ts": ts, "ts_exact": True, "side": side}  # noqa: E731
        return {
            "from": {"symbol": "AAA", "short": "0x0000…00a1", "address": a},
            "to": {"symbol": "BBB", "short": "0x0000…00b2", "address": b},
            "window_s": 1800,
            "wallets_main": 1,
            "wallets_by_grade": {"clean": 1},
            "sequences_total": 2,
            "sequences": [{"wallet": W1, "grade": "clean", "gap_s": 100, "sell": tr("sell", 1400), "buy": tr("buy", 1500)}, {"wallet": W1, "grade": "clean", "gap_s": 100, "sell": tr("sell", 1800), "buy": tr("buy", 1900)}],
        }

    def control(self, action, **kw):
        self.rev += 1
        if action == "toggle":
            self.playing = not self.playing
        if action == "seek":
            self.clock = kw["ts"]
        return self.session()


def run(coro):
    return asyncio.run(coro)


def detail_text(app) -> str:
    return str(app.query_one("#detailbody").render())


def test_rows_and_one_wallet_two_sequences():
    async def body():
        fake = FakeClient()
        app = StampedeTUI(fake, poll_s=0.3)
        async with app.run_test(size=(180, 50)) as pilot:
            table = app.query_one("#feed")
            await until(lambda: table.row_count == 2)
            await pilot.pause()
            assert "2 of 2 rows" in str(app.query_one("#feedhead").render())
            await pilot.press("enter")
            await until(lambda: app.edge_doc is not None)
            await pilot.pause()
            txt = detail_text(app)
            assert "1 WALLET\n" in txt and "2 sequences" in txt
            assert app.detail_mode == "evidence"
            await pilot.press("escape")
            await pilot.pause()
            assert app.detail_mode == "summary"

    run(body())


def test_new_rows_append_without_clearing_and_selection_stays():
    async def body():
        fake = FakeClient()
        app = StampedeTUI(fake, poll_s=1.0)
        async with app.run_test(size=(180, 50)) as pilot:
            table = app.query_one("#feed")
            await until(lambda: table.row_count == 2)
            await pilot.pause()
            first_key = table.coordinate_to_cell_key((0, 0))[0].value
            await pilot.press("up")  # select the first (older) row -> follow off
            await pilot.pause()
            assert table.cursor_row == 0 and app.follow is False
            fake.new = [ev(3, 1950), ev(4, 1990)]
            for _ in range(30):  # wait for the poll that delivers them
                await asyncio.sleep(0.1)
                if table.row_count == 4:
                    break
            await pilot.pause()
            assert table.row_count == 4
            assert table.coordinate_to_cell_key((0, 0))[0].value == first_key  # no clear, same first row
            assert table.cursor_row == 0  # selection did not jump
            assert app.fresh_ids == {3, 4}  # marked fresh for one poll cycle
            # the same snapshot again: nothing is added twice, the fresh mark is gone
            await asyncio.sleep(1.3)
            await pilot.pause()
            assert table.row_count == 4 and app.fresh_ids == set()

    run(body())


def test_seek_rebuilds_history_without_marking_it_fresh():
    async def body():
        fake = FakeClient()
        fake.history = [ev(1, 1500), ev(2, 1900), ev(5, 5000), ev(6, 5100)]
        app = StampedeTUI(fake, poll_s=0.3)
        async with app.run_test(size=(180, 50)) as pilot:
            table = app.query_one("#feed")
            await until(lambda: table.row_count == 2)
            fake.clock = 5200  # a seek far ahead of anything playback could explain
            fake.rev += 1
            await until(lambda: set(app.events) == {5, 6})
            await pilot.pause()
            assert table.row_count == 2
            assert app.fresh_ids == set() and app.last_events_kind == "history"

    run(body())


def test_search_filters_and_escape_restores():
    async def body():
        fake = FakeClient()
        app = StampedeTUI(fake, poll_s=0.3)
        async with app.run_test(size=(180, 50)) as pilot:
            await until(lambda: app.query_one("#feed").row_count == 2)
            await pilot.pause()
            await pilot.press("slash")
            for ch in "zzz":
                await pilot.press(ch)
            await pilot.pause()
            assert app.query_one("#feed").row_count == 0
            await pilot.press("escape")
            await pilot.pause()
            assert app.query_one("#feed").row_count == 2 and app.filter_text == ""

    run(body())


def test_quit_and_sizes_and_brand():
    async def body():
        for size, big in (((120, 36), True), ((180, 50), True), ((100, 28), False)):
            fake = FakeClient()
            app = StampedeTUI(fake, poll_s=0.3)
            async with app.run_test(size=size) as pilot:
                await asyncio.sleep(0.6)
                await pilot.pause()
                brand_txt = str(app.query_one("#brand").render())
                assert ("█" in brand_txt) is big, (size, brand_txt[:20])
                await pilot.press("q")
                await pilot.pause()
            assert app.return_code == 0

    run(body())


def test_api_error_shows_inverted_alert_and_freezes():
    async def body():
        fake = FakeClient()
        app = StampedeTUI(fake, poll_s=0.3)
        async with app.run_test(size=(180, 50)) as pilot:
            await until(lambda: app.query_one("#feed").row_count == 2)
            fake.fail = True
            alert = app.query_one("#alert")
            await until(lambda: alert.has_class("visible"))
            await pilot.pause()
            assert "DISCONNECTED" in str(alert.render())
            assert app.query_one("#feed").row_count == 2  # data frozen, not replaced
            fake.fail = False
            await until(lambda: not alert.has_class("visible"))

    run(body())


def test_space_toggles_shared_session():
    async def body():
        fake = FakeClient()
        app = StampedeTUI(fake, poll_s=0.3)
        async with app.run_test(size=(180, 50)) as pilot:
            await until(lambda: app.session is not None)
            await pilot.pause()
            await pilot.press("space")
            await until(lambda: fake.playing is True and app.session["playing"] is True)

    run(body())
