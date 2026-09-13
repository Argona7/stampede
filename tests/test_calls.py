"""STAMPEDE Calls (stampede/calls): rule, formatting, outcome replies, Bot API retries, catch-up / resume without
duplicates, the daily summary. No network: the Telegram and engine HTTP sessions are fakes."""
from __future__ import annotations

import json
from typing import Any

import pytest
import requests

from stampede.calls import format as fmt
from stampede.calls.poster import CallsStore, LaunchFacts, Perf, Poster, Rule, Telegram, TelegramError, journal_to_fired, load_config, sse_frames

TOKEN = "0x3bd73a113b6543402e30a6fb48b79cb006d039ac"
PLAN = ["size 0.0150 ETH (cap_per_trade)", "sell 50% at +100%", "then trail 25% below the high", "stop at −30%", "out after 45 min whatever the price", "exit now on: sell pressure spike, deployer / exempt wallet selling, liquidity drop, graduation, rotation inflow stops for 5 min"]


def alert(clock: int = 1789283299, token: str = TOKEN, symbol: str = "STACK·39ac", p: float = 0.51, size: float = 0.015, **detail: Any) -> dict[str, Any]:
    d = {"rank": 1, "p_2x_30m": p, "p_minus50_30m": 0.22, "ev_per_trade_quote": 0.0024, "size_quote": size, "exit_plan": PLAN, "source": "model", "age_s": 300, "breadth": 4, "stage": "curve", "engine": "wss", "block": 61344094, "progress": 0.1234, "radar_rank": 3}
    d.update(detail)
    return {"kind": "fired", "key": f"{token}:{clock}", "created_ts": clock, "clock_ts": clock, "mode": "live", "token": token, "symbol": symbol, "rule": "edge_enter", "score": 78.6, "inflow": 22, "mentions_1h": 0, "price": 3.8e-9, "detail": d}


class FakeResponse:
    def __init__(self, status: int = 200, body: Any = None, lines: list[str] | None = None):
        self.status_code = status
        self._body = body if body is not None else {"ok": True, "result": {}}
        self._lines = lines or []

    def json(self) -> Any:
        if isinstance(self._body, Exception):
            raise self._body
        return self._body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def iter_lines(self, decode_unicode: bool = True):
        yield from self._lines

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeTelegramSession:
    """Answers sendMessage with increasing message ids; a queue of scripted responses comes first."""

    def __init__(self, scripted: list[FakeResponse] | None = None):
        self.scripted = list(scripted or [])
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.next_id = 100

    def post(self, url: str, json: dict[str, Any], timeout: float):
        method = url.rsplit("/", 1)[-1]
        self.calls.append((method, json))
        if self.scripted:
            return self.scripted.pop(0)
        if method == "sendMessage":
            self.next_id += 1
            return FakeResponse(200, {"ok": True, "result": {"message_id": self.next_id}})
        return FakeResponse(200, {"ok": True, "result": True})

    def sent(self) -> list[dict[str, Any]]:
        return [p for m, p in self.calls if m == "sendMessage"]


class FakeApiSession:
    """The engine's HTTP API: `/api/alerts`, `/api/track-record`, `/api/stream` (scripted frames)."""

    def __init__(self, alerts: list[dict[str, Any]] | None = None, track: dict[str, Any] | None = None, stream: list[list[str]] | None = None):
        self.alerts = alerts or []
        self.track = track or {}
        self.stream = list(stream or [])
        self.urls: list[str] = []

    def get(self, url: str, params: dict[str, Any] | None = None, timeout: Any = None, stream: bool = False, headers: Any = None):
        self.urls.append(url)
        if "/api/alerts" in url:
            return FakeResponse(200, {"alerts": self.alerts})
        if "/api/track-record" in url:
            return FakeResponse(200, self.track)
        if "/api/stream" in url:
            if not self.stream:
                raise requests.ConnectionError("no more scripted streams")
            return FakeResponse(200, lines=self.stream.pop(0))
        return FakeResponse(404, {})


def frame(eid: int, typ: str, data: dict[str, Any], ts_emit: float = 1789283299.2) -> list[str]:
    body = {"id": eid, "type": typ, "ts_emit": ts_emit, "block": 61344094, "data": data}
    return [f"id: {eid}", f"event: {typ}", "data: " + json.dumps(body), ""]


