import { expect, test, type Page } from '@playwright/test'

// Stage 7: the event-stream client and the SIGNALS view.
// - against the replay instance (baseURL): the stream carries only the hello (no engine), the UI keeps polling, SIGNALS
//   renders the paper ledger from /api/paper with honest empty states and the REPLAY badge;
// - against a live engine (STAMPEDE_LIVE_URL, e.g. http://127.0.0.1:8821): RADAR rows come from radar_delta, the badge
//   shows `LIVE · <lag> ms`, SIGNALS says `LIVE · stream`, and /api/stream resumes from Last-Event-ID.

declare global {
  interface Window {
    __stampede_state: () => { view: string; live: string; streaming: boolean; liveRows: number | null; liveDeltas: number; radarRows: number; alerts: number; paperOpen: number | null; paperClosed: number | null }
  }
}

const LIVE = process.env.STAMPEDE_LIVE_URL

type Hello = { id: number | null; type: string; data: { hello: boolean; engine: string; last_event_id: number; types: string[]; replay_gap: boolean } }

/** The first frame of /api/stream through a real EventSource on the page (a process without an engine sends nothing
 *  after the hello but keepalives, so a plain GET would never end). */
async function hello(page: Page): Promise<Hello> {
  return page.evaluate(
    () =>
      new Promise<Hello>((resolve, reject) => {
        const es = new EventSource('/api/stream')
        const t = window.setTimeout(() => {
          es.close()
          reject(new Error('no hello within 15 s'))
        }, 15000)
        es.addEventListener('session', (e) => {
          window.clearTimeout(t)
          es.close()
          resolve(JSON.parse((e as MessageEvent).data as string) as Hello)
        })
      }),
  )
}

test('replay: the stream has no engine, so the views poll; SIGNALS renders the paper ledger with honest empty states', async ({ page }) => {
  const s = await (await page.request.get('/api/session')).json()
  test.skip(s.mode !== 'replay', 'this test needs the server in replay mode')
  await page.request.post('/api/session', { data: { action: 'pause' } })
  await page.request.post('/api/session', { data: { action: 'seek', ts: s.to_ts - 300 } })
  // the page never opens a stream in replay: polling as before, the badge reads REPLAY
  await page.goto('/?view=radar')
  // the hello of a process without the realtime engine: no id, engine "none", the position type is announced
  const h = await hello(page)
  expect(h.id).toBeNull()
  expect(h.data.hello).toBe(true)
  expect(h.data.engine).toBe('none')
  expect(h.data.types).toContain('position')
  await page.waitForSelector('.radar-row', { timeout: 60000 })
  await expect(page.getByTestId('mode')).toContainText('REPLAY')
  await expect(page.getByTestId('mode')).toHaveAttribute('data-live', 'n/a')
  const st = await page.evaluate(() => window.__stampede_state())
  expect(st.live).toBe('off')
  expect(st.streaming).toBe(false)
  expect(st.radarRows).toBeGreaterThan(0)
  // key 5 opens SIGNALS: the working row says the fills are simulated and the connection is polling
  await page.keyboard.press('5')
  await expect(page.getByTestId('view-signals')).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByTestId('signals')).toBeVisible()
  await expect(page.getByTestId('signals-conn')).toContainText('REPLAY · polling')
  await expect(page.locator('.signals .workbar')).toContainText('paper fills are simulated')
  const paper = await (await page.request.get('/api/paper')).json()
  expect(paper.simulated).toBe(true)
  expect(paper.source).toBe('tables')
  const stats = page.getByTestId('paper-stats')
  await expect(stats).toContainText('closed trades (coins)')
  await expect(stats).toContainText(`${paper.stats.trades} (${paper.stats.coins})`)
  if (paper.stats.trades === 0) {
    await expect(page.getByTestId('closed-empty')).toContainText('none closed yet')
    await expect(stats).toContainText('n/a') // hit rate / expectancy unknown, never 0
  }
  if (paper.positions.open.length === 0) await expect(page.getByTestId('positions-empty')).toContainText('none open')
  const alerts = await (await page.request.get('/api/alerts?limit=120')).json()
  if (alerts.alerts.length === 0) await expect(page.getByTestId('signals-empty')).toBeVisible()
  else expect(await page.locator('.sig-table.alerts .sig-row').count()).toBeGreaterThan(0)
  const tr = await (await page.request.get('/api/track-record?alerts_limit=0')).json()
  expect(tr.simulated).toBe(true)
  expect(tr.perf).toBeNull() // no engine in a replay process
  await expect(page.getByTestId('track-record')).toBeVisible()
  await expect(page.locator('.sig-stats .note')).toContainText('Not a return anyone earned')
  // the three columns share the width without a horizontal scroll
  const body = await page.locator('.signals-body').evaluate((el) => ({ scroll: el.scrollWidth, client: el.clientWidth }))
  expect(body.scroll).toBeLessThanOrEqual(body.client)
  // keys go back and forth; Esc in SIGNALS clears the selection instead of leaving the view
  await page.keyboard.press('1')
  await expect(page.getByTestId('view-radar')).toHaveAttribute('aria-selected', 'true')
  await page.keyboard.press('5')
  await expect(page.getByTestId('view-signals')).toHaveAttribute('aria-selected', 'true')
  await page.keyboard.press('Escape')
  await expect(page.getByTestId('view-signals')).toHaveAttribute('aria-selected', 'true')
})

