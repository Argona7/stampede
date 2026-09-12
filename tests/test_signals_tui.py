"""TUI screen 4 SIGNALS against the fake API: alert rows with p / EV / size and the +30 / +60 countdown, the paper ledger
pane (stats, open and closed positions, track record), Enter -> coin card, the live stream feeding alerts / positions /
the LIVE lag badge, and the polling fallback when the server has no engine."""
import os
import threading

os.environ.pop("NO_COLOR", None)
os.environ.setdefault("TEXTUAL_COLOR_SYSTEM", "truecolor")

from helpers import TOKEN_A, TOKEN_B, until  # noqa: E402
from stampede.tui.app import StampedeTUI, filter_live_rows, outcome_s  # noqa: E402
from test_tui import FakeClient, detail_text, feedhead_text, radar_row, run  # noqa: E402


def alert(i: int, tok: str, sym: str, clock: int, rule: str = "edge_enter", o30=None) -> dict:
    detail = {"rank": 1, "p_2x_30m": 0.61, "ev_per_trade_quote": 0.0042, "size_quote": 0.02, "exit_plan": ["sell 50% at +100%", "then trail 25% below the high", "stop at −30%", "out after 45 min whatever the price"], "reasons": ["p(≥2× in 30 min) 61% from model"], "engine": "wss"} if rule == "edge_enter" else {"rank": 2, "sources": [], "accel": 1.0, "breadth": 1, "age_s": 300, "stage": "curve"}
    return {"id": i, "created_ts": clock, "clock_ts": clock, "mode": "live", "token": tok, "symbol": sym, "rule": rule, "score": 80.0, "inflow": 9, "mentions_1h": None, "price": 1e-9, "detail": detail, "outcome_30m": o30, "outcome_60m": None, "graduated_after": None}


def position(pid: int, tok: str, sym: str, status: str, ret: float, pnl: float | None) -> dict:
    return {
        "id": pid, "token": tok, "symbol": sym, "curve": "0xc", "quote_token": "0x0", "quote_symbol": "ETH", "status": status, "alert_ts": 1900, "alert_block": 10, "opened_ts": 1901, "opened_block": 11,
        "size_quote": 0.02, "tokens": "10", "tokens_remaining": "10" if status == "open" else "0", "tokens_f": 1e-17, "entry_px": 2e-9, "spot_px_before": 1.9e-9, "impact_bps": 12.0, "fee_quote": 0.0002, "tax_quote": 0.0002, "snipe_quote": 0.0, "tax_bps": 100, "tax_source": "curve events",
        "p_2x_30m": 0.61, "ev_quote": 0.0042, "plan": {"tp": [[1.0, 0.5]], "trail": 0.25, "sl": 0.3, "time_s": 2700, "inflow_dies": True, "text": ["sell 50% at +100%"]}, "peak_px": 2.4e-9, "peak_ret": 0.2, "tp_done": False,
        "proceeds_quote": 0.0 if status == "open" else 0.02 + (pnl or 0), "exit_fees_quote": 0.0 if status == "open" else 0.0003, "mark_px": 2e-9 * (1 + ret), "mark_quote": 0.02 * (1 + ret), "unrealized_quote": 0.02 * ret if status == "open" else None, "ret": ret,
        "closed_ts": None if status == "open" else 1999, "closed_block": None, "exit_reason": None if status == "open" else "stop", "pnl_quote": pnl, "pnl_usd": (pnl * 4000) if pnl is not None else None, "hold_s": None if status == "open" else 98, "updated_ts": 1950, "fills": [],
    }