def hello(last: int = 0, gap: bool = False) -> list[str]:
    return ["event: session", "data: " + json.dumps({"id": None, "type": "session", "ts_emit": 1.0, "block": None, "data": {"hello": True, "engine": "wss", "last_event_id": last, "replay_gap": gap}}), ""]


def make(tmp_path, tg_session: FakeTelegramSession | None = None, api: FakeApiSession | None = None, launch: dict[str, Any] | None = None, now: float = 1789283300.0, dry_run: bool = False, store: CallsStore | None = None) -> tuple[Poster, FakeTelegramSession, FakeApiSession]:
    cfg = load_config()
    store = store or CallsStore(tmp_path / "live.sqlite")
    tgs = tg_session or FakeTelegramSession()
    api = api or FakeApiSession()
    tg = None if dry_run else Telegram("TOKEN", "-100123", session=tgs, sleep=lambda s: None)
    lf = LaunchFacts(None, None)
    if launch is not None:
        lf.cache[TOKEN] = launch
    else:
        lf.cache[TOKEN] = None
    clock = {"t": now}
    poster = Poster("http://engine", cfg, store, tg, lf, Perf(tmp_path / "perf.json"), dry_run=dry_run, session=api, channel_username="stampede_calls", now=lambda: clock["t"], sleep=lambda s: None)
    poster._clock = clock  # type: ignore[attr-defined]
    return poster, tgs, api


# ---------------------------------------------------------------- formatting
EXPECTED_CALL = "\n".join(
    [
        "<b>ENTER STACK·39ac</b>",
        f"<code>{TOKEN}</code>",
        "p(2× 30m) 51 %  ·  EV +0.0024 ETH / 0.02 ETH  ·  size 0.015 ETH",
        "inflow 22 wallets/10 min · age 5 min · curve 12 %",
        "launch: dev 2.1 % · bundle 1 · tax 25 bps",
        "exit: TP +100 % × 50 % · trail 25 % · stop −30 % · 45 min",
        "clock 07:08:19 UTC · block 61,344,094",  # 1789283299 = 2026-09-13 07:08:19 UTC
        f'<a href="https://pons.fun/token/{TOKEN}">pons</a> · <a href="https://robinhoodchain.blockscout.com/address/{TOKEN}">explorer</a> · <a href="https://www.geckoterminal.com/robinhood/tokens/{TOKEN}">GeckoTerminal</a>',
    ]
)


def test_call_message_exact():
    text = fmt.call_message(alert(), {"dev_buy_share": 0.021, "bundle_n": 1, "creator_tax_bps": 25, "launch_farm": False})
    assert text == EXPECTED_CALL


def test_call_message_without_launch_and_symbol_suffix():
    a = alert(symbol="STACK", age_s=None, progress=None)
    text = fmt.call_message(a, None)
    assert text.splitlines()[0] == "<b>ENTER STACK·39ac</b>"  # short address appended when the API did not
    assert "launch: n/a" in text
    assert "inflow 22 wallets/10 min · age n/a · curve n/a" in text
    assert fmt.call_message(alert(symbol="<b>x"), None).splitlines()[0] == "<b>ENTER &lt;b&gt;x·39ac</b>"  # symbols are escaped


def test_exit_line_from_plan_variants():
    assert fmt.exit_line({"tp": [{"gain": 1.0, "frac": 0.5}], "trail": 0.25, "sl": 0.3, "time_exit_s": 2700}) == "exit: TP +100 % × 50 % · trail 25 % · stop −30 % · 45 min"
    assert fmt.exit_line(None) == "exit: TP +100 % × 50 % · trail 25 % · stop −30 % · 45 min"
    assert fmt.exit_line(["sell 50% at +100%", "then trail 35% below the high", "stop at −30%", "out after 30 min whatever the price"]) == "exit: TP +100 % × 50 % · trail 35 % · stop −30 % · 30 min"


