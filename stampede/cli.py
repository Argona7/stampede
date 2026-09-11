"""`stampede` command line."""
from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="stampede", description="Read-only map of observed wallet rotations on Robinhood Chain.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("probe", help="measure data sources -> docs/SOURCES.md")

    p = sub.add_parser("ingest", help="fetch swap + transfer logs for a block window into SQLite")
    p.add_argument("--from-block", type=int)
    p.add_argument("--to-block", type=int)
    p.add_argument("--minutes", type=float, help="alternative: last N minutes before head-100")
    p.add_argument("--chunk", type=int, default=300)
    p.add_argument("--label", default=None, help="human label of the sample")

    p = sub.add_parser("normalize", help="raw logs -> trades with Transfer-based attribution")
    p.add_argument("--reset", action="store_true")

    p = sub.add_parser("rotate", help="trades -> sequences and edges")
    p.add_argument("--window", default="30m", help="e.g. 5m, 30m, 2h")
    p.add_argument("--all-windows", action="store_true", help="compute 5m, 30m and 2h")

    p = sub.add_parser("verify", help="cross-check a random subsample of trades against Alchemy receipts")
    p.add_argument("--n", type=int, default=200)
    p.add_argument("--seed", type=int, default=7)

    sub.add_parser("report", help="write docs/COVERAGE.md and docs/EXAMPLES.md")

    p = sub.add_parser("serve", help="run the API + web terminal")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8791)
    p.add_argument("--mode", default="fixture", choices=["fixture", "replay", "live"])
    p.add_argument("--window", default="30m")
    p.add_argument("--speed", type=float, default=10.0, help="initial replay speed of the shared session clock")
    p.add_argument("--context", default="live", choices=["live", "always", "off"], help="when to fetch external context (X, GeckoTerminal, holders): live mode only (default), always, or never")
    p.add_argument("--notify", action="store_true", help="macOS notification when an alert rule fires")

    p = sub.add_parser("terminal", help="full-screen terminal UI (TUI) on top of a running API")
    p.add_argument("--api-url", default="http://127.0.0.1:8791")
    p.add_argument("--poll", type=float, default=2.0, help="seconds between polls")

    p = sub.add_parser("repair-ts", help="fetch block headers for trades whose timestamp is unknown (0/NULL) and fix them")
    p.add_argument("--limit", type=int, default=20000)

    p = sub.add_parser("wallet-scores", help="copy wallet_scores (walk-forward runner rates, bot flags) from a research store into this store")
    p.add_argument("--from", dest="src", required=True, help="research sqlite produced by stampede.research.backtest --write-scores")

    p = sub.add_parser("sync-lifecycle", help="parse indexed TokenLaunched / PoolRegistered logs into launches and graduations")

    p = sub.add_parser("export-fixture", help="export the current sample as a static JSON fixture for the UI")
    p.add_argument("--out", default="web/public/fixture.json")
    p.add_argument("--window", default="30m")

    p = sub.add_parser("export-demo", help="pack the recorded sample into data/demo/stampede-demo.sqlite.xz (the bundle `stampede demo` replays)")
    p.add_argument("--src", default=None, help="source store (default: STAMPEDE_DB / data/stampede.sqlite)")
    p.add_argument("--windows", default="1800", help="sequence windows to include, seconds, comma-separated")

    p = sub.add_parser("backfill", help="days of PONS v2 history from HyperSync into a research store (needs HYPERSYNC_TOKEN)")
    p.add_argument("--db", default="data/research-14d.sqlite")
    p.add_argument("--days", type=float, default=14.0)
    p.add_argument("--from-block", type=int, default=None)
    p.add_argument("--to-block", type=int, default=None)
    p.add_argument("--label", default=None)
    p.add_argument("--no-resume", action="store_true", help="ignore the stored cursor and start over")
    p.add_argument("--rotate", action="store_true", help="compute 5 min and 30 min sequences when the trades pass is done")

    p = sub.add_parser("fx", help="USD rates of the quote assets (ETH hourly from CoinGecko, other pair tokens spot from GeckoTerminal) into fx_rates")
    p.add_argument("--db", default=None)
    p.add_argument("--days", type=float, default=14.0)

    p = sub.add_parser("demo", help="replay the bundled recorded sample; no API keys needed")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8791)
    p.add_argument("--window", default="30m")
    p.add_argument("--speed", type=float, default=20.0)
    p.add_argument("--refresh", action="store_true", help="re-unpack data/demo.sqlite from the bundle")
    p.add_argument("--bundle-url", default=None, help="where to download data/demo/stampede-demo.sqlite.xz from if the checkout has no bundle (or set STAMPEDE_DEMO_URL)")

    args = ap.parse_args(argv)

    if args.cmd == "probe":
        from .probe import main_probe

        main_probe()
        return 0
    if args.cmd == "ingest":
        from .ingest import main_ingest

        return main_ingest(args)
    if args.cmd == "normalize":
        from .normalize import main_normalize

        return main_normalize(args)
    if args.cmd == "rotate":
        from .rotation import main_rotate

        return main_rotate(args)
    if args.cmd == "verify":
        from .verify import main_verify

        return main_verify(args)
    if args.cmd == "report":
        from .coverage import main_report

        return main_report(args)
    if args.cmd == "serve":
        from .api.app import main_serve

        return main_serve(args)
    if args.cmd == "export-fixture":
        from .api.fixture import main_export

        return main_export(args)
    if args.cmd == "export-demo":
        from .demo import main_export as main_export_demo

        return main_export_demo(args)
    if args.cmd == "demo":
        from .demo import main_demo

        return main_demo(args)
    if args.cmd == "fx":
        from .context.fx import main_fx

        return main_fx(args)
    if args.cmd == "backfill":
        from .research.backfill import main_backfill

        return main_backfill(args)
    if args.cmd == "terminal":
        from .tui.app import main_terminal

        return main_terminal(args)
    if args.cmd == "repair-ts":
        from .normalize import main_repair_ts

        return main_repair_ts(args)
    if args.cmd == "wallet-scores":
        from .store import Store

        src = Store(args.src)
        rows = src.db.execute("SELECT wallet, rotations, runner_hits, score, is_bot, trades_per_hour, updated_at FROM wallet_scores").fetchall()
        dst = Store()
        dst.db.executemany("INSERT OR REPLACE INTO wallet_scores(wallet, rotations, runner_hits, score, is_bot, trades_per_hour, updated_at) VALUES(?,?,?,?,?,?,?)", rows)
        dst.commit()
        print(f"copied {len(rows)} wallet scores into {dst.path}")
        return 0
    if args.cmd == "sync-lifecycle":
        from .context.pons import sync_lifecycle
        from .store import Store

        print(sync_lifecycle(Store()))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
