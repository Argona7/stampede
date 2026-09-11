# Web interface rework — before / after

Date: 2026-09-11. Scope: `web/**` only (the terminal UI is reworked separately). Requirements: `docs/STEP-BY-STEP-GUIDE-BRIEF-RU.md`
(sections «Логотип», «Web-интерфейс», «Что здесь считается AI-slop», «Дополнительная приёмка интерфейса и бренда») and `assets/brand/BRAND.md`.

All frames were taken from the same replay server (`http://127.0.0.1:8791`, `data/stampede.sqlite`, mode replay) with the shared clock
pinned to **17:19:57 UTC** (`from_ts + 3000 = 1789060797`), paused, pairing window 30 min, range 16:49:57–17:19:57. Same data, same
clock, so the frames compare readability, not different event counts. Headless Chromium with a real GPU (`--use-angle=metal`); nothing
is generated or retouched. Pillow only re-encoded the PNGs (lossless); every file is under 400 KB.

| Screen | Before | After |
|---|---|---|
| RADAR 1440×900 | `docs/acceptance/ui/before/radar-1440.png` | `docs/acceptance/ui/after/radar-1440.png` |
| RADAR 1280×800 | `docs/acceptance/ui/before/radar-1280.png` | `docs/acceptance/ui/after/radar-1280.png` |
| RADAR 900×800 (narrow) | `docs/acceptance/ui/before/radar-900x800.png` | `docs/acceptance/ui/after/radar-900x800.png` |
| FLOW 1440×900 (top radar row selected, drawer open) | `docs/acceptance/ui/before/flow-1440.png` | `docs/acceptance/ui/after/flow-1440.png` |
| FLOW 1280×800 | `docs/acceptance/ui/before/flow-1280.png` | `docs/acceptance/ui/after/flow-1280.png` |
| MAP 1440×900 (`?view=map`, explore) | `docs/acceptance/ui/before/map-1440.png` | `docs/acceptance/ui/after/map-1440.png` |
| MAP 1280×800 | `docs/acceptance/ui/before/map-1280.png` | `docs/acceptance/ui/after/map-1280.png` |
| MAP evidence 1440×900 (presentation, strongest edge, `E`) | `docs/acceptance/ui/before/map-evidence-1440.png` | `docs/acceptance/ui/after/map-evidence-1440.png` |
| MAP evidence 1280×800 | `docs/acceptance/ui/before/map-evidence-1280.png` | `docs/acceptance/ui/after/map-evidence-1280.png` |

The evidence frames use the presentation layout because `E` (evidence rows) is a presentation feature; in the explore layout the same
rows are the right-hand Details panel. The selected edge in both frames is the strongest one in range: Piecoin·1a01 → TruffleHog·7b08,
54 distinct wallets, 340 sequence rows.

## Brand in the product

- **Where:** `web/src/components/TopStrip.tsx`, the persistent top strip of every view (RADAR, FLOW, MAP, presentation included).
- **What:** `assets/brand/mark.svg` copied verbatim to `web/public/brand-mark.svg` (the 16×16 `<rect>` mark, `shape-rendering="crispEdges"`),
  rendered as `<img class="mark">` with `image-rendering: pixelated` at **48×48 px = 3× the master** (integer factor, per BRAND.md).
  32 px (2×) was tried first: the mark's body is `#0D0A0A` on the `#050505` strip, so only the red face-and-chest plane (6×9 logical px)
  and the grey horns read, and at 2× that plane is 12×18 px. At 3× it is 18×27 px and the head is recognisable; the brief allows
  raising the size by measured readability. No other brand asset was touched or generated.
- **Alignment:** the mark and the wordmark (`STAMPEDE`, IBM Plex Mono 600, 20 px, `#FF3344`, letter-spacing 0.1em) sit in one flex row,
  vertically centred, 10 px gap, the same left padding as the tabs; the tagline «wallet rotations · Robinhood Chain» is hidden under 1240 px.
- **Click:** the brand is a link (`?view=<start view>`, `data-testid="brand"`); the click handler switches to the view the page opened with
  (`?view=` parameter, default RADAR) and changes nothing else: replay clock, filters, preset, coin selection and map selection stay.
  Covered by the e2e test «brand click returns to the start view…».
- **Loading / empty states** show the same mark at 32 px next to one specific line (RADAR loading, RADAR empty, FLOW empty/loading,
  MAP loading/empty/«waiting for the first live block», Details «Nothing selected»/loading, drawer loading). Errors use a different
  composition (light 2 px left rule, bold title, the error text, no mark). The mascot appears nowhere else; no panel repeats it.
