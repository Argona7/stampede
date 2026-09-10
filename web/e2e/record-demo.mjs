// Records the three key states of the terminal against a server running in REPLAY mode and writes
// screenshots, a webm walkthrough and a marks.json with timeline offsets for the cut.
//
//   python scripts/serve_daemon.py start --mode replay --port 8791
//   cd web && node e2e/record-demo.mjs
//
// The scenario is chosen from the recorded data itself: the last 4 minutes of the sample contain the
// TruffleHog -> LUNAR wave (124 distinct wallets). Nothing here is staged or edited into the data.
import { chromium } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'

const BASE = process.env.STAMPEDE_URL ?? 'http://127.0.0.1:8791'
const OUT = path.resolve(process.env.OUT ?? '../demo/raw')
const SPEED = process.env.SPEED ?? '10x'
const LEAD_S = 330 // start the replay clock this many seconds before the end of the sample (the wave starts ~125 s before the end)

fs.mkdirSync(OUT, { recursive: true })
const status = await (await fetch(`${BASE}/api/status`)).json()
if (status.mode !== 'replay') throw new Error(`server must run in replay mode, got ${status.mode}`)

const browser = await chromium.launch()
const ctx = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  deviceScaleFactor: 1,
  recordVideo: { dir: OUT, size: { width: 1440, height: 900 } },
  reducedMotion: 'no-preference',
})
const page = await ctx.newPage()
const t0 = Date.now()
const marks = []
const mark = (name, note = '') => marks.push({ name, note, offset_s: Math.round((Date.now() - t0) / 100) / 10 })

await page.goto(BASE)
await page.waitForSelector('.ticker li')
await page.waitForTimeout(2500)
mark('loaded', 'map settled at the start of the replay range')

const target = status.sample.to_ts - LEAD_S
await page.evaluate((v) => {
  const el = document.querySelector('input[aria-label="End of the visible range"]')
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set
  setter.call(el, String(v))
  el.dispatchEvent(new Event('input', { bubbles: true }))
}, target)
await page.waitForTimeout(3000)
mark('range_set', `replay clock moved to sample end minus ${LEAD_S}s`)
await page.screenshot({ path: path.join(OUT, 'state-0-map.png') })

await page.getByRole('button', { name: SPEED }).click()
await page.getByRole('button', { name: 'Play' }).click()
mark('play', `replay running at ${SPEED}`)
await page.waitForTimeout(22000)
mark('mid_play', 'new confirmed sequences arrive: edges pulse, ticker rows appear')
await page.screenshot({ path: path.join(OUT, 'state-3-new-events.png') })
await page.waitForTimeout(5000)
await page.getByRole('button', { name: 'Pause' }).click()
mark('pause')

await page.locator('.ticker li', { hasText: 'TruffleHog → LUNAR' }).first().click()
mark('edge_selected', 'edge TruffleHog -> LUNAR selected from the ticker; camera focuses')
await page.waitForSelector('.detail .seq')
await page.waitForTimeout(2000)
await page.screenshot({ path: path.join(OUT, 'state-1-edge.png') })
await page.waitForTimeout(2500) // hold the header (wallet count + meaning) on screen before scrolling to the rows

await page.locator('.detail').evaluate((el) => el.scrollBy({ top: 240, behavior: 'smooth' }))
await page.waitForTimeout(1200)
await page.locator('.detail .seq a[href*="/tx/"]').first().hover()
mark('evidence', 'evidence rows: wallet, sold/bought, exact UTC time, block, tx links')
await page.waitForTimeout(1600)
await page.screenshot({ path: path.join(OUT, 'state-2-evidence.png') })

await page.locator('.detail h1 span').filter({ hasText: 'LUNAR' }).click()
await page.waitForSelector('.detail .list')
await page.waitForTimeout(2000)
mark('token', 'coin view: buyers, sellers, inbound and outbound edges')
await page.screenshot({ path: path.join(OUT, 'state-4-token.png') })
await page.waitForTimeout(1000)
mark('end')

const video = page.video()
await ctx.close()
const vp = await video.path()
fs.renameSync(vp, path.join(OUT, 'walkthrough.webm'))
fs.writeFileSync(
  path.join(OUT, 'marks.json'),
  JSON.stringify(
    {
      recorded_at_utc: new Date().toISOString(),
      base: BASE,
      mode: status.mode,
      sample: status.sample,
      replay_speed: SPEED,
      replay_clock_start_ts: target,
      marks,
    },
    null,
    1,
  ),
)
await browser.close()
console.log(JSON.stringify(marks, null, 1))