class SignalsFake(FakeClient):
    def __init__(self, live: bool = False):
        super().__init__()
        if live:
            self.mode = "live"
        self.alert_rows = [alert(2, TOKEN_B, "BBB", 1950), alert(1, TOKEN_A, "AAA", 1000, rule="under_radar_top5", o30=-12.5)]
        self.paper_doc = {
            "positions": {"open": [position(2, TOKEN_B, "BBB", "open", 0.083, None)], "closed": [position(1, TOKEN_A, "AAA", "closed", -0.31, -0.0062)], "pending": []},
            "equity": [{"ts": 1999, "realized_quote": -0.0062, "unrealized_quote": 0.0, "equity_quote": -0.0062}],
            "stats": {"trades": 1, "coins": 1, "wins": 0, "hit_rate": 0.0, "expectancy_quote": -0.0062, "expectancy_pct": -0.31, "expectancy_usd": -24.8, "usd_known": 1, "total_quote": -0.0062, "total_usd": -24.8, "profit_factor": None, "max_drawdown_quote": -0.0062, "avg_win_quote": None, "avg_loss_quote": -0.0062, "median_hold_s": 98, "fees_quote": 0.0007, "size_mean_quote": 0.02, "exits": {"stop": 1}, "per_hour": [], "by_hour_of_day": [], "ci95_quote": None, "ci_method": "needs closed trades on at least 2 coins", "open": 1, "unrealized_quote": 0.00166, "exposure_quote": 0.02, "skipped_by_risk": 1, "simulated": True, "note": "simulated"},
            "config": {"size_cap_quote": 0.02, "max_concurrent": 3, "daily_stop_frac": 0.05, "plan": {}, "latency": "next block", "fx": True},
            "counters": {}, "clock": 2000, "simulated": True, "source": "engine", "mode": self.mode,
        }
        self.track_doc = {"generated_at": 2000, "since_ts": 900, "engine_started_at": 900, "mode": self.mode, "alerts": {"total": 2, "by_rule": {"edge_enter": {"fired": 1, "coins": 1, "due_30m": 0, "with_outcome_30m": 0, "up_30m": 0, "ge_2x_30m": 0, "median_30m": None, "mean_30m": None, "due_60m": 0, "with_outcome_60m": 0, "up_60m": 0, "median_60m": None, "graduated_after": 0, "engine": 1}, "under_radar_top5": {"fired": 1, "coins": 1, "due_30m": 1, "with_outcome_30m": 1, "up_30m": 0, "ge_2x_30m": 0, "median_30m": -12.5, "mean_30m": -12.5, "due_60m": 0, "with_outcome_60m": 0, "up_60m": 0, "median_60m": None, "graduated_after": 0, "engine": 1}}, "recent": []}, "paper": {"stats": self.paper_doc["stats"], "open": [], "closed": []}, "perf": {"uptime_s": 3720, "blocks_processed": 37000, "gaps_found": 0, "gaps_unfilled_blocks": 0, "latency_p50_ms": 101.2, "latency_p95_ms": 170.0} if live else None, "fx": {"rows": 10, "last_hour": 1800}, "simulated": True, "note": "n"}
        self.stream_handlers: list[tuple] = []
        self.stream_types = None

    def alerts(self):
        self.calls.append("alerts")
        return {"alerts": self.alert_rows, "rules": {}, "track_record": {"fired": 2, "with_outcome_30m": 1, "up_30m": 0, "median_30m": -12.5, "note": ""}, "worker": {}}

    def paper(self, closed_limit=100):
        self.calls.append("paper")
        return self.paper_doc

    def track_record(self):
        self.calls.append("track")
        return self.track_doc

    def open_stream(self, on_event, on_status, types=None):
        self.stream_types = types
        self.stream_handlers.append((on_event, on_status))
        h = self

        class Handle:
            def stop(self):
                h.calls.append("stream-stop")

        return Handle()

    def push(self, ev: dict) -> None:
        """Deliver a frame from another thread, the way the reader thread does (no join: the app thread must stay free
        to run the callback that `call_from_thread` hands it)."""
        on_event, _ = self.stream_handlers[-1]
        threading.Thread(target=on_event, args=(ev,), daemon=True).start()

    def push_status(self, status: str, detail=None) -> None:
        _, on_status = self.stream_handlers[-1]
        threading.Thread(target=on_status, args=(status, detail), daemon=True).start()


