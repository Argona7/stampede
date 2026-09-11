// Demo v5: one continuous >= 30 s take. 3D MAP in presentation with the live HUD and tape, replay running,
// autopilot tours the coins the radar ranks highest (fly -> caption -> next), then RADAR, then FLOW.
//
//   python scripts/serve_daemon.py start --mode replay --port 8791
//   cd web && node e2e/record-demo-v5.mjs            # -> ../demo/v5/raw
//   PERF=1 node e2e/record-demo-v5.mjs                # frame-time pass (no video)
import { chromium } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'

const BASE = process.env.STAMPEDE_URL ?? 'http://127.0.0.1:8791'
const OUT = path.resolve(process.env.OUT ?? '../demo/v5/raw')
const SPEED = Number(process.env.SPEED ?? 20)
const LEAD_S = Number(process.env.LEAD_S ?? 480)
const PERF = process.env.PERF === '1'
const STOPS = Number(process.env.STOPS ?? 4)
fs.mkdirSync(OUT, { recursive: true })
const post = (b) => fetch(`${BASE}/api/session`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(b) }).then((r) => r.json())
let session = await (await fetch(`${BASE}/api/session`)).json()
if (session.mode !== 'replay') throw new Error(`server must run in replay mode, got ${session.mode}`)
await post({ action: 'pause' })
await post({ action: 'speed', speed: SPEED })
session = await post({ action: 'seek', ts: session.to_ts - LEAD_S })

const browser = await chromium.launch({ args: ['--use-angle=metal', '--use-gl=angle', '--enable-gpu', '--ignore-gpu-blocklist'] })
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: Number(process.env.DPR ?? 1), recordVideo: PERF ? undefined : { dir: OUT, size: { width: 1440, height: 900 } } })
const page = await ctx.newPage()
const t0 = Date.now()
const marks = []
const mark = (name, note = '') => marks.push({ name, note, offset_s: Math.round((Date.now() - t0) / 100) / 10 })
const shot = (name) => (PERF ? Promise.resolve() : page.screenshot({ path: path.join(OUT, `${name}.png`) }))
const state = () => page.evaluate(() => window.__stampede_state())

await page.goto(`${BASE}/?view=map&layout=presentation&autopilot=1${PERF ? '&perf=1' : ''}`)
mark('boot', 'page opens: the visible history streams through the tape while the 3D network builds up in time order')
await page.waitForFunction(() => window.__stampede_state && (window.__stampede_state().edges ?? 0) > 0)
await page.waitForTimeout(1500)
await shot('boot')
await post({ action: 'play' })
mark('play', `replay at ${SPEED}x: pulses on edges, HUD counters and top-inflow list move, hot coins get red halos`)
// autopilot: first stop ~2.6 s after the radar arrives, then every 7 s
const stops = []
for (let i = 0; i < STOPS; i++) {
  await page.waitForFunction((n) => window.__stampede_state().viewState === 'evidence' && window.__stampede_state().selection?.kind === 'edge' && (window.__stampede_state().tourIdx ?? 0) >= n, i + 1, { timeout: 20000 }).catch(() => {})
  const st = await state()
  const sel = st.selection
  const cap = await page.locator('[data-testid=caption] .n').textContent().catch(() => '')
  const pair = await page.locator('[data-testid=caption] .pair').textContent().catch(() => '')
  stops.push({ i: i + 1, edge: sel, caption: `${cap} ${pair}`.trim() })
  mark(`stop${i + 1}`, `autopilot stop ${i + 1}: ${cap} ${pair}`.trim())
  await shot(`stop${i + 1}`)
  if (i < STOPS - 1) await page.waitForTimeout(6000)
}
await page.waitForTimeout(1500)
let perf = null
if (PERF) perf = { readout: await page.evaluate(() => document.querySelector('.perf')?.textContent ?? null) }
await page.keyboard.press('1')
await page.waitForSelector('.radar-row:not(.head)', { timeout: 15000 })
await page.waitForTimeout(600)
mark('radar', 'RADAR: the same coins as a ranked board with score parts, sources, price, holders, alerts journal')
await shot('radar')
await page.waitForTimeout(4200)
await page.locator('.radar-row:not(.head)').nth(0).locator('.actions button').click()
await page.waitForSelector('.flow-svg .ribbon', { timeout: 15000 })
await page.waitForTimeout(600)
mark('flow', 'FLOW: where the top coin\u2019s wallets came from and where they went')
await shot('flow')
await page.waitForTimeout(4200)
await post({ action: 'pause' })
mark('end')
const fin = await (await fetch(`${BASE}/api/session`)).json()
const video = page.video()
await ctx.close()
if (video) fs.renameSync(await video.path(), path.join(OUT, 'web-walkthrough.webm'))
fs.writeFileSync(path.join(OUT, PERF ? 'perf.json' : 'marks.json'), JSON.stringify({ recorded_at_utc: new Date().toISOString(), base: BASE, session_id: session.id, replay_speed: SPEED, replay_clock_start_ts: session.clock_ts, replay_clock_end_ts: fin.clock_ts, stops, marks, perf }, null, 1))
await browser.close()
console.log(JSON.stringify({ session_id: session.id, stops, marks, perf }, null, 1))
