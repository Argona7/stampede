#!/usr/bin/env python3
"""Control the shared replay clock from the shell (same thing the TUI and the web page do).

    python scripts/session.py status
    python scripts/session.py reset            # pause, speed 20x, seek to 5.5 min before the end of the sample
    python scripts/session.py play | pause
    python scripts/session.py speed 10
    python scripts/session.py seek 17:24:27    # UTC time inside the sample, or a unix timestamp
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

import requests

BASE = "http://127.0.0.1:8791"


def post(body: dict) -> dict:
    r = requests.post(f"{BASE}/api/session", json=body, timeout=5)
    if r.status_code >= 400:
        print(f"error {r.status_code}: {r.json().get('detail')}")
        sys.exit(1)
    return r.json()


def show(s: dict) -> None:
    clock = s.get("clock_ts")
    hhmm = datetime.fromtimestamp(clock, timezone.utc).strftime("%H:%M:%S") if clock else "?"
    print(f"{s['label']} · clock {hhmm} UTC · session {s['id']} · range ends {s.get('to_ts')}")


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    cmd = sys.argv[1]
    try:
        s = requests.get(f"{BASE}/api/session", timeout=5).json()
    except requests.RequestException as e:
        print(f"API not reachable at {BASE}: {e}\nStart it: python scripts/serve_daemon.py start --mode replay")
        return 2
    if cmd == "status":
        show(s)
        print(json.dumps(s, indent=1))
    elif cmd == "reset":
        post({"action": "pause"})
        post({"action": "speed", "speed": 20})
        show(post({"action": "seek", "ts": s["to_ts"] - 330}))
    elif cmd in ("play", "pause"):
        show(post({"action": cmd}))
    elif cmd == "speed":
        show(post({"action": "speed", "speed": float(sys.argv[2])}))
    elif cmd == "seek":
        arg = sys.argv[2]
        if ":" in arg:
            day = datetime.fromtimestamp(s["to_ts"], timezone.utc).date()
            h, m, sec = (int(x) for x in arg.split(":"))
            ts = int(datetime(day.year, day.month, day.day, h, m, sec, tzinfo=timezone.utc).timestamp())
        else:
            ts = int(arg)
        show(post({"action": "seek", "ts": ts}))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
