"""The STAMPEDE Calls poster: `GET /api/stream?types=alert,position,session` -> rule -> Telegram Bot API.

One process, one thread: the SSE loop of `Poster.run()` handles every frame inline (a post takes ~0.3 s; the target is
p95 <= 2 s from the engine's `ts_emit` to the Bot API acknowledgement). `Last-Event-ID` resumes the stream after a
reconnect; a start-up catch-up reads the alert journal so calls fired while the poster was down (last `catchup_s`,
30 min) are still posted once. Every decision (posted / skipped) is a row of the `calls` table in the live store, so a
restart or a replayed event never posts twice. Secrets come from `stampede.env.env` and are never logged.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import requests

from ..env import ROOT, env
from . import format as fmt

log = logging.getLogger("stampede.calls")

DEFAULT_CONFIG = ROOT / "stampede" / "signals" / "calls-config.json"
DEFAULT_PERF = ROOT / "data" / "calls-perf.json"
DEFAULT_DB = ROOT / "data" / "live-engine.sqlite"
STREAM_TYPES = "alert,position,session"


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    p = Path(path) if path else DEFAULT_CONFIG
    cfg = json.loads(p.read_text())
    cfg.setdefault("rule", "edge_enter")
    cfg.setdefault("min_p", 0.30)
    cfg.setdefault("max_per_hour", 5)
    cfg.setdefault("dedupe_s", 1800)
    cfg.setdefault("skip_if", {"launch_farm": True, "bundle_n_gt": 4, "dev_buy_share_gt": 0.10})
    cfg.setdefault("require_size_gt_0", True)
    cfg.setdefault("catchup_s", 1800)
    cfg.setdefault("summary_utc_hour", 9)
    cfg.setdefault("ev_basis_quote", 0.02)
    return cfg


# ---------------------------------------------------------------- Telegram
class TelegramError(Exception):
    pass


class Telegram:
    """Bot API client: `sendMessage` (HTML, no previews) and `deleteMessage`, 10 s timeout, retry on 429 (`retry_after`)
    and 5xx. The token never appears in logs (URLs are not logged)."""

    def __init__(self, token: str, chat_id: str, session: Any | None = None, timeout: float = 10.0, max_tries: int = 5, sleep: Callable[[float], None] = time.sleep):
        self.base = f"https://api.telegram.org/bot{token}"
        self.chat_id = chat_id
        self.s = session or requests.Session()
        self.timeout = timeout
        self.max_tries = max_tries
        self.sleep = sleep
        self.retries = 0

    def call(self, method: str, payload: dict[str, Any]) -> Any:
        last: str = ""
        for attempt in range(self.max_tries):
            try:
                r = self.s.post(f"{self.base}/{method}", json=payload, timeout=self.timeout)
            except requests.RequestException as e:
                last = f"{type(e).__name__}"
                self.retries += 1
                self.sleep(min(10.0, 0.5 * (2**attempt)))
                continue
            try:
                body = r.json()
            except ValueError:
                body = {}
            if r.status_code == 200 and body.get("ok"):
                return body.get("result")
            if r.status_code == 429:
                wait = float((body.get("parameters") or {}).get("retry_after") or 1)
                self.retries += 1
                self.sleep(min(60.0, wait))
                continue
            if 500 <= r.status_code < 600:
                last = f"HTTP {r.status_code}"
                self.retries += 1
                self.sleep(min(10.0, 0.5 * (2**attempt)))
                continue
            raise TelegramError(f"{method}: HTTP {r.status_code} {str(body.get('description') or '')[:120]}")
        raise TelegramError(f"{method}: gave up after {self.max_tries} tries ({last})")

    def send(self, text: str, reply_to: int | None = None) -> int:
        payload: dict[str, Any] = {"chat_id": self.chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        if reply_to:
            payload["reply_to_message_id"] = reply_to
            payload["allow_sending_without_reply"] = True
        res = self.call("sendMessage", payload)
        return int(res["message_id"])

    def delete(self, message_id: int) -> bool:
        return bool(self.call("deleteMessage", {"chat_id": self.chat_id, "message_id": int(message_id)}))


# ---------------------------------------------------------------- store
SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
  alert_key TEXT PRIMARY KEY, token TEXT, symbol TEXT, clock_ts INTEGER, message_id INTEGER, chat_id TEXT,
  posted_at REAL, latency_ms REAL, source TEXT, skipped TEXT,
  outcome_30_msg INTEGER, outcome_60_msg INTEGER, exit_msg INTEGER);
CREATE INDEX IF NOT EXISTS calls_token ON calls(token, clock_ts);
CREATE TABLE IF NOT EXISTS calls_meta (key TEXT PRIMARY KEY, value TEXT);
"""
COLS = ["alert_key", "token", "symbol", "clock_ts", "message_id", "chat_id", "posted_at", "latency_ms", "source", "skipped", "outcome_30_msg", "outcome_60_msg", "exit_msg"]