test('live engine: RADAR rows arrive from radar_delta, the badge carries the lag, SIGNALS streams, the stream resumes from Last-Event-ID', async ({ page }) => {
  test.skip(!LIVE, 'set STAMPEDE_LIVE_URL to a running `stampede serve --mode live --feed wss` instance')
  const base = LIVE!.replace(/\/$/, '')
  const s = await (await page.request.get(`${base}/api/session`)).json()
  test.skip(s.mode !== 'live', 'the live URL is not in live mode')
  await page.goto(`${base}/?view=radar`)
  const h = await hello(page)
  expect(h.data.engine).toBe('wss')
  expect(h.data.last_event_id).toBeGreaterThan(0)
  // resume: everything after the given id comes back from the ring, in order, with ids > since
  const since = Math.max(0, h.data.last_event_id - 3)
  const r = await page.request.get(`${base}/api/stream?limit=3`, { headers: { 'Last-Event-ID': String(since), accept: 'text/event-stream' } })
  const frames = (await r.text()).split('\n\n').filter((f) => f.includes('\nevent: ') || f.startsWith('event: '))
  const ids = frames.map((f) => f.split('\n').find((l) => l.startsWith('id:'))).filter(Boolean).map((l) => Number(l!.slice(3)))
  expect(ids.length).toBe(3)
  expect(ids[0]).toBeGreaterThan(since)
  expect(ids).toEqual([...ids].sort((a, b) => a - b))
  // the page: stream connected, rows folded from deltas, the badge shows one small latency number
  await page.waitForFunction(() => window.__stampede_state && window.__stampede_state().streaming && (window.__stampede_state().liveRows ?? 0) > 0, null, { timeout: 30000 })
  await expect(page.getByTestId('mode')).toHaveAttribute('data-live', 'live')
  await expect(page.getByTestId('mode')).toHaveText(/^LIVE · \d+(\.\d+)? (ms|s)$/)
  await page.waitForSelector('.radar-row', { timeout: 20000 })
  await expect(page.getByTestId('order-note')).toContainText(/ranked by|order held/)
  await expect(page.locator('.radar-meta')).toContainText('rows from the event stream', { timeout: 15000 })
  const d0 = (await page.evaluate(() => window.__stampede_state())).liveDeltas
  await page.waitForFunction((d) => window.__stampede_state().liveDeltas > d, d0, { timeout: 20000 })
  // hovering holds the order while deltas keep coming; the selection survives them
  const rows = page.locator('.radar-row')
  const a0 = await rows.nth(0).getAttribute('data-address')
  await rows.nth(0).click()
  await page.keyboard.press('d')
  await rows.nth(1).hover()
  await expect(page.getByTestId('order-note')).toContainText('order held', { timeout: 10000 })
  await page.waitForTimeout(1500)
  await expect(page.locator('.radar-row.sel')).toHaveAttribute('data-address', a0!)
  await page.mouse.move(700, 20)
  // SIGNALS on the live instance: stream state, engine latency, the ledger from the engine's memory
  await page.keyboard.press('5')
  await expect(page.getByTestId('signals-conn')).toContainText('LIVE · stream', { timeout: 15000 })
  await expect(page.getByTestId('signals-conn')).toContainText('engine p50', { timeout: 15000 })
  const paper = await (await page.request.get(`${base}/api/paper`)).json()
  expect(paper.source).toBe('engine')
  expect(paper.simulated).toBe(true)
  // /api/paper and /api/track-record read a multi-GB store on a shared machine: give them time
  await expect(page.getByTestId('paper-stats')).toContainText('closed trades (coins)', { timeout: 20000 })
  await expect(page.getByTestId('track-record')).toContainText('run', { timeout: 30000 })
  await expect(page.getByTestId('track-record')).toContainText('uptime', { timeout: 30000 })
})
