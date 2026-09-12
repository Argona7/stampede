# Development reference

Commands, modes, keys, API and layout. The README keeps the short version; this page is the complete one.

## Commands

```sh
uv sync                                                   # Python 3.13 environment (Textual, FastAPI, requests, pytest)
uv run stampede demo [--port 8791] [--speed 20]           # replay the bundled sample, no API keys (unpacks data/demo/stampede-demo.sqlite.xz once)
uv run stampede terminal --api-url http://127.0.0.1:8791  # terminal UI on top of any running server

uv run stampede probe                                     # measure data sources -> docs/SOURCES.md (needs ALCHEMY_KEY)
uv run stampede ingest --minutes 60 --label "PONS v2 sample, 60 min"
uv run stampede normalize --reset                         # logs -> trades (Transfer-based attribution)
uv run stampede rotate --all-windows                      # sequences + edges for 5 min / 30 min / 2 h
uv run stampede verify --n 200                            # receipt cross-check via Alchemy
uv run stampede report                                    # docs/COVERAGE.md + docs/EXAMPLES.md
uv run stampede repair-ts                                 # fetch block headers for trades with unknown time
uv run stampede sync-lifecycle                            # TokenLaunched / PoolRegistered logs -> launches, graduations
uv run stampede wallet-scores --from data/research.sqlite # copy wallet quality from a research store
uv run stampede traders --db data/research-14d.sqlite --out docs/RESEARCH-TRADERS.md   # FIFO ledgers with fees -> wallet_stats / wallet_positions, walk-forward, copy-test (--fees estimate for stores without fee columns)
uv run stampede export-demo [--windows 1800]              # pack the recorded sample into data/demo/stampede-demo.sqlite.xz
uv run stampede serve --mode fixture|replay|live [--speed 10] [--context live|always|off] [--notify]

cd web && npm ci && npm run build && npm run lint         # web app -> web/dist (served by the API process)
python scripts/serve_daemon.py start|stop|status --mode replay --port 8791   # detached server for development
```

Modes: `fixture` serves the recorded sample as a static snapshot; `replay` plays it on a server-side clock shared by
every client (`/api/session`: play, pause, seek, speed, span, window); `live` tails the chain head (measured head lag
2–5 s) and shows a provider error instead of a frozen "live" picture. `stampede demo` is `serve --mode replay` on the
bundled sample with external context off.

Environment (`.env`, never printed): `ALCHEMY_KEY` (ingest, verify, live, exact block times on demand),
`HYPERSYNC_TOKEN` (optional faster ingest), `PUBLIC_RPC`, `STAMPEDE_DB` (default `data/stampede.sqlite`),
`TWITTERAPI_KEY` (optional X-mentions column), `STAMPEDE_DEMO_URL` (override of the release-asset URL `stampede demo`
downloads the bundle from when `data/demo/` has none).

## Keys

Terminal: `↑/↓` select, `Enter` open evidence / coin card / wallet card, `Esc` back (filter → summary → table), `Tab`/`Shift+Tab` panes,
`1`/`2`/`3` FEED / RADAR / TRADERS screens, `/` search, `End`/`Home` follow latest / first row, `u g s a` radar presets,
`t m n b` trader presets (top / smart / snipers / bots), `r` refresh coin context, `space` play/pause, `←/→` seek 60 s, `[ ]` speed, `q` or `Ctrl+C` quit.

Web: `1 2 3 4` RADAR / FLOW / MAP / TRADERS, click a row or a ribbon, `D` coin drawer / wallet card, `E` evidence rows, `Esc` back,
`P` presentation/explore (MAP), `T` tape, `A` autopilot, `space` play/pause. URL parameters: `?view=radar|flow|map|traders`,
`?wallet=0x…` (opens the wallet card), `?layout=presentation`, `?autopilot=1`.

## API

`/api/status` (scopes: fixed sample vs whole store, session, context worker), `/api/session` GET/POST (shared clock),
`/api/events` (observed sequences with stable ids and a cursor; `history` after a seek, `new` afterwards; page while
`has_more`), `/api/radar` (ranked coins, score parts, presets, filters), `/api/coin/{token}` (everything about one coin;
`?refresh=1` fetches live context), `/api/alerts` (journal + track record), `/api/graph`, `/api/edge/{a}/{b}`,
`/api/token/{t}`, `/api/search`. The radar score, presets, context modules and the alert rule: `docs/RADAR.md`.

## Tests

- `uv run pytest -q` — 61 tests: chain constants, normalization, rotation, radar score, shared session (TUI + API in
  one process), TUI screens, the demo bundle (export, pack, unpack, boot without keys), one real recorded receipt.
- `cd web && npx playwright test` — 8 end-to-end tests against a running replay server on :8791 (`playwright.config.ts`).
- Headless Textual renders need `env -u NO_COLOR TEXTUAL_COLOR_SYSTEM=truecolor` in shells that set `NO_COLOR`.

## Research

`python -m stampede.research.backtest --db <store> --out docs/RESEARCH-RUNNERS.md --write-scores`, then
`uv run stampede wallet-scores --from <store>` to load wallet quality into the serving store. The measured numbers and
their honest reading are in `docs/RESEARCH-RUNNERS.md`.

## Layout

```
stampede/          Python package: chain constants, RPC client, ingest, normalize, rotation, coverage, demo bundle
stampede/api/      FastAPI app, queries, shared session clock, event stream, live tail, radar, context worker + alerts
stampede/context/  PONS lifecycle, GeckoTerminal market, X mentions, holders (cached external context)
stampede/research/ runner backtest (walk-forward), wallet scores
stampede/tui/      Textual terminal UI (stampede terminal)
web/               Vite + React + TypeScript: RADAR, FLOW, MAP (three.js scene, 2D fallback), tape, HUD
web/e2e/           Playwright tests and recording scripts
scripts/           serve_daemon.py, bg.py, session.py, tui_shot.py, readme_media.py
tests/             pytest: synthetic fixtures + one recorded Robinhood Chain receipt
docs/              measured reports, the algorithm, the radar, demo edit notes, briefs and acceptance
docs/assets/       README media built by scripts/readme_media.py
assets/brand/      mascot master, marks and rules (BRAND.md)
demo/              screenshots, raw recordings (ignored), cuts and edit notes
data/              SQLite stores (ignored) and the demo bundle
```

Documents: `docs/SOURCES.md` (measured data sources), `docs/ALGORITHM.md` (universe, attribution, grades, weights,
timestamps), `docs/COVERAGE.md` (what the sample contains and does not), `docs/EXAMPLES.md` (checkable sequences),
`docs/RADAR.md`, `docs/RESEARCH-RUNNERS.md`, `docs/DEMO-V5.md`, `docs/ACCEPTANCE-RU.md`, `docs/RELEASE-CHECKLIST.md`.
