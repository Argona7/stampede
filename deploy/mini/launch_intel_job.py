"""Refresh `launch_intel` of the live store on the Mac mini without fighting the engine for the write lock.

    .venv/bin/python deploy/mini/launch_intel_job.py --db data/live-engine.sqlite --horizon 3600 --hours 24

Why not `stampede launch-intel --db data/live-engine.sqlite` directly: `context/launch_intel.py` iterates a SELECT
cursor over `launches` and INSERTs into `launch_intel` on the same connection while the cursor is open. Against a store
the engine commits to every 250 ms that upgrade from the read snapshot fails at once with `database is locked`
(SQLITE_BUSY_SNAPSHOT - the busy timeout does not apply). Measured 2026-09-13 on the mini: 3 of 3 runs, 0.5 s each.

So this job (1) mirrors what launch_intel reads into a scratch store (`<db>-launch-intel.sqlite`, kept between runs and
topped up with `INSERT OR IGNORE`: registry tables in full, `trades` of the last --hours and their `blocks`, older ones
deleted; the raw `logs` table is skipped - the mini does not persist it and launch_intel only uses it for the optional
SnipeTaxExempted count), (2) runs
`launch_intel.compute` there, (3) merges the rows back in one short `BEGIN IMMEDIATE` transaction: rows of launches
inside the copied trade window replace the live ones (their facts are complete), older rows are only inserted when
the live store has none (never degrade an existing row with a computation that saw fewer trades).

Once launch_intel.py materializes the launches cursor (`.fetchall()`) or writes through a second connection, the plist
can run the plain CLI again and this file can go.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from stampede.context import launch_intel  # noqa: E402
from stampede.store import Store  # noqa: E402

FULL_TABLES = ("meta", "launches", "graduations", "quotes", "tokens", "wallets", "launch_meta")


def log(msg: str) -> None:
    print(time.strftime("%Y-%m-%dT%H:%M:%S ", time.gmtime()) + msg, flush=True)


def build_scratch(live: Path, scratch: Path, hours: float) -> tuple[int | None, int]:
    st = Store(scratch)  # the engine's schema; the file persists between runs (delete it to rebuild from scratch)
    launch_intel.ensure_table(st.db)
    db = st.db
    db.execute("ATTACH DATABASE ? AS live", (str(live),))  # read only by usage: nothing below writes to live.*

    def copy(t: str, where: str = "", params: tuple = ()) -> None:
        dst = [r[1] for r in db.execute(f"PRAGMA table_info({t})")]
        src = {r[1] for r in db.execute(f"PRAGMA live.table_info({t})")}
        cols = ",".join(f'"{c}"' for c in dst if c in src)  # explicit columns: the live store may have been migrated in another order
        if cols:
            db.execute(f"INSERT OR IGNORE INTO {t}({cols}) SELECT {cols} FROM live.{t}{where}", params)

    try:
        # one read snapshot for every copy (WAL readers never block the engine's writer)
        db.execute("BEGIN")
        for t in FULL_TABLES + ("launch_intel",):
            copy(t)
        since = time.time() - hours * 3600
        db.execute("DELETE FROM trades WHERE ts < ?", (since,))
        copy("trades", " WHERE ts >= ?", (since,))
        r = db.execute("SELECT MIN(block), COUNT(*) FROM trades").fetchone()
        min_block = int(r[0]) if r and r[0] is not None else None
        n_trades = int(r[1]) if r else 0
        if min_block is not None:
            db.execute("DELETE FROM blocks WHERE number < ?", (min_block,))
            copy("blocks", " WHERE number >= ?", (min_block,))
        db.commit()
    finally:
        db.execute("DETACH DATABASE live")
    st.close()
    return min_block, n_trades


def merge_back(live: Path, scratch: Path, min_block: int | None) -> tuple[int, int]:
    db = sqlite3.connect(str(live), timeout=30)
    db.execute("PRAGMA busy_timeout=30000")
    launch_intel.ensure_table(db)
    db.execute("ATTACH DATABASE ? AS s", (str(scratch),))
    try:
        cols = [r[1] for r in db.execute("PRAGMA table_info(launch_intel)")]
        cl = ",".join(f'"{c}"' for c in cols)
        # the rows the live store lacks are found outside the write lock (426k on the first run, ~0 afterwards)
        missing = [r[0] for r in db.execute("SELECT token FROM s.launch_intel WHERE (launch_block IS NULL OR launch_block < ?) AND token NOT IN (SELECT token FROM main.launch_intel)", (min_block or 0,))]
        db.execute("BEGIN IMMEDIATE")  # take the write lock up front: no snapshot upgrade, the busy timeout applies
        replaced = inserted = 0
        if min_block is not None:
            replaced = db.execute(f"INSERT OR REPLACE INTO launch_intel({cl}) SELECT {cl} FROM s.launch_intel WHERE launch_block >= ?", (min_block,)).rowcount
        for i in range(0, len(missing), 5000):
            chunk = missing[i:i + 5000]
            inserted += db.execute(f"INSERT OR IGNORE INTO launch_intel({cl}) SELECT {cl} FROM s.launch_intel WHERE token IN ({','.join('?' * len(chunk))})", chunk).rowcount
        db.commit()
    finally:
        db.execute("DETACH DATABASE s")
        db.close()
    return replaced, inserted


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--db", default="data/live-engine.sqlite", help="the live engine's store")
    ap.add_argument("--horizon", type=int, default=3600)
    ap.add_argument("--gain", type=float, default=1.0)
    ap.add_argument("--hours", type=float, default=24.0, help="trades (and their blocks) copied into the scratch store")
    a = ap.parse_args(argv)
    live = (ROOT / a.db) if not Path(a.db).is_absolute() else Path(a.db)
    scratch = live.with_name(live.stem + "-launch-intel.sqlite")
    t0 = time.time()
    min_block, n_trades = build_scratch(live, scratch, a.hours)
    t1 = time.time()
    st = Store(scratch)
    try:
        stats = launch_intel.compute(st, a.horizon, a.gain, 0, None, progress=None)
    finally:
        st.close()
    t2 = time.time()
    replaced, inserted = merge_back(live, scratch, min_block)
    t3 = time.time()
    dp = stats.get("deployer_pass", {})
    log(f"launch-intel: scratch {t1 - t0:.1f} s ({n_trades:,} trades since block {min_block}), compute {t2 - t1:.1f} s "
        f"({dp.get('launches', 0):,} launches, {dp.get('observed', 0):,} observed, {stats.get('farm_pass', {}).get('launch_farm', 0):,} farms), "
        f"merge {t3 - t2:.1f} s (replaced {replaced}, inserted {inserted}); total {t3 - t0:.1f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