- RADAR and FLOW never wait on the MAP «boot» reveal; the tape/scene build-up stays a 2.6 s presentation feature and is skipped under
  reduced motion.

## Per screen: problem → change → evidence

### Top strip (all views)

| Problem (before) | Change (after) |
|---|---|
| Text-only wordmark; no brand mark in the product. | Pixel mark + wordmark, see above. |
| One 64 px strip mixing everything: views, badge, clock, range, window, session id, search, Play/speed/±5m, 3D/Present. At 1440 the range was clipped («16:49:57–17:1»), the search hidden; at 900 the badge was cut («REPLAY 2»). | 52 px strip = brand · RADAR/FLOW/MAP tabs · state (badge + clock + range) · Play + speed. Everything secondary moved to a 36 px **working row** under it, per view (`.workbar`). The status block shrinks (range hidden < 1400 px, clock label < 1000 px) and the controls never do: the single Play action stays visible at 900 px (e2e «narrow viewport»). |
| Active tab = red text + red border; keyboard focus = the generic 1 px outline on the same shape. | `role="tablist"` with `aria-selected`; active = white text + 2 px red underline, focus-visible = 1 px light outline; roving tabindex, ←/→/Home/End move between tabs (e2e «view tabs are keyboard operable»). |
| Speed shown as three equal buttons 1×/10×/60×; the owner's 20× had no button and looked unset. | One segmented group 1×/10×/20×/60× (+ the current speed if it is none of those), `aria-pressed`. Play is the emphasised button while paused. |
| Error = red bar with black text — another red. | Error = light-framed uppercase text (`role="alert"`), not red. |

### RADAR

