"""The TUI against a fake API: rows, selection stability, `N new` + End, evidence counts, seek rebuild, search
(incl. typing spaces), Tab/1/2 pane and view switching, header variants by size, resize, radar summary and coin
card, quit via q and Ctrl+C, error state."""
import asyncio
import os

os.environ.pop("NO_COLOR", None)
os.environ.setdefault("TEXTUAL_COLOR_SYSTEM", "truecolor")

from helpers import TOKEN_A, TOKEN_B, until  # noqa: E402
from stampede.tui import brand  # noqa: E402
from stampede.tui.app import StampedeTUI, dur, fit  # noqa: E402
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


def radar_row(i: int, addr: str, symbol: str, inflow: int) -> dict:
    return {
        "address": addr,
        "symbol": symbol,
        "name": f"{symbol} coin",
        "source": "curve",
        "short": f"{addr[:6]}…{addr[-4:]}",
        "inflow_10m": inflow,
        "inflow_prev_per_10m": 0.0,
        "accel": float(inflow),
        "breadth": 2,
        "wallets_range": inflow,
        "sequences_range": inflow * 2,
        "sources": [{"address": TOKEN_A, "symbol": "AAA", "wallets": inflow}],
        "quality": 0.5,
        "age_s": 300 + i,
        "stage": "curve",
        "progress": 0.5,
        "mentions_1h": None,
        "mentions_24h": None,
        "score": 90.0 - i,
        "parts": {"inflow": 30.0, "acceleration": 20.0, "breadth": 5.0, "wallet_quality": 5.0, "age_bonus": 10.0, "curve_bonus": 5.0, "crowding": 0.0, "attention_penalty": 0.0},
        "chg_5m": 12.0,
        "chg_1h": None,
        "vol_1h_quote": 100.0,
        "quote_symbol": "COIN",
        "buyers_1h": 10,
    }


class FakeClient:
    base = "http://fake"

    def __init__(self):
        self.clock = 2000
        self.playing = False
        self.rev = 0
        self.history = [ev(1, 1500), ev(2, 1900)]
        self.new: list[dict] = []
        self.fail = False
        self.calls: list[str] = []
        self.radar_rows = [radar_row(1, TOKEN_B, "BBB", 7), radar_row(2, TOKEN_A, "AAA", 3)]

    mode = "replay"
    age_s = 5

    def session(self):
        self.calls.append("session")
        if self.fail:
            raise ApiError("ConnectionError: refused")
        if self.mode == "live":
            return {"id": "S-live", "rev": self.rev, "mode": "live", "label": "LIVE", "clock_ts": self.clock, "playing": True, "speed": 1, "span_s": 1800, "window_s": 1800, "from_ts": 0, "to_ts": self.clock, "controls": False, "live_paused": False}
        return {"id": "S-test", "rev": self.rev, "mode": "replay", "label": "REPLAY 10× · PAUSED", "clock_ts": self.clock, "playing": self.playing, "speed": 10, "span_s": 1800, "window_s": 1800, "from_ts": 0, "to_ts": 9000, "controls": True}

    def status(self):
        return {"chain": {"name": "Robinhood Chain"}, "data": {"age_s": self.age_s}, "live": None}

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

    def radar(self, params):
        self.calls.append("radar")
        return {"clock": self.clock, "span_s": 1800, "total": len(self.radar_rows), "preset": params.get("preset"), "presets": {"under_radar": {"label": "Under radar: young, still on the curve"}}, "context": {"enabled": False}, "rows": self.radar_rows}

    def coin(self, addr, refresh=False):
        self.calls.append(f"coin:{addr}")
        return {"address": addr, "symbol": "BBB", "name": "BBB coin", "short": "0x0000…00b2", "age_s": 400, "progress": {"stage": "curve", "progress": 0.5}, "as_of": {"chg_5m": 1.0, "chg_1h": 2.0, "trades_1h": 3, "buyers_1h": 4, "vol_1h_quote": 5.0, "quote_symbol": "COIN"}, "buyers": 6, "new_buyers": 2, "inbound": [{"wallets_main": 3, "token": {"symbol": "AAA"}}], "outbound": [], "note": "fixture"}

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


def feedhead_text(app) -> str:
    return str(app.query_one("#feedhead").render())


