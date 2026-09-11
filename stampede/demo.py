"""Bundled demo: the recorded sample as a compact SQLite that the server can replay without any API key.

`stampede export-demo` writes `data/demo/stampede-demo.sqlite.gz` from the working store (the rows of the
fixed sample only: trades, the sequences of the chosen windows, the wallets/txs they reference, token and
lifecycle tables, wallet scores, the alert journal). `stampede demo` unpacks that bundle to `data/demo.sqlite`
(once) and serves it in replay mode. Nothing here reads `.env`: no Alchemy, no twitterapi, no Higgsfield.
"""
from __future__ import annotations

import json
import lzma
import shutil
import time
from pathlib import Path
from typing import Any

from .env import ROOT, env
from .store import Store

BUNDLE = ROOT / "data" / "demo" / "stampede-demo.sqlite.xz"
DEMO_DB = ROOT / "data" / "demo.sqlite"

# copied whole: small reference tables the API joins against
FULL_TABLES = ("blocks", "curves", "pools", "tokens", "infra", "quotes", "launches", "graduations", "launch_meta", "wallet_scores", "alerts", "x_cache")
# copied for the sample only
META_KEYS = ("sample_from_block", "sample_to_block", "sample_label")


def _unlink(p: Path) -> None:
    for s in ("", "-wal", "-shm"):
        Path(str(p) + s).unlink(missing_ok=True)