def test_outcome_and_exit_messages():
    assert fmt.outcome_message(30, 37.2) == "+30 min: +37 %"
    assert fmt.outcome_message(60, -12.4, graduated=True) == "+60 min: −12 % · graduated"
    assert fmt.outcome_message(30, None) == "+30 min: n/a"
    pos = {"exit_reason": "stop", "ret": -0.304, "pnl_quote": -0.006, "hold_s": 720}
    assert fmt.exit_message(pos) == "exit stop · −30 % · pnl −0.0060 ETH · 12 min\npaper, simulated"
    assert fmt.exit_message({"exit_reason": "trigger: sell pressure", "ret": 0.12, "pnl_quote": 0.0011, "hold_s": 60}).startswith("exit sell pressure · +12 % · pnl +0.0011 ETH · 1 min")


TRACK = {
    "alerts": {"by_rule": {"edge_enter": {"fired": 30, "with_outcome_30m": 27, "ge_2x_30m": 2, "median_30m": -5.78}}},
    "paper": {"stats": {"trades": 20, "hit_rate": 0.25, "expectancy_quote": -0.00148, "ci95_quote": [-0.003254, 0.00036]}},
    "perf": {"uptime_s": 44280, "blocks_processed": 4315, "gaps_found": 0, "latency_p95_ms": 176.0},
}


def test_summary_message_exact():
    text = fmt.summary_message(TRACK, {"posted": 7, "skipped": 3, "p50_ms": 612.0, "p95_ms": 1480.0}, now=1789290000)
    assert text == "\n".join(
        [
            "<b>STAMPEDE · 24 h</b> 2026-09-13 09:00 UTC",
            "calls posted 7 · skipped by rule 3 · post latency p50 612 ms / p95 1480 ms",
            "edge_enter alerts 30 · ≥ 2× at +30 min 2/27 (7 %) · median +30 min −5.8 %",
            "paper (simulated) 20 trades · hit 25 % · expectancy −0.0015 ETH · 95 % CI [−0.0033 ETH, +0.0004 ETH]",
            "engine uptime 12.3 h · blocks 4,315 · gaps 0 · block→emit p95 176.0 ms",
        ]
    )
    empty = fmt.summary_message({}, {"posted": 0, "skipped": 0, "p50_ms": None, "p95_ms": None}, now=1789290000)
    assert "post latency p50 n/a / p95 n/a" in empty and "engine: n/a" in empty and "paper (simulated) 0 trades · hit n/a" in empty


# ---------------------------------------------------------------- rule
def test_rule_filters(tmp_path):
    cfg = load_config()
    rule = Rule(cfg)
    store = CallsStore(tmp_path / "s.sqlite")
    assert rule.check(alert(), None, store) is None
    assert rule.check({**alert(), "rule": "under_radar_top5"}, None, store).startswith("rule")
    assert rule.check(alert(p=0.29), None, store).startswith("p 0.29")
    assert rule.check(alert(size=0), None, store) == "size 0"
    assert rule.check(alert(size=None), None, store) == "size 0"
    assert rule.check(alert(), {"launch_farm": True}, store) == "launch farm"
    assert rule.check(alert(), {"bundle_n": 5}, store) == "bundle 5 > 2"
    assert rule.check(alert(), {"bundle_n": 2, "dev_buy_share": 0.10}, store) is None
    assert rule.check(alert(), {"dev_buy_share": 0.11}, store).startswith("dev buy 11.0%")
    assert rule.check(alert(), {"launch_farm": None, "bundle_n": None, "dev_buy_share": None}, store) is None  # unknown never skips


def test_dedupe_and_per_hour(tmp_path):
    poster, tgs, _ = make(tmp_path)
    assert poster.handle_fired(alert(clock=1000), ts_emit=999.5) == "posted"
    assert poster.handle_fired(alert(clock=1000), ts_emit=999.5) == "duplicate"  # same key
    assert poster.handle_fired(alert(clock=1500), ts_emit=1499.5) == "skipped: same coin within 30 min"
    assert poster.handle_fired(alert(clock=2900), ts_emit=2899.5) == "posted"  # 1900 s later: allowed (2 calls in the hour now)
    assert poster.handle_fired(alert(clock=3100, token="0x" + "f" * 40, symbol="LATE"), ts_emit=3100.0) == "skipped: 2 calls in the hour"
    assert poster.handle_fired(alert(clock=1000 + 3601, token="0x" + "e" * 40, symbol="NEXT"), ts_emit=4601.0) == "posted"  # the first call left the hour
    assert poster.handle_fired({**alert(clock=5000), "rule": "under_radar_top5"}, ts_emit=5000.0) == "ignored" and poster.store.get(f"{TOKEN}:5000") is None
    assert len(tgs.sent()) == 3
    assert poster.store.get(f"{TOKEN}:1500")["skipped"] == "same coin within 30 min"
    assert poster.store.stats(0)["posted"] == 3 and poster.store.stats(0)["skipped"] == 2


