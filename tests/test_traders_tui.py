"""TUI screen 3 TRADERS against the fake API: leaderboard rows with n/a for unknowns, selection summary, wallet card
on Enter, Esc back, presets, 1/2/3 switching and Tab panes, columns that fit the compact window."""
import os

os.environ.pop("NO_COLOR", None)
os.environ.setdefault("TEXTUAL_COLOR_SYSTEM", "truecolor")

from helpers import TOKEN_A, until  # noqa: E402
from stampede.tui.app import StampedeTUI  # noqa: E402
from test_tui import FakeClient, detail_text, feedhead_text, run  # noqa: E402

WA = "0xaaaa000000000000000000000000000000000001"
WB = "0xbbbb000000000000000000000000000000000002"


def trader_row(w: str, pnl: float, quality: float, tags: list[str], roi: float | None = 0.5, win: float | None = 0.75) -> dict:
    return {
        "wallet": w, "short": f"{w[:6]}…{w[-4:]}", "explorer": f"https://robinhoodchain.blockscout.com/address/{w}", "label": "full",
        "trades": 12, "buys": 7, "sells": 5, "coins": 4, "positions_closed": 4, "positions_open": 1, "wins": 3,
        "realized_eth": pnl - 0.01, "unrealized_eth": 0.01, "cost_eth": 0.4, "fees_eth": 0.008, "pnl_eth": pnl, "pnl_usd": None,
        "roi": roi, "roi_realized": roi, "win_rate": win, "median_hold_s": 95.0, "trades_per_hour": 1.5, "buyer_rank_median": 6.0, "sniper_share": 0.25,
        "exit_quality": 0.71, "rug_avoid": 0.5, "consistency": None, "is_bot": "bot" in tags, "deployer_linked": "deployer" in tags, "quality": quality, "tags": tags,
        "first_ts": 1500, "last_ts": 1900,
    }


class TradersFake(FakeClient):
    def __init__(self):
        super().__init__()
        self.trader_rows = [trader_row(WA, 0.1234, 0.62, []), trader_row(WB, -0.02, 0.41, ["bot"], roi=None, win=None)]
        self.traders_params: list[dict] = []

    def traders(self, params):
        self.calls.append("traders")
        self.traders_params.append(dict(params))
        return {
            "preset": params.get("preset"), "presets": {"top": {"label": "Top: biggest ETH PnL after fees"}, "smart": {"label": "Smart: highest quality"}},
            "rows": self.trader_rows, "total": len(self.trader_rows), "run": {"run_ts": 1.0, "from_ts": 1000, "to_ts": 2000, "wallets": 2}, "range": {"from_ts": 1000, "to_ts": 2000}, "sort": "pnl", "min_trades": 1,
        }

    def wallet(self, addr):
        self.calls.append(f"wallet:{addr}")
        row = next(r for r in self.trader_rows if r["wallet"] == addr)
        return {
            "address": addr, "short": row["short"], "explorer": row["explorer"], "run": {"from_ts": 1000, "to_ts": 2000}, "stats": row,
            "positions": [{"token": {"address": TOKEN_A, "symbol": "AAA", "short": "0x0000…00a1"}, "quote_symbol": "ETH", "entry_ts": 1500, "exit_ts": 1600, "closed": True, "buys": 1, "sells": 1, "cost_quote": 0.1, "proceeds_quote": 0.15, "pnl_quote": 0.05, "pnl_usd": None, "fees_quote": 0.002, "unrealized_quote": None, "hold_s": 100, "buyer_rank": 3, "since_launch_s": 40.0, "sniper": False, "snipe_paid": False, "exit_quality": 0.9, "rug_after": True, "entry_tx": "0x" + "1" * 64, "exit_tx": "0x" + "2" * 64}],
            "trades": [{"id": 1, "tx": "0x" + "c" * 64, "tx_url": "https://x/tx", "block": 1, "ts": 1600, "ts_exact": True, "token": {"symbol": "AAA"}, "side": "sell", "token_amount": 1.0, "quote_symbol": "ETH", "quote_amount": 0.15, "venue": "curve", "fee_quote": 0.0015, "tax_quote": 0.0, "snipe_paid": False}],
            "trades_total": 12, "coins": [{"address": TOKEN_A, "symbol": "AAA", "trades": 12, "buys": 7, "sells": 5}], "is_contract": False, "launched_coins": 0,
            "copy_test": "median hold 95 s: the exit is usually gone before a copier can act", "note": "fixture note",
        }


