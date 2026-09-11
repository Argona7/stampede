// Records the web scene (presentation layout) against a REPLAY server: overview -> play (pulses) -> select the
// strongest edge in range -> directed flight -> evidence caption -> evidence rows -> back to overview.
//
//   python scripts/serve_daemon.py start --mode replay --port 8791
//   cd web && node e2e/record-demo-v2.mjs            # writes ../demo/v2/raw/*
//   DPR=2 PERF=1 node e2e/record-demo-v2.mjs         # measurement pass on Retina pixel density
//
// The selected pair is whatever edge has the most distinct wallets in the visible range at that moment;
// nothing is staged. All timings are written to marks.json for the cuts.
import { chromium } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'

const BASE = process.env.STAMPEDE_URL ?? 'http://127.0.0.1:8791'
const OUT = path.resolve(process.env.OUT ?? '../demo/v2/raw')
const DPR = Number(process.env.DPR ?? 1)
const PERF = process.env.PERF === '1'
const LEAD_S = Number(process.env.LEAD_S ?? 330)
const SPEED = Number(process.env.SPEED ?? 20)

fs.mkdirSync(OUT, { recursive: true })
const post = (b) => fetch(`${BASE}/api/session`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(b) }).then((r) => r.json())
let session = await (await fetch(`${BASE}/api/session`)).json()
if (session.mode !== 'replay') throw new Error(`server must run in replay mode, got ${session.mode}`)
await post({ action: 'pause' })
await post({ action: 'speed', speed: SPEED })
session = await post({ action: 'seek', ts: session.to_ts - LEAD_S })

// real GPU in headless Chromium (otherwise SwiftShader software rendering distorts every frame time)
const browser = await chromium.launch({ args: ['--use-angle=metal', '--use-gl=angle', '--enable-gpu', '--ignore-gpu-blocklist'] })
const ctx = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  deviceScaleFactor: DPR,
  recordVideo: PERF ? undefined : { dir: OUT, size: { width: 1440, height: 900 } },
  reducedMotion: 'no-preference',
})
const page = await ctx.newPage()
const t0 = Date.now()
const marks = []
const mark = (name, note = '') => marks.push({ name, note, offset_s: Math.round((Date.now() - t0) / 100) / 10, clock_ts: null })

const shot = (name) => page.screenshot({ path: path.join(OUT, `${name}.png`) })

await page.goto(`${BASE}/?layout=presentation${PERF ? '&perf=1' : ''}`)
mark('boot', 'page opens: the visible history streams through the tape while the network builds up in time order')
await page.waitForFunction(() => window.__stampede_state && (window.__stampede_state().edges ?? 0) > 0)
await page.waitForTimeout(1300)
await shot('web-boot')
await page.waitForTimeout(2400) // build-up and dolly-in settle
mark('overview', 'presentation layout, whole network from the 3/4 angle, tape idle')
await shot('web-overview')

await post({ action: 'play' })
mark('play', `replay running at ${SPEED}x; new sequences pulse along their edges`)
await page.waitForFunction(() => document.querySelectorAll('.pulse-lbl').length > 0, null, { timeout: 20000 })
await page.waitForTimeout(400)
mark('pulses', 'first pulses visible')
await shot('web-pulses')
await page.waitForTimeout(3200)

// pick the strongest edge in the visible range right now (same numbers the caption will show)
const s = await (await fetch(`${BASE}/api/session`)).json()
const g = await (await fetch(`${BASE}/api/graph?window=${s.window_s}s&from=${s.clock_ts - s.span_s}&to=${s.clock_ts}&min_wallets=3&limit=3`)).json()
const edge = g.edges[0]
await page.evaluate((e) => window.__stampede_select({ kind: 'edge', from: e.from, to: e.to }), edge)
mark('select', `edge selected: ${edge.from} -> ${edge.to} (${edge.wallets_main} wallets at selection time)`)
await page.waitForTimeout(900)
mark('follow', 'camera at A, travelling along the route to B')
await shot('web-follow')
await page.waitForFunction(() => window.__stampede_state().viewState === 'evidence', null, { timeout: 8000 })
await page.waitForTimeout(700)
mark('evidence', 'camera settled: A · SOLD, B · BOUGHT, wallet count caption')
await shot('web-evidence')
await page.waitForTimeout(2200)
await page.keyboard.press('e')
await page.waitForSelector('.evidence-panel .seq')
await page.waitForTimeout(1200)
mark('rows', 'evidence rows: wallet, sold/bought, exact UTC block time, tx links')
await shot('web-evidence-rows')
await page.locator('.evidence-panel .seq a[href*="/tx/"]').first().hover()
await page.waitForTimeout(1800)
await post({ action: 'pause' })
mark('pause', 'replay paused from the shared session; clock frozen on both surfaces')
await page.keyboard.press('e')
await page.waitForTimeout(600)
await page.keyboard.press('Escape')
mark('back', 'Esc: back to the overview')
await page.waitForTimeout(1600)
mark('end')

let perf = null
if (PERF) {
  perf = await page.locator('.perf').textContent().catch(() => null)
  perf = { readout: perf, gpu: await page.evaluate(() => { const c = document.createElement('canvas'); const gl = c.getContext('webgl2'); const ext = gl && gl.getExtension('WEBGL_debug_renderer_info'); return ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : 'n/a' }) }
}
const finalSession = await (await fetch(`${BASE}/api/session`)).json()
const video = page.video()
await ctx.close()
if (video) fs.renameSync(await video.path(), path.join(OUT, 'web-walkthrough.webm'))
fs.writeFileSync(
  path.join(OUT, PERF ? 'perf.json' : 'marks.json'),
  JSON.stringify({ recorded_at_utc: new Date().toISOString(), base: BASE, session_id: session.id, replay_speed: SPEED, replay_clock_start_ts: session.clock_ts, replay_clock_end_ts: finalSession.clock_ts, dpr: DPR, edge, marks, perf }, null, 1),
)
await browser.close()
console.log(JSON.stringify({ session_id: session.id, edge: edge && { from: edge.from, to: edge.to, wallets: edge.wallets_main }, marks, perf }, null, 1))
