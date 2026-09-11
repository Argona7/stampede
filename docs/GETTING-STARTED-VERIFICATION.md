# Getting-started guide — verification record

What was run to check [`GETTING-STARTED.md`](GETTING-STARTED.md), in order, with exit codes and the output actually seen.
Nothing below is inferred from documentation; every number comes from the run on 2026-09-11/12 described here.

## Scope and setup

| Item | Value |
|---|---|
| Commit verified | `302e235bb513b520072c3190ec683222fdf428b2` (`tui: bison mascot header, adaptive layout, …`), which contains `ba77ada` (web: pixel bison in the top strip) and `5ffe83c` (web rework). The guide, this file, the screenshots and the README change are committed on top of it and were not part of the run. |
| Clean copy | `git clone /Users/argona/dev/stampede /tmp/stampede-guide` — a **local clone of that commit stands in for the GitHub clone**, because the commits were not pushed yet when the run happened. Nothing else differs: the demo bundle was downloaded from the **real public release URL** by `stampede demo` itself. The clone had no `.env`, no `data/demo/`, no `data/demo.sqlite` (checked with `ls`). |
| Environment of the server | started through `env -i HOME=$HOME PATH=$PATH`, so the server process saw no environment variables besides those two (the interactive shell used for the build commands carried unrelated variables; none of them reached the server). |
| OS | macOS 26.5.1 (build 25F80), Apple silicon (`arm64`) — `sw_vers`, `uname -m` |
| Tools | git 2.50.1 (Apple Git-155) · uv 0.11.21 (2026-06-11) · node v26.3.0 · npm 11.16.0 · gh 2.94.0 · CPython 3.13.14 (uv-managed, already in uv's cache — the Python *download* step itself was therefore not observed) · Textual 8.2.8 · FastAPI 0.141.1 · uvicorn 0.52.4 · requests 2.34.2 · Playwright 1.63.0 with Chromium 153.0.8010.12 (build 1243) · Vite 8.3.0 · TypeScript 6.0.3 |
| Ports | clean-copy demo server on **8795**; error scenarios on **8796**. The owner's server on 8791 (and the unrelated listeners on 8787/8797) were never touched; `lsof` at the end still shows them. |
| Verified path | macOS only. **Windows and Linux: not verified.** |

## Commands in order (final run at `302e235`)

Times are wall-clock from `time` or from timestamps in the logs.

| # | Command (from `/tmp/stampede-guide` unless noted) | Exit | Observed |
|---|---|---|---|
| 1 | `git clone /Users/argona/dev/stampede /tmp/stampede-guide` | 0 | 1.1 s; `git log -1` = `302e235`; `git status --short` empty; `.env`, `data/demo`, `data/demo.sqlite` absent |
| 2 | `uv sync` | 0 | 0.75 s (warm uv cache). `Using CPython 3.13.14` · `Creating virtual environment at: .venv` · `Resolved 37 packages` · `Installed 36 packages` (textual 8.2.8, fastapi 0.141.1, uvicorn 0.52.4, requests 2.34.2, pytest 9.1.1, …). `.venv/bin/python --version` = `Python 3.13.14` |
| 3 | `cd web && npm ci` | 0 | 1.1 s (warm npm cache). `added 61 packages, and audited 62 packages` · `found 0 vulnerabilities` · `npm warn allow-scripts 1 package has install scripts not yet covered by allowScripts: fsevents@2.3.3` |
| 4 | `npm run build` (in `web/`) | 0 | 2.6 s. `tsc -b && vite build` · `✓ 260 modules transformed` · `dist/assets/index-BRGkYoUR.js 999.55 kB │ gzip: 276.27 kB` · `✓ built in 389ms` · chunk-size notice. `du -sh dist` = 1.5M |
| 5 | `env -u NO_COLOR TEXTUAL_COLOR_SYSTEM=truecolor uv run pytest -q` | **1** | **2 failed, 59 passed, 2 warnings in 15.44s.** Failed: `tests/test_tui.py::test_bison_mascot_downsample_is_pure_and_brand_coloured` (`assert (None is not None)`) and `tests/test_tui.py::test_header_variants_by_size_and_quit` (`assert (128 == (25 * 12))`). Cause: both need Pillow, which `pyproject.toml` does not declare (see Open issues). Check: `uv pip install pillow` then the same command → `61 passed, 2 warnings in 16.70s`; `uv sync` afterwards removed pillow again (`- pillow==12.3.0`), restoring the locked environment. |
| 6 | `python3 scripts/bg.py guide-demo -- env -i HOME=$HOME PATH=$PATH uv run stampede demo --port 8795` | 0 | started 21:19:09 UTC, `/api/session` answered at 21:19:15 UTC (~6 s incl. download + unpack). Log: `fetching demo bundle (https://github.com/Argona7/stampede/releases/download/v0.1.0/stampede-demo.sqlite.xz) → /private/tmp/stampede-guide/data/demo/stampede-demo.sqlite.xz`, then the five banner lines (`146,329 trades · 12,164 observed sequences (windows: 30 min) · replay 20×`, `web: http://127.0.0.1:8795/`, …). Files: `data/demo/stampede-demo.sqlite.xz` **17,143,344 bytes**, `data/demo.sqlite` **139,350,016 bytes**. In the earlier run at `5ffe83c` the same download took ~37 s (0 → 17.1 MB between 21:02:38 and 21:03:15 UTC, then unpack); `curl -L` of the asset later took 6.8 s. |
| 7 | `curl http://127.0.0.1:8795/` · `/api/session` · `/api/status` | 0 | index `HTTP 200` (478 B); session `mode replay`, `label "REPLAY 20× · PAUSED"`, `clock_ts 1789059597` = 16:59:57 UTC, `speed 20`, `span_s 1800`, `controls true`; status `sample.trades 146329`, `wallets 33743`, `connection "static"` |
| 8 | web screenshots: `node /tmp/guide-shots/shot.mjs` (Playwright, Chromium with `--use-angle=metal --use-gl=angle --enable-gpu --ignore-gpu-blocklist`, viewport 1440×900, dsf 1) | 0 | see *Screenshots*; every text quoted in the guide (badge, clock, summary line, row text, drawer buttons, FLOW summary, ribbon tooltip, caption, evidence header, the `0xd60c…7ab6` block and its three Blockscout links) was read from the DOM during this run (`/tmp/guide-shots/out/facts.json`). Replay from `PLAY` to the self-pause at 17:29:57 UTC: **90 s** measured. |
| 9 | web key check: `node /tmp/guide-shots/keys.mjs` | 0 | `Space` play→pause · `1/2/3` · tabs `←/→/Home/End` · row click selects + opens drawer · `↑/↓` move the selection · `D` toggles the drawer · `Enter` opens FLOW · `Esc` closes drawer → back to RADAR (a third `Esc` clears the selection) · `find a coin` + click `TruffleHog·7b08` opens its drawer · `P` toggles presentation · `A` toggles autopilot only in presentation — all as the guide states |
| 10 | `env -u NO_COLOR TEXTUAL_COLOR_SYSTEM=truecolor uv run python scripts/tui_shot.py --api-url http://127.0.0.1:8795 --size 120x36 --out /tmp/guide-shots/tui --wait 5 --keys "up,up,enter" --prefix tui120` (+ `--size 180x50 --keys "2"`), then `cd web && node e2e/svg2png.mjs /tmp/guide-shots/tui/*.svg` | 0 | 4 + 2 frames, 9.7 s. Real TUI (`StampedeTUI`) against the clean-copy API through Textual's `run_test` at exactly 120×36 / 180×50 — **not** a Terminal.app window. 180×50 shows the 16×8 head mark, not the bison (Pillow missing, see Open issues). |
| 11 | `cd web && npm run lint` | 0 | `oxlint`, no findings printed, 1.0 s |
| 12 | `cd web && STAMPEDE_URL=http://127.0.0.1:8795 npx playwright test` | 0 | **13 passed (42.4s)**; the suite pointed at the clean-copy server, not at 8791 |
| 13 | TUI shared clock: `tui_shot.py … --keys "space"` then `GET /api/session` | 0 | before `REPLAY 10× · PAUSED`, after the key `playing: true` (the e2e run had set 10× and seeked); paused again with `POST /api/session {"action":"pause"}` |
| 14 | end-of-sample restart: `POST {"action":"seek","ts":1789061397}` then `{"action":"toggle"}` | 0 | `at_end true` → after toggle `clock_ts 1789059597` (16:59:57): Play at the end starts the first full range again, as the guide says |
| 15 | stop: `kill -INT <pid>` (Ctrl+C equivalent) with the page open | 0 | port 8795 free within 2 s; browser strip `API UNREACHABLE · FAILED TO FETCH`; MAP overlay `API not reachable — Failed to fetch · nothing here is live. Is stampede demo (or stampede serve) still running in its terminal?` |
| 16 | restart: `python3 scripts/bg.py guide-demo -- env -i … uv run stampede demo --port 8795` | 0 | `/api/session` answering after 0.5 s (bundle and `demo.sqlite` present, no download); fresh session `REPLAY 20× · PAUSED` at 1789059597. Then stopped again with `kill -INT`; `lsof` shows nothing on 8795. |

## Troubleshooting scenarios actually reproduced

| Scenario | Command | Exit | Text seen |
|---|---|---|---|
| tool missing | `zsh -c 'nosuchtool --version'` | 127 | `zsh:1: command not found: nosuchtool` |
| wrong directory (`uv sync`) | `cd /tmp && uv sync` | 2 | ``error: No `pyproject.toml` found in current directory or any parent directory`` |
| wrong directory (`uv run`) | `cd $HOME && uv run stampede demo` | 2 | ``error: Failed to spawn: `stampede` `` · `Caused by: No such file or directory (os error 2)` |
| wrong directory (`npm ci` in the repo root) | `cd /tmp/stampede-guide && npm ci` | 1 | `npm error code EUSAGE` · `The npm ci command can only install with an existing package-lock.json or npm-shrinkwrap.json …` |
| wrong directory (`npm run build` in the repo root) | `cd /tmp/stampede-guide && npm run build` | 254 | `npm error code ENOENT` · `npm error enoent Could not read package.json: Error: ENOENT: no such file or directory, open '/private/tmp/stampede-guide/package.json'` |
| clone twice | `git clone /Users/argona/dev/stampede /tmp/stampede-guide` | 128 | `fatal: destination path '/tmp/stampede-guide' already exists and is not an empty directory.` |
| server not running (browser) | Playwright `page.goto('http://127.0.0.1:8796/')` | — | `net::ERR_CONNECTION_REFUSED at http://127.0.0.1:8796/` |
| server not running (curl) | `curl -sS http://127.0.0.1:8796/` | 7 | `curl: (7) Failed to connect to 127.0.0.1 port 8796 after 0 ms: Couldn't connect to server` |
| server not running (TUI) | `uv run stampede terminal --api-url http://127.0.0.1:8796` | 2 | `API not reachable at http://127.0.0.1:8796: connection refused at 127.0.0.1:8796 (is the server running?)` · `Start it first: uv run stampede demo   (or: python scripts/serve_daemon.py start --mode replay)` |
| server stopped while the page is open | step 15 above | — | strip `API UNREACHABLE · FAILED TO FETCH`; MAP `API not reachable …` |
| port in use | second `uv run stampede demo --port 8795` while the first ran | 3 | the banner prints, then `ERROR:    [Errno 48] error while attempting to bind on address ('127.0.0.1', 8795): [errno 48] address already in use`; the first server kept running |
| other port | `uv run stampede demo --port 8796` | 0 | banner with `web:      http://127.0.0.1:8796/`; served until stopped |
| paused | fresh server, page opened | — | badge `PAUSED · REPLAY 20×`, `CLOCK 16:59:57 UTC`, button `PLAY`; summary `164 coins with rotation inflow · last 30 min · as of 16:59:57 UTC · paused` (screenshot 01) |
| missing web build | `mv web/dist web/dist.off`, `uv run stampede demo --port 8796`, `curl -i /` | 0 | server: `web:      not built — `cd web && npm ci && npm run build`, then reload http://127.0.0.1:8796/`; `GET /` → `HTTP/1.1 200 OK`, `content-type: application/json`, body `{"stampede":"API only — build the web terminal with `cd web && npm run build`","status":"/api/status"}`; `/api/status` 200. `dist` restored afterwards |
| demo download failed (404) | fresh clone without a bundle, `uv run stampede demo --port 8796 --bundle-url https://github.com/Argona7/stampede/releases/download/v0.1.0/does-not-exist.sqlite.xz` | 1 | `fetching demo bundle (…) → …/data/demo/stampede-demo.sqlite.xz`, traceback ending in `requests.exceptions.HTTPError: 404 Client Error: Not Found for url: https://github.com/Argona7/stampede/releases/download/v0.1.0/does-not-exist.sqlite.xz`; `data/demo/` left empty (no `.part` file) |
| demo download failed (no connection) | `--bundle-url http://127.0.0.1:9/stampede-demo.sqlite.xz` | 1 | `requests.exceptions.ConnectionError: HTTPConnectionPool(host='127.0.0.1', port=9): Max retries exceeded … Failed to establish a new connection: [Errno 61] Connection refused` |
| manual bundle route | `curl -L -o data/demo/stampede-demo.sqlite.xz https://github.com/Argona7/stampede/releases/download/v0.1.0/stampede-demo.sqlite.xz` (6.8 s, 17,143,344 bytes), then `uv run stampede demo --port 8796` | 0 | no `fetching` line; unpack + banner; index 200 |
| no X mentions / holders | drawer of `RBNHD·4231` (X column `n/a`) | — | `ATTENTION · X — not fetched · external context is off in replay · Refresh context fetches it now (paid calls)`; `HOLDERS — not fetched · …`. `TruffleHog·7b08` instead shows `0 / 22` mentions and `5` holders — values cached in the bundle when the sample was recorded (`fetched 612 min ago`) |
| TUI too small | `tui_shot.py --size 90x24` | 0 | `WALLET` column dropped, symbols truncated (`Sw…·38be`), no `HOT ROTATIONS` / `ACTIVITY` panel, footer shortened to `↑/↓ select · Enter open · / search · 2 radar · q quit`; the app keeps working |

Not reproduced (stated as such in the guide): a browser without WebGL 2, Node older than 22, Safari/Firefox error pages (only Chromium was driven), a proxy blocking GitHub.

## Screenshots (`docs/assets/getting-started/`)

All from the clean-copy server on 8795 at `302e235`; PNGs as captured by Chromium (web) or Textual's SVG export rendered by
`web/e2e/svg2png.mjs` at 2× and palette-quantised to 256 colours (terminal); nothing drawn or retouched.

| File | Size | What it shows (clock) |
|---|---|---|
| `01-open-paused.png` | 243 KB, 1440×900 | RADAR as the page opens: `PAUSED · REPLAY 20×`, `CLOCK 16:59:57 UTC`, `PLAY`, 164 coins |
| `02-playing.png` | 246 KB | replay running: `REPLAY 20×`, `CLOCK 17:01:39 UTC`, `PAUSE`, rank-change marks |
| `03-radar-selected.png` | 209 KB | 17:29:57 UTC, row 15 `TruffleHog·7b08` selected, drawer `Piecoin·1a01 → 54` |
| `04-flow.png` | 143 KB | FLOW of `TruffleHog·7b08`: `Piecoin·1a01 54`, `TruffleHog·bd82 1`, no outflow |
| `05-map-route.png` | 117 KB | MAP presentation after the ribbon click: caption `54 WALLETS · SOLD Piecoin·1a01 → BOUGHT TruffleHog·7b08`, `883 observed sequence rows` |
| `06-evidence.png` | 259 KB | evidence panel scrolled to `0xd60c…7ab6`: sell `tx 0x17da…a56d` 17:10:09 UTC, buy `tx 0x20ed…4729` 17:24:35 UTC |
| `07-terminal-feed-120x36.png` | 326 KB, 2964×1858 | TUI FEED at 120×36 on the demo API (`REPLAY 20× · PAUSED`, 17:29:57 UTC, `HOT ROTATIONS: 54 Piecoin·1a01 → TruffleHog·7b08 883 seq`) |
| `08-terminal-evidence-120x36.png` | 336 KB | TUI after `↑ ↑ Enter`: EVIDENCE pane for `egregore → LUNAR·d070` |

Cross-check of the example against the API of the clean copy (`/api/edge/0x6c36…1a01/0x4ad5…7b08?window=1800s&from=…&to=…&exact=0`):
range 16:29:57–16:59:57 → 0 wallets (the route does not exist yet at the start clock); 16:49:57–17:19:57 → 54 wallets / 340
rows; 16:54:57–17:24:57 and 16:59:57–17:29:57 → **54 wallets / 883 rows**; the `0xd60c…7ab6` row with buy `0x20ed…4729`
(ts 1789061075 = 17:24:35 UTC) appears once the clock passes 17:24:35. RADAR at 17:29:57 (`under_radar`, default filters):
146 coins, `TruffleHog·7b08` rank 15, `inflow_10m 54`, sources `Piecoin·1a01 53 · TruffleHog·bd82 1` (sources are counted
over the last 10 minutes, the evidence panel over the 30-minute range — hence 53 vs 54). At 17:24:57 the same row is rank 6.

## Markdown render and links

- `gh api -X POST /markdown -f mode=gfm -f context=Argona7/stampede -F text=@docs/GETTING-STARTED.md` (and the README) →
  HTML wrapped with `github-markdown-css@5`, light and dark, 1280 px and 420 px viewports (Playwright): no horizontal
  overflow in any of the four renders; all 8 guide images and 6 README images load; page heights 17,421 px (desktop) /
  22,606 px (narrow) for the guide.
- In-page anchors: the 12 distinct `#…` links of the guide's contents all match a heading slug computed with GitHub's
  rules (`1-prepare-your-computer`, …, `optional-the-real-terminal-terminal-2`, `want-current-chain-data-live-setup-`,
  `web-keyboard-map`).
- Repo-relative links: guide → `GETTING-STARTED-VERIFICATION.md`, `DEVELOPMENT.md`, 8 × `assets/getting-started/*.png`;
  README → `docs/GETTING-STARTED.md` (new) plus the 12 existing doc/asset paths — all exist.
- External links (`curl -I -L`, HTTP 200 each): git-scm.com/downloads · docs.astral.sh/uv/getting-started/installation/ ·
  docs.astral.sh/uv/ · nodejs.org · github.com/Argona7/stampede/releases/tag/v0.1.0 · …/releases/download/v0.1.0/stampede-v5-autopilot.mp4 ·
  github.com/Argona7/stampede/issues · three Blockscout address/tx URLs · three shields.io badges.
- Release asset: `https://github.com/Argona7/stampede/releases/download/v0.1.0/stampede-demo.sqlite.xz` downloaded twice
  by `stampede demo` and once by `curl -L`: 17,143,344 bytes each time.
- **Public guide URL** `https://github.com/Argona7/stampede/blob/main/docs/GETTING-STARTED.md` → **HTTP 404** at the time
  of writing (the repository page itself is 200): the guide exists locally only until the owner pushes `main`.

## UI / TUI acceptance (pointers)

The before/after work, logo placement, resize, focus and stream-reading checks were recorded by the interface work itself:
[`acceptance/WEB-BEFORE-AFTER.md`](acceptance/WEB-BEFORE-AFTER.md) and [`acceptance/TUI-BEFORE-AFTER.md`](acceptance/TUI-BEFORE-AFTER.md).
What this run adds from the clean copy: the web strip shows `/brand-bison.png` (46 px) next to `STAMPEDE` in every view
(screenshots 01–06); the 13 web e2e tests (brand click, keyboard tabs, held ordering, narrow viewport, reduced motion, …)
pass against the clean copy; the TUI at 120×36 uses the compact three-row header by design (no mascot below 160 columns),
and at 180×50 falls back to the 16×8 head mark because Pillow is absent from a clean install.

## Not verified

- Windows and Linux (no machine in this run).
- Live mode (`serve --mode live`, Alchemy key, indexing, provider errors) — deliberately out of scope; the guide only points to `DEVELOPMENT.md`.
- A real Terminal.app / iTerm2 session of the TUI: frames come from Textual's headless renderer at the exact sizes; the TUI acceptance record covers PTY runs.
- First-ever `uv` Python download and first-ever npm package download (both caches were warm on this Mac); the measured 0.75 s / 1.1 s are lower bounds for a first install.
- Safari and Firefox (Chromium only), a browser without WebGL 2.

## Open issues found (not fixed here — outside the guide's files)

1. **Pillow is not a declared dependency.** `stampede/tui/brand.py` imports `PIL` for the bison header (≥ 160 columns) and
   two TUI tests assert on it. On a clean `uv sync`: `pytest` → 2 failed / 59 passed; the wide TUI header shows the head
   mark instead of the bison. Fix: add `pillow` to `[project] dependencies` in `pyproject.toml` and run `uv lock`; with
   Pillow present the suite is 61 passed (checked).
2. **Web page error when entering MAP from a FLOW ribbon:** `TypeError: Cannot read properties of undefined (reading 'x')`
   at `Vector3.lerpVectors` (Scene3D camera flight, `dist/assets/index-*.js`). Non-fatal: the route, caption and evidence
   render; seen on both runs (`5ffe83c`+web commit and `302e235`).
3. README badge `tests 48 passing` and the `# 48 tests` comment in the README *Development* section and in
   `docs/DEVELOPMENT.md` are stale: the suite has 61 tests. `docs/DEVELOPMENT.md` also still lists the old TUI keys
   (`Tab` feed/radar); the TUI now uses `1`/`2` for views and `Tab` for panes (see `acceptance/TUI-BEFORE-AFTER.md`).
4. The public guide URL returns 404 until `main` is pushed; the X reply must treat it as a future link until then.

## Logs

Command logs of the run: `/tmp/guide-verify/` (`f01-clone.log` … `f23-restart.log`, plus `ts-*.log` from the earlier run at
`5ffe83c`), screenshot facts `/tmp/guide-shots/out/facts.json`, key check `/tmp/guide-verify/f16-keys.log`. Not committed.
