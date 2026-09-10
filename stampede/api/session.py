"""One shared replay clock per server process.

Every surface (web, TUI) reads the same session: the same id, the same clock value, the same play state.
The clock is computed lazily from an anchor (value, wall time, speed), so no timer thread is needed and
all readers agree to the millisecond. Fixture mode has no clock (static snapshot at the end of the
sample); live mode has no replay clock (the chain head is the clock).
"""
from __future__ import annotations

import secrets
import threading
import time
from typing import Any


class SessionError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class SessionClock:
    def __init__(self, mode: str, from_ts: int | None, to_ts: int | None, window_s: int = 1800, span_s: int = 1800, speed: float = 10.0, start_at: int | None = None):
        self.id = "S-" + secrets.token_hex(3)
        self.rev = 0
        self.mode = mode
        self.from_ts = from_ts
        self.to_ts = to_ts
        self.window_s = window_s
        self.span_s = span_s
        self.speed = speed
        self.playing = False
        self._lock = threading.Lock()
        self._anchor_ts: float = float(start_at if start_at is not None else (to_ts if mode == "fixture" else (from_ts or 0) + span_s))
        if to_ts is not None:
            self._anchor_ts = min(self._anchor_ts, to_ts)
        self._anchor_wall = time.time()
        self.created = time.time()

    # ---- reading ----
    def clock(self) -> int | None:
        """Current replay clock in unix seconds (None in live mode, where the chain head is the clock)."""
        if self.mode == "live":
            return None
        with self._lock:
            t = self._anchor_ts
            if self.playing:
                t = self._anchor_ts + (time.time() - self._anchor_wall) * self.speed
                if self.to_ts is not None and t >= self.to_ts:
                    t = float(self.to_ts)
                    self._anchor_ts = t
                    self._anchor_wall = time.time()
                    self.playing = False
                    self.rev += 1
            return int(t)

    def state(self, live_last_ts: int | None = None) -> dict[str, Any]:
        clock = self.clock()
        if self.mode == "live":
            label = "LIVE"
        elif self.mode == "fixture":
            label = "FIXTURE · static snapshot"
        else:
            label = f"REPLAY {self.speed:g}×" + ("" if self.playing else " · PAUSED")
        return {
            "id": self.id,
            "rev": self.rev,
            "mode": self.mode,
            "label": label,
            "clock_ts": clock if self.mode != "live" else live_last_ts,
            "playing": self.playing if self.mode == "replay" else False,
            "speed": self.speed,
            "span_s": self.span_s,
            "window_s": self.window_s,
            "from_ts": self.from_ts,
            "to_ts": self.to_ts,
            "at_end": (clock is not None and self.to_ts is not None and clock >= self.to_ts) if self.mode == "replay" else None,
            "server_time": int(time.time()),
            "controls": self.mode == "replay",
        }

    # ---- control ----
    def control(self, action: str, ts: int | None = None, speed: float | None = None, span_s: int | None = None, window_s: int | None = None) -> dict[str, Any]:
        if action in ("span", "window"):
            with self._lock:
                if action == "span":
                    if not span_s or span_s <= 0:
                        raise SessionError(400, "span_s must be positive")
                    self.span_s = int(span_s)
                else:
                    if not window_s or window_s <= 0:
                        raise SessionError(400, "window_s must be positive")
                    self.window_s = int(window_s)
                self.rev += 1
            return self.state()
        if self.mode == "fixture":
            raise SessionError(409, "fixture mode is a static snapshot; start the server with --mode replay to control the clock")
        if self.mode == "live":
            raise SessionError(409, "live mode follows the chain head and has no replay clock")
        now = self.clock() or 0
        with self._lock:
            if action == "play":
                self._anchor_ts, self._anchor_wall = float(now), time.time()
                if self.to_ts is not None and now >= self.to_ts:
                    self._anchor_ts = float(self.from_ts or 0) + self.span_s  # restart from the first full range
                self.playing = True
            elif action == "pause":
                self._anchor_ts, self._anchor_wall = float(now), time.time()
                self.playing = False
            elif action == "toggle":
                self._anchor_ts, self._anchor_wall = float(now), time.time()
                self.playing = not self.playing
                if self.playing and self.to_ts is not None and now >= self.to_ts:
                    self._anchor_ts = float(self.from_ts or 0) + self.span_s
            elif action == "seek":
                if ts is None:
                    raise SessionError(400, "seek needs ts")
                lo = (self.from_ts or 0)
                hi = self.to_ts if self.to_ts is not None else ts
                self._anchor_ts, self._anchor_wall = float(min(max(int(ts), lo), hi)), time.time()
            elif action == "speed":
                if not speed or speed <= 0 or speed > 600:
                    raise SessionError(400, "speed must be in (0, 600]")
                self._anchor_ts, self._anchor_wall = float(now), time.time()
                self.speed = float(speed)
            else:
                raise SessionError(400, f"unknown action {action!r}")
            self.rev += 1
        return self.state()
