// Render Textual SVG screenshots to PNG with Chromium (exact colours, embedded monospace font).
//   node e2e/svg2png.mjs ../demo/tui/*.svg
import { chromium } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'

const files = process.argv.slice(2)
if (!files.length) {
  console.error('usage: node e2e/svg2png.mjs <files.svg>')
  process.exit(2)
}
const browser = await chromium.launch()
const page = await browser.newPage({ deviceScaleFactor: 2 })
for (const f of files) {
  const svg = fs.readFileSync(f, 'utf8')
  const w = Number((svg.match(/width="([\d.]+)"/) || [])[1] || 1600)
  const h = Number((svg.match(/height="([\d.]+)"/) || [])[1] || 900)
  await page.setViewportSize({ width: Math.ceil(w), height: Math.ceil(h) })
  await page.setContent(`<html><body style="margin:0;background:#050505">${svg}</body></html>`)
  await page.waitForTimeout(150)
  const out = f.replace(/\.svg$/, '.png')
  await page.screenshot({ path: out, clip: { x: 0, y: 0, width: Math.ceil(w), height: Math.ceil(h) } })
  console.log(path.basename(out), `${Math.ceil(w)}x${Math.ceil(h)}`)
}
await browser.close()
