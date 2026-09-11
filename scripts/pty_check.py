#!/usr/bin/env python3
"""Acceptance check: the TUI runs in a real pseudo-terminal of a given size, draws the brand and rows, reacts to
keys and SIGWINCH, quits on `q` (or Ctrl+C), and leaves the terminal in normal mode (alternate screen off,
cursor shown). The raw byte stream is kept next to the result as pty-<size>-<colors>[-<tag>].log.

    .venv/bin/python scripts/pty_check.py --size 120x36 --seconds 6 --api-url http://127.0.0.1:8791
    .venv/bin/python scripts/pty_check.py --size 180x50 --colors 256 --keys "2,wait:2,down,tab,wait:1,1,/,type:tvk,wait:1,escape" --resize 120x36@4 --quit ctrl-c

Key script: comma separated tokens — key names (up down left right tab shift-tab enter escape end home space
slash ctrl-c), `type:<text>`, `wait:<seconds>`; single characters are sent as-is.
"""
from __future__ import annotations

import argparse
import fcntl
import os
import pty
import select
import signal
import struct
import sys
import termios
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KEYS = {
    "up": b"\x1b[A",
    "down": b"\x1b[B",
    "right": b"\x1b[C",
    "left": b"\x1b[D",
    "tab": b"\t",
    "shift-tab": b"\x1b[Z",
    "enter": b"\r",
    "escape": b"\x1b",
    "end": b"\x1b[F",
    "home": b"\x1b[H",
    "space": b" ",
    "slash": b"/",
    "ctrl-c": b"\x03",
}


