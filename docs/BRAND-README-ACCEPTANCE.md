# Brand + README + release: acceptance record

Facts as checked, with the command or file behind each. Brief: `docs/BRAND-README-RELEASE-BRIEF-RU.md`.
Status legend: **done** (verified), **pending** (waits for an owner decision), **open** (not started).

## 1. Mascot

| Item | Status | Evidence |
|---|---|---|
| Three separate concepts (Monolith, Stamp, Motion), black / red / neutral light, no text or props | done | `assets/brand/concepts/concept-{monolith,stamp,motion}.png` (2048×2048), `sheet.png`, `small-size-check.png` (128/64/32 px on black, 96/48/32 px on light) |
| Generation provenance (model, params, job ids, prompts, cost) | done | `assets/brand/concepts/PROVENANCE.md`, `prompt-*.txt`, `job-*.json`; Higgsfield CLI 1.1.20, `gpt_image_2_5`, 2k, quality xhigh, 8 credits each (24 total) |
| Small-size readability | done (measured) | Monolith keeps silhouette + red plane at 32 px on black and light; Stamp loses its thin grey outline at 32 px; Motion's black body merges with black at 32 px |
| Direction chosen | done | owner: Monolith, remade as pixel art with GPT Image 2.5 (image-to-image from the Monolith concept, `assets/brand/pixel/prompt-pixel-*.txt`, jobs `e2d6050f…` / `c8e0ce7f…`) |
| True pixel grid | done | `scripts/pixel_master.py` snaps the render to a real grid (cell 20 px, majority colour per cell, brand palette, real alpha): master A `pixel/bison-a-grid.png` 94×91 / 4 colours; alternative B 112×105 / 5 colours; `pixel/small-size-check.png` 256/128/64/32 px on black and light |
| Master set | done | `assets/brand/`: `mascot-grid.png`, `mascot-2048.png` (transparent, nearest ×21), `mascot-on-black.png`, `mascot-on-light.png`, `avatar-1024.png`, `avatar-128.png`, hand-drawn `mark-16/32/64.png` + `mark.svg` (real `<rect>` per pixel; installed as `web/public/favicon.svg`), terminal mark (`ascii-mark.txt` plain, `mark_halfblocks()` coloured 16×8), `BRAND.md` (palette, proportions, rules, provenance) |
| Same horns / face / proportions in every derivative | done by construction | all derivatives are nearest-neighbour scales, crops or row shifts of the one master; nothing is redrawn |

Higgsfield MCP: authenticated but exposed no tools in this session; all generations went through the `higgsfield` CLI with the catalog checked first (`higgsfield model list --image`, `model get`, `generate cost`).

## 2. Hero and README media

| Item | Status | Evidence |
|---|---|---|
| Product loop 6–8 s from the real product, replay speed unchanged | done | `docs/assets/demo-preview.gif`: 8.0 s, 960×600, 12 fps, 4.18 MB (budget ≤ 5 MB); frames from `demo/v5/raw/web-walkthrough.webm` (source untouched); shows the app's own `REPLAY 20×` and clock labels |
| Static poster | done | `docs/assets/demo-poster.png`, 1280×800, 0.80 MB (evidence card: 54 wallets, Piecoin·1a01 → TruffleHog·7b08) |
| Four-views image | done | `docs/assets/views.png`, 1280×800, 0.75 MB: TERMINAL (real Textual render), RADAR, FLOW, MAP from the same session |
| Hero 1280×480, hero.gif ≤ 3 MB, hero-static.png | done | `docs/assets/hero.gif` 3.5 s, 8 fps, 0.06 MB, seamless (first frame = base pose, stroke retracted before the seam); `hero-static.png` 0.01 MB; wordmark = TUI block glyphs at 1:2 cells with pressed shadow, tagline IBM Plex Mono, one red stroke with an arrow tip, mascot leans 3 px / sinks 2 px by row shifts only (no morphing possible) |
| Reproducible build | done | `scripts/readme_media.py` (product loop, poster, views) and `scripts/brand_build.py` (every brand file + hero); ffmpeg + gifski + Pillow + numpy, no keys |

## 3. README

| Item | Status | Evidence |
|---|---|---|
| Structure: animated hero, three badges, one phrase, exact explanation, nav anchors, demo loop + link, Quickstart in the first two desktop screens, four views + question table, event → evidence, does / does not, data & limits, development, roadmap marked planned, licence stated as not chosen | done | `README.md` (previous version kept in Git: `git show f33b206:README.md`); layout follows the BODKIN reference (hero → badges → paragraph → table → sections) in the STAMPEDE palette |
| Badges | done, three honest ones | `tests 48 passing`, `python 3.13`, `chain 4663` as shields.io static badges in brand colours; no licence, stars, build or vendor badges |
| Links and images | done | 23 links checked (anchors, repo paths, badge URLs, Blockscout tx/address URLs): 0 broken |
| GitHub renderer pre-check | done | GitHub Markdown API (`gh api /markdown`, mode `markdown`) + github-markdown-css, light and dark, 1280 and 420 px: `docs/acceptance/readme-{light,dark}-{desktop,narrow}.png`; Quickstart heading at y ≈ 1502 px (desktop) / 1182 px (narrow), all images load |
| Real GitHub page check | open | after the owner publishes |
| Details moved out of the README | done | `docs/DEVELOPMENT.md` (commands, modes, keys, API, tests, layout) |

## 4. Demo without keys and repository

| Item | Status | Evidence |
|---|---|---|
| Sample bundle the server actually loads | done | `stampede export-demo` → `data/demo/stampede-demo.sqlite.xz`, 17.1 MB; unpacks to 139 MB; row counts in `docs/RELEASE-CHECKLIST.md` |
| Boot with no `.env` and no personal store | done | clean clone `/tmp/stampede-clean` on 2026-09-11: `uv sync`, 48 tests passed, `npm ci && npm run build`, `stampede demo --bundle-url …` fetched the bundle from a stand-in HTTP server, unpacked, served; API probe (status, events, radar, coin, graph, edge, alerts) all answered; `context_enabled: false` |
| Real TUI and web on the clean clone | done | `docs/acceptance/clean-clone-terminal.png` (Textual render against the demo API), `clean-clone-radar.png`, `clean-clone-map.png` |
| History scan for secrets and runtime data | done | 545 blobs, no `.env` values, no key patterns; nothing printed |
| Licence, dependency licences, GitHub target, bundle/MP4 placement | pending owner decisions | listed in `docs/RELEASE-CHECKLIST.md` |

## 5. Media sizes (visible README media)

| File | Size |
|---|---|
| `docs/assets/demo-preview.gif` | 4.18 MB |
| `docs/assets/demo-poster.png` | 0.80 MB |
| `docs/assets/views.png` | 0.75 MB |
| `docs/assets/hero.gif` | 0.06 MB |
| `docs/assets/hero-static.png` | 0.01 MB |
| total | 5.80 MB of the 10 MB budget |

## 6. Remaining actions

1. Owner decisions: licence, bundle placement (repo vs release asset), MP4 as release asset, GitHub account/repo, visibility.
2. Dependency licence listing before publication.
3. Real GitHub page check after publication (GIF playback, badge rendering, anchors).
4. Optional: pixel-by-pixel touch-ups of `pixel/bison-a-grid.png` (a few stray light pixels on the flank), then `python3 scripts/brand_build.py`.
