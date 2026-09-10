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
    p.add_argument("--port", type=int, default=8787)
    p.add_argument("--mode", default="fixture", choices=["fixture", "replay", "live"])
    p.add_argument("--window", default="30m")

    p = sub.add_parser("export-fixture", help="export the current sample as a static JSON fixture for the UI")
    p.add_argument("--out", default="web/public/fixture.json")
    p.add_argument("--window", default="30m")

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
    return 1


if __name__ == "__main__":
    sys.exit(main())