def test_signals_screen_rows_ledger_pane_enter_and_polling_fallback_in_replay():
    async def body():
        fake = SignalsFake()
        app = StampedeTUI(fake, poll_s=0.3)
        async with app.run_test(size=(180, 50)) as pilot:
            await until(lambda: app.query_one("#feed").row_count == 2)
            await pilot.press("4")
            table = app.query_one("#signals")
            await until(lambda: table.row_count == 2)
            await pilot.pause()
            assert app.screen_mode == "signals" and app.focused.id == "signals" and table.display and not app.query_one("#radar").display
            assert fake.stream_handlers == []  # replay: no stream is opened, the screen polls
            assert "4 SIGNALS" in str(app.query_one("#brand").render())
            head = feedhead_text(app)
            assert "SIGNALS · 1 ENTER · 2 alerts" in head and "paper 1 open / 1 closed" in head and "-0.0062 ETH" in head and "simulated fills" in head
            # rows: newest first; the ENTER row carries p / EV / size and the countdown to its +30 outcome on the chain clock (2000 - 1950 = 50 s in -> 29:10 left)
            cells = app._signal_cells
            kb, ka = f"{TOKEN_B}:1950", f"{TOKEN_A}:1000"
            assert list(cells) == [kb, ka]
            assert any(c.startswith("ENTER|") for c in cells[kb]) and any(c.startswith("61%|") for c in cells[kb]) and any(c.startswith("+0.0042|") for c in cells[kb]) and any(c.startswith("0.0200|") for c in cells[kb]) and any(c.startswith("in 29:10|") for c in cells[kb]) and any(c.startswith("open +8%|") for c in cells[kb])
            assert any(c.startswith("radar|") for c in cells[ka]) and any(c.startswith("-12.5%|") for c in cells[ka]) and any(c.startswith("n/a|") for c in cells[ka])  # under-radar alert: no p, settled -12.5 % at +30, +60 not due (1000 + 3600 > 2000 -> countdown) -> the n/a is the p column
            # the ledger pane: the selected alert's plan and paper position, then the stats with an honest CI, positions, track record
            txt = detail_text(app)
            assert "PAPER LEDGER" in str(app.query_one("#detailhead").render()) and "BBB  ENTER" in txt and "P(2×)    61% in 30 min" in txt and "sell 50% at +100%" in txt and "PAPER    open · mark +8.3%" in txt
            assert "TRADES   1 closed · 1 coins · 1 open · 1 skipped by risk" in txt and "HIT      0% (0 of 1)" in txt and "-0.0062 ETH (-31%)" in txt and "USD $-24.80" in txt and "95% CI   n/a (needs 2+ coins)" in txt
            assert "OPEN · 1" in txt and "CLOSED · 1" in txt and "stop" in txt and "TRACK RECORD" in txt and "ENTER: 1 fired" in txt and "under_radar_top5: 1 fired · +30 known 1 · up 0" in txt and "median +30 -12%" in txt
            assert "simulated" in txt.lower()
            # move to the radar alert: the pane switches to it
            await pilot.press("down")
            await pilot.pause()
            assert "AAA  under-radar top-5" in detail_text(app) and "+30M     -12.5%" in detail_text(app)
            # Enter opens the coin card of the selected alert, Esc comes back
            await pilot.press("enter")
            await until(lambda: app.detail_mode == "coin" and app.coin_doc is not None)
            assert f"coin:{TOKEN_A}" in fake.calls and "COIN CARD" in str(app.query_one("#detailhead").render())
            await pilot.press("escape")
            await pilot.pause()
            assert app.detail_mode == "summary" and "PAPER LEDGER" in str(app.query_one("#detailhead").render())
            # Tab cycles to the ledger pane and back; digits switch views; the footer names the keys
            await pilot.press("tab")
            await pilot.pause()
            assert app.focused.id == "detail"
            await pilot.press("tab")
            await pilot.pause()
            assert app.focused.id == "signals"
            keys = str(app.query_one("#keys").render())
            assert "Enter coin card" in keys or "Enter card" in keys
            await pilot.press("2")
            await until(lambda: app.screen_mode == "radar")
            await pilot.press("4")
            await until(lambda: app.screen_mode == "signals" and app.focused.id == "signals")
            cols = app._signals_columns()
            assert table.size.width >= sum(w for _, _, w in cols) + 2 * len(cols)  # nothing cut horizontally at 180 columns

    run(body())


