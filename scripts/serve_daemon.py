#!/usr/bin/env python3
"""Start/stop/status for the STAMPEDE server as a detached process (new session, survives the parent shell).

    python scripts/serve_daemon.py start --mode fixture --port 8791
    python scripts/serve_daemon.py status
    python scripts/serve_daemon.py stop
"""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PID = ROOT / "data" / "serve.pid"
LOG = ROOT / "data" / "serve.log"


def running() -> int | None:
    if not PID.exists():
        return None
    try:
        pid = int(PID.read_text().strip())
        os.kill(pid, 0)
        return pid
    except (ValueError, ProcessLookupError, PermissionError):
        return None


def start(args) -> int:
    if running():
        print(f"already running (pid {running()})")
        return 0
    py = ROOT / ".venv" / "bin" / "python"
    cmd = [str(py), "-m", "stampede", "serve", "--mode", args.mode, "--port", str(args.port), "--window", args.window]
    LOG.parent.mkdir(exist_ok=True)
    with open(LOG, "ab") as log:
        p = subprocess.Popen(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, start_new_session=True, stdin=subprocess.DEVNULL)
    PID.write_text(str(p.pid))
    time.sleep(2.5)
    if p.poll() is not None:
        print("failed to start; see data/serve.log")
        return 1
    print(f"started pid {p.pid} mode={args.mode} http://127.0.0.1:{args.port}/")
    return 0


def stop(_args) -> int:
    pid = running()
    if not pid:
        print("not running")
        PID.unlink(missing_ok=True)
        return 0
    os.killpg(os.getpgid(pid), signal.SIGTERM)
    for _ in range(30):
        if not running():
            break
        time.sleep(0.2)
    PID.unlink(missing_ok=True)
    print(f"stopped {pid}")
    return 0


def status(_args) -> int:
    pid = running()
    print(f"running pid {pid}" if pid else "not running")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start")
    s.add_argument("--mode", default="fixture", choices=["fixture", "replay", "live"])
    s.add_argument("--port", type=int, default=8791)
    s.add_argument("--window", default="30m")
    sub.add_parser("stop")
    sub.add_parser("status")
    args = ap.parse_args()
    return {"start": start, "stop": stop, "status": status}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