| Problem (before) | Change (after) |
|---|---|
| Rows 60+ px tall with three competing display-size numbers per row (score 30 px, coin 22 px, inflow 26 px); 9 rows fit at 900 px. | One-line rows of 30 px in the mono face, 25 rows at 900 px; the coin and its inflow read first, the score is one right-aligned number. |
| Micro-text in every row: truncated score parts («in 36 · acc 2…»), «↑ ×22.0 vs prev · …», «5m · 1h -28.5% · 173 buy…», holder shares. | Details moved to the drawer (click / `D`) and to `title` tooltips (score parts, sources, X 24 h). Rows carry only numbers. |
| The grid was wider than its container: PRICE / X / HOLDERS / action columns were clipped under the alerts panel at 1440; at 900 everything after INFLOW was gone. | A real `<table>` (`.radar-table`) that fits its container; columns leave in order of importance via container queries (`from` < 1100 px, spark + holders < 960 px, Δ 5 min + stage < 760 px) instead of clipping. e2e checks `scrollWidth ≤ clientWidth` at 900 px. |
| Numbers left-aligned, proportional figures, headers without units («PRICE», «X»). | `font-variant-numeric: tabular-nums`, numeric columns right-aligned (`th.num/td.num`), headers name the unit/window: «in · 10 min», «× prev», «from · wallets», «last 30 min», «Δ 5 min», «X · 1 h». The sorted column is highlighted. |
| Duplicate tickers hard to tell apart. | `symbol` (the API's disambiguated form, e.g. `LUNAR·d070`) plus the short address in every row. |
| Unknown values looked like data: «n/a / not fetched» and «—» in micro-text; acceleration «×22.0» when there was no previous window. | X mentions `n/a` (title: not fetched, context off), holders `—`, price `—`, «new» instead of ×N when the previous 10 min had zero inflow. The meta line states it: «X mentions and holders not fetched: n/a = unknown, not 0». |
| Selection by `address` existed but the row order changed under the cursor with every poll (4 s), and nothing marked what moved. | **Ordering rule:** rows re-rank with each update *except* while the pointer or keyboard focus is inside the table; then the order is held, the numbers update in place and the meta line says «order held · N rows would move · leave the table to re-rank». ▲n/▼n/`new` in the rank cell mark movement since the last re-rank. A mouse click that focuses a row does not hold the order (only `:focus-visible` does). e2e «radar selection is keyed by address, survives polling…». |
| No keyboard access to rows. | ↑/↓/Home/End move the selection (the drawer follows if open), Enter opens FLOW for it, Esc closes the drawer, then returns from FLOW, then clears the selection. Rows have `aria-selected`, roving tabindex and a visible focus outline. |
| Filters as a pile of equal controls next to the presets; the alerts rule paragraph repeated on every load. | Working row: presets (segmented) · **Filters** disclosure · sort · find a coin · summary «158 coins · last 30 min · as of 17:19:57 UTC». The alerts rule is a `<details>`; the alerts panel is a fixed 300 px column at ≥ 1400 px and an «Alerts N» toggle below. |
| Empty state: «No coins match. Widen the filters or wait for the clock.» (also shown while loading). | Loading: mark + «Loading RADAR · asking /api/radar…». Empty: mark + «No coins match at HH:MM:SS UTC · 0 coins pass preset X (stage curve, age < 4 h, ≥ 2 rotating wallets, ≤ 3 X mentions, bots hidden) in the last 30 min. Next: switch the preset to ALL, or open Filters and set age to any.» |
| Multi-colour source chips with red counts; red progress bar under the coin; 2.4 s red background flash per updated row. | Sources as text «13 src · egregore 6 · PACKZ·4a8c 5 · +1»; no bar; a fresh inflow = 2 px red mark on the row for 2.5 s, no animation. |
| The coin drawer overlaid the alerts panel (`rgba(5,5,5,.97)` glass). | The drawer is a 420 px grid column next to the table (alerts hide while it is open); below 1100 px it overlays. |

### FLOW

| Problem (before) | Change (after) |
|---|---|
| The drawer covered the right half of the diagram: outbound labels bled through its 0.97 alpha, the header text disappeared under it. | Two-column grid: diagram + 420 px drawer; the SVG gets the remaining width (e2e asserts `svg.right ≤ drawer.left`). |
| Header sentence + explanation paragraph above the diagram; «Coin details (D)» floating over the SVG. | Working row: «19 wallets rotated into CAT·fd23 from 8 coins · 8 rotated out to 3 coins · pairing window 30 min» · **Radar (Esc)** · **Coin details (D)**. One legend line at the bottom. |
| 92 px red disk in the centre; inbound ribbons carried red hairline arrows, outbound grey ones; every label repeated «wallets». | 44 px node (red = B · BOUGHT, the selected object) with the symbol above it; the coin's facts sit in the free band under the column headers, out of the ribbons' path. Every ribbon has the same grey centreline + arrowhead in the observed order (sold → bought); red only while a ribbon just received sequences. Column headers «A · SOLD FIRST · 8 COINS» / «B · BOUGHT» / «THEN BOUGHT · 3 COINS»; label boxes show symbol + count. |
| Long lists (12 per side) could leave the frame. | The stacks scale to the frame height; boxes never overlap each other or the centre label (e2e). |
| `filter: drop-shadow` glow on hot ribbons. | Removed. |

### MAP (explore)

| Problem (before) | Change (after) |
|---|---|
| Left rail duplicated Play / speed / Start from the strip and carried a paragraph under every section. | Rail keeps only what is not elsewhere: pairing window, visible range + seek slider, edges drawn (min wallets, top flows, ambiguous), «In this range», «Coverage». Explanations moved to tooltips; one coverage note stays. Range, window, «SHOWING 300 OF 302 FLOWS», −5 / +5 min / Start, find a coin, 3D/2D and Present (P) sit in the working row. |
| «Nothing selected» panel: 22 px heading + two paragraphs. | Mark + one line: «Nothing selected · Click a line (A → B) for the wallets…». |
| The overview drifted for 9 s after every arrival, also in the working layout. | Drift is a presentation-only key (`drift` prop of `Scene3D`); in explore the camera stays where it lands (e2e compares two frames 3 s apart). |
| Hot-coin halos «breathed» endlessly; up to 12 red halos and red «+N» labels in the overview. | Static halos for the top 8 inflow coins; red stays the mark of newly observed inflow. |
| Legend paragraph wrapped over scene labels at 1280. | Legend clamped to two lines with an opaque background in explore; full text in `title`. |
| Search selected a token that opened a drawer which the MAP view never renders. | «find a coin» selects the token on the map in MAP, opens the drawer elsewhere. |
| Ticker footer 120 px with a paragraph. | 96 px, one label line. |

### MAP presentation / evidence

| Problem (before) | Change (after) |
|---|---|
| The bottom-right hint («drag to orbit…») sat under the evidence panel; at 1280 the caption's meta line ran under the panel. | `.map.has-panel` shifts the legend and caps the caption to the free 52 %; e2e asserts the hint ends before the panel. |
| Caption 72 px / 34 px; panel `rgba(5,5,5,.94)` glass; tx links underlined in `#351419` (unreadable). | Caption 56 / 26 / 14 px; opaque panel with a border; links underlined in `#A3A3A3`, hover `#F2F2F2`. |
| Counts argued: «54 WALLETS … observed sequences · 340 rows», panel «54 wallets · 340 sequences», HUD «flows · seq». | Both labelled the same way everywhere: **distinct wallets** vs **sequence rows** (caption, evidence header, Details grades, rail). The evidence header also names A/B addresses and, only when present, «approx. = interpolated block time». |
| HUD rows `rgba(5,5,5,.55)`; tape `rgba(5,5,5,.62)`. | Opaque. |
| Autopilot ran in any layout and only pointer/wheel stopped it. | `?autopilot=1` opens the presentation layout; `A` works only there; any pointer, wheel **or key** stops the tour. |
| «Autopilot (A)» sat alone in the corner; Explore toggle lived in the strip. | Corner: state · sample label · Autopilot (A) · Explore (P). |

## AI-slop removed (and what stayed)

Removed: glass panels (drawer, evidence panel, HUD rows, tape), the ribbon drop-shadow glow, breathing halos, 2.4 s row-flash
animations, three display-size numerals per radar row, the red progress bar, chip badges with red counts, repeated «wallets» labels,
repeated explanatory paragraphs (rail, ticker, alerts, details), duplicated Play/speed controls, the 92 px disk, the 72 px caption,
uniform equal buttons for primary and secondary actions.

Kept on purpose: the red accent (brand, the selected route / B node, newly observed sequences and inflow), the pixel mark, dense IBM
Plex Mono with Bricolage Grotesque for the few display labels, the spatial 3D map with its presentation flights, tape and autopilot.
`border-radius` was already 0 everywhere.

## States

loading (mark + line) · empty (mark + cause + next action) · error (light left rule, `role="alert"`, explicit text) · stale
(`LIVE · STALE` double-border badge + corner note with data age) · paused (`PAUSED · REPLAY 20×` solid badge, «· paused» in the radar
summary, emphasised Play) · selected (dark red row background + 2 px red mark; red node/route on the map) · hover (surface background,
no colour change) · focus (1 px light outline, tabs: inset 6 px). Distinct by text and composition, not by another red.

`prefers-reduced-motion` (and `?motion=reduce`): CSS animations/transitions off, camera flights instant, no travelling pulses, no
afterglow, no drift, no tape streaming; the «+N sequences» count still appears. e2e «reduced motion».

## Keyboard map

| Key | Where | Action |
|---|---|---|
| `1` `2` `3` | everywhere | RADAR / FLOW / MAP |
| `←` `→` `Home` `End` | view tabs focused | move and activate tabs |
| `↑` `↓` `Home` `End` | RADAR | move the coin selection (drawer follows if open) |
| `Enter` | RADAR | open FLOW for the selected coin |
| `D` | RADAR, FLOW | toggle the coin drawer |
| `Esc` | RADAR / FLOW / MAP | close drawer → back to RADAR from FLOW → clear the selection |
| `Space` | everywhere | play / pause the shared clock |
| `E` | MAP | evidence rows (presentation) |
| `P` | MAP | presentation ↔ explore |
| `T` | MAP | tape on/off (presentation) |
| `A` | MAP presentation | autopilot on/off; any other key, pointer or wheel stops it |
| brand click | everywhere | back to the start view, nothing reset |

## Checks

- `cd web && npm run lint` — oxlint, 0 errors, 0 warnings.
- `npm run build` — `tsc -b && vite build`, clean (`dist/assets/index-*.js` ≈ 1.0 MB, 276 kB gzip; the chunk-size notice is unchanged).
- `npx playwright test` — **13 passed** (8 existing tests updated to the new DOM + 5 new: brand click, selection persistence /
  keyboard rows, keyboard tabs, narrow viewport, reduced motion). One run showed 2 failures in the two tests that assume nobody else moves
  the shared clock («pause adds no events…», the no-drift frame comparison) while another client of `:8791` seeked; the re-run was 13/13.
  The drift test now compares frames only if the clock did not move in between.
- `uv run pytest -q` — 59 passed (Python, not touched here).
- Viewports: 1440×900 and 1280×800 — every column and control visible; 900×800 — Play, brand and tabs visible, table drops `from`,
  spark, holders, keeps score (see `radar-900x800.png`).

## Not done / open

- The screenshot script lives outside the repo (`/tmp/stampede-shots/shot.mjs`); the e2e suite is the reproducible check.
- Brand mark at 48 px instead of the brief's 24–32 px range: measured readability on black (see above). If the owner prefers 32 px, it is
  one CSS value (`.brand .mark`) and the mark stays crisp at any integer factor.
- The RADAR shows the `X · 1 h` and `holders` columns as `n/a` / `—` in replay by design; they fill in live mode or after «Refresh context».
- Not touched: `assets/brand/**`, `demo/**`, `docs/assets/hero*`, the video, README.md, `stampede/**` (no API change was needed).
- The shared clock is one server-side session: browser, terminal and tests move it together. Tests pin it themselves; the acceptance
  frames re-check the clock before and after each frame and retake the frame if it moved.