class CallsStore:
    """The poster's own tables in the live store (its own connection; the engine writes other tables)."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        self.db = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
        self.db.execute("PRAGMA busy_timeout=10000")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def get(self, key: str) -> dict[str, Any] | None:
        r = self.db.execute(f"SELECT {','.join(COLS)} FROM calls WHERE alert_key=?", (key,)).fetchone()
        return dict(zip(COLS, r)) if r else None

    def record_skip(self, key: str, token: str, symbol: str | None, clock_ts: int, reason: str, source: str) -> None:
        self.db.execute("INSERT OR IGNORE INTO calls(alert_key, token, symbol, clock_ts, skipped, source, posted_at) VALUES(?,?,?,?,?,?,?)", (key, token, symbol, clock_ts, reason, source, time.time()))
        self.db.commit()

    def record_post(self, key: str, token: str, symbol: str | None, clock_ts: int, message_id: int, chat_id: str, posted_at: float, latency_ms: float | None, source: str) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO calls(alert_key, token, symbol, clock_ts, message_id, chat_id, posted_at, latency_ms, source) VALUES(?,?,?,?,?,?,?,?,?)",
            (key, token, symbol, clock_ts, message_id, chat_id, posted_at, latency_ms, source),
        )
        self.db.commit()

    def recent_call(self, token: str, since_clock: int) -> bool:
        return self.db.execute("SELECT 1 FROM calls WHERE token=? AND message_id IS NOT NULL AND clock_ts>? LIMIT 1", (token, since_clock)).fetchone() is not None

    def posted_since(self, since_clock: int) -> int:
        return int(self.db.execute("SELECT COUNT(*) FROM calls WHERE message_id IS NOT NULL AND clock_ts>?", (since_clock,)).fetchone()[0])

    def for_position(self, token: str, alert_ts: int | None) -> dict[str, Any] | None:
        if alert_ts is None:
            return None
        return self.get(f"{token}:{int(alert_ts)}")

    def set_msg(self, key: str, col: str, message_id: int) -> None:
        assert col in ("outcome_30_msg", "outcome_60_msg", "exit_msg")
        self.db.execute(f"UPDATE calls SET {col}=? WHERE alert_key=?", (message_id, key))
        self.db.commit()

    def pending_outcomes(self) -> list[dict[str, Any]]:
        rows = self.db.execute(f"SELECT {','.join(COLS)} FROM calls WHERE message_id IS NOT NULL AND (outcome_30_msg IS NULL OR outcome_60_msg IS NULL)").fetchall()
        return [dict(zip(COLS, r)) for r in rows]

    def stats(self, since_wall: float) -> dict[str, Any]:
        posted = int(self.db.execute("SELECT COUNT(*) FROM calls WHERE message_id IS NOT NULL AND posted_at>?", (since_wall,)).fetchone()[0])
        skipped = int(self.db.execute("SELECT COUNT(*) FROM calls WHERE message_id IS NULL AND posted_at>?", (since_wall,)).fetchone()[0])
        lat = sorted(float(r[0]) for r in self.db.execute("SELECT latency_ms FROM calls WHERE latency_ms IS NOT NULL AND source='live' AND posted_at>?", (since_wall,)))
        return {"posted": posted, "skipped": skipped, "p50_ms": _pct(lat, 0.5), "p95_ms": _pct(lat, 0.95), "n_latency": len(lat)}

    def meta(self, key: str) -> str | None:
        r = self.db.execute("SELECT value FROM calls_meta WHERE key=?", (key,)).fetchone()
        return r[0] if r else None

    def set_meta(self, key: str, value: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO calls_meta(key, value) VALUES(?,?)", (key, value))
        self.db.commit()


def _pct(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    i = min(len(xs) - 1, int(round(q * (len(xs) - 1))))
    return round(xs[i], 1)


# ---------------------------------------------------------------- launch facts
LAUNCH_FIELDS = ("dev_buy_share", "bundle_n", "creator_tax_bps", "launch_farm", "snipe_tax_zero_ts", "deployer_prior_launches_30d")


class LaunchFacts:
    """`launch_intel` facts for one coin, cached. Read from the store's table first (sub-millisecond: the same row
    `/api/coin/{addr}` returns as `launch.intel`); when no store is available, `GET /api/coin/{addr}` with a short
    timeout (the endpoint computes the whole coin card, 5-10 s on the live store, so it is only a fallback). Unknown is
    `None` and never skips a call."""

    def __init__(self, store: CallsStore | None, api: str | None, session: Any | None = None, timeout: float = 3.0):
        self.store = store
        self.api = api.rstrip("/") if api else None
        self.s = session or requests.Session()
        self.timeout = timeout
        self.cache: dict[str, dict[str, Any] | None] = {}
        self.requests = 0

    def for_token(self, token: str, payload: dict[str, Any] | None = None) -> dict[str, Any] | None:
        if payload:  # a `launch` object inside the alert / radar row wins (future engines may carry it)
            return {k: payload.get(k) for k in LAUNCH_FIELDS}
        if token in self.cache:
            return self.cache[token]
        li: dict[str, Any] | None = None
        if self.store is not None:
            try:
                has = self.store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='launch_intel'").fetchone()
                if has:
                    r = self.store.db.execute(f"SELECT {','.join(LAUNCH_FIELDS)} FROM launch_intel WHERE token=?", (token,)).fetchone()
                    if r:
                        li = dict(zip(LAUNCH_FIELDS, r))
                        li["launch_farm"] = None if li["launch_farm"] is None else bool(li["launch_farm"])
            except sqlite3.Error as e:
                log.warning("launch_intel read failed: %s", e)
        elif self.api:
            try:
                self.requests += 1
                r = self.s.get(f"{self.api}/api/coin/{token}", timeout=self.timeout)
                if r.status_code == 200:
                    intel = ((r.json().get("launch") or {}).get("intel")) or None
                    li = {k: intel.get(k) for k in LAUNCH_FIELDS} if intel else None
            except (requests.RequestException, ValueError) as e:
                log.info("launch facts for %s: %s", token[-6:], type(e).__name__)
        self.cache[token] = li
        return li


# ---------------------------------------------------------------- rule
class Rule:
    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg

    def check(self, alert: dict[str, Any], launch: dict[str, Any] | None, store: CallsStore | None) -> str | None:
        """None when the alert becomes a call, otherwise the reason it is skipped."""
        c = self.cfg
        if alert.get("rule") != c["rule"]:
            return f"rule {alert.get('rule')}"
        d = alert.get("detail") or {}
        p = d.get("p_2x_30m")
        if p is None or float(p) < float(c["min_p"]):
            return f"p {p} < {c['min_p']}"
        if c.get("require_size_gt_0") and not ((d.get("size_quote") or 0) > 0):
            return "size 0"
        sk = c.get("skip_if") or {}
        if launch:
            if sk.get("launch_farm") and launch.get("launch_farm"):
                return "launch farm"
            if sk.get("bundle_n_gt") is not None and launch.get("bundle_n") is not None and int(launch["bundle_n"]) > int(sk["bundle_n_gt"]):
                return f"bundle {launch['bundle_n']} > {sk['bundle_n_gt']}"
            if sk.get("dev_buy_share_gt") is not None and launch.get("dev_buy_share") is not None and float(launch["dev_buy_share"]) > float(sk["dev_buy_share_gt"]):
                return f"dev buy {float(launch['dev_buy_share']):.1%} > {float(sk['dev_buy_share_gt']):.0%}"
        if store is not None:
            clock = int(alert.get("clock_ts") or 0)
            if store.recent_call(alert["token"], clock - int(c["dedupe_s"])):
                return f"same coin within {int(c['dedupe_s']) // 60} min"
            if store.posted_since(clock - 3600) >= int(c["max_per_hour"]):
                return f"{c['max_per_hour']} calls in the hour"
        return None


# ---------------------------------------------------------------- perf
class Perf:
    def __init__(self, path: str | Path | None = DEFAULT_PERF, keep: int = 500):
        self.path = Path(path) if path else None
        self.samples: list[float] = []
        self.keep = keep
        self.n = 0
        if self.path and self.path.exists():
            try:
                old = json.loads(self.path.read_text())
                self.samples = [float(x) for x in old.get("samples") or []][-keep:]
                self.n = int(old.get("n") or len(self.samples))
            except (ValueError, OSError):
                pass

    def record(self, ms: float) -> None:
        self.n += 1
        self.samples.append(round(ms, 1))
        self.samples = self.samples[-self.keep :]
        self.save()

    def snapshot(self) -> dict[str, Any]:
        xs = sorted(self.samples)
        return {"n": self.n, "window": len(xs), "p50_ms": _pct(xs, 0.5), "p95_ms": _pct(xs, 0.95), "max_ms": xs[-1] if xs else None, "target_p95_ms": 2000, "updated": time.time(), "samples": self.samples}

    def save(self) -> None:
        if not self.path:
            return
        try:
            self.path.parent.mkdir(exist_ok=True)
            self.path.write_text(json.dumps(self.snapshot()))
        except OSError as e:
            log.warning("perf file: %s", e)


# ---------------------------------------------------------------- SSE
def sse_frames(lines) -> Any:
    """Parse `id:` / `event:` / `data:` frames from an iterator of text lines; yields the JSON body (`id, type, ts_emit,
    block, data`)."""
    frame: dict[str, str] = {}
    for raw in lines:
        if raw is None:
            continue
        line = raw if isinstance(raw, str) else raw.decode("utf-8", "replace")
        if line == "":
            if "data" in frame:
                try:
                    yield json.loads(frame["data"])
                except ValueError:
                    pass
            frame = {}
            continue
        if line.startswith(":"):
            continue
        k, _, v = line.partition(":")
        frame[k] = v[1:] if v.startswith(" ") else v


def journal_to_fired(row: dict[str, Any]) -> dict[str, Any]:
    """A `/api/alerts` journal row in the shape of the `fired` SSE payload."""
    return {
        "kind": "fired", "key": f"{row['token']}:{int(row['clock_ts'])}", "created_ts": row.get("created_ts"), "clock_ts": row.get("clock_ts"), "mode": row.get("mode"), "token": row["token"],
        "symbol": row.get("symbol"), "rule": row.get("rule"), "score": row.get("score"), "inflow": row.get("inflow"), "mentions_1h": row.get("mentions_1h"), "price": row.get("price"),
        "detail": row.get("detail") or {}, "outcome_30m": row.get("outcome_30m"), "outcome_60m": row.get("outcome_60m"), "graduated_after": row.get("graduated_after"),
    }


# ---------------------------------------------------------------- poster
class Poster:
    def __init__(
        self,
        api: str,
        cfg: dict[str, Any],
        store: CallsStore,
        tg: Telegram | None,
        launch: LaunchFacts,
        perf: Perf,
        dry_run: bool = False,
        session: Any | None = None,
        channel_username: str | None = None,
        now: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.api = api.rstrip("/")
        self.cfg = cfg
        self.rule = Rule(cfg)
        self.store = store
        self.tg = tg
        self.launch = launch
        self.perf = perf
        self.dry_run = dry_run
        self.s = session or requests.Session()
        self.username = channel_username
        self.now = now
        self.sleep = sleep
        self.last_id: int | None = None
        self.stop = False
        self.frames = 0
        self.reconnects = 0
        self.posted = 0
        self.skipped = 0
        self.printed: list[str] = []  # dry-run output, also inspected by tests
        self.session_state: dict[str, Any] = {}

    # ---- transport
    def link(self, message_id: int | None) -> str:
        if not message_id:
            return ""
        return f"https://t.me/{self.username}/{message_id}" if self.username else f"message {message_id}"

    def _send(self, text: str, reply_to: int | None = None) -> int | None:
        if self.dry_run or self.tg is None:
            self.printed.append(text)
            print(("--- reply to %s ---\n" % reply_to if reply_to else "--- message ---\n") + text, flush=True)
            return None
        return self.tg.send(text, reply_to=reply_to)

    def _get(self, path: str, params: dict[str, Any] | None = None, timeout: float = 20.0) -> Any:
        r = self.s.get(self.api + path, params=params, timeout=timeout)
        r.raise_for_status()
        return r.json()

    # ---- alerts
    def handle_fired(self, alert: dict[str, Any], ts_emit: float | None = None, source: str = "live") -> str:
        """One alert -> `posted` / `duplicate` / `skipped: <reason>`."""
        if alert.get("rule") != self.cfg["rule"]:
            return "ignored"  # the other rule's alerts (under_radar_top5, ~500/day) are not the channel's business: no row, no log line
        key = alert.get("key") or f"{alert['token']}:{int(alert.get('clock_ts') or 0)}"
        if self.store.get(key) is not None:
            return "duplicate"
        token = alert["token"]
        clock_ts = int(alert.get("clock_ts") or 0)
        launch = self.launch.for_token(token, (alert.get("detail") or {}).get("launch"))
        reason = self.rule.check(alert, launch, self.store)
        if reason:
            if not self.dry_run:  # a dry run reads the store but never writes: the real poster must not see its decisions
                self.store.record_skip(key, token, alert.get("symbol"), clock_ts, reason, source)
            self.skipped += 1
            log.info("skip %s %s: %s", fmt.title(alert.get("symbol"), token), key[-10:], reason)
            return f"skipped: {reason}"
        text = fmt.call_message(alert, launch, float(self.cfg.get("ev_basis_quote") or 0.02))
        t0 = self.now()
        mid = self._send(text)
        t1 = self.now()
        latency = (t1 - ts_emit) * 1000 if (ts_emit and source == "live") else None
        if not self.dry_run:
            self.store.record_post(key, token, alert.get("symbol"), clock_ts, mid or 0, self.tg.chat_id if self.tg else "dry-run", t1, latency, source)
        self.posted += 1
        if latency is not None:
            self.perf.record(latency)
        snap = self.perf.snapshot()
        log.info("call %s %s -> %s post %.0f ms%s p50 %s p95 %s", fmt.title(alert.get("symbol"), token), key[-10:], self.link(mid) or "dry-run", (t1 - t0) * 1000, f" latency {latency:.0f} ms" if latency is not None else f" ({source})", snap["p50_ms"], snap["p95_ms"])
        # a journal row may already carry outcomes (catch-up of an old alert)
        if mid and alert.get("outcome_30m") is not None:
            self.handle_outcome({"key": key, "token": token, "outcome_30m": alert.get("outcome_30m"), "outcome_60m": alert.get("outcome_60m"), "graduated_after": alert.get("graduated_after")})
        return "posted"

    def handle_outcome(self, data: dict[str, Any]) -> list[str]:
        key = data.get("key") or f"{data.get('token')}:{int(data.get('clock_ts') or 0)}"
        row = self.store.get(key)
        if not row or not row.get("message_id"):
            return []
        done: list[str] = []
        for h, field, col in ((30, "outcome_30m", "outcome_30_msg"), (60, "outcome_60m", "outcome_60_msg")):
            if data.get(field) is None or row.get(col):
                continue
            text = fmt.outcome_message(h, float(data[field]), bool(data.get("graduated_after")) if h == 60 else None)
            mid = self._send(text, reply_to=int(row["message_id"]))
            if not self.dry_run:
                self.store.set_msg(key, col, mid or 0)
            log.info("outcome +%d %s %s -> %s", h, row.get("symbol"), key[-10:], self.link(mid) or "dry-run")
            done.append(col)
        return done

    def handle_position(self, pos: dict[str, Any]) -> bool:
        if pos.get("kind") != "closed":
            return False
        row = self.store.for_position(pos.get("token", ""), pos.get("alert_ts"))
        if not row or not row.get("message_id") or row.get("exit_msg"):
            return False
        mid = self._send(fmt.exit_message(pos), reply_to=int(row["message_id"]))
        if not self.dry_run:
            self.store.set_msg(row["alert_key"], "exit_msg", mid or 0)
        log.info("exit %s %s %s -> %s", row.get("symbol"), pos.get("exit_reason"), fmt.signed_eth(pos.get("pnl_quote")), self.link(mid) or "dry-run")
        return True

    def handle_frame(self, ev: dict[str, Any]) -> None:
        self.frames += 1
        if ev.get("id") is not None:
            self.last_id = int(ev["id"])
        t = ev.get("type")
        d = ev.get("data") or {}
        if t == "alert":
            if d.get("kind") == "fired":
                self.handle_fired(d, ts_emit=ev.get("ts_emit"), source="live")
            elif d.get("kind") == "outcome":
                self.handle_outcome(d)
        elif t == "position":
            self.handle_position(d)
        elif t == "session":
            self.session_state = d

    # ---- catch-up
    def catch_up(self, limit: int = 400) -> dict[str, int]:
        """Alerts of the journal fired in the last `catchup_s` (wall clock), oldest first, through the same path as live
        events; outcome replies still missing for posted calls are filled from the journal too."""
        since = self.now() - float(self.cfg.get("catchup_s") or 1800)
        try:
            body = self._get("/api/alerts", {"limit": limit})
        except (requests.RequestException, ValueError) as e:
            log.warning("catch-up: /api/alerts failed: %s", type(e).__name__)
            return {"error": 1}
        rows = [a for a in (body.get("alerts") or []) if a.get("rule") == self.cfg["rule"]]
        res = {"posted": 0, "skipped": 0, "duplicate": 0, "outcomes": 0}
        for a in sorted((a for a in rows if (a.get("created_ts") or a.get("clock_ts") or 0) >= since), key=lambda a: (a.get("clock_ts") or 0, a.get("id") or 0)):
            r = self.handle_fired(journal_to_fired(a), source="catchup")
            res["posted" if r == "posted" else "duplicate" if r == "duplicate" else "skipped"] += 1
        pending = {r["alert_key"]: r for r in self.store.pending_outcomes()}
        if pending:
            for a in rows:
                key = f"{a['token']}:{int(a['clock_ts'])}"
                if key in pending and (a.get("outcome_30m") is not None or a.get("outcome_60m") is not None):
                    res["outcomes"] += len(self.handle_outcome({"key": key, "token": a["token"], "outcome_30m": a.get("outcome_30m"), "outcome_60m": a.get("outcome_60m"), "graduated_after": a.get("graduated_after")}))
        log.info("catch-up: %s", res)
        return res

    # ---- summary
    def summary_text(self) -> str:
        now = self.now()
        tr = self._get("/api/track-record", {"since": int(now - 86400), "alerts_limit": 0})
        return fmt.summary_message(tr, self.store.stats(now - 86400), now)

    def post_summary(self) -> int | None:
        text = self.summary_text()
        mid = self._send(text)
        log.info("summary -> %s", self.link(mid) or "dry-run")
        return mid

    def maybe_daily_summary(self) -> bool:
        hour = int(self.cfg.get("summary_utc_hour", 9))
        dt = datetime.fromtimestamp(self.now(), tz=timezone.utc)
        day = dt.strftime("%Y-%m-%d")
        if dt.hour < hour or self.store.meta("last_summary_day") == day:
            return False
        try:
            self.post_summary()
        except Exception as e:  # noqa: BLE001 - the stream must go on
            log.warning("summary failed: %s", e)
            return False
        if not self.dry_run:
            self.store.set_meta("last_summary_day", day)
        return True

    # ---- the loop
    def stream_url(self) -> str:
        return f"{self.api}/api/stream?types={STREAM_TYPES}" + (f"&last_event_id={self.last_id}" if self.last_id is not None else "")

    def run(self, backoff_max_s: float = 15.0) -> None:
        self.catch_up()
        self.maybe_daily_summary()
        fails = 0
        while not self.stop:
            try:
                with self.s.get(self.stream_url(), stream=True, timeout=(6, 40), headers={"accept": "text/event-stream"}) as r:
                    if r.status_code != 200:
                        raise requests.HTTPError(f"HTTP {r.status_code} /api/stream")
                    log.info("stream connected%s", f" (resume from {self.last_id})" if self.last_id is not None else "")
                    for ev in sse_frames(r.iter_lines(decode_unicode=True)):
                        if self.stop:
                            return
                        d = ev.get("data") or {}
                        if ev.get("type") == "session" and d.get("hello"):
                            fails = 0
                            if d.get("replay_gap"):
                                log.warning("replay gap: the ring no longer reaches %s; re-reading the journal", self.last_id)
                                self.catch_up()
                        try:
                            self.handle_frame(ev)
                        except Exception as e:  # noqa: BLE001 - one bad frame must not kill the stream
                            log.exception("frame %s failed: %s", ev.get("id"), e)
                        self.maybe_daily_summary()
                    raise requests.ConnectionError("stream ended")
            except (requests.RequestException, OSError) as e:
                if self.stop:
                    return
                fails += 1
                self.reconnects += 1
                wait = min(backoff_max_s, 0.5 * (2 ** min(fails, 5)))
                log.warning("stream %s; reconnect in %.1f s (resume from %s)", type(e).__name__, wait, self.last_id)
                self.sleep(wait)
                self.maybe_daily_summary()


# ---------------------------------------------------------------- CLI
def _setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%S")
    logging.Formatter.converter = time.gmtime


def build(args: Any) -> Poster:
    cfg = load_config(args.config)
    store = CallsStore(args.db or DEFAULT_DB)
    tg: Telegram | None = None
    if not args.dry_run:
        token, chat = env("TELEGRAM_BOT_TOKEN"), env("TELEGRAM_CHANNEL")
        if not token or not chat:
            raise SystemExit("TELEGRAM_BOT_TOKEN / TELEGRAM_CHANNEL missing in .env (use --dry-run to print instead)")
        tg = Telegram(token, chat)
    launch = LaunchFacts(store, args.api)
    perf = Perf(args.perf_json or DEFAULT_PERF)
    return Poster(args.api, cfg, store, tg, launch, perf, dry_run=args.dry_run, channel_username=env("TELEGRAM_CHANNEL_USERNAME"))


def main_calls(args: Any) -> int:
    _setup_logging()
    if getattr(args, "delete", None):
        token, chat = env("TELEGRAM_BOT_TOKEN"), env("TELEGRAM_CHANNEL")
        if not token or not chat:
            raise SystemExit("TELEGRAM_BOT_TOKEN / TELEGRAM_CHANNEL missing in .env")
        ok = Telegram(token, chat).delete(int(args.delete))
        print(f"deleteMessage {args.delete}: {ok}")
        return 0 if ok else 1
    poster = build(args)
    if args.summary:
        mid = poster.post_summary()
        if mid:
            print(poster.link(mid))
        return 0
    if args.once:
        body = poster._get("/api/alerts", {"limit": 400})
        rows = [a for a in body.get("alerts") or [] if a.get("rule") == poster.cfg["rule"]]
        if not rows:
            print("no alert of the rule in the journal")
            return 1
        a = journal_to_fired(rows[0])
        launch = poster.launch.for_token(a["token"])
        reason = poster.rule.check(a, launch, None)
        text = fmt.call_message(a, launch, float(poster.cfg.get("ev_basis_quote") or 0.02))
        if reason:
            print(f"note: the most recent alert would be skipped live ({reason}); posting it anyway as a format check")
        mid = poster._send(text)
        if mid:
            print(f"{poster.link(mid)}  (test message, not recorded; delete with: stampede calls --delete {mid})")
        return 0
    log.info("calls poster: api %s, db %s, rule %s, min_p %s, %s/h, dedupe %ss, %s", poster.api, poster.store.path, poster.cfg["rule"], poster.cfg["min_p"], poster.cfg["max_per_hour"], poster.cfg["dedupe_s"], "dry-run" if poster.dry_run else f"channel @{poster.username or '?'}")
    try:
        poster.run()
    except KeyboardInterrupt:
        pass
    return 0