def test_formatting_helpers():
    assert dur(1800) == "30m" and dur(929) == "15m29s" and dur(44) == "44s" and dur(3600) == "1h" and dur(3900) == "1h05m"
    assert fit("TruffleHog·7b08", 11) == "Truff…·7b08"  # the address suffix that tells same-ticker coins apart survives
    assert fit("STABLEPAIR", 11) == "STABLEPAIR" and fit("STABLEPAIRLONG", 11) == "STABLEPAIR…"


def test_bison_mascot_downsample_is_pure_and_brand_coloured():
    cells = brand.bison_cells(12)
    assert cells is not None and len(cells) == 24 and len(cells[0]) == 25  # 94x91 -> 24 tall, aspect kept
    assert set("".join(cells)) <= set("BRGD.") and all(c in "".join(cells) for c in "BRGD")  # only the 4 brand colours
    assert cells[-1].strip(".").replace("G", "") == ""  # hooves (grey) are the lowest row: the pose is not flipped
    assert brand.bison_cells(12) is cells  # cached, never recomputed per frame
    rows = brand.bison_halfblocks(12)
    assert len(rows) == 12 and all(r.count("▀") == 25 for r in rows)
    assert all(c in "".join(rows) for c in ("#0D0A0A", "#FF3344", "#A3A3A3", "#351419", "#050505")) and "#0d0a0a" not in rows[0]


def test_rows_and_one_wallet_two_sequences():
    async def body():
        fake = FakeClient()
        app = StampedeTUI(fake, poll_s=0.3)
        async with app.run_test(size=(180, 50)) as pilot:
            table = app.query_one("#feed")
            await until(lambda: table.row_count == 2)
            await pilot.pause()
            assert "2 rows" in feedhead_text(app) and "following latest" in feedhead_text(app)
            txt = detail_text(app)
            assert W1 in txt and ("0x" + "b" * 64) in txt  # full wallet and tx hash in the detail pane
            await pilot.press("enter")
            await until(lambda: app.edge_doc is not None)
            await pilot.pause()
            txt = detail_text(app)
            assert "1 WALLET\n" in txt and "2 sequences" in txt
            assert app.detail_mode == "evidence"
            assert "EVIDENCE" in str(app.query_one("#detailhead").render())
            await pilot.press("escape")
            await pilot.pause()
            assert app.detail_mode == "summary"

    run(body())


def test_new_rows_append_without_clearing_and_selection_stays_then_end_follows():
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
            assert "End follows latest" in feedhead_text(app)
            fake.new = [ev(3, 1950), ev(4, 1990)]
            await until(lambda: table.row_count == 4, timeout=6)
            await pilot.pause()
            assert table.coordinate_to_cell_key((0, 0))[0].value == first_key  # no clear, same first row
            assert table.cursor_row == 0  # selection did not jump
            assert app.fresh_ids == {3, 4}  # marked fresh for one poll cycle
            assert app.unseen == 2 and "▲ 2 new below" in feedhead_text(app) and "End follows latest" in feedhead_text(app)
            # the same snapshot again: nothing is added twice, the fresh mark is gone, the unseen count stays
            await asyncio.sleep(1.3)
            await pilot.pause()
            assert table.row_count == 4 and app.fresh_ids == set() and app.unseen == 2
            await pilot.press("end")  # explicit follow latest
            await pilot.pause()
            assert app.follow is True and app.unseen == 0 and table.cursor_row == 3
            assert "following latest" in feedhead_text(app)
            fake.new = [ev(5, 1995)]
            await until(lambda: table.row_count == 5, timeout=6)
            await pilot.pause()
            assert table.cursor_row == 4 and app.unseen == 0  # following: the cursor rides the tail

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
            await until(lambda: table.row_count == 2)  # history streams in, then the table holds exactly the new range
            await pilot.pause()
            assert app.fresh_ids == set() and app.last_events_kind == "history"

    run(body())


