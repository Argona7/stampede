# STAMPEDE

Watch wallets move between coins on Robinhood Chain.

STAMPEDE is a read-only map of *observed* wallet rotations between memecoins: a wallet sold token A and
later bought token B inside a chosen time window. Nodes are coins, directed edges are observed
`sell A → buy B` sequences, and every edge opens to the addresses, timestamps and transaction hashes
behind it.

What an edge is **not**: it is not proof that the money from the sale funded the purchase, not proof
that several addresses share an owner, not a prediction, and not a buy signal.

Status: working prototype on one explicitly bounded sample (PONS v2 launchpad, 60 minutes). Not
published anywhere; no token; nothing is sent to the chain.

## Documents

- `docs/SOURCES.md`: measured data sources (public RPC, Alchemy, HyperSync, Blockscout, GeckoTerminal).
- `docs/ALGORITHM.md`: universe, attribution, sequence grades, edge weight, timestamps.
- `docs/COVERAGE.md`: what the sample contains, what it does not, verification against receipts.
- `docs/EXAMPLES.md`: independently checkable sequences with transaction links and exact block times.
- `docs/DEMO.md`: how the demo was recorded and edited; how to record it yourself.

## Run

Requirements: Python 3.13 with [uv](https://docs.astral.sh/uv/), Node 22+, an Alchemy app key for
`robinhood-mainnet` (free tier is enough). Copy `.env.example` to `.env` and fill `ALCHEMY_KEY`.
`HYPERSYNC_TOKEN` is optional; without it the ingest uses Alchemy.

```sh
uv sync
uv run stampede probe                                    # docs/SOURCES.md, measured
uv run stampede ingest --minutes 60 --label "PONS v2 sample, 60 min"
uv run stampede normalize --reset                        # logs -> trades (Transfer-based attribution)
uv run stampede rotate --all-windows                     # sequences + edges for 5 min / 30 min / 2 h
uv run stampede verify --n 200                           # receipt cross-check via Alchemy
uv run stampede report                                   # docs/COVERAGE.md + docs/EXAMPLES.md
uv run pytest -q

cd web && npm install && npm run build && cd ..
python scripts/serve_daemon.py start --mode fixture      # or --mode replay / --mode live
open http://127.0.0.1:8791/
```

Modes: `fixture` serves the recorded sample as a static snapshot; `replay` plays it back with a visible
clock; `live` tails the chain head (measured head lag 2–5 s) and shows a connection error instead of a
frozen "live" picture when the provider fails.

## Layout

```
stampede/          Python package: chain constants, RPC client, ingest, normalize, rotation, coverage, API
stampede/api/      FastAPI app, queries, live tail
web/               Vite + React + TypeScript terminal (canvas map, evidence panel, replay controls)
web/e2e/           Playwright recording script for the demo
tests/             pytest: synthetic fixtures + one recorded Robinhood Chain receipt
docs/              measured reports and the algorithm description
demo/              screenshots, raw recording, cuts and edit notes
data/              SQLite store (not committed)
```
