#!/usr/bin/env python3
"""Run a command detached from the current terminal session (survives the parent shell), logging to data/<name>.log.

    python scripts/bg.py rotate-30m -- .venv/bin/python -m stampede rotate --window 30m
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    if "--" not in sys.argv or len(sys.argv) < 4:
        print(__doc__)
        return 2
    i = sys.argv.index("--")
    name = sys.argv[1]
    cmd = sys.argv[i + 1 :]
    log = ROOT / "data" / f"{name}.log"
    log.parent.mkdir(exist_ok=True)
    with open(log, "ab") as f:
        p = subprocess.Popen(cmd, cwd=ROOT, stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
    print(f"started pid {p.pid}, log {log}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
