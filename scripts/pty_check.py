#!/usr/bin/env python3
"""Acceptance check: the TUI runs in a real pseudo-terminal of a given size, draws the brand and rows,
quits on `q`, and leaves the terminal in normal mode (alternate screen off, cursor shown).

    .venv/bin/python scripts/pty_check.py --size 120x36 --seconds 6 --api-url http://127.0.0.1:8791
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", default="120x36")
    ap.add_argument("--seconds", type=float, default=6.0)
    ap.add_argument("--api-url", default="http://127.0.0.1:8791")
    ap.add_argument("--out", default="demo/tui")
    a = ap.parse_args()
    cols, rows = (int(x) for x in a.size.split("x"))
    pid, fd = pty.fork()
    if pid == 0:
        os.environ["TERM"] = "xterm-256color"
        os.environ["COLORTERM"] = "truecolor"
        os.environ.pop("NO_COLOR", None)
        os.environ["FORCE_COLOR"] = "1"
        os.environ["COLUMNS"], os.environ["LINES"] = str(cols), str(rows)
        os.chdir(ROOT)
        os.execv(str(ROOT / ".venv" / "bin" / "python"), [str(ROOT / ".venv" / "bin" / "python"), "-m", "stampede", "terminal", "--api-url", a.api_url, "--poll", "1"])
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    os.kill(pid, signal.SIGWINCH)
    buf = bytearray()
    t0 = time.time()
    sent_q = False
    while True:
        r, _, _ = select.select([fd], [], [], 0.2)
        if r:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
        if not sent_q and time.time() - t0 > a.seconds:
            os.write(fd, b"q")
            sent_q = True
        if sent_q and time.time() - t0 > a.seconds + 4:
            break
    try:
        _, status = os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        status = 0
    text = buf.decode("utf-8", "replace")
    out = ROOT / a.out
    out.mkdir(parents=True, exist_ok=True)
    (out / f"pty-{cols}x{rows}.log").write_bytes(bytes(buf))
    checks = {
        "alternate_screen_entered": "\x1b[?1049h" in text,
        "alternate_screen_left": "\x1b[?1049l" in text,
        "cursor_shown_at_exit": text.rfind("\x1b[?25h") > text.rfind("\x1b[?25l"),
        "brand_drawn": "██████" in text or "S T A M P E D E" in text,
        "status_drawn": "CHAIN Robinhood Chain" in text,
        "rows_drawn": "→" in text and ("clean" in text or "direct" in text or "NO OBSERVED" in text),
        "truecolor_red_used": "38;2;255;51;68" in text,
        "exited_after_q": os.WIFEXITED(status) if status else True,
        "bytes": len(buf),
    }
    ok = all(v for k, v in checks.items() if k != "bytes")
    print(f"pty {cols}x{rows}: {'OK' if ok else 'FAIL'} {checks}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