def parse_script(script: str, start_at: float) -> list[tuple[float, bytes]]:
    """Turn the key script into (absolute seconds, bytes) pairs; keys are spaced 0.15 s apart unless `wait:` says otherwise."""
    t = start_at
    out: list[tuple[float, bytes]] = []
    for tok in [x.strip() for x in script.split(",") if x.strip()]:
        if tok.startswith("wait:"):
            t += float(tok[5:])
            continue
        if tok.startswith("type:"):
            for ch in tok[5:]:
                out.append((t, ch.encode()))
                t += 0.08
            continue
        out.append((t, KEYS.get(tok, tok.encode())))
        t += 0.15
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", default="120x36")
    ap.add_argument("--seconds", type=float, default=6.0, help="seconds before the key script starts (first poll + history stream)")
    ap.add_argument("--api-url", default="http://127.0.0.1:8791")
    ap.add_argument("--out", default="demo/tui")
    ap.add_argument("--colors", default="truecolor", choices=["truecolor", "256"], help="terminal colour capability to emulate")
    ap.add_argument("--keys", default="", help="key script sent after --seconds (see module doc)")
    ap.add_argument("--resize", default="", help="WxH@seconds: change the pty size and send SIGWINCH at that time")
    ap.add_argument("--quit", default="q", choices=["q", "ctrl-c"], help="how the TUI is asked to exit at the end")
    ap.add_argument("--tag", default="", help="suffix for the log file name")
    a = ap.parse_args()
    cols, rows = (int(x) for x in a.size.split("x"))
    pid, fd = pty.fork()
    if pid == 0:
        os.environ["TERM"] = "xterm-256color"
        if a.colors == "truecolor":
            os.environ["COLORTERM"] = "truecolor"
        else:
            os.environ.pop("COLORTERM", None)
            os.environ["TEXTUAL_COLOR_SYSTEM"] = "256"
        os.environ.pop("NO_COLOR", None)
        os.environ["FORCE_COLOR"] = "1"
        # the size comes from the pty (TIOCSWINSZ below); exported COLUMNS/LINES would pin it and hide SIGWINCH resizes
        os.environ.pop("COLUMNS", None)
        os.environ.pop("LINES", None)
        os.chdir(ROOT)
        os.execv(str(ROOT / ".venv" / "bin" / "python"), [str(ROOT / ".venv" / "bin" / "python"), "-m", "stampede", "terminal", "--api-url", a.api_url, "--poll", "1"])
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    os.kill(pid, signal.SIGWINCH)
    script = parse_script(a.keys, a.seconds)
    end_at = (script[-1][0] + 1.5) if script else a.seconds
    resize_at = None
    if a.resize:
        dims, at = a.resize.split("@")
        rcols, rrows = (int(x) for x in dims.split("x"))
        resize_at = (float(at), rcols, rrows)
        end_at = max(end_at, float(at) + 2.5)
    buf = bytearray()
    marks: dict[str, int] = {}  # byte offsets of moments we want to check against
    t0 = time.time()
    sent_quit = False
    resized = False
    while True:
        r, _, _ = select.select([fd], [], [], 0.05)
        if r:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
        now = time.time() - t0
        if resize_at and not resized and now >= resize_at[0]:
            marks["resize"] = len(buf)
            fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", resize_at[2], resize_at[1], 0, 0))
            os.kill(pid, signal.SIGWINCH)
            resized = True
        while script and now >= script[0][0]:
            _, data = script.pop(0)
            os.write(fd, data)
        if not sent_quit and now > end_at:
            marks["quit"] = len(buf)
            os.write(fd, KEYS["ctrl-c"] if a.quit == "ctrl-c" else b"q")
            sent_quit = True
        if sent_quit and now > end_at + 4:
            break
    status = None
    for _ in range(60):  # give the process up to 3 s to exit
        wpid, st = os.waitpid(pid, os.WNOHANG)
        if wpid:
            status = st
            break
        time.sleep(0.05)
    if status is None:
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
    raw = buf.decode("utf-8", "replace")
    strip = __import__("re").compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[()][0-9A-Za-z]|\x1b[=>]")
    text = strip.sub("", raw)  # plain cells in emission order: styles split "CLOCK   17:19:57" into several escapes otherwise
    plain_upto = lambda n: strip.sub("", buf[:n].decode("utf-8", "replace"))  # noqa: E731
    out = ROOT / a.out
    out.mkdir(parents=True, exist_ok=True)
    tag = f"-{a.tag}" if a.tag else ""
    (out / f"pty-{cols}x{rows}-{a.colors}{tag}.log").write_bytes(bytes(buf))
    checks = {
        "alternate_screen_entered": "\x1b[?1049h" in raw,
        "alternate_screen_left": "\x1b[?1049l" in raw,
        "cursor_shown_at_exit": raw.rfind("\x1b[?25h") > raw.rfind("\x1b[?25l"),
        # wide: block wordmark + the bison as half-blocks (25 x 12 = 300 cells); compact: the wordmark line, no mascot
        "brand_drawn": ("██████" in text and text.count("▀") >= 300) if cols >= 160 else ("S T A M P E D E" in text),
        "status_drawn": "Robinhood Chain" in text and "CLOCK" in text,
        "rows_drawn": "→" in text and ("clean" in text or "direct" in text or "NO OBSERVED" in text),
        "red_used": ("38;2;255;51;68" in raw) if a.colors == "truecolor" else ("38;5;" in raw),
        "exited": status is not None and os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0,
        "bytes": len(buf),
    }
    keys = a.keys.split(",")
    if a.keys:
        if "type:" in a.keys or "slash" in keys:
            checks["search_drawn"] = "type to filter" in text
        if "2" in keys:
            checks["radar_drawn"] = "SCORE" in text and "IN 10M" in text
        if "tab" in keys:
            checks["details_focused"] = "DETAILS" in text
        if "space" in keys:
            checks["clock_moved"] = len(set(__import__("re").findall(r"CLOCK\s+(\d\d:\d\d:\d\d)", text))) >= 2
            checks["playing_then_paused"] = "PAUSED" in text and __import__("re").search(r"\[REPLAY [^\]]*×\]", text) is not None
        if "end" in keys or "up" in keys:
            checks["follow_state_shown"] = "following latest" in text or "End follows latest" in text
    if resize_at:
        before, after = plain_upto(marks["resize"]), text[len(plain_upto(marks["resize"])) :]
        big_before, big_after = "██████" in before, "██████" in after
        compact_after = "S T A M P E D E" in after
        checks["redrawn_after_resize"] = len(after) > 500 and (compact_after or big_after)
        if resize_at[1] < 160 <= cols:
            checks["compact_header_after_shrink"] = compact_after and big_before and "TIME UTC" in after
        if cols < 160 <= resize_at[1]:
            checks["block_wordmark_after_grow"] = big_after and after.count("▀") >= 300 and "SOLD UTC" in after
    ok = all(v for k, v in checks.items() if k != "bytes")
    print(f"pty {cols}x{rows} {a.colors}{tag}: {'OK' if ok else 'FAIL'} {checks}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