def test_live_stream_feeds_alerts_positions_radar_and_the_lag_badge():
    async def body():
        fake = SignalsFake(live=True)
        fake.radar_rows = [radar_row(1, TOKEN_B, "BBB", 7)]
        app = StampedeTUI(fake, poll_s=0.3)
        async with app.run_test(size=(180, 50)) as pilot:
            await until(lambda: app.query_one("#feed").row_count == 2)
            await until(lambda: len(fake.stream_handlers) == 1)  # live: the reader thread is opened once the session says live
            assert set(fake.stream_types) == {"session", "alert", "position", "radar_delta"}
            fake.push_status("live", None)
            fake.push({"id": 1, "type": "session", "ts_emit": 1.0, "block": 5, "data": {"hello": True, "engine": "wss", "lag_ms": 133.4, "mode": "live"}})
            await until(lambda: app.live_status == "live" and app.live_lag_ms == 133.4)
            await pilot.pause()
            assert " LIVE " in str(app.query_one("#brand").render()) and "133 ms" in str(app.query_one("#brand").render())
            await pilot.press("4")
            table = app.query_one("#signals")
            await until(lambda: table.row_count == 2)
            # a fired alert arrives on the stream: a new first row, marked fresh, before any poll
            fake.push({"id": 2, "type": "alert", "ts_emit": 2.0, "block": 6, "data": {"kind": "fired", "key": f"{TOKEN_A}:1990", "created_ts": 1990, "clock_ts": 1990, "mode": "live", "token": TOKEN_A, "symbol": "AAA", "rule": "edge_enter", "score": 77.0, "inflow": 12, "mentions_1h": None, "price": 2e-9, "detail": {"p_2x_30m": 0.55, "ev_per_trade_quote": 0.003, "size_quote": 0.02, "exit_plan": ["sell 50% at +100%"], "reasons": [], "engine": "wss"}}})
            await until(lambda: table.row_count == 3)
            await pilot.pause()
            assert list(app._signal_cells)[0] == f"{TOKEN_A}:1990" and any(c.startswith("▌|") for c in app._signal_cells[f"{TOKEN_A}:1990"]) and any(c.startswith("55%|") for c in app._signal_cells[f"{TOKEN_A}:1990"])
            assert "2 ENTER · 3 alerts" in feedhead_text(app) and "stream 133 ms" in feedhead_text(app)
            # its outcome settles on the stream
            fake.push({"id": 3, "type": "alert", "ts_emit": 3.0, "block": 7, "data": {"kind": "outcome", "key": f"{TOKEN_B}:1950", "token": TOKEN_B, "symbol": "BBB", "clock_ts": 1950, "price": 1e-9, "outcome_30m": 42.0}})
            await until(lambda: any(c.startswith("+42.0%|") for c in app._signal_cells[f"{TOKEN_B}:1950"]))
            # a position event marks the open trade and then closes it
            fake.push({"id": 4, "type": "position", "ts_emit": 4.0, "block": 8, "data": {"kind": "mark", **position(2, TOKEN_B, "BBB", "open", 0.5, None)}})
            await until(lambda: "open +50%" in " ".join(app._signal_cells[f"{TOKEN_B}:1950"]))
            fake.push({"id": 5, "type": "position", "ts_emit": 5.0, "block": 9, "data": {"kind": "closed", **position(2, TOKEN_B, "BBB", "closed", 0.9, 0.018)}})
            await until(lambda: len(app.paper_doc["positions"]["closed"]) == 2 and not app.paper_doc["positions"]["open"])
            await pilot.pause()
            assert "OPEN · 0" in detail_text(app) and "CLOSED · 2" in detail_text(app)
            # radar deltas feed the radar table (under-radar preset applied client-side) and the poll no longer overwrites them
            await pilot.press("2")
            await until(lambda: app.screen_mode == "radar")
            new_tok = "0x" + "77" * 20
            rows = [{**radar_row(1, TOKEN_B, "BBB", 9), "rank": 1}, {**radar_row(2, new_tok, "NEW", 4), "rank": 2}, {**radar_row(3, TOKEN_A, "OLD", 3), "rank": 3, "age_s": 5 * 3600}]
            fake.push({"id": 6, "type": "radar_delta", "ts_emit": 6.0, "block": 10, "data": {"full": True, "reason": "snapshot", "clock": 2000, "window_s": 1800, "span_s": 1800, "rows": rows, "removed": [], "top": [TOKEN_B, new_tok, TOKEN_A]}})
            rt = app.query_one("#radar")
            await until(lambda: rt.row_count == 2)  # OLD is 5 h old: the under-radar preset drops it
            assert [r["address"] for r in app.radar_rows] == [TOKEN_B, new_tok] and app.live_deltas == 1
            fake.push({"id": 7, "type": "radar_delta", "ts_emit": 7.0, "block": 11, "data": {"full": False, "reason": "block", "clock": 2001, "window_s": 1800, "span_s": 1800, "rows": [], "removed": [new_tok], "top": [TOKEN_B]}})
            await until(lambda: app.live_deltas == 2)
            app._live_radar_at = 0.0
            app._radar_loaded(fake.radar({"preset": "under_radar"}))  # a poll answer keeps the stream's rows
            await pilot.pause()
            assert [r["address"] for r in app.radar_rows] == [TOKEN_B]
            # the reader reports a drop: the badge says so and the lag disappears; back on live it returns
            fake.push_status("reconnecting", "ConnectionError")
            await until(lambda: app.live_status == "reconnecting")
            await pilot.pause()
            assert "RECONNECTING" in str(app.query_one("#brand").render()) and app.live_lag_ms is None
            await pilot.press("q")
            await pilot.pause()
        assert "stream-stop" in fake.calls and app.return_code == 0

    run(body())