# ---------------------------------------------------------------- outcomes
def test_outcome_and_exit_replies(tmp_path):
    poster, tgs, _ = make(tmp_path)
    assert poster.handle_fired(alert(), ts_emit=1789283299.2) == "posted"
    call_id = 101  # the fake's first message id
    assert tgs.sent()[0]["parse_mode"] == "HTML" and tgs.sent()[0]["disable_web_page_preview"] is True and tgs.sent()[0]["chat_id"] == "-100123"
    # an outcome for an unknown key is ignored
    assert poster.handle_outcome({"key": "0xdead:1", "outcome_30m": 5.0}) == []
    assert poster.handle_outcome({"key": f"{TOKEN}:1789283299", "token": TOKEN, "outcome_30m": 37.2}) == ["outcome_30_msg"]
    r = tgs.sent()[1]
    assert r["reply_to_message_id"] == call_id and r["text"] == "+30 min: +37 %"
    # the same outcome again: nothing; +60 with graduation: one reply
    assert poster.handle_outcome({"key": f"{TOKEN}:1789283299", "outcome_30m": 37.2}) == []
    assert poster.handle_outcome({"key": f"{TOKEN}:1789283299", "outcome_30m": 37.2, "outcome_60m": -12.4, "graduated_after": 1}) == ["outcome_60_msg"]
    assert tgs.sent()[2]["text"] == "+60 min: −12 % · graduated" and tgs.sent()[2]["reply_to_message_id"] == call_id
    # paper exit: matched by token + alert_ts; mark / opened events do nothing; a second close does nothing
    assert poster.handle_position({"kind": "mark", "token": TOKEN, "alert_ts": 1789283299}) is False
    assert poster.handle_position({"kind": "closed", "token": TOKEN, "alert_ts": 1789283299, "exit_reason": "stop", "ret": -0.304, "pnl_quote": -0.006, "hold_s": 720}) is True
    assert tgs.sent()[3]["text"].startswith("exit stop · −30 % · pnl −0.0060 ETH · 12 min") and tgs.sent()[3]["reply_to_message_id"] == call_id
    assert poster.handle_position({"kind": "closed", "token": TOKEN, "alert_ts": 1789283299, "exit_reason": "stop"}) is False
    assert poster.handle_position({"kind": "closed", "token": TOKEN, "alert_ts": 5, "exit_reason": "stop"}) is False
    row = poster.store.get(f"{TOKEN}:1789283299")
    assert row["outcome_30_msg"] == 102 and row["outcome_60_msg"] == 103 and row["exit_msg"] == 104
    assert row["latency_ms"] == pytest.approx(800.0)


# ---------------------------------------------------------------- Bot API
def test_telegram_retries_on_429_and_5xx():
    slept: list[float] = []
    s = FakeTelegramSession([FakeResponse(429, {"ok": False, "parameters": {"retry_after": 3}}), FakeResponse(502, {"ok": False}), FakeResponse(200, {"ok": True, "result": {"message_id": 7}})])
    tg = Telegram("T", "-1", session=s, sleep=slept.append)
    assert tg.send("x") == 7
    assert slept[0] == 3.0 and len(slept) == 2 and tg.retries == 2
    assert len(s.calls) == 3 and all(m == "sendMessage" for m, _ in s.calls)


def test_telegram_gives_up_and_rejects_400():
    s = FakeTelegramSession([FakeResponse(400, {"ok": False, "description": "Bad Request: message is too long"})])
    tg = Telegram("T", "-1", session=s, sleep=lambda x: None)
    with pytest.raises(TelegramError, match="too long"):
        tg.send("x")
    s2 = FakeTelegramSession([FakeResponse(500, {"ok": False})] * 3)
    with pytest.raises(TelegramError, match="gave up"):
        Telegram("T", "-1", session=s2, sleep=lambda x: None, max_tries=3).send("x")
    s3 = FakeTelegramSession([FakeResponse(200, {"ok": True, "result": True})])
    assert Telegram("T", "-1", session=s3).delete(5) is True and s3.calls[0] == ("deleteMessage", {"chat_id": "-1", "message_id": 5})


