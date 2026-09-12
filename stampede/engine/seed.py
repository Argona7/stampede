"""Seed the registry of an engine store from other stores (no network).

    python -m stampede.engine.seed --into data/live-engine.sqlite --from data/research-14d.sqlite --from data/stampede.sqlite

Copies `curves`, `pools`, `launches`, `graduations`, `quotes`, `infra`, `wallet_scores` and the `tokens` rows that
carry a symbol (INSERT OR IGNORE: the first source wins, existing rows are kept). The engine then knows every curve
and pool launched before it started, so a trade on an old curve resolves without an `eth_call`; the resolver fills
whatever launched between the seed and the chain head.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from ..store import Store

TABLES = ("curves", "pools", "launches", "graduations", "quotes", "infra", "wallet_scores", "tokens")


def seed(into: Path, sources: list[Path], tables: tuple[str, ...] = TABLES) -> dict[str, Any]:
    dst = Store(into)
    db = dst.db
    out: dict[str, Any] = {"into": str(into), "sources": [str(s) for s in sources], "copied": {}, "seconds": 0.0}
    t0 = time.time()
    for i, src in enumerate(sources):
        if not Path(src).exists():
            out["copied"][str(src)] = "missing"
            continue
        alias = f"src{i}"
        db.execute(f"ATTACH DATABASE ? AS {alias}", (str(src),))  # read only by usage: nothing below writes to the source
        try:
            copied: dict[str, int] = {}
            src_tables = {r[0] for r in db.execute(f"SELECT name FROM {alias}.sqlite_master WHERE type='table'")}
            for t in tables:
                if t not in src_tables:
                    continue
                dst_cols = [r[1] for r in db.execute(f"PRAGMA table_info({t})")]
                src_cols = {r[1] for r in db.execute(f"PRAGMA {alias}.table_info({t})")}
                cols = [c for c in dst_cols if c in src_cols]
                if not cols:
                    continue
                col_list = ",".join(f'"{c}"' for c in cols)
                where = " WHERE symbol IS NOT NULL AND symbol<>''" if t == "tokens" else ""
                before = db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                db.execute(f"INSERT OR IGNORE INTO {t}({col_list}) SELECT {col_list} FROM {alias}.{t}{where}")
                copied[t] = db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] - before
            db.commit()
            out["copied"][str(src)] = copied
        finally:
            db.execute(f"DETACH DATABASE {alias}")
    db.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", ("engine_seed", json.dumps({"sources": out["sources"], "at": int(time.time())})))
    db.commit()
    out["seconds"] = round(time.time() - t0, 1)
    out["totals"] = {t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
    dst.close()
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m stampede.engine.seed", description=__doc__.split("\n\n")[0])
    ap.add_argument("--into", required=True, help="engine store to seed (created if missing)")
    ap.add_argument("--from", dest="sources", action="append", required=True, help="source store; repeatable, first wins")
    args = ap.parse_args(argv)
    try:
        res = seed(Path(args.into), [Path(s) for s in args.sources])
    except sqlite3.Error as e:
        print(f"seed failed: {e}")
        return 1
    print(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