def test_traders_screen_rows_summary_card_presets_and_panes():
    async def body():
        fake = TradersFake()
        app = StampedeTUI(fake, poll_s=0.3)
        async with app.run_test(size=(180, 50)) as pilot:
            await until(lambda: app.query_one("#feed").row_count == 2)
            await pilot.pause()
            await pilot.press("3")
            table = app.query_one("#traders")
            await until(lambda: table.row_count == 2)
            await pilot.pause()
            assert app.screen_mode == "traders" and app.focused.id == "traders" and table.display and not app.query_one("#radar").display
            assert "3 TRADERS" in str(app.query_one("#brand").render()) and "TRADERS · TOP" in feedhead_text(app) and "2 wallets" in feedhead_text(app)
            assert fake.traders_params[0]["preset"] == "top"
            # row cells: PnL signed, unknown ROI / win rate as n/a (never 0), tags column
            cells = app._traders_cells
            assert any(c.startswith("+0.1234|") for c in cells[WA]) and any(c.startswith("n/a|") for c in cells[WB]) and any(c.startswith("bot|") for c in cells[WB])
            assert not any(c.startswith("n/a|") for c in cells[WA])
            # summary pane for the selected (first) wallet: full address, rank, facts
            txt = detail_text(app)
            assert "SELECTED WALLET" in str(app.query_one("#detailhead").render()) and WA in txt and "rank 1 of 2" in txt and "+0.1234 ETH" in txt and "QUALITY  0.620" in txt
            await pilot.press("down")
            await pilot.pause()
            assert WB in detail_text(app) and "rank 2 of 2" in detail_text(app) and "TAGS     bot" in detail_text(app) and "ROI      n/a" in detail_text(app)
            await pilot.press("up")
            await pilot.pause()
            # Enter opens the wallet card with positions, coins, trades and the copy-test note; Esc returns to the summary
            await pilot.press("enter")
            await until(lambda: app.detail_mode == "wallet" and app.wallet_doc is not None)
            await pilot.pause()
            assert "WALLET CARD" in str(app.query_one("#detailhead").render()) and f"wallet:{WA}" in fake.calls
            txt = detail_text(app)
            assert "POSITIONS · 1 in range" in txt and "AAA" in txt and "+0.0500 ETH closed" in txt and "RECENT TRADES · 1 of 12" in txt and "0xcccc…cccc" in txt and "COPY-TEST" in txt and "fixture note" in txt
            assert "Enter card" in str(app.query_one("#keys").render()) or "Enter wallet card" in str(app.query_one("#keys").render()) or "DETAILS" in str(app.query_one("#keys").render()) or "Esc back" in str(app.query_one("#keys").render())
            await pilot.press("escape")
            await pilot.pause()
            assert app.detail_mode == "summary" and "SELECTED WALLET" in str(app.query_one("#detailhead").render())
            # presets: m = smart (refetch with min_trades 5), t = top
            await pilot.press("m")
            await until(lambda: any(p.get("preset") == "smart" for p in fake.traders_params))
            assert app.traders_preset == "smart" and next(p for p in fake.traders_params if p.get("preset") == "smart")["min_trades"] == 5
            await until(lambda: "TRADERS · SMART" in feedhead_text(app))
            await pilot.press("t")
            await until(lambda: app.traders_preset == "top")
            # Tab cycles table -> details -> table on this screen too
            await pilot.press("tab")
            await pilot.pause()
            assert app.focused.id == "detail" and app.query_one("#detailwrap").has_class("focused")
            await pilot.press("tab")
            await pilot.pause()
            assert app.focused.id == "traders"
            # End/Home move within the leaderboard
            await pilot.press("end")
            await pilot.pause()
            assert table.cursor_row == 1
            await pilot.press("home")
            await pilot.pause()
            assert table.cursor_row == 0
            # digits switch views both ways; the radar preset keys still switch to the radar
            await pilot.press("2")
            await until(lambda: app.screen_mode == "radar")
            await pilot.press("3")
            await until(lambda: app.screen_mode == "traders" and app.focused.id == "traders")
            await pilot.press("1")
            await pilot.pause()
            assert app.screen_mode == "feed" and app.focused.id == "feed" and not table.display
            # a traders preset key from the feed opens the traders screen
            await pilot.press("n")
            await until(lambda: app.screen_mode == "traders" and app.traders_preset == "snipers")

    run(body())


def test_traders_columns_fit_compact_and_wide_windows():
    async def body():
        for size in ((120, 36), (180, 50)):
            fake = TradersFake()
            app = StampedeTUI(fake, poll_s=0.3)
            async with app.run_test(size=size) as pilot:
                await until(lambda: app.query_one("#feed").row_count == 2)
                await pilot.press("3")
                table = app.query_one("#traders")
                await until(lambda: table.row_count == 2)
                await pilot.pause()
                cols = app._traders_columns()
                assert table.size.width >= sum(w for _, _, w in cols) + 2 * len(cols)  # no horizontal cut
                assert ("last" in [k for _, k, _ in cols]) is (size[0] >= 160)  # the last-trade column only on wide windows
                await pilot.press("q")
                await pilot.pause()
            assert app.return_code == 0

    run(body())


def test_traders_empty_store_explains_itself():
    async def body():
        fake = TradersFake()
        fake.trader_rows = []
        empty_doc = {"preset": "top", "presets": {}, "rows": [], "total": 0, "run": None, "range": None, "empty_reason": "no wallet_stats in this store: run `stampede traders --db <store>`"}
        fake.traders = lambda params: empty_doc  # type: ignore[method-assign]
        app = StampedeTUI(fake, poll_s=0.3)
        async with app.run_test(size=(180, 50)) as pilot:
            await until(lambda: app.query_one("#feed").row_count == 2)
            await pilot.press("3")
            await until(lambda: app.traders_doc is not None)
            await pilot.pause()
            assert "no wallet_stats" in feedhead_text(app) and "run `stampede traders" in detail_text(app)

    run(body())
