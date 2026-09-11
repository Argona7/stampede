# STAMPEDE brand

One character, one wordmark, three colours. Everything in this folder is built by `scripts/brand_build.py` from
two hand-controlled sources: the pixel master `pixel/bison-a-grid.png` and the 16×16 mark matrix in
`stampede/tui/brand.py`. Nothing here is a "vector" unless it is `.svg` with real paths/rects.

## Palette

| Role | Hex | Use |
|---|---|---|
| black | `#050505` | page / scene background |
| panel | `#0D0A0A` | panels, the mascot's body |
| red | `#FF3344` | the brand, the mascot's face-and-chest plane, the selected route, newly observed sequences — nothing else |
| dark red | `#351419` | separators, the wordmark's pressed shadow, the mascot's shadow |
| text | `#F2F2F2` | primary text |
| secondary | `#A3A3A3` | secondary text, the mascot's horns, hooves and edge highlights |

The mascot brings no new accent colour. Red never becomes pink or neon; on light backgrounds the black body
carries the shape and the red plane stays `#FF3344`.

## The character

A compact charging bison, three-quarter view facing right, low centre of gravity, shoulder hump, short curved
grey horns, one red plane over the face and chest, thin grey edge highlights so the black body reads on black.
Alert and determined; not cute, not demonic. No coins, charts, rockets, flames, gold, clothing, glasses, armour.

Proportions (logical pixels of the master, 94 wide × 91 tall): head + red plane occupy the right 45 %, the hump
peaks at the top third, the stance spans the full width; the red plane covers ≈ 15 % of the opaque area.

## Files

| File | What it is |
|---|---|
| `pixel/bison-a-grid.png` | **master**: 94×91 logical pixels, 4 colours, real alpha. The only file to edit by hand (pixel by pixel) |
| `pixel/bison-b-grid.png` | the finer alternative (112×105, 5 colours), kept for large formats; not used in the README |
| `pixel/raw-pixel-*.png`, `pixel/job-*.json`, `pixel/prompt-*.txt` | the generator output the masters were snapped from, with job ids and prompts |
| `mascot-grid.png` | copy of the master used by the build |
| `mascot-2048.png` | nearest-neighbour ×21 on a transparent 2048×2048 canvas |
| `mascot-on-black.png`, `mascot-on-light.png` | 1024×1024 previews |
| `avatar-1024.png`, `avatar-128.png` | head + red plane on black; safe inside a circular crop |
| `mark-16.png`, `mark-32.png`, `mark-64.png`, `mark.svg` | the hand-drawn 16×16 head mark; the SVG is one `<rect>` per pixel (`shape-rendering="crispEdges"`), also installed as `web/public/favicon.svg` |
| `ascii-mark.txt`, `ascii-mark-halfblocks.rich.txt` | terminal mark: plain 16×16 fallback, and the coloured 16×8 half-block version (`stampede.tui.brand.mark_halfblocks()`) |
| `hero-static.png` → `docs/assets/hero-static.png`, `docs/assets/hero.gif` | README hero, 1280×480 |
| `concepts/` | the three text-to-image concepts the direction was chosen from (`PROVENANCE.md`) |

## Wordmark

The block glyphs of `stampede/tui/brand.py` (5 rows × 55 columns), rendered at the terminal's 1:2 cell aspect,
red with a half-cell `#351419` pressed shadow down-right. It is the same mark the TUI prints on start. Captions
next to it are IBM Plex Mono (the web bundle's font); display text elsewhere is Bricolage Grotesque. The
generator never draws letters.

## Motion

Hero loop (`docs/assets/hero.gif`, 3.5 s, 8 fps, seamless): the bison leans into one heavy step — top rows
shift toward the head by up to 3 logical px while the body sinks 2 px — holds, returns; the red stroke sweeps
under the wordmark toward the mascot and retracts before the seam. Pure row shifts: horns, legs and face
never change shape. The wordmark never moves. Any other animation of the character must keep the same rule:
transform the master, do not redraw it.

## Provenance

- Direction: three GPT Image 2.5 text-to-image concepts (`concepts/PROVENANCE.md`), owner chose Monolith.
- Master: GPT Image 2.5 image-to-image from the Monolith concept, prompt `pixel/prompt-pixel-64.txt`, job
  `e2d6050f-9281-49c8-be7b-63f3b31d750a` (2026-09-11), then `scripts/pixel_master.py` (cell 20 px, offset 4/9,
  majority colour per cell, brand palette). Alternative B: job `c8e0ce7f-3cce-417f-951c-1f46faf7f4a6`, cell 16 px.
- No keys, tokens or account identifiers are needed to rebuild anything in this folder.

## Rules of use

- Black or the brand's light grey behind the character; never a chart, candles or a rising line.
- Scale the pixel master by integer factors with nearest-neighbour only. No blur, no anti-aliased resampling, no outlines added.
- The red plane is the only red on the character. Do not recolour horns or eyes.
- Wordmark and character may appear together (hero) or apart (avatar + text); the character never replaces evidence in the product.