def test_search_filters_accepts_spaces_and_escape_restores():
    async def body():
        fake = FakeClient()
        app = StampedeTUI(fake, poll_s=0.3)
        async with app.run_test(size=(180, 50)) as pilot:
            await until(lambda: app.query_one("#feed").row_count == 2)
            await pilot.pause()
            await pilot.press("slash")
            await pilot.pause()
            assert app.focused.id == "search" and "type to filter" in str(app.query_one("#keys").render())
            for ch in "zzz":
                await pilot.press(ch)
            await pilot.pause()
            assert app.query_one("#feed").row_count == 0
            assert "NO ROWS MATCH 'zzz'" in str(app.query_one("#empty").render())
            for _ in range(3):
                await pilot.press("backspace")
            await pilot.press("a", "space")  # space is typed into the filter, it does not toggle playback
            await pilot.pause()
            assert app.filter_text == "a " and fake.playing is False
            assert app.query_one("#feed").row_count == 2 and "2 of 2 match 'a '" in feedhead_text(app)
            await pilot.press("enter")  # keeps the filter, focus back on the table
            await pilot.pause()
            assert app.focused.id == "feed" and app.filter_text == "a " and app.query_one("#search").has_class("visible")
            await pilot.press("escape")
            await pilot.pause()
            assert app.query_one("#feed").row_count == 2 and app.filter_text == "" and not app.query_one("#search").has_class("visible")

    run(body())


def test_tab_cycles_panes_and_digits_switch_views():
    async def body():
        fake = FakeClient()
        app = StampedeTUI(fake, poll_s=0.3)
        async with app.run_test(size=(180, 50)) as pilot:
            await until(lambda: app.query_one("#feed").row_count == 2)
            await pilot.pause()
            assert app.focused.id == "feed" and app.screen_mode == "feed"
            await pilot.press("tab")
            await pilot.pause()
            assert app.focused.id == "detail" and app.query_one("#detailwrap").has_class("focused")
            assert "DETAILS" in str(app.query_one("#keys").render())
            await pilot.press("tab")
            await pilot.pause()
            assert app.focused.id == "feed" and not app.query_one("#detailwrap").has_class("focused")
            await pilot.press("2")  # view switch is a digit, not Tab
            await until(lambda: app.query_one("#radar").row_count == 2)
            await pilot.pause()
            assert app.screen_mode == "radar" and app.focused.id == "radar"
            assert "2 RADAR" in str(app.query_one("#brand").render())
            assert "SELECTED COIN" in str(app.query_one("#detailhead").render()) and "BBB coin" in detail_text(app) and "SCORE 89.0 · rank 1 of 2" in detail_text(app)
            assert TOKEN_B in detail_text(app)  # full address in the detail pane
            await pilot.press("tab")
            await pilot.pause()
            assert app.focused.id == "detail"
            await pilot.press("shift+tab")
            await pilot.pause()
            assert app.focused.id == "radar"
            await pilot.press("enter")
            await until(lambda: app.detail_mode == "coin" and app.coin_doc is not None)
            await pilot.pause()
            assert "COIN CARD" in str(app.query_one("#detailhead").render()) and "BBB coin" in detail_text(app)
            await pilot.press("escape")
            await pilot.pause()
            assert app.detail_mode == "summary" and "SELECTED COIN" in str(app.query_one("#detailhead").render())
            await pilot.press("1")
            await pilot.pause()
            assert app.screen_mode == "feed" and app.focused.id == "feed"
            # Tab from the search box goes to the table and does not switch views
            await pilot.press("slash")
            await pilot.pause()
            assert app.focused.id == "search"
            await pilot.press("tab")
            await pilot.pause()
            assert app.focused.id == "feed" and app.screen_mode == "feed" and app.query_one("#search").has_class("visible")

    run(body())


def test_header_variants_by_size_and_quit():
    async def body():
        for size, variant in (((180, 50), "wide"), ((120, 36), "compact"), ((100, 28), "tiny")):
            fake = FakeClient()
            app = StampedeTUI(fake, poll_s=0.3)
            async with app.run_test(size=size) as pilot:
                await until(lambda: app.query_one("#feed").row_count == 2)
                await pilot.pause()
                brand_txt = str(app.query_one("#brand").render())
                mark_txt = str(app.query_one("#mark").render())
                assert ("█" in brand_txt) is (variant == "wide"), (size, brand_txt[:40])  # block wordmark only on wide windows
                assert ("S T A M P E D E" in brand_txt) is (variant != "wide")
                assert "1 FEED" in brand_txt and "REPLAY 10×" in brand_txt and "Robinhood Chain" in brand_txt
                assert app.query_one("#header").has_class("tiny") is (variant == "tiny")
                assert app.query_one("#header").has_class("compact") is (variant == "compact")
                if variant == "wide":
                    # the README bison as half-blocks: 25 columns x 12 rows, bottom-aligned with the 5-row wordmark
                    assert mark_txt.count("▀") == 25 * 12 and app.query_one("#mark").display
                    assert app.query_one("#header").size.height == 13
                    brand_rows = brand_txt.split("\n")
                    assert [("█" in r) for r in brand_rows] == [False] * 7 + [True] * 5 + [False]
                else:
                    assert app.query_one("#header").size.height == 3 and not app.query_one("#mark").display
                    assert "CLOCK" in brand_txt and "RANGE" in brand_txt
                if variant != "tiny":
                    assert app.query_one("#feed").size.width >= sum(w for _, _, w in app._feed_columns()) + 2 * len(app._feed_columns())  # no horizontal cut
                assert app.query_one("#bottom").has_class("hidden") is (size[1] < 34)
                await pilot.press("q")
                await pilot.pause()
            assert app.return_code == 0

    run(body())


