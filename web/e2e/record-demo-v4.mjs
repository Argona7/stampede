// Demo v4: RADAR -> coin drawer -> FLOW -> evidence flight -> alerts, against a REPLAY server.
//
//   python scripts/serve_daemon.py start --mode replay --port 8791
//   cd web && node e2e/record-demo-v4.mjs        # -> ../demo/v4/raw
//
// The replay is first run ahead at high speed so the alert journal already holds outcomes, then the clock is
// put back to the recording start. Every number on screen is computed by the running product.
import { chromium } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'

const BASE = process.env.STAMPEDE_URL ?? 'http://127.0.0.1:8791'
const OUT = path.resolve(process.env.OUT ?? '../demo/v4/raw')
const SPEED = Number(process.env.SPEED ?? 20)
const LEAD_S = Number(process.env.LEAD_S ?? 420)
fs.mkdirSync(OUT, { recursive: true })
const post = (b) => fetch(`${BASE}/api/session`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(b) }).then((r) => r.json())
let session = await (await fetch(`${BASE}/api/session`)).json()
if (session.mode !== 'replay') throw new Error(`server must run in replay mode, got ${session.mode}`)
await post({ action: 'pause' })
await post({ action: 'speed', speed: SPEED })
session = await post({ action: 'seek', ts: session.to_ts - LEAD_S })

const browser = await chromium.launch({ args: ['--use-angle=metal', '--use-gl=angle', '--enable-gpu', '--ignore-gpu-blocklist'] })
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1, recordVideo: { dir: OUT, size: { width: 1440, height: 900 } } })
const page = await ctx.newPage()
const t0 = Date.now()
const marks = []
const mark = (name, note = '') => marks.push({ name, note, offset_s: Math.round((Date.now() - t0) / 100) / 10 })
const shot = (name) => page.screenshot({ path: path.join(OUT, `${name}.png`) })
const state = () => page.evaluate(() => window.__stampede_state())

await page.goto(`${BASE}/?view=radar`)
await page.waitForSelector('.radar-row:not(.head)', { timeout: 30000 })
await page.waitForTimeout(1200)
mark('radar', 'RADAR: coins ranked by rotation inflow, filters and presets, alerts journal on the right')
await shot('radar')
await post({ action: 'play' })
mark('play', `replay running at ${SPEED}x: inflow counters move, rows flash when a coin gains wallets`)
await page.waitForTimeout(6500)
await shot('radar-playing')
const top = page.locator('.radar-row:not(.head)').nth(0)
const topSymbol = (await top.locator('.coin b').textContent()) ?? ''
await top.click()
await page.waitForSelector('.coin-drawer h1')
await page.waitForFunction(() => document.querySelector('.coin-drawer')?.textContent?.includes('On-chain'), null, { timeout: 15000 })
await page.waitForTimeout(2600)
mark('drawer', `coin drawer for ${topSymbol}: age, curve progress, price, rotation from/to, holders and X attention when fetched`)
await shot('drawer')
await page.locator('.coin-drawer button', { hasText: 'Flow' }).first().click()
await page.waitForSelector('.flow-svg .ribbon', { timeout: 15000 })
await page.keyboard.press('Escape') // close the drawer so the flow is unobstructed
await page.waitForTimeout(3200)
mark('flow', `FLOW: where ${topSymbol}'s wallets came from and where they went; ribbon width = distinct wallets`)
await shot('flow')
await page.locator('.flow-svg .ribbon').nth(0).locator('path').first().click({ force: true })
await page.waitForFunction(() => window.__stampede_state().view === 'map' && window.__stampede_state().viewState !== 'overview', null, { timeout: 15000 }).catch(() => {})
await page.waitForFunction(() => window.__stampede_state().viewState === 'evidence', null, { timeout: 12000 }).catch(() => {})
await page.waitForTimeout(1200)
mark('evidence', 'MAP: the ribbon opens the pair in the 3D scene with the computed wallet count and evidence')
await shot('evidence')
await page.keyboard.press('e')
await page.waitForSelector('.evidence-panel .seq', { timeout: 10000 }).catch(() => {})
await page.waitForTimeout(2200)
await shot('evidence-rows')
await page.keyboard.press('e')
await page.keyboard.press('1')
await page.waitForSelector('.radar-row:not(.head)')
await page.waitForTimeout(1500)
mark('alerts', 'back to RADAR: the alert journal with 30-minute outcomes measured on indexed trades')
await shot('alerts')
await post({ action: 'pause' })
await page.waitForTimeout(800)
mark('end')
const st = await state()
const video = page.video()
await ctx.close()
if (video) fs.renameSync(await video.path(), path.join(OUT, 'web-walkthrough.webm'))
fs.writeFileSync(path.join(OUT, 'marks.json'), JSON.stringify({ recorded_at_utc: new Date().toISOString(), base: BASE, session_id: session.id, replay_speed: SPEED, replay_clock_start_ts: session.clock_ts, top_symbol: topSymbol, marks, final_state: st }, null, 1))
await browser.close()
console.log(JSON.stringify({ session_id: session.id, topSymbol, marks }, null, 1))