# ---------------------------------------------------------------- catch-up / resume
def journal_row(a: dict[str, Any], aid: int, **extra: Any) -> dict[str, Any]:
    return {"id": aid, "created_ts": a["created_ts"], "clock_ts": a["clock_ts"], "mode": "live", "token": a["token"], "symbol": a["symbol"], "rule": a["rule"], "score": a["score"], "inflow": a["inflow"], "mentions_1h": 0, "price": a["price"], "detail": a["detail"], "outcome_30m": None, "outcome_60m": None, "graduated_after": None, **extra}


def test_catch_up_then_stream_without_duplicates(tmp_path):
    now = 1789283300.0
    old = alert(clock=int(now) - 4000, token="0x" + "a" * 40, symbol="OLD")  # older than catchup_s: not posted
    recent = alert(clock=int(now) - 600)
    radar = {**alert(clock=int(now) - 500, token="0x" + "b" * 40, symbol="RADAR"), "rule": "under_radar_top5"}
    journal = [journal_row(radar, 3), journal_row(recent, 2), journal_row(old, 1)]  # newest first, like /api/alerts
    api = FakeApiSession(alerts=journal, track=TRACK)
    poster, tgs, api = make(tmp_path, api=api, now=now)
    res = poster.catch_up()
    assert res == {"posted": 1, "skipped": 0, "duplicate": 0, "outcomes": 0}
    assert len(tgs.sent()) == 1 and "ENTER STACK·39ac" in tgs.sent()[0]["text"]
    assert poster.store.get(recent["key"])["source"] == "catchup" and poster.store.get(recent["key"])["latency_ms"] is None
    # the same alert replayed by the stream (Last-Event-ID resume) is a duplicate; outcome and exit map to the catch-up post
    poster.handle_frame({"id": 11, "type": "alert", "ts_emit": now, "block": 1, "data": recent})
    poster.handle_frame({"id": 12, "type": "alert", "ts_emit": now, "block": 1, "data": {"kind": "outcome", "key": recent["key"], "token": TOKEN, "outcome_30m": 12.5}})
    poster.handle_frame({"id": 13, "type": "position", "ts_emit": now, "block": 1, "data": {"kind": "closed", "token": TOKEN, "alert_ts": recent["clock_ts"], "exit_reason": "trail", "ret": 0.41, "pnl_quote": 0.004, "hold_s": 900}})
    assert [p["text"] for p in tgs.sent()[1:]] == ["+30 min: +12 %", "exit trail · +41 % · pnl +0.0040 ETH · 15 min\npaper, simulated"]
    assert poster.last_id == 13 and poster.stream_url().endswith("types=alert,position,session&last_event_id=13")
    # a restart on the same store: the journal now carries the +60 outcome -> only that reply is added
    journal[1] = journal_row(recent, 2, outcome_30m=12.5, outcome_60m=-8.0, graduated_after=0)
    poster2, tgs2, _ = make(tmp_path, api=FakeApiSession(alerts=journal, track=TRACK), now=now + 60, store=CallsStore(tmp_path / "live.sqlite"))
    assert poster2.catch_up() == {"posted": 0, "skipped": 0, "duplicate": 1, "outcomes": 1}
    assert [p["text"] for p in tgs2.sent()] == ["+60 min: −8 %"] and tgs2.sent()[0]["reply_to_message_id"] == 101


def test_run_loop_resumes_and_reconnects(tmp_path):
    now = 1789283300.0
    a1 = alert(clock=int(now) - 10)
    a2 = alert(clock=int(now) - 5, token="0x" + "c" * 40, symbol="TWO")
    stream1 = hello(10) + frame(11, "alert", a1, ts_emit=now - 0.4)
    stream2 = hello(11) + frame(11, "alert", a1, ts_emit=now - 0.4) + frame(12, "alert", a2, ts_emit=now - 0.3) + frame(13, "session", {"clock_ts": int(now), "lag_ms": 120})
    api = FakeApiSession(alerts=[], track=TRACK, stream=[stream1, stream2])
    poster, tgs, api = make(tmp_path, api=api, now=now)
    orig = poster.handle_frame

    def stop_after(ev):
        orig(ev)
        if ev.get("id") == 13:
            poster.stop = True

    poster.handle_frame = stop_after  # type: ignore[method-assign]
    poster.run()
    assert [p["text"].splitlines()[0] for p in tgs.sent()] == ["<b>ENTER STACK·39ac</b>", "<b>ENTER TWO·cccc</b>"]  # the replayed 11 was a duplicate
    assert poster.reconnects == 1 and "last_event_id=11" in api.urls[-1] and poster.session_state.get("lag_ms") == 120
    assert poster.perf.snapshot()["n"] == 2 and poster.perf.snapshot()["p95_ms"] == pytest.approx(400.0, abs=1)
    assert json.loads((tmp_path / "perf.json").read_text())["n"] == 2


