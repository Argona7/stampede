# Release checklist (publication itself is a separate owner decision)

Statuses re-checked 2026-09-11 after the demo-bundle and README work. `[x]` = verified today with the command shown;
`[ ]` = open; `[d]` = needs an owner decision.

## Runtime files and history

- [x] `.gitignore` covers `.env`, `data/*.sqlite*`, `data/*.log`, `data/serve.pid`, `data/cache/`, `data/demo/*.sqlite`, `data/demo/*.part`, `web/node_modules`, `web/dist`, `web/test-results`, `demo/*/raw/*.webm`, `demo/*/out/*.mp4`, `assets/brand/concepts/*.png`.
- [x] Full-history secrets scan (545 blobs, every commit): the two values from `.env` appear in no blob; no `alchemy.com/v2/<key>`, `Bearer …`, or `ALCHEMY_KEY=…`-style assignments anywhere; `.env` was never tracked. Method: `scripts`-free one-off Python over `git rev-list --all --objects` + `git cat-file`, values compared in memory and never printed.
- [x] `data/probe.json` is tracked on purpose and holds no key-like string (URLs redacted by `env.redact`).
- [x] Tracked runtime-ish files: only `demo/tui/pty-*.log` (terminal capability probes from stage 4, plain text). Keep or drop before publishing; they contain no data or keys.
- [d] `demo/v5/out/stampede-v5-autopilot.mp4` (16.7 MB) and `demo/v5/raw/web-walkthrough.webm` are ignored. Ship the MP4 as a release asset (recommended) or commit it once. README links to `docs/DEMO-V5.md`, not to the file, so nothing breaks either way.

## Configuration

- [x] `.env.example` documents `ALCHEMY_KEY` (ingest/live/verify), `HYPERSYNC_TOKEN` (optional), `PUBLIC_RPC`, `STAMPEDE_DB`, `TWITTERAPI_KEY` (optional). Add `STAMPEDE_DEMO_URL` when the bundle URL exists.
- [x] Ports: API + web on 8791; the TUI connects with `--api-url`. `stampede demo` uses the same default.
- [x] Modes: `serve --mode fixture|replay|live [--speed] [--context] [--notify]`; `demo` = replay of the bundle with context off.

## Demo sample (runs without keys)

- [x] `uv run stampede export-demo` packs the fixed sample out of the working store into `data/demo/stampede-demo.sqlite.xz`: 17.1 MB (xz), 139 MB unpacked; 146,329 trades, 87,497 sequences (30-min window; 331 direct, 11,833 clean, 75,333 ambiguous), 33,743 wallets, 3,148 txs, blocks/tokens/curves/pools/quotes/launches/wallet_scores/alerts whole; `live_*` meta keys dropped; long `candidates` lists compacted to counts + coins bought in between.
- [x] `uv run stampede demo` unpacks once to `data/demo.sqlite`, serves replay 20× on :8791 with external context off; `--bundle-url` / `STAMPEDE_DEMO_URL` download the bundle when the checkout has none. Checked with an empty environment (`env -i HOME PATH`): `/api/status` (sample = store = 146,329 trades, `context_enabled: false`), `/api/events`, `/api/radar` (254 rows), `/api/coin`, `/api/graph`, `/api/edge` (evidence rows with tx links), `/api/alerts` (15 journal rows with outcomes), web index 200.
- [x] Clean clone check, 2026-09-11: `git clone` → no `.env`, no bundle → `uv sync` (1.2 s from cache) → `uv run pytest -q` = 48 passed → `cd web && npm ci && npm run build` (dist 1.3 MB) → `uv run stampede demo --port 8796 --bundle-url http://127.0.0.1:8798/…` (bundle served from a local HTTP server standing in for the release asset) → all API surfaces as above → real Textual TUI frame and web screenshots (RADAR, MAP, FLOW) captured from the clone: `docs/acceptance/` (see `docs/BRAND-README-ACCEPTANCE.md`).
- [d] Where the 17 MB bundle lives: committed once in `data/demo/` (simplest clone-and-run) or as a release asset with `STAMPEDE_DEMO_URL` defaulted in `stampede/demo.py`. Currently untracked; README describes both paths.
- [x] Tests: `tests/test_demo_bundle.py` covers export (live-tail rows excluded, wallets filtered, candidates compacted, single file), pack/unpack/refresh, boot of `create_app` on the unpacked file with the key variables removed from the environment, and the missing-bundle error.

## Fresh-install check (what a reader does)

```sh
git clone <repo> stampede && cd stampede
uv sync && uv run pytest -q                 # 48 passed (headless Textual tests need NO_COLOR unset)
cd web && npm ci && npm run build && npm run lint && cd ..
uv run stampede demo                        # web on http://127.0.0.1:8791/, replay 20x, paused
uv run stampede terminal --api-url http://127.0.0.1:8791
```

Known: `uv sync` installs Textual 8.2.8, FastAPI, requests; `npm ci` pulls three 0.186, d3-force-3d, Playwright 1.63
(browsers via `npx playwright install chromium` only for the e2e tests).

## Dependencies and licence

- [ ] Python dependencies (uv.lock) and npm dependencies (package-lock.json): list licences before publishing (`uv pip list` + metadata, `npm ls --json`); nothing copyleft is expected (FastAPI/Starlette MIT, Textual MIT, requests Apache-2.0, three MIT, React MIT, d3 ISC) — verify, do not assume.
- [d] Project licence: not chosen. README says so explicitly and does not claim MIT.

## Repository

- [x] Local Git only, no remote (`git remote -v` empty). Latest commits: demo v5 with terminal intro, `stampede demo` / `export-demo`, README + docs/DEVELOPMENT.md, README media, mascot concept provenance.
- [d] Target GitHub account/repo and public visibility: owner decision. The brief does not authorise publication, a site, or posts.
- [ ] Final check on the real GitHub page after publication (rendering, image sizes, anchors); the pre-publication check used the GitHub Markdown API + github-markdown-css in light/dark, desktop/narrow (`docs/acceptance/readme-*.png`).

## Rollback

- Previous working version (Stage 1–3 web explorer, no TUI/3D): `git checkout v0.1-stage3`.
- The database format is forward-compatible: new tables are additive; `repair-ts` only fills unknown timestamps.

## Not part of this stage

Publishing the repository, a site, and the product posts remain separate owner decisions (brief, section 6–7).
