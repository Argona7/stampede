# TUI rework: before / after acceptance record

Facts as checked, with the command or file behind each. Scope: `stampede/tui/**`, `tests/test_tui.py`,
`scripts/pty_check.py`, `scripts/tui_shot.py`, this folder. Nothing under `web/**`, `assets/brand/**`,
`demo/**`, `docs/assets/**` or the README was changed.

Environment: macOS 26.5.1, Python 3.13 (`.venv`), Textual 8.2.8, Rich (bundled). Frames are Textual's own SVG
export (`scripts/tui_shot.py`, exact cells and colours) converted with `web/e2e/svg2png.mjs`, then palette-quantised
(flat colours, visually lossless) to stay under 400 KB each. All frames were taken against the owner's replay session
on `127.0.0.1:8791`, paused at **17:19:57 UTC** (`clock_ts 1789060797`, window 30 m, `REPLAY 20× · PAUSED`), and the
session was put back to its previous state afterwards (`1789061393`, paused, 20×).

Frames: `docs/acceptance/tui/before/{feed,radar,pair}-{120x36,180x50}.png` (the TUI as committed before this work) and
`docs/acceptance/tui/after/` (same six frames, same clock, same key sequences: FEED = start view; RADAR = `2`;
PAIR = `↑ ↑ ↑ Enter` on the feed).

## 1. Brand and header

| Problem (before) | Change | Evidence |
|---|---|---|
| Header was a one-line `STAMPEDE` string plus a wall of facts; no mark, no wordmark; the brand of the README / web view did not appear in the terminal at all | Wide windows (≥ 160 cols, ≥ 30 rows): the README bison (`assets/brand/mascot-grid.png`, 94×91 px, 4 brand colours) rendered once at import as **25 cols × 12 rows of half-blocks** (`brand.bison_cells` / `bison_halfblocks`, majority colour per output pixel, ties to horns/plane over body, cached with `lru_cache`, never recomputed per frame); the 5-row block wordmark (`brand.big()`) sits right of it, **bottom-aligned** with the mascot; mode badge above the fact column, CHAIN / CLOCK / RANGE / SESSION / API to the right of the wordmark; view tabs on the 13th row. Exact colours `#0D0A0A` body, `#FF3344` red, `#A3A3A3` grey, `#351419` dark red on `#050505`; transparent = background. Falls back to the native 16×8 head mark if Pillow or the asset is missing, and to plain glyphs when the console has no colour system | `after/feed-180x50.png`, `after/radar-180x50.png` (charging pose, red face/chest plane, grey horns and hooves, as in `docs/assets/hero-static.png`); `test_bison_mascot_downsample_is_pure_and_brand_coloured`, `test_header_variants_by_size_and_quit` |
| At 120×36 the facts took 7 of 36 rows and the table showed ~15 rows | Compact header (< 160 cols, or < 30 rows): **3 rows** — `S T A M P E D E` + mode badge + chain, one status line (`CLOCK · RANGE · window · SESSION · API`; below 100 cols only clock + range), view tabs. No mascot | `after/feed-120x36.png`: 20 feed rows visible; `test_header_variants_by_size_and_quit` (100×28 tiny, 120×36 compact, 180×50 wide) |
| 256-colour terminals | Rich downgrades the five colours to the nearest palette entries only: `16` background, `232` body, `203` red, `247` grey, `52` dark red; the 300 half-block cells are identical, no other hue | `/tmp/stampede-tui/pty/pty-180x50-256-c256.log` (PTY run, `--colors 256`): set of `38;5;N` / `48;5;N` codes on `▀` cells = `{16, 52, 203, 232, 247}`, 1200 cells over the run |
| Loading / empty state was a blank table | `CONNECTING TO API · nothing loaded yet` / `NO ROWS MATCH 'x' · Esc clears the filter` / `NO OBSERVED SEQUENCES in the last 30m before hh:mm:ss` as one status line under the header | `_render_empty()`; `test_api_error_shows_inverted_alert_and_freezes` |

## 2. Composition

