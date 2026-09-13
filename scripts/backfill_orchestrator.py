#!/usr/bin/env python3
"""Keeps the 14-day research backfill alive across worker crashes and machine reboots, then finishes it.

Parts (block ranges, stores, paths) are fixed below. Every 60 s: a part whose `meta.backfill_cursor` is not past its
target and whose worker is not running gets (re)started (resume from the cursor; both paths are INSERT OR IGNORE).
When every part is done: the HyperSync store with the infra-seed defect is set aside, part A becomes
`data/research-14d.sqlite`, B / C / C2 are merged into it, wallets are recomputed from trades, the sample range is set,
the streaming rotate (300 / 1800 s) and `stampede fx` run, `READY` is written to `data/backfill-14d.log`, and the research
pipeline (launch-intel, traders, edge-features, edge) follows, ending with `PIPELINE DONE`.

    python scripts/bg.py backfill-orchestrator -- .venv/bin/python scripts/backfill_orchestrator.py

Idempotent: safe to start again after a reboot; it never prints secrets.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = ROOT / ".venv" / "bin" / "python"
LOG = ROOT / "data" / "backfill-14d.log"
FROM_BLOCK, TO_BLOCK = 48_872_434, 60_968_434
PARTS = [
    # name, store, from, to, path, log
    ("a", "data/research-14d-a.sqlite", 48_872_434, 52_900_000, "rpc", "data/backfill-A-rpc.log"),
    ("b", "data/research-14d-b.sqlite", 52_900_001, 56_930_000, "rpc", "data/backfill-B.log"),
    ("c", "data/research-14d-c.sqlite", 56_930_001, 60_000_000, "rpc", "data/backfill-C.log"),
    ("c2", "data/research-14d-c2.sqlite", 60_000_001, 60_968_434, "hypersync", "data/backfill-C2.log"),
]
MAX_RESTARTS = 40
TRADE_COLS = "tx_hash,block,ts,ts_exact,token,wallet,side,token_amount,quote_token,quote_amount,venue,swap_logs,attribution,flags,fee_raw,tax_raw,snipe_raw"


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {msg}"
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def cursor(store: str) -> int | None:
    p = ROOT / store
    if not p.exists():
        return None
    try:
        c = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        r = c.execute("SELECT value FROM meta WHERE key='backfill_cursor'").fetchone()
        c.close()
        return int(json.loads(r[0])) if r else None
    except sqlite3.Error:
        return None


def running(store: str) -> bool:
    out = subprocess.run(["pgrep", "-f", f"stampede.cli backfill(-rpc)? --db {store} "], capture_output=True, text=True).stdout
    return bool(out.strip())


def start(name: str, store: str, fr: int, to: int, path: str, logf: str) -> None:
    if path == "rpc":
        cmd = [str(PY), "-m", "stampede.cli", "backfill-rpc", "--db", store, "--from-block", str(fr), "--to-block", str(to), "--workers", "3", "--label", f"part {name} (rpc)"]
    else:
        cmd = [str(PY), "-m", "stampede.cli", "backfill", "--db", store, "--from-block", str(fr), "--to-block", str(to), "--label", f"part {name} (hypersync)"]
    with open(ROOT / logf, "ab") as f:
        subprocess.Popen(cmd, cwd=ROOT, stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
    log(f"started worker {name} ({path}) from cursor {cursor(store)}")


def run_step(args: list[str], logf: Path) -> int:
    log(f">>> {' '.join(args)}")
    with open(logf, "ab") as f:
        rc = subprocess.run([str(PY), "-m", "stampede.cli", *args], cwd=ROOT, stdout=f, stderr=subprocess.STDOUT).returncode
    log(f"<<< exit {rc}")
    return rc


def finish() -> None:
    main = ROOT / "data" / "research-14d.sqlite"
    hs_backup = ROOT / "data" / "research-14d-hypersync.sqlite"
    part_a = ROOT / PARTS[0][1]
    if main.exists() and not hs_backup.exists():
        # the first HyperSync pass normalized v4 rows without the infra seed: keep it aside, do not delete
        c = sqlite3.connect(main)
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        c.close()
        for suf in ("", "-wal", "-shm"):
            p = Path(str(main) + suf)
            if p.exists():
                p.rename(Path(str(hs_backup) + suf))
        log("hypersync store set aside as research-14d-hypersync.sqlite")
    if not main.exists():
        for suf in ("", "-wal", "-shm"):
            p = Path(str(part_a) + suf)
            if p.exists():
                p.rename(Path(str(main) + suf))
        log("part a installed as research-14d.sqlite")
    c = sqlite3.connect(main)
    c.execute("PRAGMA synchronous=OFF")
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA cache_size=-524288")
    for name, store, *_ in PARTS[1:]:
        src = ROOT / store
        if not src.exists():
            log(f"merge: {store} missing, skipped")
            continue
        n0 = c.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        c.execute("ATTACH DATABASE ? AS p", (str(src),))
        c.execute(f"INSERT OR IGNORE INTO trades({TRADE_COLS}) SELECT {TRADE_COLS} FROM p.trades")
        c.execute("INSERT OR IGNORE INTO txs SELECT * FROM p.txs")
        c.execute("INSERT OR REPLACE INTO blocks SELECT * FROM p.blocks")
        for t in ("launches", "graduations", "curves", "pools", "tokens", "logs", "launch_meta", "quotes", "infra"):
            try:
                c.execute(f"INSERT OR IGNORE INTO {t} SELECT * FROM p.{t}")
            except sqlite3.Error as e:  # a table missing in a segment store
                log(f"merge {name}: {t}: {e}")
        c.commit()
        c.execute("DETACH DATABASE p")
        n1 = c.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        log(f"merged {name}: +{n1 - n0:,} trades (total {n1:,})")
    c.execute("DELETE FROM wallets")
    c.execute("INSERT INTO wallets(address,is_contract,trades) SELECT wallet, NULL, COUNT(*) FROM trades GROUP BY wallet")
    for k, v in (("sample_from_block", FROM_BLOCK), ("sample_to_block", TO_BLOCK), ("backfill_cursor", TO_BLOCK + 1), ("sample_label", "PONS v2 backfill, 14 days")):
        c.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (k, json.dumps(v)))
    c.commit()
    rng = c.execute("SELECT MIN(block), MAX(block), datetime(MIN(ts),'unixepoch'), datetime(MAX(ts),'unixepoch'), COUNT(*) FROM trades").fetchone()
    c.close()
    log(f"merged store: blocks {rng[0]}-{rng[1]}, {rng[2]} .. {rng[3]} UTC, {rng[4]:,} trades")
    sys.path.insert(0, str(ROOT))
    from stampede.rotation import rotate  # noqa: E402
    from stampede.store import Store  # noqa: E402

    s = Store(main)
    s.db.execute("PRAGMA synchronous=OFF")
    for w in (300, 1800):
        t0 = time.time()
        r = rotate(s, w, progress=log)
        r.pop("top_edges", None)
        log(f"rotate {w}: {json.dumps(r)} in {time.time() - t0:.0f}s")
    s.close()
    run_step(["fx", "--db", "data/research-14d.sqlite", "--days", "16"], LOG)
    log("READY")
    plog = ROOT / "data" / "research-14d-pipeline.log"
    run_step(["launch-intel", "--db", "data/research-14d.sqlite", "--horizon", "3600", "--resolve-quotes", "--out", "docs/RESEARCH-LAUNCHES.md"], plog)
    run_step(["traders", "--db", "data/research-14d.sqlite", "--fees", "strict", "--out", "docs/RESEARCH-TRADERS.md"], plog)
    run_step(["edge-features", "--db", "data/research-14d.sqlite"], plog)
    run_step(["edge", "--db", "data/research-14d.sqlite", "--train-days", "7", "--test-days", "7", "--out", "docs/RESEARCH-EDGE.md"], plog)
    log("PIPELINE DONE")


def main() -> int:
    os.chdir(ROOT)
    if LOG.exists() and b"PIPELINE DONE" in LOG.read_bytes()[-4000:]:
        print("already done")
        return 0
    restarts = {p[0]: 0 for p in PARTS}
    log("orchestrator start")
    while True:
        pending = []
        for name, store, fr, to, path, logf in PARTS:
            cur = cursor(store)
            if cur is not None and cur > to:
                continue
            pending.append(name)
            if not running(store):
                if restarts[name] >= MAX_RESTARTS:
                    log(f"worker {name} exceeded {MAX_RESTARTS} restarts; giving up on it")
                    continue
                restarts[name] += 1
                start(name, store, fr, to, path, logf)
        if not pending:
            log("all parts done; finishing")
            finish()
            return 0
        time.sleep(60)


if __name__ == "__main__":
    sys.exit(main())