def test_ctrl_c_quits():
    async def body():
        fake = FakeClient()
        app = StampedeTUI(fake, poll_s=0.3)
        async with app.run_test(size=(120, 36)) as pilot:
            await until(lambda: app.query_one("#feed").row_count == 2)
            await pilot.press("ctrl+c")
            await pilot.pause()
        assert app.return_code == 0

    run(body())


def test_resize_keeps_selection_and_switches_columns():
    async def body():
        fake = FakeClient()
        app = StampedeTUI(fake, poll_s=0.3)
        async with app.run_test(size=(180, 50)) as pilot:
            table = app.query_one("#feed")
            await until(lambda: table.row_count == 2)
            await pilot.pause()
            assert any(k == "sold_t" for _, k, _ in app._feed_columns())  # wide: sold-time column present
            await pilot.press("up")
            await pilot.pause()
            assert table.cursor_row == 0 and app.follow is False
            await pilot.resize_terminal(120, 36)
            await pilot.pause()
            await until(lambda: table.row_count == 2)
            await pilot.pause()
            assert not any(k == "sold_t" for _, k, _ in app._feed_columns())  # compact columns
            assert table.cursor_row == 0 and app.follow is False  # selection survived the column rebuild
            assert "S T A M P E D E" in str(app.query_one("#brand").render())
            await pilot.resize_terminal(180, 50)
            await pilot.pause()
            await until(lambda: table.row_count == 2)
            assert "█" in str(app.query_one("#brand").render()) and table.cursor_row == 0

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
            assert "DISCONNECTED" in str(alert.render()) and "retrying every 0.3s" in str(alert.render())
            assert "DISCONNECTED" in str(app.query_one("#keys").render())
            assert app.query_one("#feed").row_count == 2  # data frozen, not replaced
            fake.fail = False
            await until(lambda: not alert.has_class("visible"))
            await pilot.pause()
            assert "DISCONNECTED" not in str(app.query_one("#keys").render())

    run(body())


def test_live_badge_differs_from_replay_and_marks_stale():
    async def body():
        fake = FakeClient()
        fake.mode = "live"
        app = StampedeTUI(fake, poll_s=0.3)
        async with app.run_test(size=(180, 50)) as pilot:
            await until(lambda: app.query_one("#feed").row_count == 2)
            await pilot.pause()
            head = str(app.query_one("#brand").render())
            assert " LIVE " in head and "REPLAY" not in head and "BLOCK" in head and "data age 5s" in head and "STALE" not in head
            assert "space" not in str(app.query_one("#keys").render())  # no transport controls in live mode
            fake.age_s = 130
            await until(lambda: "STALE" in str(app.query_one("#brand").render()), timeout=8)
            assert "data age 2m10s" in str(app.query_one("#brand").render())

    run(body())


def test_space_toggles_shared_session_and_seek_keys():
    async def body():
        fake = FakeClient()
        app = StampedeTUI(fake, poll_s=0.3)
        async with app.run_test(size=(180, 50)) as pilot:
            await until(lambda: app.session is not None)
            await pilot.pause()
            await pilot.press("space")
            await until(lambda: fake.playing is True and app.session["playing"] is True)
            assert "[REPLAY 10× · PAUSED]" in str(app.query_one("#brand").render())  # label comes from the server
            await pilot.press("left")
            await until(lambda: fake.clock == 1940)
            await pilot.press("right")
            await until(lambda: fake.clock == 2000)

    run(body())