| Problem (before) | Change | Evidence |
|---|---|---|
| 120×36: the feed table had a horizontal scrollbar and the SOLD/BOUGHT symbols were cut | Adaptive columns: `_feed_columns()` / `_radar_columns()` compute widths from the pane width; wide windows add `SOLD UTC`, `IN 30M`, `5M`, `1H`, `X 1H`; below the 120-column minimum the wallet column is dropped first (it is in the details pane anyway). Symbol columns share what is left | `after/feed-120x36.png` (no scrollbar, all columns), `after/radar-180x50.png` (11 radar columns); assertion `feed width ≥ Σ column widths + padding` in `test_header_variants_by_size_and_quit`; `test_resize_keeps_selection_and_switches_columns` |
| Details pane was a modal-like dump under the table | Details are the adjacent right pane (`SELECTED PAIR` / `EVIDENCE` / `SELECTED COIN` / `COIN CARD`), with a red `▌` marker and red left border when it has focus; the table keeps 5/8 (compact) or 3/5 (wide) of the width | `after/pair-120x36.png`, `after/radar-180x50.png` |
| 180×50 was mostly empty | Wide windows show the extra columns, the full wallet address and both full tx hashes in the details, the hot-rotations list and a 30-bucket sparkline (11 rows when ≥ 44 rows tall, hidden below 34 rows) | `after/*-180x50.png` |

## 3. Reading a moving stream

| Problem (before) | Change | Evidence |
|---|---|---|
| Every poll cleared and refilled the table: the selection jumped, the scroll reset | Rows have keys; new rows are appended, existing rows updated in place (`update_cell`, fresh marks cleared via `_refresh_mark`). Selection and scroll position survive polls and resizes | `test_new_rows_append_without_clearing_and_selection_stays_then_end_follows`, `test_resize_keeps_selection_and_switches_columns` |
| No way to know rows arrived below the viewport | Header of the feed says `following latest` while the cursor is on the last row; once the user moves up it switches to `▲ N new below · End follows latest` and counts unseen rows; `End` jumps to the tail and re-enables following, `Home` goes to the first row | same test; PTY `follow_state_shown` in all runs |
| Seeking the shared clock kept stale "new" marks | A seek rebuilds the table as history (no fresh marks), following on | `test_seek_rebuilds_history_without_marking_it_fresh` |
| Radar rows re-sorted under the cursor | Radar updates in place when the address order is unchanged, otherwise rebuilds and restores scroll and cursor | `_fill_radar()`; `after/radar-*.png` |

## 4. Keyboard

Final map (footer hints show the same words; long forms shrink to short forms, then lower-priority hints drop, so the line always fits):

| Key | Action |
|---|---|
| `↑` / `↓` | move the selection (table) · scroll (details) |
| `Enter` | open: evidence for the selected pair / coin card for the selected coin |
| `Esc` | back: clears the search filter → returns from evidence/card to the summary → focuses the table |
| `Tab` / `Shift+Tab` | cycle panes: table → details → (search, when open) → table |
| `1` / `2` | switch view: FEED / RADAR |
| `/` | search (symbol or address; spaces allowed; `Enter` keeps the filter, `Esc` clears it) |
| `End` / `Home` | follow latest / first row (also: end/start of the search text, bottom/top of the details) |
| `Space` | play / pause the shared session clock |
| `←` / `→` | seek the shared clock −60 s / +60 s (unless the table needs horizontal scrolling) |
| `[` / `]` | slower / faster |
| `u` `g` `s` `a` | radar presets: under radar · graduating · smart rotators · all |
| `r` | refresh the coin context |
| `q`, `Ctrl+C` | quit (terminal restored: alternate screen left, cursor shown) |

Resolved: `Tab` no longer switches screens (it conflicted with pane focus) — `1` / `2` do; `Space` types into the
search field instead of toggling playback there; `End` / `Home` reach the app even when the DataTable would consume
them (priority bindings); `Ctrl+C` quits instead of showing Textual's help notification.

Tests: `test_tab_cycles_panes_and_digits_switch_views`, `test_search_filters_accepts_spaces_and_escape_restores`,
`test_space_toggles_shared_session_and_seek_keys`, `test_ctrl_c_quits`, `test_header_variants_by_size_and_quit`
(`q` returns code 0).

## 5. Typography

