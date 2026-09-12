"""`stampede terminal`: full-screen TUI on top of the running API.

Black / red terminal: the pixel bison and the block wordmark in the header, white and grey working text,
dark-red hairlines, red only for the brand, the selected row marker and the newest confirmed rows.
Everything shown is read from the API (shared session clock, event stream with cursor, edge evidence,
radar); nothing is simulated.

Composition (top to bottom): brand block (mascot · wordmark or compact line · mode/clock facts · view tabs),
the active table (FEED, RADAR, TRADERS or SIGNALS) with the details of the selected object in the adjacent pane, hot
rotations + activity sparkline, one line of shortcuts. In live mode one SSE reader thread (`client.open_stream`)
carries the engine's alerts, paper positions, radar deltas and its head -> event lag (shown after the LIVE badge);
the feed keeps polling `/api/events` with its cursor, so `N new` means exactly what it meant before. Header variants by width: >= 160 columns (and >= 30
rows) get the README bison as 25x12 half-blocks with the block wordmark bottom-aligned to it (13 rows);
narrower or shorter windows get a three-line header: compact wordmark + mode + chain, one status line, tabs.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from rich.text import Text
from textual import events, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.widgets import DataTable, Input, Sparkline, Static

from . import brand
from .client import ApiClient, ApiError

BG = "#050505"
SURFACE = "#0D0A0A"
BORDER = "#351419"
PRIMARY = "#FF3344"
ACTIVE = "#260D12"
TEXT = "#F2F2F2"
SECONDARY = "#A3A3A3"
MUTED = "#747474"

STALE_AFTER_S = 60
BUCKET_S = 60
WIDE_COLS = 160  # mascot + block wordmark + fact column; narrower windows get the 3-row compact header
MARK_ROWS = 30  # below this many rows the header is the three-line compact one even when wide
MASCOT_ROWS = 12  # the README bison as half-blocks: 12 rows (24 logical pixels) ...
MASCOT_COLS = brand.bison_width(MASCOT_ROWS) or 16  # ... x 25 columns (16 when it falls back to the head mark)
HEADER_ROWS = MASCOT_ROWS + 1  # mascot rows + the view tabs line
MARK_COLS = 100


def utc(ts: int | None) -> str:
    if not ts:
        return "--:--:--"
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%H:%M:%S")


def short(a: str) -> str:
    return f"{a[:6]}…{a[-4:]}" if a and len(a) > 12 else a


def launch_line(li: dict | None) -> str:
    """One line of launch quality (stage 3) for the coin card; n/a = unknown, never 0."""
    if not li:
        return "launch: n/a (run stampede launch-intel on this store)"
    if not li.get("observed"):
        dep = li.get("deployer_prior_launches_30d")
        return f"launch: before the indexed range · deployer {dep if dep is not None else 'n/a'} prior launches in 30d"
    dev = f"{li['dev_buy_share'] * 100:.2f}%" if li.get("dev_buy_share") is not None else "n/a"
    bundle = li.get("exempt_declared_n") if li.get("exempt_declared_n") is not None else li.get("bundle_n")
    tax = f"{li['creator_tax_bps']} bps" if li.get("creator_tax_bps") is not None else "n/a"
    prior = li.get("deployer_prior_launches_30d")
    grad = "first" if prior == 0 else (f"{(li.get('deployer_graduation_rate') or 0) * 100:.0f}% of {prior}" if prior is not None else "n/a")
    farm = {True: "yes", False: "no"}.get(li.get("launch_farm"), "n/a")
    return f"launch: dev {dev} · bundle {bundle if bundle is not None else 'n/a'} · tax {tax} · dep grad {grad} · farm {farm} · tax-free from {utc(li.get('snipe_tax_zero_ts'))} UTC (+{li.get('snipe_window_s') or 3}s)"


def verdict_line(v: dict | None) -> str:
    """One line of the stage-4/5 verdict for the coin card: action, p(2x), size and the exit plan; n/a when absent."""
    if not v:
        return "verdict: n/a"
    p = v.get("p_2x_30m")
    pd = v.get("p_minus50_30m")
    sz = v.get("size") or {}
    plan = " · ".join((v.get("exit_plan") or {}).get("text") or [])
    src = "model" if v.get("source") == "model" else "rules"
    bits = [f"verdict: {v.get('action', '?')}", f"p2x {p * 100:.0f}%" if p is not None else "p2x n/a"]
    if pd is not None:
        bits.append(f"p-50% {pd * 100:.0f}%")
    if v.get("action") == "ENTER" and sz.get("allowed"):
        bits.append(f"size {sz.get('quote'):.4f}")
    bits.append(f"({src})")
    return " · ".join(bits) + (f"\n  plan: {plan}" if plan else "")


def eth_s(v: float | None, d: int = 4) -> str:
    """Signed quote amount for the ledger tables; n/a when unknown."""
    return "n/a" if v is None else f"{v:+.{d}f}"


def pct_s(v: float | None, d: int = 1) -> str:
    """A fraction as a signed percent (0.12 -> +12.0%); n/a when unknown."""
    return "n/a" if v is None else f"{v * 100:+.{d}f}%"


def mmss(s: int | float | None) -> str:
    if s is None:
        return "n/a"
    s = int(s)
    return f"{s // 60}:{s % 60:02d}"


def outcome_s(value: float | None, due: int, clock: int | None) -> str:
    """An alert outcome cell: the settled percent, the countdown on the chain clock, or why it is empty."""
    if value is not None:
        return f"{value:+.1f}%" if abs(value) < 1000 else f"{value / 100:+.1f}k%"
    if clock is None:
        return "n/a"
    left = due - clock
    if left > 0:
        return f"in {mmss(left)}"
    return "settling" if left > -300 else "no price"


def dur(s: int | None) -> str:
    """Compact duration: 44s · 15m20s · 30m · 1h05m · 2h."""
    if s is None:
        return "?"
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m" if s % 60 == 0 else f"{s // 60}m{s % 60:02d}s"
    return f"{s // 3600}h" if s % 3600 == 0 else f"{s // 3600}h{(s % 3600) // 60:02d}m"


def fit(sym: str, w: int) -> str:
    """Truncate a display symbol to `w` cells, keeping the `·xxxx` address suffix that tells same-ticker coins apart."""
    if len(sym) <= w:
        return sym
    if "·" in sym:
        name, suf = sym.rsplit("·", 1)
        keep = w - len(suf) - 2
        if keep >= 2:
            return f"{name[:keep]}…·{suf}"
    return sym[: max(1, w - 1)] + "…"


def num(s: str, style: str = TEXT) -> Text:
    return Text(s, style=style, justify="right")


def filter_live_rows(rows: list[dict[str, Any]], preset: str) -> list[dict[str, Any]]:
    """The /api/radar preset semantics (api/radar.py PRESETS) on rows folded from `radar_delta`: the stream carries the
    whole ranking, the preset picks and orders like the polled endpoint does. Bots are already excluded by the engine."""
    out = [r for r in rows if (r.get("wallets_range") or 0) >= 2]
    if preset == "under_radar":
        out = [r for r in out if r.get("stage") == "curve" and r.get("age_s") is not None and r["age_s"] <= 4 * 3600 and (r.get("mentions_1h") is None or r["mentions_1h"] <= 3)]
    elif preset == "graduating":
        out = sorted((r for r in out if r.get("stage") == "curve" and (r.get("progress") or 0) >= 0.6), key=lambda r: -(r.get("progress") or 0))
    elif preset == "smart_rotators":
        out = sorted((r for r in out if (r.get("quality") or 0) >= 0.55), key=lambda r: -(r.get("quality") or 0))
    elif preset == "clean_launch":
        out = [r for r in out if (r.get("launch") or {}).get("observed") and ((r["launch"].get("bundle_n") if r["launch"].get("bundle_n") is not None else 99) <= 2) and ((r["launch"].get("dev_buy_share") if r["launch"].get("dev_buy_share") is not None else 1) <= 0.05) and not r["launch"].get("launch_farm")]
    return out


class StampedeTUI(App):
    TITLE = "STAMPEDE"
    CSS = f"""
    Screen {{ background: {BG}; color: {TEXT}; }}
    #header {{ height: {HEADER_ROWS}; }}
    #header.compact, #header.tiny {{ height: 3; }}
    #mark {{ width: {MASCOT_COLS + 2}; height: {MASCOT_ROWS}; padding: 0 1; }}
    #header.compact #mark, #header.tiny #mark {{ display: none; }}
    #brand {{ width: 1fr; height: {HEADER_ROWS}; padding: 0 1; }}
    #header.compact #brand, #header.tiny #brand {{ height: 3; }}
    #alert {{ height: auto; display: none; padding: 0 1; background: {PRIMARY}; color: {BG}; text-style: bold; }}
    #alert.visible {{ display: block; }}
    #rule1 {{ height: 1; color: {BORDER}; padding: 0 1; }}
    #main {{ height: 1fr; }}
    #feedwrap {{ width: 5fr; }}
    #feedwrap.wide {{ width: 3fr; }}
    #feedhead {{ height: 1; color: {SECONDARY}; padding: 0 1; }}
    #feed {{ height: 1fr; background: {BG}; }}
    #radar {{ height: 1fr; background: {BG}; display: none; }}
    #radar.visible {{ display: block; }}
    #traders {{ height: 1fr; background: {BG}; display: none; }}
    #traders.visible {{ display: block; }}
    #signals {{ height: 1fr; background: {BG}; display: none; }}
    #signals.visible {{ display: block; }}
    #feed.hidden {{ display: none; }}
    #empty {{ height: auto; color: {SECONDARY}; padding: 1 2; display: none; }}
    #empty.visible {{ display: block; }}
    #detailwrap {{ width: 3fr; border-left: solid {BORDER}; }}
    #detailwrap.wide {{ width: 2fr; }}
    #detailwrap.focused {{ border-left: solid {PRIMARY}; }}
    #detailhead {{ height: 1; color: {SECONDARY}; padding: 0 1; }}
    #detail {{ height: 1fr; padding: 0 0 0 1; scrollbar-color: {BORDER}; scrollbar-background: {BG}; scrollbar-size-vertical: 1; }}
    #bottom {{ height: 11; border-top: solid {BORDER}; }}
    #bottom.hidden {{ display: none; }}
    #hot {{ width: 3fr; padding: 0 1; }}
    #activity {{ width: 2fr; padding: 0 1; border-left: solid {BORDER}; }}
    #acthead, #actfoot {{ height: 1; }}
    #spark {{ height: 1fr; margin: 0; }}
    #spark > .sparkline--max-color {{ color: {PRIMARY}; }}
    #spark > .sparkline--min-color {{ color: {MUTED}; }}
    #keys {{ height: 1; color: {SECONDARY}; background: {SURFACE}; padding: 0 1; }}
    #search {{ height: 1; display: none; border: none; padding: 0 1; background: {SURFACE}; color: {TEXT}; }}
    #search.visible {{ display: block; }}
    #search:focus {{ background: {ACTIVE}; }}
    DataTable {{ background: {BG}; color: {TEXT}; scrollbar-color: {BORDER}; scrollbar-background: {BG}; scrollbar-size-vertical: 1; }}
    DataTable:focus {{ background-tint: {TEXT} 0%; }}
    DataTable > .datatable--header {{ background: {BG}; color: {SECONDARY}; text-style: none; }}
    DataTable:focus > .datatable--header {{ background-tint: {TEXT} 0%; }}
    DataTable > .datatable--cursor {{ background: {ACTIVE} 55%; color: {TEXT}; text-style: none; }}
    DataTable:focus > .datatable--cursor {{ background: {ACTIVE}; color: {TEXT}; text-style: bold; }}
    DataTable > .datatable--hover {{ background: {SURFACE}; }}
    DataTable > .datatable--fixed {{ background: {BG}; color: {SECONDARY}; }}
    """
    BINDINGS = [
        Binding("q", "quit", "quit"),
        Binding("ctrl+c", "quit", "quit", priority=True, show=False),
        Binding("enter", "open", "open"),  # tables open via RowSelected; this covers the detail pane
        Binding("escape", "back", "back", priority=True),
        Binding("slash", "search", "search"),
        Binding("space", "toggle_play", "play/pause"),
        Binding("left", "seek(-60)", "seek -60s"),
        Binding("right", "seek(60)", "seek +60s"),
        Binding("bracketleft", "speed(-1)", "slower"),
        Binding("bracketright", "speed(1)", "faster"),
        Binding("end", "follow", "follow latest", priority=True),
        Binding("home", "first_row", "first row", priority=True),
        Binding("tab", "cycle_pane(1)", "next pane", priority=True),
        Binding("shift+tab", "cycle_pane(-1)", "previous pane", priority=True),
        Binding("1", "screen('feed')", "feed"),
        Binding("2", "screen('radar')", "radar"),
        Binding("3", "screen('traders')", "traders"),
        Binding("4", "screen('signals')", "signals"),
        Binding("t", "tpreset('top')", "top traders"),
        Binding("m", "tpreset('smart')", "smart traders"),
        Binding("n", "tpreset('snipers')", "snipers"),
        Binding("b", "tpreset('bots')", "bots"),
        Binding("u", "preset('under_radar')", "under radar"),
        Binding("g", "preset('graduating')", "graduating"),
        Binding("s", "preset('smart_rotators')", "smart rotators"),
        Binding("a", "preset('all')", "all"),
        Binding("r", "refresh_coin", "refresh context"),
    ]

    def __init__(self, client: ApiClient, poll_s: float = 2.0, **kw: Any):
        super().__init__(**kw)
        self.client = client
        self.poll_s = poll_s
        self.session: dict[str, Any] | None = None
        self.status_doc: dict[str, Any] | None = None
        self.cursor: str | None = None
        self.last_clock: int | None = None
        self.last_clock_wall: float = 0.0
        self.events: dict[int, dict[str, Any]] = {}  # id -> event (all rows currently in the feed)
        self.fresh_ids: set[int] = set()
        self.follow = True
        self.unseen = 0  # rows that arrived below the fold while the user was reading history
        self.selected_pair: tuple[str, str] | None = None
        self.detail_mode = "summary"
        self.edge_doc: dict[str, Any] | None = None
        self.filter_text = ""
        self.api_error: str | None = None
        self.error_count = 0
        self.hot_prev: dict[str, int] = {}
        self.hot_prev_at: float | None = None
        self.graph_doc: dict[str, Any] | None = None
        self.poll_n = 0
        self.last_events_kind = "history"
        self.spark_values: list[float] = []
        self._pending_poll = False
        self._sym_w = 16
        self._columns_wide: bool | None = None
        self._feed_keys: list[str] = []
        self.screen_mode = "feed"  # feed | radar
        self.radar_preset = "under_radar"
        self.radar_doc: dict[str, Any] | None = None
        self.radar_rows: list[dict[str, Any]] = []
        self._radar_cells: dict[str, list[str]] = {}  # address -> plain cell values currently shown
        self.coin_doc: dict[str, Any] | None = None
        self.coin_addr: str | None = None
        self.traders_preset = "top"  # top | smart | snipers | bots
        self.traders_doc: dict[str, Any] | None = None
        self.traders_rows: list[dict[str, Any]] = []
        self._traders_cells: dict[str, list[str]] = {}
        self.wallet_doc: dict[str, Any] | None = None
        self.wallet_addr: str | None = None
        # stage 7: SIGNALS screen (alerts + paper ledger + track record) and the live event stream
        self.signal_alerts: dict[str, dict[str, Any]] = {}  # token:clock_ts -> alert row (journal + stream)
        self.signal_rows: list[dict[str, Any]] = []
        self._signal_cells: dict[str, list[str]] = {}
        self.paper_doc: dict[str, Any] | None = None
        self.track_doc: dict[str, Any] | None = None
        self.alerts_doc: dict[str, Any] | None = None
        self.stream: Any = None  # StreamReader in live mode
        self.live_status = "off"  # off | connecting | live | reconnecting | unavailable
        self.live_detail: str | None = None
        self.live_lag_ms: float | None = None  # engine head -> last event (session.lag_ms), the header's small number
        self.live_rows: dict[str, dict[str, Any]] = {}  # radar_delta rows by address
        self.live_top: list[str] = []
        self.live_deltas = 0
        self._live_radar_at = 0.0
        self._stream_queue: list[dict[str, Any]] = []  # history rows still to be added (streamed in over STREAM_S)
        self._stream_total = 0
        self._stream_done = 0
        self._stream_started = 0.0
        self._stream_timer = None

    # ---- layout ----
    def compose(self) -> ComposeResult:
        with Horizontal(id="header"):
            yield Static(id="mark")
            yield Static(id="brand")
        yield Static(id="alert")
        yield Static(id="rule1")
        with Horizontal(id="main"):
            with Vertical(id="feedwrap"):
                yield Static(id="feedhead")
                yield Input(placeholder="filter by symbol or address · Enter keeps it · Esc clears", id="search")
                yield Static(id="empty")
                yield DataTable(id="feed", cursor_type="row", zebra_stripes=False, show_row_labels=False)
                yield DataTable(id="radar", cursor_type="row", zebra_stripes=False, show_row_labels=False)
                yield DataTable(id="traders", cursor_type="row", zebra_stripes=False, show_row_labels=False)
                yield DataTable(id="signals", cursor_type="row", zebra_stripes=False, show_row_labels=False)
            with Vertical(id="detailwrap"):
                yield Static(id="detailhead")
                yield VerticalScroll(Static(id="detailbody"), id="detail")
        with Horizontal(id="bottom"):
            yield Static(id="hot")
            with Vertical(id="activity"):
                yield Static(id="acthead")
                yield Sparkline([], id="spark", summary_function=max)
                yield Static(id="actfoot")
        yield Static(id="keys")

    def on_mount(self) -> None:
        self.query_one("#search", Input).can_focus = False
        self.query_one("#detail", VerticalScroll).can_focus = True
        self._apply_size()
        self._setup_columns(self.query_one("#feed", DataTable))
        self._setup_radar_columns(self.query_one("#radar", DataTable))
        self._setup_traders_columns(self.query_one("#traders", DataTable))
        self._setup_signals_columns(self.query_one("#signals", DataTable))
        self.query_one("#feed", DataTable).focus()
        self.set_interval(5.0, self.radar_tick)
        self.set_interval(6.0, self.traders_tick)
        self.set_interval(5.0, self.signals_tick)
        self.render_brand()
        self.render_status()
        self.render_feedhead()
        self.render_detail()
        self.render_keys()
        self.render_hot()
        self.poll_tick()
        self.set_interval(self.poll_s, self.poll_tick)
        self.set_interval(1.0, self.render_status)

    # ---- size variants ----
    def _wide(self) -> bool:
        return self.size.width >= WIDE_COLS

    def _narrow(self) -> bool:
        return not self._wide()

    def _header_variant(self) -> str:
        w, h = self.size.width, self.size.height
        if h < MARK_ROWS or w < MARK_COLS:
            return "tiny"
        return "wide" if w >= WIDE_COLS else "compact"

    def _colorless(self) -> bool:
        """No per-cell colours at all (Rich colour system off): the half-block mark would be a plain rectangle."""
        return getattr(self.console, "color_system", None) is None

    def _apply_size(self) -> None:
        w, h = self.size.width, self.size.height
        variant = self._header_variant()
        header = self.query_one("#header")
        header.set_class(variant == "tiny", "tiny")
        header.set_class(variant == "compact", "compact")
        wide = self._wide()
        self.query_one("#feedwrap").set_class(wide, "wide")
        self.query_one("#detailwrap").set_class(wide, "wide")
        bottom = self.query_one("#bottom")
        bottom.set_class(h < 34, "hidden")
        bottom.styles.height = 11 if h >= 44 else 9

    def _feed_pane_width(self) -> int:
        w = self.size.width
        return (w * 3) // 5 if self._wide() else (w * 5) // 8

    def _feed_columns(self) -> list[tuple[str, str, int]]:
        """(label, key, width) for the feed at the current width; the two symbol columns share what is left.
        Below the documented 120-column minimum the wallet column goes first (it is in the detail pane anyway)."""
        wide = self._wide()
        if wide:
            fixed = [("", "mark", 1), ("SOLD UTC", "sold_t", 9), ("BOUGHT UTC", "time", 10), ("WALLET", "wallet", 11)]
            tail = [("", "arrow", 1), ("GAP", "gap", 8), ("GRADE", "grade", 9)]
        else:
            fixed = [("", "mark", 1), ("TIME UTC", "time", 9), ("WALLET", "wallet", 11)]
            tail = [("", "arrow", 1), ("GAP", "gap", 6), ("GRADE", "grade", 6)]
        inner = self._feed_pane_width() - 2  # vertical scrollbar

        def sym_width() -> int:
            used = sum(w for _, _, w in fixed + tail) + 2 * (len(fixed) + len(tail) + 2)
            return (inner - used) // 2

        if sym_width() < 8:
            fixed = [c for c in fixed if c[1] != "wallet"]
        sym_w = max(6, min(18, sym_width()))
        self._sym_w = sym_w
        return fixed + [("SOLD", "sold", sym_w)] + tail[:1] + [("BOUGHT", "bought", sym_w)] + tail[1:]

    def _setup_columns(self, table: DataTable) -> None:
        for label, key, w in self._feed_columns():
            table.add_column(label, key=key, width=w)
        self._columns_wide = self._wide()

    def _radar_columns(self) -> list[tuple[str, str, int]]:
        wide = self._wide()
        span = dur(int((self.session or {}).get("span_s") or 1800))
        head = [("#", "rank", 2), ("SCORE", "score", 5)]
        if wide:
            tail = [("IN 10M", "inflow", 6), (f"IN {span.upper()}", "wallets", max(6, len(span) + 3)), ("ACC", "acc", 6), ("SRC", "src", 3), ("AGE", "age", 7), ("STAGE", "stage", 10), ("5M", "chg5", 6), ("1H", "chg1h", 7), ("X 1H", "x", 4)]
        else:
            tail = [("IN 10M", "inflow", 6), ("ACC", "acc", 6), ("SRC", "src", 3), ("AGE", "age", 7), ("STAGE", "stage", 9)]
        ncols = len(head) + len(tail) + 1
        inner = self._feed_pane_width() - 2
        used = sum(w for _, _, w in head + tail) + 2 * ncols
        coin_w = max(10, min(18, inner - used))
        return head + [("COIN", "coin", coin_w)] + tail

    def _setup_radar_columns(self, table: DataTable) -> None:
        for label, key, w in self._radar_columns():
            table.add_column(label, key=key, width=w)

    def _traders_columns(self) -> list[tuple[str, str, int]]:
        """Leaderboard columns; wide windows add tags and the last-trade time. Numbers are right-justified."""
        wide = self._wide()
        if wide:
            head = [("#", "rank", 3), ("WALLET", "wallet", 11), ("TRADES", "trades", 6), ("COINS", "coins", 5), ("WIN", "win", 5), ("PNL ETH", "pnl", 9), ("ROI", "roi", 6), ("HOLD", "hold", 7), ("QUAL", "quality", 5), ("LAST UTC", "last", 8)]
        else:
            head = [("#", "rank", 3), ("WALLET", "wallet", 11), ("TRADES", "trades", 6), ("WIN", "win", 5), ("PNL ETH", "pnl", 9), ("ROI", "roi", 6), ("QUAL", "quality", 5)]
        ncols = len(head) + 1
        inner = self._feed_pane_width() - 2
        used = sum(w for _, _, w in head) + 2 * ncols
        tags_w = max(8, min(28, inner - used))
        return head + [("TAGS", "tags", tags_w)]

    def _setup_traders_columns(self, table: DataTable) -> None:
        for label, key, w in self._traders_columns():
            table.add_column(label, key=key, width=w)

    def on_resize(self, event: events.Resize) -> None:
        # our handler runs before App._on_resize stores the new size: finish on the next loop iteration
        self.call_later(self._after_resize)

    def _after_resize(self) -> None:
        try:
            self._apply_size()
        except NoMatches:
            return
        self.render_brand()
        self.render_status()
        self.render_feedhead()
        self.render_activity()
        self.render_hot()
        self.render_keys()
        if self._columns_wide is not None and self._columns_wide != self._wide():
            table = self.query_one("#feed", DataTable)
            sel, was_following = self._selected_event(), self.follow
            table.clear(columns=True)
            self._setup_columns(table)
            self.rebuild_rows(sel, was_following)
            rt = self.query_one("#radar", DataTable)
            rt.clear(columns=True)
            self._setup_radar_columns(rt)
            self._radar_cells.clear()
            rows, self.radar_rows = self.radar_rows, []
            self._fill_radar(rows)
            tt = self.query_one("#traders", DataTable)
            tt.clear(columns=True)
            self._setup_traders_columns(tt)
            self._traders_cells.clear()
            trows, self.traders_rows = self.traders_rows, []
            self._fill_traders(trows)
            sg = self.query_one("#signals", DataTable)
            sg.clear(columns=True)
            self._setup_signals_columns(sg)
            self._signal_cells.clear()
            self.signal_rows = []
            self._fill_signals()

    # ---- polling (thread worker -> main thread) ----
    def poll_tick(self) -> None:
        if self._pending_poll:
            return
        self._pending_poll = True
        self.poll_n += 1
        self.fetch(self.poll_n % 3 == 1)

    @work(thread=True, exclusive=True, group="poll")
    def fetch(self, with_graph: bool) -> None:
        out: dict[str, Any] = {}
        try:
            sess = self.client.session()
            out["session"] = sess
            if with_graph or self.status_doc is None:
                out["status"] = self.client.status()
            clock = sess.get("clock_ts")
            w = int(sess.get("window_s") or 1800)
            span = int(sess.get("span_s") or 1800)
            seek = self._is_seek(sess)
            after = None if seek or self.cursor is None else self.cursor
            ev = self.client.events(w, clock, after, limit=2000, backfill_s=span)
            pages = [ev]
            kind0 = ev.get("kind")
            while ev.get("has_more") and len(pages) < 25:  # to the end: an unfinished history must not resurface as "new"
                ev = self.client.events(w, clock, ev["next_cursor"], limit=2000, backfill_s=span)
                if kind0 == "history":
                    ev["kind"] = "history"
                pages.append(ev)
            out["events"] = pages
            out["seek"] = seek
            if (with_graph or self.graph_doc is None) and clock is not None:
                out["graph"] = self.client.graph(w, clock - span, clock, 1, 10)
            elif clock is None:
                out["graph"] = {"edges": [], "nodes": [], "waiting": True}
            self._deliver(self.apply_update, out)
        except ApiError as e:
            self._deliver(self.apply_error, str(e))
        except Exception as e:  # noqa: BLE001
            self._deliver(self.apply_error, f"{type(e).__name__}: {e}")

    def _deliver(self, fn, *args: Any) -> None:
        """Hand a worker result to the UI thread; results that land after the app closed are dropped."""
        if not self.is_running:
            return
        try:
            self.call_from_thread(fn, *args)
        except (NoMatches, RuntimeError):
            pass

    def _is_seek(self, sess: dict[str, Any]) -> bool:
        """The clock moved further than playback could explain since the last poll."""
        clock = sess.get("clock_ts")
        if clock is None or self.last_clock is None or self.session is None:
            return False
        if sess.get("id") != self.session.get("id"):
            return True
        elapsed = time.time() - self.last_clock_wall
        speed = float(sess.get("speed") or 1)
        expected_max = self.last_clock + (elapsed + 2 * self.poll_s) * speed + 5
        return clock < self.last_clock - 1 or clock > expected_max

    def apply_update(self, out: dict[str, Any]) -> None:
        try:
            self._apply_update(out)
        except NoMatches:
            pass  # app is closing

    def _at_tail(self, table: DataTable) -> bool:
        """Cursor on the last row and the view scrolled to the bottom (a wheel scroll into history breaks this)."""
        if table.row_count == 0:
            return True
        return table.cursor_row >= table.row_count - 1 and table.scroll_y >= table.max_scroll_y - 0.5

    def _apply_update(self, out: dict[str, Any]) -> None:
        self._pending_poll = False
        was_error = self.api_error is not None
        self.api_error = None
        self.error_count = 0
        if "status" in out:
            self.status_doc = out["status"]
        self.session = out["session"]
        self.last_clock = self.session.get("clock_ts")
        self.last_clock_wall = time.time()
        if self.session.get("mode") == "live" and self.stream is None and hasattr(self.client, "open_stream"):
            self._start_live_stream()
        table = self.query_one("#feed", DataTable)
        pages = out.get("events", [])
        if out.get("seek") or self.cursor is None:
            # history rebuild: one clear per seek, rows are not "fresh"
            table.clear()
            self.events.clear()
            self.fresh_ids.clear()
            self._feed_keys.clear()
            self._stream_queue.clear()
            self.follow = True
            self.unseen = 0
        else:
            self.fresh_ids.clear()
            for eid in list(self.events):
                if self.events[eid].get("_fresh"):
                    self.events[eid]["_fresh"] = False
                    self._refresh_mark(table, eid)
            if self.follow and not self._at_tail(table):
                self.follow = False  # the reader scrolled into history without moving the cursor
        new_ids: list[int] = []
        history_rows: list[dict[str, Any]] = []
        added_visible = 0
        for page in pages:
            for e in page.get("events", []):
                if e["id"] in self.events:
                    continue
                e["_fresh"] = page.get("kind") == "new"
                self.events[e["id"]] = e
                if not e["_fresh"]:
                    history_rows.append(e)  # streamed in below, in time order, like a tape catching up
                elif self._matches_filter(e):
                    self._add_row(table, e)
                    added_visible += 1
                if e["_fresh"]:
                    new_ids.append(e["id"])
            if page.get("next_cursor"):
                self.cursor = page["next_cursor"]
        if history_rows:
            self._start_stream(history_rows)
        self.fresh_ids = set(new_ids)
        self.last_events_kind = "new" if new_ids else "history"
        if self.follow and table.row_count:
            table.move_cursor(row=table.row_count - 1, scroll=True)
        elif added_visible:
            self.unseen += added_visible
        if "graph" in out:
            self.graph_doc = out["graph"]
            self.render_hot()
        self.render_status()
        self.render_feedhead()
        self.render_activity()
        self._render_empty()
        if self.detail_mode == "summary":
            self.render_detail()
        if was_error or self.poll_n <= 1:
            self.render_keys()

    def _render_empty(self) -> None:
        table = self.query_one("#feed", DataTable)
        empty = self.query_one("#empty", Static)
        show = table.row_count == 0 and self.screen_mode == "feed" and not self._stream_queue
        empty.set_class(show, "visible")
        if not show:
            empty.update("")
            return
        s = self.session or {}
        clock = s.get("clock_ts")
        t = Text()
        if self._colorless() and self.size.height >= 40 and self.session is None:
            t.append("\n".join(brand.mark_plain()) + "\n\n", style=TEXT)
        if self.session is None:
            t.append("CONNECTING TO API · nothing loaded yet", style=SECONDARY)
        elif self.filter_text:
            t.append(f"NO ROWS MATCH '{self.filter_text}' · Esc clears the filter", style=SECONDARY)
        elif clock:
            span = int(s.get("span_s") or 1800)
            t.append(f"NO OBSERVED SEQUENCES {utc(clock - span)}–{utc(clock)} UTC · ", style=SECONDARY)
            t.append("space plays the clock · ←/→ seek 60 s" if s.get("controls") else "waiting for the next block", style=TEXT)
        else:
            t.append("NO CLOCK YET · waiting for the first block", style=SECONDARY)
        empty.update(t)

    def apply_error(self, msg: str) -> None:
        self._pending_poll = False
        first = self.api_error is None
        self.api_error = msg
        self.error_count += 1
        try:
            self.render_status()
            if first:
                self.render_keys()
                self._render_empty()
        except NoMatches:
            pass  # app is closing

    # ---- streaming a history load into the table (no clear, rows fly in over ~2.5 s) ----
    STREAM_S = 3.0  # cap: a history load flies in over up to this many seconds, whatever the tick rate

    def _start_stream(self, rows: list[dict[str, Any]]) -> None:
        self._stream_queue.extend(sorted(rows, key=lambda x: (x["buy_ts"], x["id"])))
        self._stream_total = len(self._stream_queue)
        self._stream_started = time.time()
        self._stream_done = 0
        if self._stream_timer is None:
            self._stream_timer = self.set_interval(1 / 25, self._stream_tick)

    def _stream_tick(self) -> None:
        try:
            table = self.query_one("#feed", DataTable)
        except NoMatches:  # app is closing: stop the timer quietly
            if self._stream_timer is not None:
                self._stream_timer.stop()
                self._stream_timer = None
            return
        if not self._stream_queue:
            if self._stream_timer is not None:
                self._stream_timer.stop()
                self._stream_timer = None
            self.render_feedhead()
            self.render_activity()
            self._render_empty()
            return
        dur_s = min(self.STREAM_S, 0.3 + self._stream_total / 2500)  # tiny loads are instant, big ones take ~3 s
        t = min(1.0, (time.time() - self._stream_started) / dur_s)
        eased = 1 - (1 - t) ** 3
        target = int(self._stream_total * eased) if t < 1 else self._stream_total
        n = max(0, target - self._stream_done)
        for e in self._stream_queue[:n]:
            if self._matches_filter(e):
                self._add_row(table, e)
        del self._stream_queue[:n]
        self._stream_done += n
        if table.row_count:
            self.query_one("#empty", Static).set_class(False, "visible")
        if self.follow and table.row_count:
            table.move_cursor(row=table.row_count - 1, scroll=True)
        self.render_feedhead()
        if n and self._stream_done % 400 < n:
            self.render_activity(streaming=True)

    # ---- radar screen ----
    def radar_tick(self) -> None:
        if self.screen_mode == "radar":
            self.fetch_radar()

    @work(thread=True, exclusive=True, group="radar")
    def fetch_radar(self) -> None:
        try:
            doc = self.client.radar({"preset": self.radar_preset, "limit": 40})
            self._deliver(self._radar_loaded, doc)
        except ApiError as e:
            self._deliver(self.apply_error, str(e))

    def _radar_cell_values(self, i: int, r: dict[str, Any], prev_inflow: dict[str, int]) -> dict[str, tuple[str, str]]:
        """key -> (plain value, style) for one radar row."""
        bump = r["inflow_10m"] > prev_inflow.get(r["address"], r["inflow_10m"])
        age = dur(r["age_s"]) if r.get("age_s") is not None else "?"
        stage = f"curve {int(r['progress'] * 100)}%" if r["stage"] == "curve" and r.get("progress") is not None else r["stage"]
        chg5 = f"{r['chg_5m']:+.0f}%" if r.get("chg_5m") is not None else "n/a"
        chg1 = f"{r['chg_1h']:+.0f}%" if r.get("chg_1h") is not None else "n/a"
        x = "n/a" if r.get("mentions_1h") is None else str(r["mentions_1h"])
        coin_w = next((w for _, k, w in self._radar_columns() if k == "coin"), 16)
        return {
            "rank": (str(i), MUTED),
            "score": (f"{r['score']:.0f}", f"bold {TEXT}"),
            "coin": (fit(r["symbol"], coin_w), f"bold {TEXT}"),
            "inflow": (str(r["inflow_10m"]), f"bold {PRIMARY}" if bump else TEXT),
            "wallets": (str(r.get("wallets_range", "")), SECONDARY),
            "acc": (f"×{r['accel']:.1f}", SECONDARY),
            "src": (str(r["breadth"]), SECONDARY),
            "age": (age, SECONDARY),
            "stage": (stage, SECONDARY),
            "chg5": (chg5, TEXT if (r.get("chg_5m") or 0) >= 0 else SECONDARY),
            "chg1h": (chg1, SECONDARY),
            "x": (x, MUTED if x == "n/a" else SECONDARY),
        }

    def _radar_loaded(self, doc: dict[str, Any]) -> None:
        self.radar_doc = doc
        if self.live_status == "live" and self.live_deltas and self.live_top:
            # the stream feeds the table every block; the poll only refreshes total / presets / context
            self._live_radar_at = 0.0
            rows = [self.live_rows[a] for a in self.live_top if a in self.live_rows]
            self._fill_radar(filter_live_rows(rows, self.radar_preset)[:40])
        else:
            self._fill_radar(doc.get("rows", []))
        self.render_feedhead()
        if self.screen_mode == "radar" and self.detail_mode == "summary":
            self.render_detail()

    def _fill_radar(self, rows: list[dict[str, Any]]) -> None:
        table = self.query_one("#radar", DataTable)
        cols = self._radar_columns()
        prev_inflow = {r["address"]: r["inflow_10m"] for r in self.radar_rows}
        same_order = [r["address"] for r in rows] == [r["address"] for r in self.radar_rows] and table.row_count == len(rows)
        cur = self._selected_radar_row()
        cur_addr = cur["address"] if cur else None
        self.radar_rows = rows
        if same_order:
            # in-place: only cells whose value changed are touched, cursor and scroll stay where they are
            for i, r in enumerate(rows, 1):
                vals = self._radar_cell_values(i, r, prev_inflow)
                shown = self._radar_cells.get(r["address"], [])
                new_plain = []
                for ci, (_, key, w) in enumerate(cols):
                    plain, style = vals[key]
                    new_plain.append(plain + "|" + style)
                    if ci < len(shown) and shown[ci] == new_plain[-1]:
                        continue
                    table.update_cell(r["address"], key, Text(plain, style=style, justify="left" if key in ("coin", "stage") else "right"), update_width=False)
                self._radar_cells[r["address"]] = new_plain
            return
        scroll_y = table.scroll_y
        table.clear()
        self._radar_cells.clear()
        for i, r in enumerate(rows, 1):
            vals = self._radar_cell_values(i, r, prev_inflow)
            cells = []
            plain_row = []
            for _, key, w in cols:
                plain, style = vals[key]
                plain_row.append(plain + "|" + style)
                cells.append(Text(plain, style=style, justify="left" if key in ("coin", "stage") else "right"))
            table.add_row(*cells, key=r["address"])
            self._radar_cells[r["address"]] = plain_row
        if rows:
            idx = next((i for i, r in enumerate(rows) if r["address"] == cur_addr), 0)

            def _restore() -> None:
                try:
                    table.scroll_y = scroll_y
                    table.move_cursor(row=idx, scroll=True)
                except NoMatches:
                    pass

            self.call_after_refresh(_restore)

    def _selected_radar_row(self) -> dict[str, Any] | None:
        table = self.query_one("#radar", DataTable)
        if table.row_count == 0:
            return None
        try:
            row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
            return next((r for r in self.radar_rows if r["address"] == row_key.value), None)
        except Exception:  # noqa: BLE001
            return None

    # ---- signals screen (stage 7): alerts with outcomes, the paper ledger, the track record ----
    def _signals_columns(self) -> list[tuple[str, str, int]]:
        wide = self._wide()
        head = [("", "mark", 1), ("CLOCK", "time", 8), ("RULE", "rule", 5)]
        tail = [("P2X", "p", 4), ("SIZE", "size", 6), ("+30M", "o30", 9), ("+60M", "o60", 9)]
        if wide:
            tail = [("P2X", "p", 4), ("EV", "ev", 8), ("SIZE", "size", 6), ("IN", "inflow", 3), ("+30M", "o30", 9), ("+60M", "o60", 9), ("PAPER", "paper", 12)]
        ncols = len(head) + len(tail) + 1
        inner = self._feed_pane_width() - 2
        used = sum(w for _, _, w in head + tail) + 2 * ncols
        coin_w = max(8, min(18, inner - used))
        return head + [("COIN", "coin", coin_w)] + tail

    def _setup_signals_columns(self, table: DataTable) -> None:
        for label, key, w in self._signals_columns():
            table.add_column(label, key=key, width=w)

    def signals_tick(self) -> None:
        if self.screen_mode == "signals":
            self.fetch_signals()

    @work(thread=True, exclusive=True, group="signals")
    def fetch_signals(self) -> None:
        try:
            out = {"alerts": self.client.alerts(), "paper": self.client.paper()}
            try:
                out["track"] = self.client.track_record()
            except ApiError:
                out["track"] = None
            self._deliver(self._signals_loaded, out)
        except ApiError as e:
            self._deliver(self.apply_error, str(e))

    def _signals_loaded(self, out: dict[str, Any]) -> None:
        self.alerts_doc = out.get("alerts")
        for a in (self.alerts_doc or {}).get("alerts", []):
            key = f"{a['token']}:{a['clock_ts']}"
            prev = self.signal_alerts.get(key)
            if prev:  # the journal row wins on identity; outcomes settled on the stream survive a stale poll
                a = {**a, "outcome_30m": a.get("outcome_30m") if a.get("outcome_30m") is not None else prev.get("outcome_30m"), "outcome_60m": a.get("outcome_60m") if a.get("outcome_60m") is not None else prev.get("outcome_60m")}
            self.signal_alerts[key] = a
        if out.get("paper") is not None:
            self.paper_doc = out["paper"]
        if out.get("track") is not None:
            self.track_doc = out["track"]
        self._fill_signals()
        self.render_feedhead()
        if self.screen_mode == "signals" and self.detail_mode == "summary":
            self.render_detail()

    def _paper_for(self, token: str) -> dict[str, Any] | None:
        d = self.paper_doc or {}
        pos = (d.get("positions") or {})
        for p in pos.get("open", []):
            if p["token"] == token:
                return p
        for p in pos.get("closed", []):
            if p["token"] == token:
                return p
        return None

    def _signal_cell_values(self, a: dict[str, Any]) -> dict[str, tuple[str, str]]:
        d = a.get("detail") or {}
        enter = a.get("rule") == "edge_enter"
        clock = (self.session or {}).get("clock_ts")
        coin_w = next((w for _, k, w in self._signals_columns() if k == "coin"), 12)
        p = d.get("p_2x_30m")
        ev = d.get("ev_per_trade_quote")
        size = d.get("size_quote")
        o30, o60 = outcome_s(a.get("outcome_30m"), int(a["clock_ts"]) + 1800, clock), outcome_s(a.get("outcome_60m"), int(a["clock_ts"]) + 3600, clock)
        pp = self._paper_for(a["token"]) if enter else None
        if pp is None:
            paper = ""
        elif pp.get("status") == "open":
            paper = f"open {pct_s(pp.get('ret'), 0)}"
        else:
            paper = f"{(pp.get('exit_reason') or '?')[:5]} {eth_s(pp.get('pnl_quote'))}"

        def out_style(v: Any, text: str) -> str:
            if v is None:
                return MUTED if text in ("n/a", "no price", "settling") else SECONDARY
            return TEXT if v > 0 else SECONDARY

        return {
            "mark": ("▌" if a.get("_fresh") else " ", PRIMARY),
            "time": (utc(a.get("clock_ts")), SECONDARY),
            "rule": ("ENTER" if enter else "radar", f"bold {PRIMARY}" if enter else MUTED),
            "coin": (fit(a.get("symbol") or "?", coin_w), f"bold {TEXT}"),
            "p": (f"{p * 100:.0f}%" if p is not None else "n/a", TEXT if p is not None else MUTED),
            "ev": (eth_s(ev) if ev is not None else "n/a", SECONDARY if ev is not None else MUTED),
            "size": (f"{size:.4f}" if size is not None else "n/a", SECONDARY if size is not None else MUTED),
            "inflow": (str(a.get("inflow", "")), SECONDARY),
            "o30": (o30, out_style(a.get("outcome_30m"), o30)),
            "o60": (o60, out_style(a.get("outcome_60m"), o60)),
            "paper": (paper, TEXT if paper.startswith("open") or (pp or {}).get("pnl_quote", 0) is not None and (pp or {}).get("pnl_quote", 0) > 0 else SECONDARY),
        }

    def _fill_signals(self) -> None:
        table = self.query_one("#signals", DataTable)
        cols = self._signals_columns()
        rows = sorted(self.signal_alerts.values(), key=lambda a: (-int(a["clock_ts"]), -int(a.get("id") or 0)))[:200]
        same_order = [f"{a['token']}:{a['clock_ts']}" for a in rows] == [f"{a['token']}:{a['clock_ts']}" for a in self.signal_rows] and table.row_count == len(rows)
        cur = self._selected_signal_row()
        cur_key = f"{cur['token']}:{cur['clock_ts']}" if cur else None
        self.signal_rows = rows
        left = ("coin", "rule", "paper", "mark")
        if same_order:
            for a in rows:
                key = f"{a['token']}:{a['clock_ts']}"
                vals = self._signal_cell_values(a)
                shown = self._signal_cells.get(key, [])
                new_plain = []
                for ci, (_, k, w) in enumerate(cols):
                    plain, style = vals[k]
                    new_plain.append(plain + "|" + style)
                    if ci < len(shown) and shown[ci] == new_plain[-1]:
                        continue
                    table.update_cell(key, k, Text(plain, style=style, justify="left" if k in left else "right"), update_width=False)
                self._signal_cells[key] = new_plain
            return
        scroll_y = table.scroll_y
        table.clear()
        self._signal_cells.clear()
        for a in rows:
            key = f"{a['token']}:{a['clock_ts']}"
            vals = self._signal_cell_values(a)
            cells, plain_row = [], []
            for _, k, w in cols:
                plain, style = vals[k]
                plain_row.append(plain + "|" + style)
                cells.append(Text(plain, style=style, justify="left" if k in left else "right"))
            table.add_row(*cells, key=key)
            self._signal_cells[key] = plain_row
        if rows:
            idx = next((i for i, a in enumerate(rows) if f"{a['token']}:{a['clock_ts']}" == cur_key), 0)

            def _restore() -> None:
                try:
                    table.scroll_y = scroll_y
                    table.move_cursor(row=idx, scroll=True)
                except NoMatches:
                    pass

            self.call_after_refresh(_restore)

    def _selected_signal_row(self) -> dict[str, Any] | None:
        try:
            table = self.query_one("#signals", DataTable)
        except NoMatches:
            return None
        if table.row_count == 0:
            return None
        try:
            row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
            return self.signal_alerts.get(str(row_key.value))
        except Exception:  # noqa: BLE001
            return None

    def render_signals_summary(self, t: Text) -> None:
        """Right pane of SIGNALS: the selected alert (p, EV, size, plan, outcome, its paper position), then the ledger
        statistics with the CI, open positions with the live mark, closed positions, and the track record."""
        narrow = self._narrow()
        clock = (self.session or {}).get("clock_ts")
        a = self._selected_signal_row()
        d = self.paper_doc or {}
        st = d.get("stats") or {}
        pos = d.get("positions") or {}

        def fact(label: str, value: str, style: str = TEXT) -> None:
            t.append(f"{label:<9}", style=SECONDARY)
            t.append(value + "\n", style=style)

        if a is not None:
            det = a.get("detail") or {}
            enter = a.get("rule") == "edge_enter"
            t.append(f"{a.get('symbol') or '?'}", style=f"bold {TEXT}")
            t.append(f"  {'ENTER' if enter else 'under-radar top-5'} · {utc(a.get('clock_ts'))} UTC\n", style=f"bold {PRIMARY}" if enter else SECONDARY)
            t.append(f"{a['token']}\n", style=MUTED)
            if enter:
                p = det.get("p_2x_30m")
                fact("P(2×)", f"{p * 100:.0f}% in 30 min" if p is not None else "n/a", TEXT)
                fact("EV", f"{eth_s(det.get('ev_per_trade_quote'))} ETH per trade at size {det.get('size_quote') if det.get('size_quote') is not None else 'n/a'}", SECONDARY)
                for line in (det.get("exit_plan") or [])[: 4 if narrow else 6]:
                    fact("PLAN", line, SECONDARY)
                for r in (det.get("reasons") or [])[:3]:
                    t.append(f"  · {r}\n", style=MUTED)
            else:
                fact("INFLOW", f"{a.get('inflow')} wallets in 10 min · score {float(a.get('score') or 0):.0f}", SECONDARY)
            fact("+30M", outcome_s(a.get("outcome_30m"), int(a["clock_ts"]) + 1800, clock), TEXT if (a.get("outcome_30m") or 0) > 0 else SECONDARY)
            fact("+60M", outcome_s(a.get("outcome_60m"), int(a["clock_ts"]) + 3600, clock), TEXT if (a.get("outcome_60m") or 0) > 0 else SECONDARY)
            pp = self._paper_for(a["token"])
            if pp:
                if pp.get("status") == "open":
                    fact("PAPER", f"open · mark {pct_s(pp.get('ret'))} · unreal {eth_s(pp.get('unrealized_quote'))} ETH · peak {pct_s(pp.get('peak_ret'), 0)}", TEXT)
                else:
                    fact("PAPER", f"closed {pp.get('exit_reason')} · {eth_s(pp.get('pnl_quote'))} ETH" + (f" · ${pp['pnl_usd']:+.2f}" if pp.get("pnl_usd") is not None else "") + f" · hold {dur(pp.get('hold_s'))}", TEXT if (pp.get("pnl_quote") or 0) > 0 else SECONDARY)
            t.append("\n")
        else:
            t.append("PAPER LEDGER · simulated\n\n", style=SECONDARY)
        t.append("PAPER STATS · simulated fills, no order sent\n", style=SECONDARY)
        if not st:
            t.append("  loading…\n", style=MUTED)
        else:
            hr = f"{st['hit_rate'] * 100:.0f}% ({st['wins']} of {st['trades']})" if st.get("hit_rate") is not None else "n/a"
            ex = f"{eth_s(st.get('expectancy_quote'))} ETH" + (f" ({pct_s(st.get('expectancy_pct'), 0)})" if st.get("expectancy_pct") is not None else "") if st.get("expectancy_quote") is not None else "n/a"
            ci = f"[{eth_s(st['ci95_quote'][0])}, {eth_s(st['ci95_quote'][1])}]" if st.get("ci95_quote") else "n/a (needs 2+ coins)"
            usd_ = f"${st['expectancy_usd']:+.2f} · {st.get('usd_known', 0)} rated" if st.get("expectancy_usd") is not None else "n/a"
            pf = st.get("profit_factor")
            pf_s = "n/a" if pf is None else ("∞" if pf == float("inf") else f"{pf:.2f}")
            fact("TRADES", f"{st.get('trades', 0)} closed · {st.get('coins', 0)} coins · {st.get('open', 0)} open · {st.get('skipped_by_risk', 0)} skipped by risk")
            fact("HIT", hr)
            fact("EXPECT.", f"{ex} · USD {usd_}")
            fact("95% CI", ci, SECONDARY)
            fact("PF · DD", f"{pf_s} · max drawdown {eth_s(st.get('max_drawdown_quote'))} ETH", SECONDARY)
            fact("TOTAL", f"{eth_s(st.get('total_quote'))} ETH · fees {st.get('fees_quote', 0):.4f}", TEXT if (st.get("total_quote") or 0) >= 0 else SECONDARY)
            if st.get("exits"):
                fact("EXITS", " · ".join(f"{k} {v}" for k, v in sorted(st["exits"].items(), key=lambda kv: -kv[1])), SECONDARY)
        opens = pos.get("open", [])
        t.append(f"\nOPEN · {len(opens)}\n", style=SECONDARY)
        for p in opens[: 6 if narrow else 10]:
            age = (clock - p["opened_ts"]) if clock else None
            t.append(f"  {utc(p['opened_ts'])} {fit(p['symbol'], 12):<12} {pct_s(p.get('ret')):>8} {eth_s(p.get('unrealized_quote')):>8}" + ("" if narrow else f"  peak {pct_s(p.get('peak_ret'), 0):>6} · {mmss(age)} · {'TP✓ trail' if p.get('tp_done') else 'TP'}") + "\n", style=TEXT if (p.get("ret") or 0) >= 0 else SECONDARY)
        if not opens:
            t.append("  none\n", style=MUTED)
        closed = pos.get("closed", [])
        t.append(f"\nCLOSED · {len(closed)}\n", style=SECONDARY)
        for p in closed[: 6 if narrow else 12]:
            t.append(f"  {utc(p['opened_ts'])} {fit(p['symbol'], 12):<12} {(p.get('exit_reason') or '?')[:14]:<14} {eth_s(p.get('pnl_quote')):>8}" + ("" if narrow else f"  {('$' + format(p['pnl_usd'], '+.2f')) if p.get('pnl_usd') is not None else 'USD n/a':>10} · {dur(p.get('hold_s'))}") + "\n", style=TEXT if (p.get("pnl_quote") or 0) > 0 else SECONDARY)
        if not closed:
            t.append("  none yet\n", style=MUTED)
        tr = self.track_doc or {}
        by_rule = (tr.get("alerts") or {}).get("by_rule") or {}
        t.append("\nTRACK RECORD\n", style=SECONDARY)
        for rule, x in by_rule.items():
            name = "ENTER" if rule == "edge_enter" else rule
            m30 = f"{x['median_30m']:+.0f}%" if x.get("median_30m") is not None else "n/a"
            m60 = f"{x['median_60m']:+.0f}%" if x.get("median_60m") is not None else "n/a"
            t.append(f"  {name}: {x['fired']} fired · +30 known {x['with_outcome_30m']} · up {x['up_30m']} · ≥2× {x['ge_2x_30m']} · median +30 {m30} · +60 {m60}\n", style=TEXT)
        if not by_rule:
            t.append("  no alerts in this mode yet\n", style=MUTED)
        pf_ = tr.get("perf")
        if pf_:
            t.append(f"  run: uptime {dur(int(pf_['uptime_s'] or 0))} · {pf_.get('blocks_processed') or 0:,} blocks · gaps {pf_.get('gaps_found')} · unfilled {pf_.get('gaps_unfilled_blocks')} · head→event p50 {pf_.get('latency_p50_ms')} ms · p95 {pf_.get('latency_p95_ms')} ms\n", style=SECONDARY)
        if tr.get("since_ts"):
            t.append(f"  counting from the engine start {utc(tr['since_ts'])} UTC\n", style=MUTED)
        t.append("\nEnter: coin card of the selected alert · ↑/↓ select · outcomes = price change on indexed trades, pnl = simulated fills", style=MUTED)

    # ---- live event stream (SSE) ----
    def _start_live_stream(self) -> None:
        self.live_status = "connecting"
        self.stream = self.client.open_stream(lambda ev: self._deliver(self._on_stream_event, ev), lambda s, d: self._deliver(self._on_stream_status, s, d), types=["session", "alert", "position", "radar_delta"])

    def _on_stream_status(self, status: str, detail: str | None) -> None:
        self.live_status = status
        self.live_detail = detail
        if status != "live":
            self.live_lag_ms = None
        try:
            self.render_status()
            self.render_feedhead()
        except NoMatches:
            pass

    def _on_stream_event(self, ev: dict[str, Any]) -> None:
        typ = ev.get("type")
        d = ev.get("data") or {}
        try:
            if typ == "session":
                lag = float(d["lag_ms"]) if d.get("lag_ms") is not None else None
                changed = lag != self.live_lag_ms or self.live_status != "live"
                self.live_lag_ms = lag
                self.live_status = "live"
                if changed:
                    self.render_status()
                if self.screen_mode == "signals":
                    self.render_feedhead()
            elif typ == "alert":
                key = d.get("key") or f"{d.get('token')}:{d.get('clock_ts')}"
                prev = self.signal_alerts.get(key)
                if d.get("kind") == "fired":
                    row = {k: d.get(k) for k in ("created_ts", "clock_ts", "mode", "token", "symbol", "rule", "score", "inflow", "mentions_1h", "price", "detail")}
                    row.update({"id": (prev or {}).get("id") or 0, "outcome_30m": (prev or {}).get("outcome_30m"), "outcome_60m": (prev or {}).get("outcome_60m"), "graduated_after": (prev or {}).get("graduated_after"), "_fresh": True})
                    self.signal_alerts[key] = row
                    self.set_timer(2.5, lambda k=key: self._unfresh_signal(k))
                elif prev is not None:
                    for k in ("outcome_30m", "outcome_60m", "graduated_after"):
                        if d.get(k) is not None:
                            prev[k] = d[k]
                if self.screen_mode == "signals":
                    self._fill_signals()
                    self.render_feedhead()
                    if self.detail_mode == "summary":
                        self.render_detail()
            elif typ == "position":
                if d.get("kind") == "skipped" or self.paper_doc is None:
                    return
                pos = self.paper_doc.setdefault("positions", {"open": [], "closed": [], "pending": []})
                pos["open"] = [p for p in pos.get("open", []) if p["id"] != d["id"]]
                pos["closed"] = [p for p in pos.get("closed", []) if p["id"] != d["id"]]
                if d.get("status") == "closed":
                    pos["closed"].insert(0, d)
                else:
                    pos["open"].append(d)
                    pos["open"].sort(key=lambda p: p["id"])
                if self.screen_mode == "signals":
                    self._fill_signals()
                    if self.detail_mode == "summary":
                        self.render_detail()
            elif typ == "radar_delta":
                self._apply_radar_delta(d)
        except NoMatches:
            pass

    def _unfresh_signal(self, key: str) -> None:
        a = self.signal_alerts.get(key)
        if a and a.get("_fresh"):
            a["_fresh"] = False
            if self.screen_mode == "signals":
                try:
                    self._fill_signals()
                except NoMatches:
                    pass

    def _apply_radar_delta(self, d: dict[str, Any]) -> None:
        if d.get("full"):
            self.live_rows.clear()
        for r in d.get("rows", []):
            self.live_rows[r["address"]] = r
        for a in d.get("removed", []):
            self.live_rows.pop(a, None)
        if d.get("top"):
            self.live_top = list(d["top"])
        self.live_deltas += 1
        now = time.time()
        if self.screen_mode == "radar" and now - self._live_radar_at >= 0.5:
            self._live_radar_at = now
            rows = [self.live_rows[a] for a in self.live_top if a in self.live_rows]
            self._fill_radar(filter_live_rows(rows, self.radar_preset)[:40])

    # ---- traders screen ----
    def traders_tick(self) -> None:
        if self.screen_mode == "traders":
            self.fetch_traders()

    @work(thread=True, exclusive=True, group="traders")
    def fetch_traders(self) -> None:
        try:
            doc = self.client.traders({"preset": self.traders_preset, "limit": 40, "min_trades": 5 if self.traders_preset == "smart" else 1})
            self._deliver(self._traders_loaded, doc)
        except ApiError as e:
            self._deliver(self.apply_error, str(e))

    def _traders_loaded(self, doc: dict[str, Any]) -> None:
        self.traders_doc = doc
        self._fill_traders(doc.get("rows", []))
        self.render_feedhead()
        if self.screen_mode == "traders" and self.detail_mode == "summary":
            self.render_detail()

    def _traders_cell_values(self, i: int, r: dict[str, Any]) -> dict[str, tuple[str, str]]:
        """key -> (plain value, style) for one leaderboard row; unknown = n/a, never 0."""
        pnl = r.get("pnl_eth")
        roi = r.get("roi")
        win = r.get("win_rate")
        tags = ", ".join(r.get("tags") or []) or "—"
        tags_w = next((w for _, k, w in self._traders_columns() if k == "tags"), 14)
        return {
            "rank": (str(i), MUTED),
            "wallet": (short(r["wallet"]), f"bold {TEXT}"),
            "trades": (str(r.get("trades", "")), SECONDARY),
            "coins": (str(r.get("coins", "")), SECONDARY),
            "win": ("n/a" if win is None else f"{win * 100:.0f}%", MUTED if win is None else TEXT),
            "pnl": ("n/a" if pnl is None else f"{pnl:+.4f}", TEXT if (pnl or 0) >= 0 else SECONDARY),
            "roi": ("n/a" if roi is None else f"{roi * 100:+.0f}%", MUTED if roi is None else SECONDARY),
            "hold": (dur(int(r["median_hold_s"])) if r.get("median_hold_s") is not None else "n/a", SECONDARY if r.get("median_hold_s") is not None else MUTED),
            "quality": (f"{r['quality']:.2f}" if r.get("quality") is not None else "n/a", f"bold {TEXT}" if (r.get("quality") or 0) >= 0.55 else SECONDARY),
            "last": (utc(r.get("last_ts")), SECONDARY),
            "tags": (tags[:tags_w], PRIMARY if "bot" in (r.get("tags") or []) or "deployer" in (r.get("tags") or []) else MUTED),
        }

    def _fill_traders(self, rows: list[dict[str, Any]]) -> None:
        table = self.query_one("#traders", DataTable)
        cols = self._traders_columns()
        same_order = [r["wallet"] for r in rows] == [r["wallet"] for r in self.traders_rows] and table.row_count == len(rows)
        cur = self._selected_trader_row()
        cur_w = cur["wallet"] if cur else None
        self.traders_rows = rows
        left = ("wallet", "tags")
        if same_order:
            for i, r in enumerate(rows, 1):
                vals = self._traders_cell_values(i, r)
                shown = self._traders_cells.get(r["wallet"], [])
                new_plain = []
                for ci, (_, key, w) in enumerate(cols):
                    plain, style = vals[key]
                    new_plain.append(plain + "|" + style)
                    if ci < len(shown) and shown[ci] == new_plain[-1]:
                        continue
                    table.update_cell(r["wallet"], key, Text(plain, style=style, justify="left" if key in left else "right"), update_width=False)
                self._traders_cells[r["wallet"]] = new_plain
            return
        scroll_y = table.scroll_y
        table.clear()
        self._traders_cells.clear()
        for i, r in enumerate(rows, 1):
            vals = self._traders_cell_values(i, r)
            cells, plain_row = [], []
            for _, key, w in cols:
                plain, style = vals[key]
                plain_row.append(plain + "|" + style)
                cells.append(Text(plain, style=style, justify="left" if key in left else "right"))
            table.add_row(*cells, key=r["wallet"])
            self._traders_cells[r["wallet"]] = plain_row
        if rows:
            idx = next((i for i, r in enumerate(rows) if r["wallet"] == cur_w), 0)

            def _restore() -> None:
                try:
                    table.scroll_y = scroll_y
                    table.move_cursor(row=idx, scroll=True)
                except NoMatches:
                    pass

            self.call_after_refresh(_restore)

    def _selected_trader_row(self) -> dict[str, Any] | None:
        table = self.query_one("#traders", DataTable)
        if table.row_count == 0:
            return None
        try:
            row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
            return next((r for r in self.traders_rows if r["wallet"] == row_key.value), None)
        except Exception:  # noqa: BLE001
            return None

    def action_tpreset(self, name: str) -> None:
        if self.query_one("#search", Input).has_focus:
            return
        self.traders_preset = name
        if self.screen_mode != "traders":
            self.action_screen("traders")
        else:
            self.fetch_traders()

    @work(thread=True, exclusive=True, group="wallet")
    def load_wallet(self, addr: str) -> None:
        try:
            doc = self.client.wallet(addr)
            self._deliver(self._wallet_loaded, doc)
        except ApiError as e:
            self._deliver(self.apply_error, str(e))

    def _wallet_loaded(self, doc: dict[str, Any]) -> None:
        self.wallet_doc = doc
        self.detail_mode = "wallet"
        self.render_detail()
        self.render_keys()

    def render_wallet(self, t: Text) -> None:
        """The wallet card: stats, tags, positions timeline, coins, recent trades with tx hashes, copy-test note."""
        d = self.wallet_doc or {}
        st = d.get("stats")
        t.append(f"{d.get('short', short(self.wallet_addr or ''))}\n", style=f"bold {TEXT}")
        t.append(f"{d.get('address', self.wallet_addr or '')}\n", style=MUTED)
        narrow = self._narrow()
        if not st:
            t.append("\nno wallet_stats for this address in the computed range · run stampede traders on this store\n", style=SECONDARY)
        else:
            tags = ", ".join(st.get("tags") or []) or "no tags"
            t.append(f"{tags}\n\n", style=PRIMARY if ("bot" in st.get("tags", []) or "deployer" in st.get("tags", [])) else SECONDARY)

            def fact(label: str, value: str, style: str = TEXT) -> None:
                t.append(f"{label:<9}", style=SECONDARY)
                t.append(value + "\n", style=style)

            pnl_usd = f" · ${st['pnl_usd']:,.0f}" if st.get("pnl_usd") is not None else " · USD n/a"
            fact("PNL", f"{st['pnl_eth']:+.4f} ETH (real {st['realized_eth']:+.4f} · unreal {st['unrealized_eth']:+.4f}){pnl_usd}")
            fact("ROI", f"{st['roi'] * 100:+.0f}% on {st['cost_eth']:.3f} ETH deployed · fees {st['fees_eth']:.4f} ETH" if st.get("roi") is not None else "n/a", SECONDARY)
            wr = f"{st['win_rate'] * 100:.0f}%" if st.get("win_rate") is not None else "n/a"
            fact("TRADES", f"{st['trades']} trades · {st['coins']} coins · {st['positions_closed']} closed · {st['positions_open']} open · win {wr}", SECONDARY)
            fact("HOLD", f"median {dur(int(st['median_hold_s'])) if st.get('median_hold_s') is not None else 'n/a'} · {st['trades_per_hour']:.1f} trades/h", SECONDARY)
            eq = f"{st['exit_quality']:.2f}" if st.get("exit_quality") is not None else "n/a"
            ra = f"{st['rug_avoid'] * 100:.0f}%" if st.get("rug_avoid") is not None else "n/a"
            sn = f"{st['sniper_share'] * 100:.0f}%" if st.get("sniper_share") is not None else "n/a"
            br = f"{st['buyer_rank_median']:.0f}" if st.get("buyer_rank_median") is not None else "n/a"
            fact("ENTRY", f"median buyer rank {br} · sniper {sn}", SECONDARY)
            fact("EXIT", f"exit quality {eq} · rug avoided {ra} · consistency {f'{st['consistency'] * 100:.0f}%' if st.get('consistency') is not None else 'n/a'}", SECONDARY)
            fact("QUALITY", f"{st['quality']:.3f}", f"bold {TEXT}")
        pos = d.get("positions") or []
        t.append(f"\nPOSITIONS · {len(pos)} in range · newest first\n", style=SECONDARY)
        for p in pos[: 8 if narrow else 14]:
            pnl = p.get("pnl_quote") if p.get("closed") else p.get("unrealized_quote")
            pnl_s = f"{pnl:+.4f}" if pnl is not None else "n/a"
            mark = "closed" if p.get("closed") else "open"
            line = f"  {utc(p['entry_ts'])} {fit(p['token']['symbol'], 12):<12} {pnl_s:>9} {p.get('quote_symbol') or ''} {mark}"
            if not narrow:
                line += f" · hold {dur(p['hold_s']) if p.get('hold_s') is not None else 'n/a'} · rank {p['buyer_rank'] if p.get('buyer_rank') else 'n/a'} · exit q {f'{p['exit_quality']:.2f}' if p.get('exit_quality') is not None else 'n/a'}"
            t.append(line + "\n", style=TEXT if (pnl or 0) >= 0 else SECONDARY)
        if not pos:
            t.append("  none\n", style=MUTED)
        coins = d.get("coins") or []
        t.append(f"\nCOINS · {len(coins)}\n", style=SECONDARY)
        t.append("  " + " · ".join(f"{fit(c['symbol'], 12)} {c['trades']}" for c in coins[:10]) + ("\n" if coins else "none\n"), style=TEXT)
        tr = d.get("trades") or []
        t.append(f"\nRECENT TRADES · {len(tr)} of {d.get('trades_total', len(tr))}\n", style=SECONDARY)
        for x in tr[: 6 if narrow else 10]:
            qa = f"{x['quote_amount']:.4f} {x['quote_symbol']}" if x.get("quote_amount") is not None else "quote n/a"
            t.append(f"  {utc(x['ts'])} {x['side']:<4} {fit(x['token']['symbol'], 12):<12} {qa} tx {short(x['tx'])}\n", style=SECONDARY)
        if d.get("copy_test"):
            t.append(f"\nCOPY-TEST\n  {d['copy_test']}\n", style=MUTED)
        if d.get("note"):
            t.append(f"\n{d['note']}\n", style=MUTED)
        t.append("\nEsc back to the leaderboard row", style=MUTED)

    def render_traders_summary(self, t: Text) -> None:
        d = self.traders_doc or {}
        pr = (d.get("presets") or {}).get(self.traders_preset, {})
        r = self._selected_trader_row()
        if r is None:
            t.append("SELECTED WALLET\n\n", style=SECONDARY)
            if d and not d.get("rows"):
                t.append((d.get("empty_reason") or "no wallets pass this preset in the computed range") + "\n\n", style=TEXT)
            else:
                t.append("Move with ↑/↓ to read a wallet, Enter opens its card (positions, coins, trades).\n\n", style=SECONDARY)
            t.append(pr.get("label", ""), style=MUTED)
            return
        rank = next((i for i, x in enumerate(self.traders_rows, 1) if x["wallet"] == r["wallet"]), 0)
        t.append(f"{short(r['wallet'])}\n", style=f"bold {TEXT}")
        t.append(f"{r['wallet']}\n", style=MUTED)
        t.append(f"rank {rank} of {d.get('total', len(self.traders_rows))} · {self.traders_preset}\n\n", style=SECONDARY)

        def fact(label: str, value: str, style: str = TEXT) -> None:
            t.append(f"{label:<9}", style=SECONDARY)
            t.append(value + "\n", style=style)

        tags = ", ".join(r.get("tags") or []) or "no tags"
        fact("TAGS", tags, PRIMARY if ("bot" in (r.get("tags") or []) or "deployer" in (r.get("tags") or [])) else TEXT)
        fact("PNL", f"{r['pnl_eth']:+.4f} ETH · realized {r['realized_eth']:+.4f} · unrealized {r['unrealized_eth']:+.4f}")
        fact("USD", f"${r['pnl_usd']:,.0f}" if r.get("pnl_usd") is not None else "n/a (no hourly rate)", TEXT if r.get("pnl_usd") is not None else MUTED)
        fact("ROI", f"{r['roi'] * 100:+.0f}% on {r['cost_eth']:.3f} ETH · fees {r['fees_eth']:.4f}" if r.get("roi") is not None else "n/a", SECONDARY)
        wr = f"{r['win_rate'] * 100:.0f}%" if r.get("win_rate") is not None else "n/a"
        fact("TRADES", f"{r['trades']} · {r['coins']} coins · {r['positions_closed']} closed ({wr} won) · {r['positions_open']} open", SECONDARY)
        fact("HOLD", f"median {dur(int(r['median_hold_s'])) if r.get('median_hold_s') is not None else 'n/a'} · {r['trades_per_hour']:.1f}/h", SECONDARY)
        eq = f"{r['exit_quality']:.2f}" if r.get("exit_quality") is not None else "n/a"
        ra = f"{r['rug_avoid'] * 100:.0f}%" if r.get("rug_avoid") is not None else "n/a"
        sn = f"{r['sniper_share'] * 100:.0f}%" if r.get("sniper_share") is not None else "n/a"
        fact("ENTRY", f"buyer rank {f'{r['buyer_rank_median']:.0f}' if r.get('buyer_rank_median') is not None else 'n/a'} · sniper {sn}", SECONDARY)
        fact("EXIT", f"exit quality {eq} · rug avoided {ra}", SECONDARY)
        fact("QUALITY", f"{r['quality']:.3f}", f"bold {TEXT}")
        fact("LAST", f"{utc(r.get('last_ts'))} UTC", SECONDARY)
        t.append("\nEnter: wallet card (positions, coins, trades, copy-test note)", style=MUTED)

    def action_screen(self, name: str) -> None:
        if name not in ("feed", "radar", "traders", "signals") or name == self.screen_mode:
            return
        self.screen_mode = name
        feed = self.query_one("#feed", DataTable)
        rt = self.query_one("#radar", DataTable)
        tt = self.query_one("#traders", DataTable)
        sg = self.query_one("#signals", DataTable)
        feed.set_class(name != "feed", "hidden")
        rt.set_class(name == "radar", "visible")
        tt.set_class(name == "traders", "visible")
        sg.set_class(name == "signals", "visible")
        {"feed": feed, "radar": rt, "traders": tt, "signals": sg}[name].focus()
        self.detail_mode = "summary"
        self.edge_doc = None
        self.coin_doc = None
        self.wallet_doc = None
        if name == "radar":
            self.fetch_radar()
        elif name == "traders":
            self.fetch_traders()
        elif name == "signals":
            self.fetch_signals()
        self.render_brand()
        self.render_feedhead()
        self.render_detail()
        self.render_keys()
        self._render_empty()

    def action_toggle_screen(self) -> None:
        self.action_screen("radar" if self.screen_mode == "feed" else "feed")

    def action_preset(self, name: str) -> None:
        if self.query_one("#search", Input).has_focus:
            return
        self.radar_preset = name
        if self.screen_mode != "radar":
            self.action_screen("radar")
        else:
            self.fetch_radar()

    def action_refresh_coin(self) -> None:
        if self.coin_addr and self.detail_mode == "coin":
            self.load_coin(self.coin_addr, True)

    @work(thread=True, exclusive=True, group="coin")
    def load_coin(self, addr: str, refresh: bool = False) -> None:
        try:
            doc = self.client.coin(addr, refresh)
            self._deliver(self._coin_loaded, doc)
        except ApiError as e:
            self._deliver(self.apply_error, str(e))

    def _coin_loaded(self, doc: dict[str, Any]) -> None:
        self.coin_doc = doc
        self.detail_mode = "coin"
        self.render_detail()
        self.render_keys()

    def render_coin(self, t: Text) -> None:
        c = self.coin_doc or {}
        t.append(f"{c.get('symbol', '?')}\n", style=f"bold {TEXT}")
        t.append(f"{c.get('name', '')} · {c.get('short', '')}\n", style=SECONDARY)
        t.append(f"{c.get('address', self.coin_addr or '')}\n\n", style=MUTED)
        prog = c.get("progress") or {}
        aso = c.get("as_of") or {}
        la = c.get("launch") or {}
        stage = prog.get("stage", "?")
        if stage == "curve" and prog.get("progress") is not None:
            stage += f" · {int(prog['progress'] * 100)}% to graduation"
        t.append(f"age {dur(c['age_s']) if c.get('age_s') is not None else '?'} · {stage}\n", style=TEXT)
        if la:
            t.append(f"launched {utc(la.get('ts'))} UTC · deployer {short(la.get('deployer', ''))}\n", style=SECONDARY)
        t.append(launch_line(la.get("intel") if la else None) + "\n", style=SECONDARY)
        v = c.get("verdict")
        t.append(verdict_line(v) + "\n", style=(f"bold {PRIMARY}" if (v or {}).get("action") == "ENTER" else SECONDARY))
        c5 = aso.get("chg_5m")
        c1 = aso.get("chg_1h")
        t.append(f"price 5m {c5:+.1f}% · 1h {c1:+.1f}%\n" if c5 is not None and c1 is not None else "price change: n/a\n", style=TEXT)
        t.append(f"trades 1h {aso.get('trades_1h', 0)} · buyers 1h {aso.get('buyers_1h', 0)} · volume 1h {aso.get('vol_1h_quote', 0):,.0f} {aso.get('quote_symbol') or ''}\n", style=SECONDARY)
        t.append(f"buyers in range {c.get('buyers', 0)} · new {c.get('new_buyers', 0)}\n\n", style=SECONDARY)
        inb = [e for e in c.get("inbound", []) if e["wallets_main"] > 0][:6]
        outb = [e for e in c.get("outbound", []) if e["wallets_main"] > 0][:6]
        t.append("WALLETS CAME FROM\n", style=SECONDARY)
        for e in inb:
            t.append(f"  {e['wallets_main']:>4}  {e['token']['symbol']}\n", style=TEXT)
        if not inb:
            t.append("  none in range\n", style=MUTED)
        t.append("THEN WENT TO\n", style=SECONDARY)
        for e in outb:
            t.append(f"  {e['wallets_main']:>4}  {e['token']['symbol']}\n", style=TEXT)
        if not outb:
            t.append("  none in range\n", style=MUTED)
        h = c.get("holders") or {}
        t.append("\nHOLDERS\n", style=SECONDARY)
        if h and "holders" in h:
            dev = f"{h['dev_share'] * 100:.2f}%" if h.get("dev_share") is not None else "?"
            t.append(f"  {h['holders']} holders · top-10 {h.get('top10_share', 0) * 100:.1f}% · dev holds {dev} · sold {h.get('dev_sold_share', 0) * 100:.1f}% · launch-block buyers {h.get('launch_block_buyers', 0)}\n", style=TEXT)
        else:
            t.append("  not fetched · r = refresh context\n", style=MUTED)
        m = c.get("mentions") or {}
        t.append("X MENTIONS\n", style=SECONDARY)
        if m:
            t.append(f"  1h {m.get('mentions_1h')} · 24h {m.get('mentions_24h')} · authors {m.get('distinct_authors')}\n", style=TEXT)
            for tw in (m.get("top") or [])[:2]:
                t.append(f"  @{tw.get('author')} ({tw.get('followers') or 0:,} followers) {tw.get('text', '')[:70]}\n", style=SECONDARY)
        else:
            t.append("  not fetched · r = refresh context\n", style=MUTED)
        so = c.get("socials") or {}
        if so:
            t.append("DECLARED AT LAUNCH\n", style=SECONDARY)
            t.append(f"  X @{so.get('twitter') or '—'} · tg {so.get('telegram') or '—'} · {so.get('website') or '—'}\n", style=TEXT)
        mk = c.get("market_now") or c.get("market_cached") or {}
        if mk and mk.get("found"):
            t.append("MARKET NOW (GeckoTerminal)\n", style=SECONDARY)
            t.append(f"  FDV ${mk.get('fdv_usd') or 0:,.0f} · liq ${mk.get('reserve_usd') or 0:,.0f} · vol 1h ${mk.get('vol_h1') or 0:,.0f} · 1h {mk.get('chg_h1') or 0:+.1f}%\n", style=TEXT)
        if c.get("note"):
            t.append(f"\n{c.get('note', '')}\n", style=MUTED)
        t.append("\nEsc back to the coin summary · r refresh context", style=MUTED)

    def render_radar_summary(self, t: Text) -> None:
        d = self.radar_doc or {}
        pr = (d.get("presets") or {}).get(self.radar_preset, {})
        r = self._selected_radar_row()
        if r is None:
            t.append("SELECTED COIN\n\n", style=SECONDARY)
            t.append("Move with ↑/↓ to read a coin, Enter opens its card (holders, launch, market).\n\n", style=SECONDARY)
            t.append(pr.get("label", ""), style=MUTED)
            return
        rank = next((i for i, x in enumerate(self.radar_rows, 1) if x["address"] == r["address"]), 0)
        t.append(f"{r['symbol']}\n", style=f"bold {TEXT}")
        t.append(f"{r.get('name') or ''}{' · ' if r.get('name') else ''}{r.get('short', short(r['address']))} · {r.get('source', '')}\n", style=SECONDARY)
        t.append(f"{r['address']}\n\n", style=MUTED)

        def fact(label: str, value: str, style: str = TEXT) -> None:
            t.append(f"{label:<8}", style=SECONDARY)
            t.append(value + "\n", style=style)

        t.append(f"SCORE {r['score']:.1f}", style=f"bold {TEXT}")
        t.append(f" · rank {rank} of {d.get('total', len(self.radar_rows))} · {self.radar_preset.replace('_', ' ')}\n", style=SECONDARY)
        p = r.get("parts") or {}
        narrow = self._narrow()  # the pane is 42 columns wide at 120x36: one score part per line, short fact lines
        if p:
            pairs = [("inflow", p.get("inflow", 0)), ("acceleration", p.get("acceleration", 0)), ("breadth", p.get("breadth", 0)), ("wallet quality", p.get("wallet_quality", 0)), ("age bonus", p.get("age_bonus", 0)), ("curve bonus", p.get("curve_bonus", 0)), ("crowding", p.get("crowding", 0)), ("attention", p.get("attention_penalty", 0))]
            step = 1 if narrow else 2
            for i in range(0, len(pairs), step):
                t.append("  " + "   ".join(f"{la:<15}{va:>6.1f}" for la, va in pairs[i : i + step]) + "\n", style=MUTED)
        span = dur(int(d.get("span_s") or (self.session or {}).get("span_s") or 1800))
        t.append("\n")
        srcs = f"{r['breadth']} source coin{'s' if r['breadth'] != 1 else ''}"
        if narrow:
            fact("INFLOW", f"{r['inflow_10m']} in 10m · ×{r['accel']:.1f} vs {r.get('inflow_prev_per_10m', 0):.1f}/10m")
            fact("RANGE", f"{r.get('wallets_range', '?')} wallets · {r.get('sequences_range', '?')} sequences", SECONDARY)
            fact("SOURCES", f"{srcs} · last {span}", SECONDARY)
        else:
            fact("INFLOW", f"{r['inflow_10m']} wallets in 10m · ×{r['accel']:.1f} vs {r.get('inflow_prev_per_10m', 0):.1f}/10m before")
            fact("RANGE", f"{r.get('wallets_range', '?')} wallets · {r.get('sequences_range', '?')} sequences · {srcs} · last {span}", SECONDARY)
        age = dur(r["age_s"]) if r.get("age_s") is not None else "unknown"
        stage = r["stage"] + (f" · {int(r['progress'] * 100)}% to graduation" if r["stage"] == "curve" and r.get("progress") is not None else "")
        fact("AGE", f"{age} · {stage}")
        c5, c1 = r.get("chg_5m"), r.get("chg_1h")
        fact("PRICE", f"5m {c5:+.0f}% · 1h {c1:+.0f}%" if c5 is not None and c1 is not None else "change unknown", SECONDARY)
        if r.get("vol_1h_quote") is not None:
            fact("VOLUME", f"1h {r.get('vol_1h_quote', 0):,.0f} {r.get('quote_symbol') or ''} · buyers 1h {r.get('buyers_1h', 0)}", SECONDARY)
        m1 = r.get("mentions_1h")
        ctx = d.get("context") or {}
        fact("X", f"1h {m1} · 24h {r.get('mentions_24h')}" if m1 is not None else ("n/a · context off in replay" if not ctx.get("enabled") else "n/a"), SECONDARY if m1 is not None else MUTED)
        t.append("\nWALLETS CAME FROM · distinct wallets per source coin\n", style=SECONDARY)
        for s in r.get("sources", [])[:8]:
            t.append(f"  {s['wallets']:>4}  {s['symbol']}\n", style=TEXT)
        if not r.get("sources"):
            t.append("  none in range\n", style=MUTED)
        t.append("\nEnter: coin card (holders, launch, market)", style=MUTED)

    # ---- rows ----
    def _row_cells(self, e: dict[str, Any]) -> list[Text]:
        fresh = e.get("_fresh")
        wide = self._wide()
        grade = e["grade"]
        grade_txt = grade if wide or grade != "ambiguous" else "amb."
        cells = {
            "mark": Text("▌" if fresh else " ", style=PRIMARY),
            "sold_t": Text(utc(e["sell_ts"]), style=SECONDARY),
            "time": Text(utc(e["buy_ts"]) + ("" if e.get("buy_ts_exact") else "≈"), style=SECONDARY),
            "wallet": Text(short(e["wallet"]), style=SECONDARY),
            "sold": Text(fit(e["from_symbol"], self._sym_w), style=TEXT),
            "arrow": Text("→", style=PRIMARY if fresh else SECONDARY),
            "bought": Text(fit(e["to_symbol"], self._sym_w), style=TEXT),
            "gap": num(("same tx" if wide else "same") if grade == "direct" else dur(e["gap_s"]), SECONDARY),
            "grade": Text(grade_txt, style=TEXT if grade != "ambiguous" else MUTED),
        }
        return [cells[key] for _, key, _ in self._feed_columns()]

    def _add_row(self, table: DataTable, e: dict[str, Any]) -> None:
        table.add_row(*self._row_cells(e), key=str(e["id"]))
        self._feed_keys.append(str(e["id"]))

    def _refresh_mark(self, table: DataTable, eid: int) -> None:
        try:
            table.update_cell(str(eid), "mark", Text(" "), update_width=False)
            table.update_cell(str(eid), "arrow", Text("→", style=SECONDARY), update_width=False)
        except Exception:  # noqa: BLE001 - row filtered out
            pass

    def _matches_filter(self, e: dict[str, Any]) -> bool:
        t = self.filter_text.strip().lower()
        if not t:
            return True
        return t in e["from_symbol"].lower() or t in e["to_symbol"].lower() or t in e["wallet"].lower() or t in e["from"].lower() or t in e["to"].lower()

    def rebuild_rows(self, sel: dict[str, Any] | None = None, was_following: bool | None = None) -> None:
        """Re-add all rows (filter or column change) keeping the selected row selected when it is still shown."""
        table = self.query_one("#feed", DataTable)
        if sel is None and was_following is None:
            sel = self._selected_event()
            was_following = self.follow
        table.clear()
        self._feed_keys.clear()
        for e in sorted(self.events.values(), key=lambda x: (x["buy_ts"], x["id"])):
            if self._matches_filter(e):
                self._add_row(table, e)
        if table.row_count:
            idx = self._feed_keys.index(str(sel["id"])) if sel and str(sel["id"]) in self._feed_keys else None
            if idx is not None and not was_following:
                table.move_cursor(row=idx, scroll=True)
            else:
                table.move_cursor(row=table.row_count - 1, scroll=True)
                self.follow = True
                self.unseen = 0
        self.render_feedhead()
        self._render_empty()

    # ---- rendering ----
    def _mode_badge(self) -> Text:
        s = self.session or {}
        st = self.status_doc or {}
        if s.get("mode") == "live":
            age = (st.get("data") or {}).get("age_s")
            b = Text(" LIVE ", style=f"bold {BG} on {TEXT}")
            if age is not None and age > STALE_AFTER_S:
                b.append(" ! STALE ", style=f"bold {TEXT}")
                b.append(f"data age {dur(age)}", style=PRIMARY)
            elif self.live_status == "live":
                # the stream is on: the engine's head -> last event latency, one small number (docs/ENGINE.md)
                b.append(f" · {self.live_lag_ms:.0f} ms" if self.live_lag_ms is not None else " · stream", style=SECONDARY)
            elif self.live_status == "reconnecting":
                b.append(" · RECONNECTING", style=f"bold {TEXT}")
            elif self.live_status == "connecting":
                b.append(" · connecting", style=MUTED)
            return b
        if not s:
            return Text("[CONNECTING…]", style=SECONDARY)
        return Text(f"[{s.get('label') or (s.get('mode') or '?').upper()}]", style=f"bold {TEXT}")

    def _facts(self, tight: bool = False) -> list[Text]:
        """Labelled facts for the header: mode · chain · clock · range/window · session · api.
        tight=True puts one space after the label (facts joined on one line) instead of an aligned column."""
        s = self.session or {}
        st = self.status_doc or {}
        chainname = (st.get("chain") or {}).get("name", "Robinhood Chain")
        clock = s.get("clock_ts")
        span = int(s.get("span_s") or 1800)

        def fact(label: str, value: str, style: str = TEXT) -> Text:
            t = Text(f"{label} " if tight else f"{label:<8}", style=SECONDARY)
            t.append(value, style=style)
            return t

        facts = [self._mode_badge(), fact("CHAIN", chainname)]
        if s.get("mode") == "live":
            age = (st.get("data") or {}).get("age_s")
            facts.append(fact("BLOCK", f"{utc(clock)} UTC · data age {dur(age) if age is not None else '?'}", TEXT if (age or 0) <= STALE_AFTER_S else PRIMARY))
        else:
            facts.append(fact("CLOCK", f"{utc(clock)} UTC" if clock else "waiting for the session clock"))
        facts.append(fact("RANGE", f"{utc(clock - span) if clock else '--:--:--'}–{utc(clock)} UTC · window {dur(int(s.get('window_s') or 1800))}", SECONDARY))
        facts.append(fact("SESSION", f"{s.get('id') or '…'} · shared with the web view", SECONDARY))
        facts.append(fact("API", f"{getattr(self.client, 'base', '')} · poll {self.poll_s:g}s", MUTED))
        return facts

    def _status_line(self, full: bool) -> Text:
        """The one-line status under the compact wordmark: clock/block · range · window [· session · api].
        Fits 120 columns with full=True and 100 columns without."""
        s = self.session or {}
        st = self.status_doc or {}
        clock = s.get("clock_ts")
        span = int(s.get("span_s") or 1800)
        parts: list[Text] = []

        def fact(label: str, value: str, style: str = TEXT) -> None:
            t = Text(f"{label} ", style=SECONDARY)
            t.append(value, style=style)
            parts.append(t)

        if s.get("mode") == "live":
            age = (st.get("data") or {}).get("age_s")
            fact("BLOCK", f"{utc(clock)} UTC · age {dur(age) if age is not None else '?'}", TEXT if (age or 0) <= STALE_AFTER_S else PRIMARY)
        else:
            fact("CLOCK", f"{utc(clock)} UTC" if clock else "waiting for the session clock")
        fact("RANGE", f"{utc(clock - span) if clock else '--:--:--'}–{utc(clock)} · window {dur(int(s.get('window_s') or 1800))}", SECONDARY)
        if full:
            fact("SESSION", s.get("id") or "…", SECONDARY)
            fact("API", (getattr(self.client, "base", "") or "").replace("http://", ""), MUTED)
        return Text("  ·  ", style=BORDER).join(parts)

    def _tabs_line(self) -> Text:
        t = Text()
        for key, name, mode in (("1", "FEED", "feed"), ("2", "RADAR", "radar"), ("3", "TRADERS", "traders"), ("4", "SIGNALS", "signals")):
            active = self.screen_mode == mode
            if active:
                t.append("▌", style=f"{PRIMARY} on {ACTIVE}")
                t.append(f"{key} {name} ", style=f"bold {TEXT} on {ACTIVE}")
            else:
                t.append(f" {key} {name} ", style=SECONDARY)
            t.append("  ")
        t.append("1/2/3/4 switch view · Tab moves between panes", style=MUTED)
        return t

    def render_brand(self) -> None:
        variant = self._header_variant()
        if variant == "wide" and getattr(self, "_mark_drawn", None) != variant:
            # The README bison, pre-rendered once (brand.bison_halfblocks is cached) as 25 cols x 12 rows of
            # half-blocks; the 16x8 head mark at native size when Pillow/the asset is unavailable; plain glyphs
            # when there is no colour system at all.
            mark = self.query_one("#mark", Static)
            bison = None if self._colorless() else brand.bison_halfblocks(MASCOT_ROWS, BG)
            if bison:
                mark.update(Text.from_markup("\n".join(bison)))
            elif self._colorless():
                mark.update(Text("\n".join(brand.mark_plain()[:8]), style=TEXT))
            else:
                mark.update(Text.from_markup("\n".join(brand.mark_halfblocks(BG))))
            self._mark_drawn = variant
        facts = self._facts(tight=variant != "wide")
        lines: list[Text] = []
        dot = Text("  ·  ", style=BORDER)
        if variant == "wide":
            # HEADER_ROWS lines beside the mascot: the 5-row wordmark bottom-aligned with it (rows 7-11),
            # the mode badge above the fact column, facts to the right of the wordmark, view tabs on the last row.
            rows = brand.big()
            top = MASCOT_ROWS - 5
            pad = " " * brand.BIG_WIDTH
            for i in range(HEADER_ROWS - 1):
                word = rows[i - top] if top <= i < top + 5 else pad
                line = Text(word, style=f"bold {PRIMARY}")
                k = i - (top - 1)  # facts[0] (mode) on the row above the wordmark, then one fact per wordmark row
                if 0 <= k < len(facts):
                    line.append("   ")
                    line.append(facts[k])
                lines.append(line)
            lines.append(self._tabs_line())
        else:
            # compact/tiny: the compact wordmark line with the mode badge, one status line, the view tabs.
            first = Text("■ ", style=PRIMARY) if variant == "tiny" else Text()
            first.append(brand.compact(), style=f"bold {PRIMARY}")
            first.append("   ")
            first.append(facts[0])
            first.append(dot)
            first.append(facts[1])
            lines = [first, self._status_line(full=variant == "compact"), self._tabs_line()]
        block = Text("\n").join(lines)
        self.query_one("#brand", Static).update(block)
        self.query_one("#rule1", Static).update(Text("─" * max(10, self.size.width - 2), style=BORDER))

    def render_status(self) -> None:
        try:
            self._render_status()
        except NoMatches:
            pass  # periodic timer fired while the app is closing

    def _render_status(self) -> None:
        self.render_brand()
        s = self.session or {}
        st = self.status_doc or {}
        alert = self.query_one("#alert", Static)
        if self.api_error:
            alert.update(Text(f" ! DISCONNECTED FROM API · {self.api_error} · retrying every {self.poll_s:g}s (attempt {self.error_count}) · data frozen at {utc(self.last_clock)} UTC "))
            alert.set_class(True, "visible")
        elif s.get("mode") == "live" and s.get("live_paused"):
            reason = ((st.get("live") or {}).get("last_error")) or "provider error"
            alert.update(Text(f" ! PROVIDER ERROR · {reason} · map frozen at the last good block "))
            alert.set_class(True, "visible")
        else:
            alert.set_class(False, "visible")

    def _pane_marker(self, widget_id: str) -> Text:
        try:
            focused = self.query_one(f"#{widget_id}").has_focus
        except NoMatches:
            focused = False
        return Text("▌" if focused else " ", style=PRIMARY)

    def render_feedhead(self) -> None:
        try:
            self._render_feedhead()
        except NoMatches:
            pass

    def _render_feedhead(self) -> None:
        n = len(self.events)
        table = self.query_one("#feed", DataTable)
        shown = table.row_count
        head = self._pane_marker({"radar": "radar", "traders": "traders", "signals": "signals"}.get(self.screen_mode, "feed"))
        if self.screen_mode == "signals":
            d = self.paper_doc or {}
            pos = d.get("positions") or {}
            st = d.get("stats") or {}
            n_enter = sum(1 for a in self.signal_alerts.values() if a.get("rule") == "edge_enter")
            head.append("SIGNALS", style=f"bold {TEXT}")
            head.append(f" · {n_enter} ENTER · {len(self.signal_alerts)} alerts", style=SECONDARY)
            if d:
                tot = st.get("total_quote") or 0.0
                head.append(f" · paper {len(pos.get('open', []))} open / {len(pos.get('closed', []))} closed · {eth_s(tot)} ETH", style=TEXT if tot >= 0 else SECONDARY)
            if (self.session or {}).get("mode") == "live":
                head.append(f" · {'stream ' + (f'{self.live_lag_ms:.0f} ms' if self.live_lag_ms is not None else 'on') if self.live_status == 'live' else self.live_status.upper()}", style=MUTED)
            head.append(" · simulated fills" if not self._narrow() else "", style=MUTED)
            self.query_one("#feedhead", Static).update(head)
            self.render_detailhead()
            return
        if self.screen_mode == "traders":
            d = self.traders_doc or {}
            rng = d.get("range") or {}
            head.append(f"TRADERS · {self.traders_preset.upper()}", style=f"bold {TEXT}")
            if d.get("run"):
                head.append(f" · {d.get('total', 0)} wallets" + ("" if self._narrow() else f" · stats {utc(rng.get('from_ts'))}–{utc(rng.get('to_ts'))} UTC · FIFO after fees"), style=SECONDARY)
            elif d:
                head.append(" · no wallet_stats in this store · run stampede traders", style=MUTED)
            else:
                head.append(" · loading…", style=MUTED)
            self.query_one("#feedhead", Static).update(head)
            self.render_detailhead()
            return
        if self.session is None and not self.api_error:
            head.append("CONNECTING TO API · loading the visible history…", style=SECONDARY)
            self.query_one("#feedhead", Static).update(head)
            self.render_detailhead()
            return
        s = self.session or {}
        span = dur(int(s.get("span_s") or 1800))
        if self.screen_mode == "radar":
            d = self.radar_doc or {}
            ctx = d.get("context") or {}
            head.append(f"RADAR · {self.radar_preset.replace('_', ' ').upper()}", style=f"bold {TEXT}")
            head.append(f" · {d.get('total', 0)} coins with rotation inflow · last {span}" if not self._narrow() else f" · {d.get('total', 0)} coins · last {span}", style=SECONDARY)
            head.append(" · X context on" if ctx.get("enabled") else " · X context off (replay)", style=MUTED)
            self.query_one("#feedhead", Static).update(head)
            self.render_detailhead()
            return
        if self._stream_queue:
            head.append(f"STREAMING {shown:,} / {n:,} OBSERVED SEQUENCES · last {span}", style=f"bold {TEXT}")
            bar = min(30, int(30 * shown / max(1, n)))
            head.append(f" · {'█' * bar}{'·' * (30 - bar)}", style=PRIMARY)
            self.query_one("#feedhead", Static).update(head)
            self.render_detailhead()
            return
        narrow = self._narrow()
        head.append("OBSERVED SEQUENCES", style=f"bold {TEXT}")
        head.append(" · " if narrow else f" · last {span} · ", style=SECONDARY)
        if self.filter_text:
            head.append(f"{shown:,} of {n:,} match '{self.filter_text}'", style=TEXT)
        else:
            head.append(f"{n:,} rows", style=SECONDARY)
        if self.fresh_ids:
            head.append(f" · +{len(self.fresh_ids)} new", style=PRIMARY)
        if self.follow:
            head.append(" · following latest", style=MUTED)
        elif self.unseen:
            head.append(f" · ▲ {self.unseen} new below", style=f"bold {PRIMARY}")
            head.append(" · End follows latest", style=TEXT)
        else:
            head.append(" · history · End follows latest", style=MUTED)
        if not narrow:
            head.append(" · ≈ interpolated block time", style=MUTED)
        self.query_one("#feedhead", Static).update(head)
        self.render_detailhead()

    def render_detailhead(self) -> None:
        try:
            marker = self._pane_marker("detail")
            focused = self.query_one("#detail").has_focus
            self.query_one("#detailwrap").set_class(focused, "focused")
        except NoMatches:
            return
        if self.detail_mode == "evidence":
            d = self.edge_doc
            marker.append("EVIDENCE", style=f"bold {TEXT}")
            if d:
                marker.append(f" · {len(d['sequences'])} of {d['sequences_total']} sequences · Esc back", style=SECONDARY)
            else:
                marker.append(" · loading…", style=SECONDARY)
        elif self.detail_mode == "coin":
            marker.append("COIN CARD", style=f"bold {TEXT}")
            marker.append(" · Esc back · r refresh context", style=SECONDARY)
        elif self.detail_mode == "wallet":
            marker.append("WALLET CARD", style=f"bold {TEXT}")
            marker.append(" · Esc back", style=SECONDARY)
        elif self.screen_mode == "traders":
            marker.append("SELECTED WALLET", style=f"bold {TEXT}")
            marker.append(" · Enter opens the card", style=SECONDARY)
        elif self.screen_mode == "radar":
            marker.append("SELECTED COIN", style=f"bold {TEXT}")
            marker.append(" · Enter opens the card", style=SECONDARY)
        elif self.screen_mode == "signals":
            marker.append("PAPER LEDGER", style=f"bold {TEXT}")
            marker.append(" · simulated · Enter opens the coin card", style=SECONDARY)
        else:
            marker.append("SELECTED PAIR", style=f"bold {TEXT}")
            marker.append(" · Enter opens the evidence", style=SECONDARY)
        if focused:
            marker.append(" · ↑/↓ scroll", style=MUTED)
        self.query_one("#detailhead", Static).update(marker)

    def render_detail(self) -> None:
        body = self.query_one("#detailbody", Static)
        t = Text()
        if self.detail_mode == "coin" and self.coin_doc:
            self.render_coin(t)
            body.update(t)
            self.render_detailhead()
            return
        if self.detail_mode == "wallet" and self.wallet_doc:
            self.render_wallet(t)
            body.update(t)
            self.render_detailhead()
            return
        if self.detail_mode == "evidence" and self.edge_doc:
            d = self.edge_doc
            fs, ts_ = d["from"], d["to"]
            t.append(f"{fs['symbol']} → {ts_['symbol']}\n", style=f"bold {TEXT}")
            t.append(f"{fs['short']} → {ts_['short']}\n\n", style=SECONDARY)
            t.append(f"{d['wallets_main']} WALLET{'' if d['wallets_main'] == 1 else 'S'}\n", style=f"bold {TEXT}")
            t.append(f"sold {fs['symbol']}, then bought {ts_['symbol']} within {dur(d['window_s'])}\n", style=SECONDARY)
            g = d.get("wallets_by_grade", {})
            t.append(f"{d['sequences_total']} sequences · direct {g.get('direct', 0)} · clean {g.get('clean', 0)} · ambiguous {g.get('ambiguous', 0)} (not counted)\n\n", style=SECONDARY)
            t.append("Same address, observed order of trades. Not proof that the sale paid for the purchase or that addresses share an owner.\n\n", style=MUTED)
            t.append(f"EVIDENCE · {len(d['sequences'])} of {d['sequences_total']} rows\n", style=SECONDARY)
            narrow = self._narrow()  # 42-column pane: time + tx per leg; block numbers only when they fit on the line
            for s in d["sequences"]:
                sl, by = s["sell"], s["buy"]
                t.append(f"{short(s['wallet'])} ", style=TEXT)
                t.append(f"[{s['grade']}] ", style=TEXT if s["grade"] != "ambiguous" else MUTED)
                t.append(f"gap {dur(s['gap_s'])}\n", style=SECONDARY)
                for label, leg in (("sold  ", sl), ("bought", by)):
                    when = f"{utc(leg['ts'])}{'' if leg['ts_exact'] else '≈'}"
                    t.append(f"  {label} {when} tx {short(leg['tx'])}\n" if narrow else f"  {label} {when} block {leg['block']} tx {short(leg['tx'])}\n", style=SECONDARY)
            t.append("\n≈ interpolated block time · Esc back", style=MUTED)
        elif self.screen_mode == "radar":
            self.render_radar_summary(t)
        elif self.screen_mode == "traders":
            self.render_traders_summary(t)
        elif self.screen_mode == "signals":
            self.render_signals_summary(t)
        else:
            row = self._selected_event()
            if row is None:
                t.append("SELECTED PAIR\n\n", style=SECONDARY)
                t.append("Move with ↑/↓, press Enter to open the pair.\n\n", style=SECONDARY)
                t.append("Edge A → B: distinct wallets that sold A, then bought B within the pairing window. Same address, observed order of trades; not proof of money flow or shared ownership.", style=MUTED)
            else:
                t.append(f"{row['from_symbol']} → {row['to_symbol']}\n", style=f"bold {TEXT}")
                t.append(f"{row['from_short']} → {row['to_short']}\n\n", style=SECONDARY)
                t.append("WALLET  ", style=SECONDARY)
                t.append(f"{row['wallet']}\n", style=TEXT)
                t.append("SOLD    ", style=SECONDARY)
                t.append(f"{utc(row['sell_ts'])} UTC  {row['from_symbol']}\n", style=TEXT)
                t.append("BOUGHT  ", style=SECONDARY)
                t.append(f"{utc(row['buy_ts'])}{'' if row.get('buy_ts_exact') else '≈'} UTC  {row['to_symbol']}\n", style=TEXT)
                t.append("GAP     ", style=SECONDARY)
                t.append(f"{'same tx' if row['grade'] == 'direct' else dur(row['gap_s'])} · {row['grade']}\n\n", style=TEXT)
                t.append("SELL TX ", style=SECONDARY)
                t.append(f"{row['sell_tx']}\n", style=MUTED)
                t.append("BUY TX  ", style=SECONDARY)
                t.append(f"{row['buy_tx']}\n\n", style=MUTED)
                if not row.get("buy_ts_exact"):
                    t.append("≈ interpolated block time\n", style=MUTED)
                t.append("Enter: all wallets on this pair", style=MUTED)
        body.update(t)
        self.render_detailhead()

    def render_hot(self) -> None:
        g = self.graph_doc
        t = Text()
        s = self.session or {}
        span = dur(int(s.get("span_s") or 1800))
        limit = 7 if self.size.height >= 44 else 6
        t.append(f"HOT ROTATIONS · distinct wallets, last {span}", style=SECONDARY)
        if g is None:
            t.append("\n…", style=MUTED)
        else:
            now = time.time()
            edges = g.get("edges", [])[:limit]
            if not edges:
                t.append("\nwaiting for the first live block" if g.get("waiting") else "\nno edges in range", style=MUTED)
            labels = {n["address"]: n for n in g.get("nodes", [])}
            cur: dict[str, int] = {}
            for e in edges:
                key = f"{e['from']}->{e['to']}"
                cur[key] = e["wallets_main"]
                a = labels.get(e["from"], {}).get("symbol", short(e["from"]))
                b = labels.get(e["to"], {}).get("symbol", short(e["to"]))
                delta = e["wallets_main"] - self.hot_prev.get(key, e["wallets_main"])
                dtxt = f"+{delta}" if delta > 0 else ("" if delta == 0 else str(delta))
                t.append(f"\n{e['wallets_main']:>4} ", style=f"bold {TEXT}")
                t.append(f"{fit(a, 16):<16} → {fit(b, 16):<16}", style=TEXT)
                t.append(f"  {e['sequences']:>4} seq", style=SECONDARY)
                if dtxt:
                    t.append(f"  {dtxt} since last refresh", style=PRIMARY if delta > 0 else SECONDARY)
            if self.hot_prev_at:
                t.append(f"\nΔ measured over {now - self.hot_prev_at:.0f}s between refreshes", style=MUTED)
            self.hot_prev = cur
            self.hot_prev_at = now
        self.query_one("#hot", Static).update(t)

    def render_activity(self, streaming: bool = False) -> None:
        s = self.session or {}
        clock = s.get("clock_ts")
        span = int(s.get("span_s") or 1800)
        if clock is None:
            return
        nb = max(1, span // BUCKET_S)
        buckets = [0.0] * nb
        lo = clock - span
        pending = {e["id"] for e in self._stream_queue} if streaming else set()
        for e in self.events.values():
            if e["id"] in pending:
                continue
            i = int((e["buy_ts"] - lo) // BUCKET_S)
            if 0 <= i < nb:
                buckets[i] += 1
        self.spark_values = buckets
        self.query_one("#spark", Sparkline).data = buckets
        self.query_one("#acthead", Static).update(Text(f"ACTIVITY · sequences per {BUCKET_S}s · {nb} buckets" if self._narrow() else f"ACTIVITY · observed sequences per {BUCKET_S}s bucket · {nb} buckets", style=SECONDARY))
        self.query_one("#actfoot", Static).update(Text(f"{utc(lo)} → {utc(clock)} UTC · peak {int(max(buckets)) if buckets else 0}/bucket · total {int(sum(buckets))}", style=SECONDARY))

    def render_keys(self) -> None:
        try:
            self._render_keys()
        except NoMatches:
            pass

    def _key_hints(self) -> list[tuple[int, str]]:
        """(priority, hint) for the footer; lower priority is dropped first when the line does not fit."""
        s = self.session or {}
        focused = self.focused
        fid = focused.id if focused is not None else None
        ctl = [(3, "space play/pause"), (3, "←/→ seek 60s"), (4, "[ ] speed")] if s.get("controls") else []
        if self.api_error:
            return [(0, "DISCONNECTED"), (0, f"retrying every {self.poll_s:g}s"), (0, "data frozen"), (0, "q quit")]
        if fid == "search":
            return [(0, "type to filter symbol / address"), (0, "Enter keeps the filter"), (0, "Esc clears it"), (1, "Tab back to the table")]
        if fid == "detail":
            back = "Esc back" if self.detail_mode != "summary" else "Esc to the table"
            return [(0, "DETAILS"), (0, "↑/↓ PgUp/PgDn scroll"), (0, "Tab back to the table"), (1, back), *ctl, (0, "q quit")]
        if self.screen_mode == "radar":
            return [(0, "↑/↓ select"), (0, "Enter coin card"), (1, "Esc back"), (2, "presets u under radar · g graduating · s smart rotators · a all"), (1, "Tab details"), (0, "1 feed"), (0, "3 traders"), *ctl, (0, "q quit")]
        if self.screen_mode == "traders":
            return [(0, "↑/↓ select"), (0, "Enter wallet card"), (1, "Esc back"), (2, "presets t top · m smart · n snipers · b bots"), (1, "Tab details"), (0, "1 feed"), (0, "2 radar"), (0, "4 signals"), *ctl, (0, "q quit")]
        if self.screen_mode == "signals":
            return [(0, "↑/↓ select alert"), (0, "Enter coin card"), (1, "Esc back"), (1, "Tab ledger"), (0, "1 feed"), (0, "2 radar"), (0, "3 traders"), *ctl, (0, "q quit")]
        return [(0, "↑/↓ select"), (0, "Enter evidence"), (1, "Esc back"), (0, "/ search"), (1, "End follow latest"), (1, "Tab details"), (0, "2 radar"), (0, "3 traders"), (0, "4 signals"), *ctl, (0, "q quit")]

    def _render_keys(self) -> None:
        hints = self._key_hints()
        width = max(20, self.size.width - 2)
        short_forms = {"presets u under radar · g graduating · s smart rotators · a all": "u g s a presets", "presets t top · m smart · n snipers · b bots": "t m n b presets", "End follow latest": "End follow", "Enter evidence": "Enter open", "Enter coin card": "Enter card", "Enter wallet card": "Enter card", "space play/pause": "space play", "←/→ seek 60s": "←/→ ±60s", "Tab back to the table": "Tab table", "Esc to the table": "Esc table", "↑/↓ PgUp/PgDn scroll": "↑/↓ scroll"}
        line = " · ".join(h for _, h in hints)
        if len(line) > width:
            hints = [(p, short_forms.get(h, h)) for p, h in hints]
            line = " · ".join(h for _, h in hints)
        for drop in (4, 3, 2, 1):
            if len(line) <= width:
                break
            hints = [(p, h) for p, h in hints if p != drop]
            line = " · ".join(h for _, h in hints)
        self.query_one("#keys", Static).update(Text(line, style=SECONDARY))

    # ---- focus / panes ----
    def _active_table(self) -> DataTable:
        return self.query_one({"radar": "#radar", "traders": "#traders", "signals": "#signals"}.get(self.screen_mode, "#feed"), DataTable)

    def _panes(self) -> list[Any]:
        table = self._active_table()
        panes: list[Any] = [table, self.query_one("#detail", VerticalScroll)]
        search = self.query_one("#search", Input)
        if search.has_class("visible"):
            panes.append(search)
        return panes

    def action_cycle_pane(self, direction: int) -> None:
        panes = self._panes()
        cur = self.focused
        idx = panes.index(cur) if cur in panes else -1
        panes[(idx + direction) % len(panes)].focus()

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        self._focus_changed()

    def on_descendant_blur(self, event: events.DescendantBlur) -> None:
        self._focus_changed()

    def _focus_changed(self) -> None:
        try:
            self.render_feedhead()
            self.render_keys()
        except NoMatches:
            pass

    # ---- selection / actions ----
    def _selected_event(self) -> dict[str, Any] | None:
        table = self.query_one("#feed", DataTable)
        if table.row_count == 0:
            return None
        try:
            row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
            return self.events.get(int(str(row_key.value)))
        except Exception:  # noqa: BLE001
            return None

    def on_data_table_row_highlighted(self, ev: DataTable.RowHighlighted) -> None:
        table = ev.data_table
        if table.id == "feed":
            if table.cursor_row >= table.row_count - 1:
                self.follow = True
                self.unseen = 0
            else:
                self.follow = False
            if self.detail_mode == "summary":
                self.render_detail()
            self.render_feedhead()
        elif table.id in ("radar", "traders", "signals") and self.detail_mode == "summary":
            self.render_detail()

    def on_data_table_row_selected(self, ev: DataTable.RowSelected) -> None:
        self._open_selected()

    def action_open(self) -> None:
        search = self.query_one("#search", Input)
        if search.has_focus:
            self.query_one("#feed", DataTable).focus()
            return
        self._open_selected()

    def _open_selected(self) -> None:
        if self.screen_mode == "signals":
            a = self._selected_signal_row()
            if a:
                self.coin_addr = a["token"]
                self.detail_mode = "coin"
                self.coin_doc = None
                self.query_one("#detailbody", Static).update(Text(f"{a.get('symbol') or '?'}\nloading coin card…", style=SECONDARY))
                self.render_detailhead()
                self.load_coin(a["token"])
            return
        if self.screen_mode == "traders":
            r = self._selected_trader_row()
            if r:
                self.wallet_addr = r["wallet"]
                self.detail_mode = "wallet"
                self.wallet_doc = None
                self.query_one("#detailbody", Static).update(Text(f"{short(r['wallet'])}\nloading wallet card…", style=SECONDARY))
                self.render_detailhead()
                self.load_wallet(r["wallet"])
            return
        if self.screen_mode == "radar":
            r = self._selected_radar_row()
            if r:
                self.coin_addr = r["address"]
                self.detail_mode = "coin"
                self.coin_doc = None
                self.query_one("#detailbody", Static).update(Text(f"{r['symbol']}\nloading coin card…", style=SECONDARY))
                self.render_detailhead()
                self.load_coin(r["address"])
            return
        row = self._selected_event()
        if not row:
            return
        self.selected_pair = (row["from"], row["to"])
        self.detail_mode = "evidence"
        self.edge_doc = None
        self.query_one("#detailbody", Static).update(Text(f"{row['from_symbol']} → {row['to_symbol']}\nloading evidence…", style=SECONDARY))
        self.render_detailhead()
        self.load_edge(row["from"], row["to"])

    @work(thread=True, exclusive=True, group="edge")
    def load_edge(self, a: str, b: str) -> None:
        s = self.session or {}
        clock = s.get("clock_ts")
        w = int(s.get("window_s") or 1800)
        span = int(s.get("span_s") or 1800)
        try:
            doc = self.client.edge(a, b, w, (clock - span) if clock else None, clock, limit=40)
            self._deliver(self._edge_loaded, doc)
        except ApiError as e:
            self._deliver(self.apply_error, str(e))

    def _edge_loaded(self, doc: dict[str, Any]) -> None:
        self.edge_doc = doc
        if self.detail_mode == "evidence":
            self.render_detail()
            self.render_keys()

    def action_back(self) -> None:
        search = self.query_one("#search", Input)
        table = self._active_table()
        if search.has_class("visible"):
            search.value = ""
            self.filter_text = ""
            search.set_class(False, "visible")
            search.can_focus = False
            table.focus()
            self.rebuild_rows()
            return
        if self.detail_mode != "summary":
            self.detail_mode = "summary"
            self.edge_doc = None
            self.coin_doc = None
            self.wallet_doc = None
            self.render_detail()
            self.render_keys()
            return
        if self.focused is not table:
            table.focus()

    def action_search(self) -> None:
        if self.screen_mode != "feed":
            self.action_screen("feed")
        search = self.query_one("#search", Input)
        search.set_class(True, "visible")
        search.can_focus = True
        search.focus()

    def on_input_changed(self, ev: Input.Changed) -> None:
        self.filter_text = ev.value
        self.rebuild_rows()

    def on_input_submitted(self, ev: Input.Submitted) -> None:
        self.query_one("#feed", DataTable).focus()

    def _edge_key(self, last: bool) -> None:
        """Home/End: in the search box move the text cursor, in the details scroll, in a table jump to the first/last row."""
        focused = self.focused
        fid = focused.id if focused is not None else None
        if fid == "search":
            focused.action_end() if last else focused.action_home()
            return
        if fid == "detail":
            focused.scroll_end(animate=False) if last else focused.scroll_home(animate=False)
            return
        if self.screen_mode in ("radar", "traders", "signals"):
            rt = self._active_table()
            if rt.row_count:
                rt.move_cursor(row=rt.row_count - 1 if last else 0, scroll=True)
            return
        table = self.query_one("#feed", DataTable)
        if last:
            self.follow = True
            self.unseen = 0
        if table.row_count:
            table.move_cursor(row=table.row_count - 1 if last else 0, scroll=True)
        self.render_feedhead()

    def action_follow(self) -> None:
        self._edge_key(last=True)

    def action_first_row(self) -> None:
        self._edge_key(last=False)

    def action_toggle_play(self) -> None:
        if self.query_one("#search", Input).has_focus:
            return
        self._control("toggle")

    def action_seek(self, delta: int) -> None:
        s = self.session or {}
        if s.get("clock_ts") is None:
            return
        self._control("seek", ts=int(s["clock_ts"]) + delta)

    def action_speed(self, direction: int) -> None:
        s = self.session or {}
        speeds = [1, 2, 5, 10, 20, 60, 120]
        cur = float(s.get("speed") or 10)
        idx = min(range(len(speeds)), key=lambda i: abs(speeds[i] - cur))
        idx = max(0, min(len(speeds) - 1, idx + direction))
        self._control("speed", speed=speeds[idx])

    @work(thread=True, group="control")
    def _control(self, action: str, **kw: Any) -> None:
        try:
            sess = self.client.control(action, **kw)
            self._deliver(self._control_done, sess)
        except ApiError as e:
            self._deliver(lambda m: self.notify(m, severity="warning"), str(e))

    def _control_done(self, sess: dict[str, Any]) -> None:
        self.session = sess
        self.render_status()
        self.render_keys()
        self.poll_tick()

    def on_unmount(self) -> None:
        if self.stream is not None:
            try:
                self.stream.stop()
            except Exception:  # noqa: BLE001
                pass


def main_terminal(args) -> int:
    client = ApiClient(args.api_url)
    try:
        client.session()
    except ApiError as e:
        print(f"API not reachable at {args.api_url}: {e}\nStart it first: uv run stampede demo   (or: python scripts/serve_daemon.py start --mode replay)", flush=True)
        return 2
    app = StampedeTUI(client, poll_s=args.poll)
    app.run()
    return 0