def test_replay_gap_triggers_journal_reread_and_fresh_connect(tmp_path):
    now = 1789283300.0
    a1 = alert(clock=int(now) - 100)
    api = FakeApiSession(alerts=[journal_row(a1, 1)], track=TRACK, stream=[hello(50, gap=True) + frame(51, "session", {"clock_ts": int(now)}), hello(50) + frame(51, "session", {"clock_ts": int(now)})])
    poster, tgs, api = make(tmp_path, api=api, now=now)
    poster.last_id = 5
    orig = poster.handle_frame

    def stop_after(ev):
        orig(ev)
        if ev.get("id") == 51:
            poster.stop = True

    poster.handle_frame = stop_after  # type: ignore[method-assign]
    poster.run()
    assert len(tgs.sent()) == 1 and sum("/api/alerts" in u for u in api.urls) == 2  # start-up catch-up + the gap re-read, one post
    streams = [u for u in api.urls if "/api/stream" in u]
    assert "last_event_id=5" in streams[0] and "last_event_id" not in streams[1] and poster.resyncs == 1 and poster.reconnects == 0  # reconnected at once without the stale id


def test_engine_restart_resets_cursor(tmp_path):
    """The engine process restarted: its counter starts at 1 while the poster still holds 7614. The hello's
    `last_event_id` is behind ours (and `started_at` changed): drop the cursor, re-read the journal, reconnect fresh."""
    now = 1789283300.0
    a1 = alert(clock=int(now) - 100)
    a2 = alert(clock=int(now) - 50, token="0x" + "d" * 40, symbol="AFTER")
    old_hello = ["event: session", "data: " + json.dumps({"id": None, "type": "session", "ts_emit": 1.0, "block": None, "data": {"hello": True, "engine": "wss", "last_event_id": 7614, "replay_gap": False, "started_at": 1000.0}}), ""]
    new_hello = lambda last: ["event: session", "data: " + json.dumps({"id": None, "type": "session", "ts_emit": 1.0, "block": None, "data": {"hello": True, "engine": "wss", "last_event_id": last, "replay_gap": False, "started_at": 2000.0}}), ""]
    api = FakeApiSession(alerts=[journal_row(a1, 1)], track=TRACK, stream=[old_hello + frame(7614, "session", {"clock_ts": int(now)}), new_hello(40) + frame(41, "alert", a1), new_hello(41) + frame(42, "alert", a2)])
    poster, tgs, api = make(tmp_path, api=api, now=now)
    orig = poster.handle_frame

    def stop_after(ev):
        orig(ev)
        if ev.get("id") == 42:
            poster.stop = True

    poster.handle_frame = stop_after  # type: ignore[method-assign]
    poster.run()
    streams = [u for u in api.urls if "/api/stream" in u]
    assert len(streams) == 3 and "last_event_id" not in streams[0] and "last_event_id=7614" in streams[1] and "last_event_id" not in streams[2]
    assert poster.resyncs == 1 and poster.reconnects == 1 and poster.server_started == 2000.0 and poster.last_id == 42
    assert [p["text"].splitlines()[0] for p in tgs.sent()] == ["<b>ENTER STACK·39ac</b>", "<b>ENTER AFTER·dddd</b>"]
    # an old server that only changed started_at (counter already past ours) is caught by started_at alone
    p2, _, _ = make(tmp_path, now=now, store=CallsStore(tmp_path / "other.sqlite"))
    p2.last_id, p2.server_started = 10, 1000.0
    assert p2.check_hello({"hello": True, "last_event_id": 5000, "started_at": 3000.0}) is True and p2.last_id is None and p2.resyncs == 1
    p2.last_id = 3
    assert p2.check_hello({"hello": True, "last_event_id": 5001, "started_at": 3000.0}) is False and p2.last_id == 3