def export_demo(src: Path, out: Path, windows: tuple[int, ...] = (1800,)) -> dict[str, Any]:
    """Copy the fixed sample out of `src` into a fresh single-file SQLite at `out`. Returns row counts."""
    _unlink(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    dst = Store(out)
    db = dst.db
    db.execute("ATTACH DATABASE ? AS src", (str(src),))
    fr = json.loads(db.execute("SELECT value FROM src.meta WHERE key='sample_from_block'").fetchone()[0])
    to = json.loads(db.execute("SELECT value FROM src.meta WHERE key='sample_to_block'").fetchone()[0])
    counts: dict[str, int] = {}
    for t in FULL_TABLES:
        cur = db.execute(f"INSERT OR IGNORE INTO main.{t} SELECT * FROM src.{t}")
        counts[t] = cur.rowcount
    for k in META_KEYS:
        db.execute("INSERT OR REPLACE INTO main.meta SELECT key, value FROM src.meta WHERE key=?", (k,))
    counts["trades"] = db.execute("INSERT INTO main.trades SELECT * FROM src.trades WHERE block BETWEEN ? AND ?", (fr, to)).rowcount
    wl = ",".join("?" * len(windows))
    counts["sequences"] = db.execute(
        f"""INSERT INTO main.sequences SELECT s.* FROM src.sequences s
            WHERE s.window_s IN ({wl}) AND s.sell_trade IN (SELECT id FROM main.trades) AND s.buy_trade IN (SELECT id FROM main.trades)""",
        windows,
    ).rowcount
    # `candidates` of ambiguous sequences carries the full list of other coins the wallet sold in the window; the
    # UI shows the count (`sold_in_window_n`) and the coins bought in between, so keep those and drop the long list.
    for sid, cand in db.execute("SELECT id, candidates FROM main.sequences WHERE candidates IS NOT NULL AND length(candidates) > 120").fetchall():
        try:
            c = json.loads(cand)
        except ValueError:
            continue
        c = {"sold_in_window": [], "sold_in_window_n": c.get("sold_in_window_n", len(c.get("sold_in_window") or [])), "bought_between": c.get("bought_between") or []}
        db.execute("UPDATE main.sequences SET candidates=? WHERE id=?", (json.dumps(c, separators=(",", ":")), sid))
    counts["wallets"] = db.execute("INSERT OR IGNORE INTO main.wallets SELECT * FROM src.wallets WHERE address IN (SELECT DISTINCT wallet FROM main.trades)").rowcount
    counts["txs"] = db.execute("INSERT OR IGNORE INTO main.txs SELECT * FROM src.txs WHERE hash IN (SELECT DISTINCT tx_hash FROM main.trades)").rowcount
    for t in ("market_cache", "holders_cache"):
        counts[t] = db.execute(f"INSERT OR IGNORE INTO main.{t} SELECT * FROM src.{t} WHERE token IN (SELECT DISTINCT token FROM main.trades)").rowcount
    db.commit()
    db.execute("DETACH DATABASE src")
    dst.set_meta("demo_bundle", {"exported_at": int(time.time()), "windows_s": list(windows), "source_sample_blocks": [fr, to], "rows": counts})
    db.execute("PRAGMA journal_mode=DELETE")
    db.execute("VACUUM")
    db.commit()
    dst.close()
    return {"from_block": fr, "to_block": to, "rows": counts, "bytes": out.stat().st_size}


def pack(sqlite_path: Path, bundle: Path = BUNDLE) -> int:
    bundle.parent.mkdir(parents=True, exist_ok=True)
    with open(sqlite_path, "rb") as f, lzma.open(bundle, "wb", preset=9 | lzma.PRESET_EXTREME) as g:
        shutil.copyfileobj(f, g)
    return bundle.stat().st_size


def fetch_bundle(url: str, bundle: Path = BUNDLE) -> Path:
    """Download the bundle (a release asset) when it is not in the checkout. Plain HTTPS GET, no credentials."""
    import requests

    bundle.parent.mkdir(parents=True, exist_ok=True)
    tmp = bundle.with_suffix(".part")
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(tmp, "wb") as g:
            for chunk in r.iter_content(1 << 20):
                g.write(chunk)
    tmp.rename(bundle)
    return bundle


def ensure_demo_db(refresh: bool = False, bundle: Path = BUNDLE, dest: Path = DEMO_DB, url: str | None = None) -> Path:
    """Unpack the bundled sample to `dest` unless it is already there (or `refresh` is set)."""
    if dest.exists() and not refresh:
        return dest
    if not bundle.exists():
        url = url or env("STAMPEDE_DEMO_URL")
        if url:
            print(f"fetching demo bundle from {url}")
            fetch_bundle(url, bundle)
        else:
            raise SystemExit(f"demo bundle not found: {bundle}\n(download it from the release assets to that path, set STAMPEDE_DEMO_URL, or run `stampede export-demo` on a store with a recorded sample)")
    _unlink(dest)
    tmp = dest.with_suffix(".tmp")
    with lzma.open(bundle, "rb") as f, open(tmp, "wb") as g:
        shutil.copyfileobj(f, g)
    tmp.rename(dest)
    return dest


def main_export(args) -> int:
    src = Path(args.src) if args.src else None
    from .env import db_path

    src = src or db_path()
    tmp = BUNDLE.parent / "stampede-demo.sqlite"
    windows = tuple(int(w) for w in str(args.windows).split(","))
    info = export_demo(src, tmp, windows=windows)
    packed = pack(tmp, BUNDLE)
    tmp.unlink(missing_ok=True)
    print(json.dumps({**info, "bundle": str(BUNDLE), "bundle_bytes": packed}, indent=1))
    return 0


def main_demo(args) -> int:
    import uvicorn

    from .api.app import WEB_DIST, create_app

    db = ensure_demo_db(refresh=bool(getattr(args, "refresh", False)), url=getattr(args, "bundle_url", None))
    s = Store(db)
    try:
        smp = json.loads(s.db.execute("SELECT value FROM meta WHERE key='sample_label'").fetchone()[0])
        n_tr = s.db.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        n_sq = s.db.execute("SELECT COUNT(*) FROM sequences WHERE grade IN ('direct','clean')").fetchone()[0]
        wins = sorted({r[0] for r in s.db.execute("SELECT DISTINCT window_s FROM sequences")})
    finally:
        s.close()
    app = create_app(mode="replay", window=args.window, db=db, speed=args.speed, context_policy="off", notify=False)
    url = f"http://{args.host}:{args.port}/"
    wl = ", ".join(f"{w // 60} min" for w in wins) or "none"
    lines = [
        f'STAMPEDE demo · recorded sample "{smp}" · {n_tr:,} trades · {n_sq:,} observed sequences (windows: {wl}) · replay {args.speed:g}×',
        "no API keys are read in this mode; external context (X, GeckoTerminal, holders) stays off",
        f"web:      {url}" if WEB_DIST.exists() else f"web:      not built — `cd web && npm ci && npm run build`, then reload {url}",
        f"terminal: uv run stampede terminal --api-url {url.rstrip('/')}",
        "controls: space play/pause · ←/→ seek · [ ] speed · q quit",
    ]
    print("\n".join(lines), flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0