| Problem (before) | Change | Evidence |
|---|---|---|
| Numbers left-aligned, ragged | Numeric cells are right-justified `Text` (`num()`), header labels aligned to the cells | `after/radar-180x50.png` |
| Addresses shown as 42-char hex or cut mid-word | `short()` → `0xd60c…7ab6`; full address and tx hashes in the details pane | `after/pair-180x50.png` |
| Symbols cut so `TruffleHog·7b08` and `TruffleHog·bd82` became the same string | `fit()` keeps the `·xxxx` suffix: `Truff…·7b08`; truncation never spills into the next column | `test_formatting_helpers`; `after/feed-120x36.png` |
| `30m00s`, `1h00m` | `dur()` drops zero parts: `30m`, `15m29s`, `1h`, `1h05m` | `test_formatting_helpers` |

## 6. Reliability

| Item | Change | Evidence |
|---|---|---|
| Resize | `on_resize` defers to `call_later` (Textual stores the new size after the subclass hook), rebuilds columns and header variant, keeps the selected row and follow state | PTY `shrink` (180×50 → 120×36 at 8 s) and `grow` (120×36 → 180×50) runs: `redrawn_after_resize`, `compact_header_after_shrink`, `block_wordmark_after_grow` all true; `test_resize_keeps_selection_and_switches_columns` |
| API error / reconnect | Inverted red alert line with the reason (`connection refused at host (is the server running?)` / `no answer from host within 6s`) and the attempt count; data frozen, footer says `DISCONNECTED · retrying every 1s`; recovers silently when the server is back | `test_api_error_shows_inverted_alert_and_freezes`; reconnect scenario run against an own instance on port 8792 (killed and restarted mid-run) — the owner's server on 8791 was never stopped |
| Clean exit | `q` and `Ctrl+C` leave the alternate screen and show the cursor; exit status 0 | PTY checks `alternate_screen_left`, `cursor_shown_at_exit`, `exited` in all six runs |
| LIVE vs REPLAY | LIVE: inverted white ` LIVE ` badge, `BLOCK hh:mm:ss UTC · data age …`, `! STALE` + red age when older than the threshold; REPLAY: `[REPLAY 20× · PAUSED]` bold, `CLOCK` | `test_live_badge_differs_from_replay_and_marks_stale` |

## 7. PTY checks (`scripts/pty_check.py`, real pseudo-terminal, own replay instance on 8792, clock re-seeked to 17:19:57 between runs)

Key script for the full runs: `↑ ↑ · Tab · Tab · 2 · ↓ ↓ · Enter · Esc · 1 · / tvk · Esc · Space (5 s) · Space · End`.

| Run | Result |
|---|---|
| 120×36 truecolor, full script, quit `q` | OK — brand, status, rows, red used, search drawn, radar drawn, details focused, clock moved then paused, follow state shown, alt screen left, cursor shown, exit 0 |
| 180×50 truecolor, full script, quit `Ctrl+C` | OK — same checks, mascot present (≥ 300 `▀` cells) |
| 180×50 → 120×36 at 8 s, `q` | OK — redrawn, block wordmark before / compact line + `TIME UTC` column after |
| 120×36 → 180×50 at 8 s, `q` | OK — redrawn, block wordmark + mascot + `SOLD UTC` column after |
| 120×36 256-colour, `2`, `1`, `↑`, `q` | OK |
| 180×50 256-colour, `Ctrl+C` | OK — palette entries `{16, 52, 203, 232, 247}` only |

Logs: `/tmp/stampede-tui/pty/pty-*.log` (not committed).

## 8. Tests

`env -u NO_COLOR TEXTUAL_COLOR_SYSTEM=truecolor uv run pytest -q` → **61 passed** (13 in `tests/test_tui.py`, the rest
unchanged). TUI tests drive the real app through Textual's Pilot against a fake API client (`FakeClient`) and cover:
formatting helpers, the mascot downsample, rows / one wallet two sequences, append-without-clear + `N new` + `End`,
seek as history, search with spaces and `Esc`, `Tab` / `1` / `2`, header variants by size + `q`, `Ctrl+C`, resize
keeps selection, API error alert, LIVE vs REPLAY badge, `Space` / seek keys.

## 9. Not done / notes

- The mascot needs Pillow at import of the header (a project dependency); without it, or without
  `assets/brand/mascot-grid.png`, the header shows the 16×8 head mark at native size.
- `scripts/pty_check.py` scenarios are driven from a temporary shell script outside the repository; the script itself
  and its checks are committed.
- Frames are quantised PNGs (≤ 400 KB); the SVG originals are not kept.