def test_sse_frames_and_journal_shape():
    lines = [": keepalive", "id: 5", "event: alert", 'data: {"id": 5, "type": "alert", "data": {"kind": "fired"}}', "", "data: not json", "", "event: session", 'data: {"id": null, "type": "session", "data": {"hello": true}}', ""]
    out = list(sse_frames(lines))
    assert [f.get("id") for f in out] == [5, None]
    a = alert()
    j = journal_to_fired(journal_row(a, 9, outcome_30m=1.5))
    assert j["key"] == a["key"] and j["detail"] == a["detail"] and j["outcome_30m"] == 1.5 and j["kind"] == "fired"


# ---------------------------------------------------------------- summary / dry run
def test_daily_summary_once_per_day(tmp_path):
    now = 1789290000.0  # 2026-09-13 09:00 UTC
    poster, tgs, _ = make(tmp_path, api=FakeApiSession(alerts=[], track=TRACK), now=now - 3600)
    assert poster.maybe_daily_summary() is False  # 08:00: too early
    poster._clock["t"] = now  # type: ignore[attr-defined]
    assert poster.maybe_daily_summary() is True
    assert poster.maybe_daily_summary() is False  # once per day
    assert tgs.sent()[0]["text"].startswith("<b>STAMPEDE · 24 h</b> 2026-09-13 09:00 UTC")
    assert poster.store.meta("last_summary_day") == "2026-09-13"
    poster._clock["t"] = now + 86400 + 60  # type: ignore[attr-defined]
    assert poster.maybe_daily_summary() is True and len(tgs.sent()) == 2


def test_dry_run_prints_and_writes_nothing(tmp_path, capsys):
    poster, tgs, _ = make(tmp_path, dry_run=True)
    assert poster.handle_fired(alert(), ts_emit=1.0) == "posted"
    assert poster.handle_fired(alert(), ts_emit=1.0) == "posted"  # nothing persisted, so nothing is a duplicate
    assert poster.store.get(alert()["key"]) is None and tgs.sent() == []
    assert "ENTER STACK·39ac" in capsys.readouterr().out and len(poster.printed) == 2


def test_launch_facts_from_store_table(tmp_path):
    store = CallsStore(tmp_path / "live.sqlite")
    store.db.execute("CREATE TABLE launch_intel (token TEXT PRIMARY KEY, dev_buy_share REAL, bundle_n INTEGER, creator_tax_bps INTEGER, launch_farm INTEGER, snipe_tax_zero_ts INTEGER, deployer_prior_launches_30d INTEGER)")
    store.db.execute("INSERT INTO launch_intel VALUES(?,?,?,?,?,?,?)", (TOKEN, 0.021, 1, 25, 0, None, 2))
    store.db.commit()
    lf = LaunchFacts(store, "http://engine")
    li = lf.for_token(TOKEN)
    assert li["dev_buy_share"] == 0.021 and li["bundle_n"] == 1 and li["launch_farm"] is False
    assert lf.for_token("0x" + "9" * 40) is None and lf.requests == 0  # no API fallback while a store is present
    assert lf.for_token(TOKEN, {"launch_farm": True, "bundle_n": 9})["bundle_n"] == 9  # payload wins
    assert fmt.launch_line(li) == "launch: dev 2.1 % · bundle 1 · tax 25 bps"


def test_flow_shape_negatives_skip_thin_breadth_and_fresh_launches():
    from stampede.calls.poster import Rule

    r = Rule({"rule": "edge_enter", "min_p": 0.3, "max_per_hour": 5, "dedupe_s": 1800, "skip_if": {"breadth_lt": 2, "age_s_lt": 180}, "require_size_gt_0": False})
    base = {"rule": "edge_enter", "token": "0x" + "1" * 40, "clock_ts": 1000, "detail": {"p_2x_30m": 0.5, "breadth": 1, "age_s": 400}}
    assert r.check(base, None, None) == "breadth 1 < 2"
    base["detail"].update(breadth=5, age_s=90)
    assert r.check(base, None, None) == "age 90s < 180s"
    base["detail"].update(age_s=400)
    assert r.check(base, None, None) is None