def test_signals_helpers_and_live_filter():
    assert outcome_s(12.34, 100, 50) == "+12.3%" and outcome_s(None, 1800, 1000) == "in 13:20" and outcome_s(None, 1800, 1900) == "settling" and outcome_s(None, 1800, 3000) == "no price" and outcome_s(None, 1, None) == "n/a"
    assert outcome_s(1234.5, 1, 1) == "+12.3k%"
    rows = [{**radar_row(1, TOKEN_A, "A", 5), "wallets_range": 5, "progress": 0.7, "quality": 0.6}, {**radar_row(2, TOKEN_B, "B", 3), "wallets_range": 1}, {**radar_row(3, "0x" + "33" * 20, "C", 4), "stage": "graduated", "progress": 1.0, "quality": 0.9, "wallets_range": 4}]
    assert [r["symbol"] for r in filter_live_rows(rows, "all")] == ["A", "C"]  # one rotating wallet never ranks
    assert [r["symbol"] for r in filter_live_rows(rows, "under_radar")] == ["A"]
    assert [r["symbol"] for r in filter_live_rows(rows, "graduating")] == ["A"]
    assert [r["symbol"] for r in filter_live_rows(rows, "smart_rotators")] == ["C", "A"]
    assert filter_live_rows(rows, "clean_launch") == []  # no launch intel on these rows: nothing passes, never a guess
