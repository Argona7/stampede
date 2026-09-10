"""`stampede terminal`: full-screen TUI on top of the running API.

Black / red terminal: red block wordmark, white and grey working text, dark-red hairlines, red only for
the brand, the selected row marker and the newest confirmed rows. Everything shown is read from the API
(shared session clock, event stream with cursor, edge evidence); nothing is simulated.
"""
from __future__ import annotations

import time
from collections import deque
from datetime import datetime, timezone
from typing import Any

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
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


def utc(ts: int | None) -> str:
    if not ts:
        return "--:--:--"
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%H:%M:%S")


def short(a: str) -> str:
    return f"{a[:6]}…{a[-4:]}" if a and len(a) > 12 else a


def dur(s: int | None) -> str:
    if s is None:
        return "?"
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m{s % 60:02d}s"
    return f"{s // 3600}h{(s % 3600) // 60:02d}m"


class StampedeTUI(App):
    TITLE = "STAMPEDE"
    CSS = f"""
    Screen {{ background: {BG}; color: {TEXT}; }}
    #brand {{ color: {PRIMARY}; height: auto; padding: 0 1; text-style: bold; }}
    #statusline {{ height: 1; color: {SECONDARY}; padding: 0 1; }}
    #alert {{ height: auto; display: none; padding: 0 1; background: {PRIMARY}; color: {BG}; text-style: bold; }}
    #alert.visible {{ display: block; }}
    #rule1 {{ height: 1; color: {BORDER}; padding: 0 1; }}
    #main {{ height: 1fr; }}
    #feedwrap {{ width: 3fr; }}
    #feedhead {{ height: 1; color: {SECONDARY}; padding: 0 1; }}
    #feed {{ height: 1fr; background: {BG}; }}
    #empty {{ height: auto; color: {SECONDARY}; padding: 1 2; display: none; }}
    #empty.visible {{ display: block; }}
    #detail {{ width: 2fr; padding: 0 1; border-left: solid {BORDER}; }}
    #bottom {{ height: 11; border-top: solid {BORDER}; }}
    #hot {{ width: 3fr; padding: 0 1; }}
    #activity {{ width: 2fr; padding: 0 1; border-left: solid {BORDER}; }}
    #spark {{ height: 3; margin: 0; }}
    #spark > .sparkline--max-color {{ color: {PRIMARY}; }}
    #spark > .sparkline--min-color {{ color: {MUTED}; }}
    #keys {{ height: 1; color: {SECONDARY}; background: {SURFACE}; padding: 0 1; }}
    #search {{ height: 3; display: none; border: solid {BORDER}; background: {SURFACE}; color: {TEXT}; }}
    #search.visible {{ display: block; }}
    DataTable {{ background: {BG}; color: {TEXT}; scrollbar-color: {BORDER}; scrollbar-background: {BG}; }}
    DataTable > .datatable--header {{ background: {BG}; color: {SECONDARY}; text-style: none; }}
    DataTable > .datatable--cursor {{ background: {ACTIVE}; color: {TEXT}; text-style: none; }}
    DataTable > .datatable--hover {{ background: {SURFACE}; }}
    DataTable > .datatable--fixed {{ background: {BG}; color: {SECONDARY}; }}
    """
    BINDINGS = [
        Binding("q", "quit", "quit"),
        Binding("enter", "open", "open pair", priority=True),
        Binding("escape", "back", "back", priority=True),
        Binding("slash", "search", "search"),
        Binding("space", "toggle_play", "play/pause", priority=True),
        Binding("left", "seek(-60)", "seek -60s"),
        Binding("right", "seek(60)", "seek +60s"),
        Binding("bracketleft", "speed(-1)", "slower"),
        Binding("bracketright", "speed(1)", "faster"),
        Binding("end,f", "follow", "follow tail"),
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
        self._sym_w = 14
        self._columns_narrow: bool | None = None

    # ---- layout ----
    def compose(self) -> ComposeResult:
        yield Static(id="brand")
        yield Static(id="statusline")
        yield Static(id="alert")
        yield Static(id="rule1")
        with Horizontal(id="main"):
            with Vertical(id="feedwrap"):
                yield Static(id="feedhead")
                yield Input(placeholder="filter by symbol or address, Esc to clear", id="search")
                yield Static(id="empty")
                yield DataTable(id="feed", cursor_type="row", zebra_stripes=False, show_row_labels=False)
            yield VerticalScroll(Static(id="detailbody"), id="detail")
        with Horizontal(id="bottom"):
            yield Static(id="hot")
            with Vertical(id="activity"):
                yield Static(id="acthead")
                yield Sparkline([], id="spark", summary_function=max)
                yield Static(id="actfoot")
        yield Static(id="keys")

    def on_mount(self) -> None:
        table = self.query_one("#feed", DataTable)
        self._setup_columns(table)
        self.query_one("#search", Input).can_focus = False
        table.focus()
        self.render_brand()
        self.render_status()
        self.render_detail()
        self.render_keys()
        self.render_hot()
        self.poll_tick()
        self.set_interval(self.poll_s, self.poll_tick)
        self.set_interval(1.0, self.render_status)

    def _narrow(self) -> bool:
        return self.size.width < 150

    def _setup_columns(self, table: DataTable) -> None:
        narrow = self._narrow()
        self._sym_w = 10 if narrow else 14
        table.add_column("", key="mark", width=1)
        table.add_column("TIME UTC", key="time", width=9)
        table.add_column("WALLET", key="wallet", width=11)
        table.add_column("SOLD", key="sold", width=self._sym_w)
        table.add_column("", key="arrow", width=1)
        table.add_column("BOUGHT", key="bought", width=self._sym_w)
        table.add_column("GAP", key="gap", width=6 if narrow else 8)
        table.add_column("GRADE", key="grade", width=6 if narrow else 9)
        self._columns_narrow = narrow

    def on_resize(self) -> None:
        self.render_brand()
        self.render_status()
        if getattr(self, "_columns_narrow", None) is not None and self._columns_narrow != self._narrow():
            table = self.query_one("#feed", DataTable)
            table.clear(columns=True)
            self._setup_columns(table)
            self.rebuild_rows()

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
            ev = self.client.events(w, clock, after, limit=500, backfill_s=span)
            pages = [ev]
            while ev.get("has_more") and len(pages) < 10:
                ev = self.client.events(w, clock, ev["next_cursor"], limit=500, backfill_s=span)
                pages.append(ev)
            out["events"] = pages
            out["seek"] = seek
            if with_graph or self.graph_doc is None:
                fr = (clock - span) if clock is not None else None
                out["graph"] = self.client.graph(w, fr, clock, 1, 10)
            self.call_from_thread(self.apply_update, out)
        except ApiError as e:
            self.call_from_thread(self.apply_error, str(e))
        except Exception as e:  # noqa: BLE001
            self.call_from_thread(self.apply_error, f"{type(e).__name__}: {e}")

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
        self._pending_poll = False
        self.api_error = None
        self.error_count = 0
        if "status" in out:
            self.status_doc = out["status"]
        self.session = out["session"]
        self.last_clock = self.session.get("clock_ts")
        self.last_clock_wall = time.time()
        table = self.query_one("#feed", DataTable)
        pages = out.get("events", [])
        if out.get("seek") or self.cursor is None:
            # history rebuild: one clear per seek, rows are not "fresh"
            table.clear()
            self.events.clear()
            self.fresh_ids.clear()
            self.follow = True
        else:
            self.fresh_ids.clear()
            for eid in list(self.events):
                if self.events[eid].get("_fresh"):
                    self.events[eid]["_fresh"] = False
                    self._refresh_mark(table, eid)
        new_ids: list[int] = []
        for page in pages:
            for e in page.get("events", []):
                if e["id"] in self.events:
                    continue
                e["_fresh"] = page.get("kind") == "new"
                self.events[e["id"]] = e
                if self._matches_filter(e):
                    self._add_row(table, e)
                if e["_fresh"]:
                    new_ids.append(e["id"])
            if page.get("next_cursor"):
                self.cursor = page["next_cursor"]
        self.fresh_ids = set(new_ids)
        self.last_events_kind = "new" if new_ids else "history"
        if self.follow and table.row_count:
            table.move_cursor(row=table.row_count - 1, scroll=True)
        if "graph" in out:
            self.graph_doc = out["graph"]
            self.render_hot()
        self.render_status()
        self.render_feedhead()
        self.render_activity()
        self.query_one("#empty", Static).set_class(table.row_count == 0, "visible")
        self.query_one("#empty", Static).update(Text("NO OBSERVED SEQUENCES IN THE VISIBLE RANGE. Widen the range or wait for the clock.", style=SECONDARY) if table.row_count == 0 else "")
        if self.detail_mode == "summary":
            self.render_detail()

    def apply_error(self, msg: str) -> None:
        self._pending_poll = False
        self.api_error = msg
        self.error_count += 1
        self.render_status()

    # ---- rows ----
    def _row_cells(self, e: dict[str, Any]) -> list[Text]:
        fresh = e.get("_fresh")
        return [
            Text("▌" if fresh else " ", style=PRIMARY),
            Text(utc(e["buy_ts"]) + ("" if e.get("buy_ts_exact") else "≈"), style=SECONDARY),
            Text(short(e["wallet"]), style=SECONDARY),
            Text(e["from_symbol"][: self._sym_w], style=TEXT),
            Text("→", style=PRIMARY if fresh else SECONDARY),
            Text(e["to_symbol"][: self._sym_w], style=TEXT),
            Text(("same" if self._narrow() else "same tx") if e["grade"] == "direct" else dur(e["gap_s"]), style=SECONDARY),
            Text(("amb." if self._narrow() else "ambiguous") if e["grade"] == "ambiguous" else e["grade"], style=TEXT if e["grade"] != "ambiguous" else MUTED),
        ]

    def _add_row(self, table: DataTable, e: dict[str, Any]) -> None:
        table.add_row(*self._row_cells(e), key=str(e["id"]))

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

    def rebuild_rows(self) -> None:
        table = self.query_one("#feed", DataTable)
        table.clear()
        for e in sorted(self.events.values(), key=lambda x: (x["buy_ts"], x["id"])):
            if self._matches_filter(e):
                self._add_row(table, e)
        if table.row_count:
            table.move_cursor(row=table.row_count - 1)
        self.render_feedhead()

    # ---- rendering ----
    def render_brand(self) -> None:
        w = self.size.width
        h = self.size.height
        if w >= brand.BIG_WIDTH + 4 and h >= 30:
            self.query_one("#brand", Static).update(Text("\n".join(brand.big()), style=f"bold {PRIMARY}"))
        else:
            self.query_one("#brand", Static).update(Text(brand.compact(), style=f"bold {PRIMARY}"))
        self.query_one("#rule1", Static).update(Text("─" * max(10, w - 2), style=BORDER))

    def render_status(self) -> None:
        s = self.session or {}
        st = self.status_doc or {}
        parts: list[Text] = []
        mode = (s.get("mode") or "?").upper()
        label = s.get("label") or mode
        chainname = (st.get("chain") or {}).get("name", "Robinhood Chain")
        parts.append(Text(f"CHAIN {chainname}", style=TEXT))
        parts.append(Text(f"[{label}]", style=TEXT))
        clock = s.get("clock_ts")
        if s.get("mode") == "live":
            age = (st.get("data") or {}).get("age_s")
            parts.append(Text(f"LAST BLOCK {utc(clock)} UTC", style=TEXT))
            parts.append(Text(f"DATA AGE {dur(age) if age is not None else '?'}", style=TEXT if (age or 0) <= STALE_AFTER_S else PRIMARY))
            if age is not None and age > STALE_AFTER_S:
                parts.append(Text("[STALE]", style=f"bold {TEXT}"))
        else:
            parts.append(Text(f"CLOCK {utc(clock)} UTC", style=TEXT))
        span = s.get("span_s") or 1800
        if clock:
            parts.append(Text(f"RANGE {utc(clock - span)}–{utc(clock)}", style=SECONDARY))
        parts.append(Text(f"WINDOW {dur(s.get('window_s') or 1800)}", style=SECONDARY))
        sid = s.get("id")
        if sid:
            parts.append(Text(f"SESSION {sid}", style=MUTED))
        line = Text("  ·  ", style=BORDER).join(parts) if parts else Text("connecting…", style=SECONDARY)
        self.query_one("#statusline", Static).update(line)
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

    def render_feedhead(self) -> None:
        n = len(self.events)
        shown = self.query_one("#feed", DataTable).row_count
        s = self.session or {}
        span = dur(s.get("span_s") or 1800)
        flt = f" · FILTER '{self.filter_text}'" if self.filter_text else ""
        fresh = f" · +{len(self.fresh_ids)} new" if self.fresh_ids else ""
        self.query_one("#feedhead", Static).update(Text(f"OBSERVED SEQUENCES · last {span} · {shown} of {n} rows{flt}{fresh} · one row = one wallet that sold A, then bought B · ≈ interpolated block time", style=SECONDARY))

    def render_detail(self) -> None:
        body = self.query_one("#detailbody", Static)
        t = Text()
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
            for s in d["sequences"]:
                sl, by = s["sell"], s["buy"]
                t.append(f"{short(s['wallet'])} ", style=TEXT)
                t.append(f"[{s['grade']}] ", style=TEXT if s["grade"] != "ambiguous" else MUTED)
                t.append(f"gap {dur(s['gap_s'])}\n", style=SECONDARY)
                t.append(f"  sold   {utc(sl['ts'])}{'' if sl['ts_exact'] else '≈'} block {sl['block']} tx {short(sl['tx'])}\n", style=SECONDARY)
                t.append(f"  bought {utc(by['ts'])}{'' if by['ts_exact'] else '≈'} block {by['block']} tx {short(by['tx'])}\n", style=SECONDARY)
            t.append("\n≈ interpolated block time · Esc back", style=MUTED)
        else:
            row = self._selected_event()
            if row is None:
                t.append("SELECTED PAIR\n\n", style=SECONDARY)
                t.append("Move with ↑/↓, press Enter to open the pair.\n\n", style=SECONDARY)
                t.append("Edge A → B: distinct wallets that sold A, then bought B within the pairing window. Same address, observed order of trades; not proof of money flow or shared ownership.", style=MUTED)
            else:
                t.append(f"{row['from_symbol']} → {row['to_symbol']}\n", style=f"bold {TEXT}")
                t.append(f"{row['from_short']} → {row['to_short']}\n\n", style=SECONDARY)
                t.append(f"wallet {short(row['wallet'])}\n", style=TEXT)
                t.append(f"sold   {utc(row['sell_ts'])} UTC\n", style=SECONDARY)
                t.append(f"bought {utc(row['buy_ts'])}{'' if row.get('buy_ts_exact') else '≈'} UTC · gap {dur(row['gap_s'])} · {row['grade']}\n", style=SECONDARY)
                t.append(f"sell tx {short(row['sell_tx'])}\nbuy tx  {short(row['buy_tx'])}\n\n", style=SECONDARY)
                t.append("Enter: all wallets on this pair", style=MUTED)
        body.update(t)

    def render_hot(self) -> None:
        g = self.graph_doc
        t = Text()
        s = self.session or {}
        span = dur(s.get("span_s") or 1800)
        t.append(f"HOT ROTATIONS · distinct wallets, last {span}", style=SECONDARY)
        if g is None:
            t.append("\n…", style=MUTED)
        else:
            now = time.time()
            edges = g.get("edges", [])[:7]
            if not edges:
                t.append("\nno edges in range", style=MUTED)
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
                t.append(f"{a[:12]:<12} → {b[:12]:<12}", style=TEXT)
                t.append(f"  {e['sequences']} seq", style=SECONDARY)
                if dtxt:
                    t.append(f"  {dtxt} since last refresh", style=PRIMARY if delta > 0 else SECONDARY)
            if self.hot_prev_at:
                t.append(f"\nΔ measured over {now - self.hot_prev_at:.0f}s between refreshes", style=MUTED)
            self.hot_prev = cur
            self.hot_prev_at = now
        self.query_one("#hot", Static).update(t)

    def render_activity(self) -> None:
        s = self.session or {}
        clock = s.get("clock_ts")
        span = int(s.get("span_s") or 1800)
        if clock is None:
            return
        nb = max(1, span // BUCKET_S)
        buckets = [0.0] * nb
        lo = clock - span
        for e in self.events.values():
            i = int((e["buy_ts"] - lo) // BUCKET_S)
            if 0 <= i < nb:
                buckets[i] += 1
        self.spark_values = buckets
        self.query_one("#spark", Sparkline).data = buckets
        self.query_one("#acthead", Static).update(Text(f"ACTIVITY · sequences per {BUCKET_S}s bucket · {nb} buckets" if self._narrow() else f"ACTIVITY · observed sequences per {BUCKET_S}s bucket · {nb} buckets", style=SECONDARY))
        self.query_one("#actfoot", Static).update(Text(f"{utc(lo)} → {utc(clock)} UTC · peak {int(max(buckets)) if buckets else 0}/bucket · total {int(sum(buckets))}", style=SECONDARY))

    def render_keys(self) -> None:
        s = self.session or {}
        ctl = " · space play/pause · ←/→ seek 60s · [ ] speed" if s.get("controls") else ""
        self.query_one("#keys", Static).update(Text(f"↑/↓ select · Enter open · Esc back · / search · End follow tail{ctl} · q quit", style=SECONDARY))

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
        table = self.query_one("#feed", DataTable)
        self.follow = table.cursor_row >= table.row_count - 1
        if self.detail_mode == "summary":
            self.render_detail()

    def action_open(self) -> None:
        if self.query_one("#search", Input).has_focus:
            self.query_one("#search", Input).blur()
            self.query_one("#feed", DataTable).focus()
            return
        row = self._selected_event()
        if not row:
            return
        self.selected_pair = (row["from"], row["to"])
        self.detail_mode = "evidence"
        self.edge_doc = None
        self.query_one("#detailbody", Static).update(Text(f"{row['from_symbol']} → {row['to_symbol']}\nloading evidence…", style=SECONDARY))
        self.load_edge(row["from"], row["to"])

    @work(thread=True, exclusive=True, group="edge")
    def load_edge(self, a: str, b: str) -> None:
        s = self.session or {}
        clock = s.get("clock_ts")
        w = int(s.get("window_s") or 1800)
        span = int(s.get("span_s") or 1800)
        try:
            doc = self.client.edge(a, b, w, (clock - span) if clock else None, clock, limit=40)
            self.call_from_thread(self._edge_loaded, doc)
        except ApiError as e:
            self.call_from_thread(self.apply_error, str(e))

    def _edge_loaded(self, doc: dict[str, Any]) -> None:
        self.edge_doc = doc
        if self.detail_mode == "evidence":
            self.render_detail()

    def action_back(self) -> None:
        search = self.query_one("#search", Input)
        if search.has_class("visible"):
            search.value = ""
            self.filter_text = ""
            search.set_class(False, "visible")
            search.can_focus = False
            self.query_one("#feed", DataTable).focus()
            self.rebuild_rows()
            return
        self.detail_mode = "summary"
        self.edge_doc = None
        self.render_detail()

    def action_search(self) -> None:
        search = self.query_one("#search", Input)
        search.set_class(True, "visible")
        search.can_focus = True
        search.focus()

    def on_input_changed(self, ev: Input.Changed) -> None:
        self.filter_text = ev.value
        self.rebuild_rows()

    def on_input_submitted(self, ev: Input.Submitted) -> None:
        self.query_one("#feed", DataTable).focus()

    def action_follow(self) -> None:
        self.follow = True
        table = self.query_one("#feed", DataTable)
        if table.row_count:
            table.move_cursor(row=table.row_count - 1, scroll=True)

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
            self.call_from_thread(self._control_done, sess)
        except ApiError as e:
            self.call_from_thread(self.notify, str(e), severity="warning")

    def _control_done(self, sess: dict[str, Any]) -> None:
        self.session = sess
        self.render_status()
        self.render_keys()
        self.poll_tick()

    def on_unmount(self) -> None:
        pass


def main_terminal(args) -> int:
    client = ApiClient(args.api_url)
    try:
        client.session()
    except ApiError as e:
        print(f"API not reachable at {args.api_url}: {e}\nStart it first: python scripts/serve_daemon.py start --mode replay", flush=True)
        return 2
    app = StampedeTUI(client, poll_s=args.poll)
    app.run()
    return 0
