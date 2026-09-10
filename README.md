# STAMPEDE

Watch wallets move between coins on Robinhood Chain.

STAMPEDE is a read-only map of *observed* wallet rotations between memecoins: a wallet sold token A and
later bought token B inside a chosen time window. Nodes are coins, directed edges are observed
`sell A → buy B` sequences, and every edge opens to the addresses, timestamps and transaction hashes
behind it.

What an edge is **not**: it is not proof that the money from the sale funded the purchase, not proof
that several addresses share an owner, not a prediction, and not a buy signal.

Two surfaces read one server and one shared replay clock: a full-screen **terminal UI** (`stampede
terminal`) and a **web scene** (2D explorer plus a WebGL 3D presentation mode). Black / red / neutral
text; red marks the brand, the selected route and newly observed sequences only.

Status: working prototype on one explicitly bounded sample (PONS v2 launchpad, 60 minutes). Not
published anywhere; no token; nothing is sent to the chain.

## Documents

- `docs/SOURCES.md`: measured data sources (public RPC, Alchemy, HyperSync, Blockscout, GeckoTerminal).
- `docs/ALGORITHM.md`: universe, attribution, sequence grades, edge weight, timestamps.
- `docs/COVERAGE.md`: what the sample contains, what it does not, verification against receipts.
- `docs/EXAMPLES.md`: independently checkable sequences with transaction links and exact block times.
- `docs/DEMO.md`: the first (v1) demo; `docs/DEMO-V2.md`: terminal + spatial scene demo, frame timing, edit notes.
- `docs/ACCEPTANCE-RU.md`: acceptance table with evidence; `docs/RELEASE-CHECKLIST.md`: what to do before any publication.
- `docs/VISUAL-AUDIT-RU.md`, `docs/TERMINAL-SPECTACLE-BRIEF-RU.md`, `docs/REFERENCE-NOTES-RU.md`: the review, the brief and the reference notes behind this stage.

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
python scripts/serve_daemon.py start --mode replay       # or --mode fixture / --mode live
open http://127.0.0.1:8791/                              # web explorer; ?layout=presentation for the scene
uv run stampede terminal --api-url http://127.0.0.1:8791 # the terminal UI, in Terminal/iTerm (120x36 or larger)
```

Modes: `fixture` serves the recorded sample as a static snapshot; `replay` plays it back on a
server-side clock shared by every client (`/api/session`: play, pause, seek, speed); `live` tails the
chain head (measured head lag 2–5 s) and shows a provider error instead of a frozen "live" picture.

Terminal keys: `↑/↓` select, `Enter` open pair, `Esc` back, `/` filter, `End` follow tail, `space`
play/pause, `←/→` seek 60 s, `[ ]` speed, `q` quit. Web keys: click a line or a coin, `E` evidence rows,
`Esc` overview, `P` presentation/explore, `space` play/pause.

API: `/api/status` (scopes: fixed sample vs whole store), `/api/session` (shared clock), `/api/events`
(observed sequences with stable ids and a cursor; `history` after a seek, `new` afterwards),
`/api/graph`, `/api/edge/{a}/{b}`, `/api/token/{t}`, `/api/search`.

Tests: `uv run pytest -q` (41) and, with the replay server running, `cd web && npx playwright test` (6).

## Layout

```
stampede/          Python package: chain constants, RPC client, ingest, normalize, rotation, coverage, API
stampede/api/      FastAPI app, queries, shared session clock, event stream, live tail
stampede/tui/      Textual terminal UI (stampede terminal)
web/               Vite + React + TypeScript: strip, 2D explorer, WebGL 3D scene, presentation layout
web/e2e/           Playwright tests and recording scripts
scripts/           serve_daemon.py, bg.py, pty_check.py, tui_shot.py
tests/             pytest: synthetic fixtures + one recorded Robinhood Chain receipt
docs/              measured reports and the algorithm description
demo/              screenshots, raw recording, cuts and edit notes
data/              SQLite store (not committed)
```
