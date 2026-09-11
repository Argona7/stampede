import { expect, test, type Page } from '@playwright/test'

type Sel = { kind: 'edge'; from: string; to: string } | { kind: 'token'; address: string } | null
declare global {
  interface Window {
    __stampede_select: (s: Sel) => void
    __stampede_state: () => { viewState: string; selection: Sel; layout: string; renderer: string; edges: number; sessionClock: number | null; playing: boolean }
  }
}

const post = (page: Page, body: Record<string, unknown>) => page.request.post('/api/session', { data: body })

async function topEdge(page: Page) {
  const s = await (await page.request.get('/api/session')).json()
  const to = s.clock_ts as number
  const g = await (await page.request.get(`/api/graph?window=${s.window_s}s&from=${to - s.span_s}&to=${to}&min_wallets=3&limit=3`)).json()
  return { edge: g.edges[0], session: s }
}

test.beforeEach(async ({ page }) => {
  const s = await (await page.request.get('/api/session')).json()
  test.skip(s.mode !== 'replay', 'these tests need the server in replay mode')
  await post(page, { action: 'pause' })
  await post(page, { action: 'seek', ts: s.to_ts - 300 })
})

test('strip reflects the shared session and the scene renders', async ({ page }) => {
  await page.goto('/?layout=presentation&view=map')
  await expect(page.locator('.badge')).toContainText(/REPLAY/)
  await expect(page.locator('.scene3d canvas')).toBeVisible()
  await page.waitForFunction(() => window.__stampede_state && (window.__stampede_state().edges ?? 0) > 0)
  const st = await page.evaluate(() => window.__stampede_state())
  expect(st.layout).toBe('presentation')
  expect(st.viewState).toBe('overview')
  await expect(page.locator('.strip .mid')).toContainText('SHOWING')
})

test('selecting an edge flies to it and the caption shows the computed wallet count', async ({ page }) => {
  await page.goto('/?layout=presentation&view=map')
  await page.waitForFunction(() => window.__stampede_state && (window.__stampede_state().edges ?? 0) > 0)
  const { edge, session } = await topEdge(page)
  const detail = await (await page.request.get(`/api/edge/${edge.from}/${edge.to}?window=${session.window_s}s&from=${session.clock_ts - session.span_s}&to=${session.clock_ts}&exact=0`)).json()
  await page.evaluate((e) => window.__stampede_select({ kind: 'edge', from: e.from, to: e.to }), edge)
  await page.waitForFunction(() => window.__stampede_state().viewState === 'follow' || window.__stampede_state().viewState === 'evidence')
  await page.waitForFunction(() => window.__stampede_state().viewState === 'evidence', null, { timeout: 8000 })
  const cap = page.getByTestId('caption')
  await expect(cap).toContainText(`${detail.wallets_main} WALLET`)
  await expect(cap).toContainText(`SOLD ${detail.from.symbol}`)
  await expect(cap).toContainText(`BOUGHT ${detail.to.symbol}`)
  await expect(cap).toContainText('observed sequences')
  // the pair labels exist and do not overlap
  const a = await page.locator('.lbl.big.a').boundingBox()
  const b = await page.locator('.lbl.big.b').boundingBox()
  expect(a && b).toBeTruthy()
  const overlap = !(a!.x + a!.width < b!.x || b!.x + b!.width < a!.x || a!.y + a!.height < b!.y || b!.y + b!.height < a!.y)
  expect(overlap).toBe(false)
  // E opens the evidence rows, Esc returns to the overview
  await page.keyboard.press('e')
  await expect(page.locator('.evidence-panel .seq').first()).toBeVisible()
  await page.keyboard.press('Escape')
  await page.waitForFunction(() => window.__stampede_state().viewState === 'overview', null, { timeout: 5000 })
})

test('pause adds no events and a repeated snapshot changes no counts', async ({ page }) => {
  await page.goto('/?layout=presentation&view=map')
  await page.waitForFunction(() => window.__stampede_state && (window.__stampede_state().edges ?? 0) > 0)
  const before = await page.evaluate(() => window.__stampede_state())
  await page.waitForTimeout(4000)
  const after = await page.evaluate(() => window.__stampede_state())
  expect(after.edges).toBe(before.edges)
  expect(after.sessionClock).toBe(before.sessionClock)
  expect(await page.locator('.pulse-lbl').count()).toBe(0)
})

test('play produces pulses that stop on pause; seek does not replay history as fresh', async ({ page }) => {
  await page.goto('/?layout=presentation&view=map')
  await page.waitForFunction(() => window.__stampede_state && (window.__stampede_state().edges ?? 0) > 0)
  await post(page, { action: 'speed', speed: 10 })
  await post(page, { action: 'play' })
  await page.waitForFunction(() => document.querySelectorAll('.pulse-lbl').length > 0, null, { timeout: 15000 })
  await post(page, { action: 'pause' })
  await page.waitForTimeout(3500)
  expect(await page.locator('.pulse-lbl').count()).toBe(0)
  // seek far back: history is loaded, nothing pulses
  const s = await (await page.request.get('/api/session')).json()
  await post(page, { action: 'seek', ts: s.from_ts + s.span_s + 600 })
  await page.waitForTimeout(3500)
  expect(await page.locator('.pulse-lbl').count()).toBe(0)
})

test('2D fallback renderer is available', async ({ page }) => {
  await page.goto('/?renderer=2d&view=map')
  await expect(page.locator('.map-wrap canvas')).toBeVisible()
  await page.getByRole('button', { name: 'Toggle renderer' }).click()
  await expect(page.locator('.scene3d canvas')).toBeVisible()
})

test('explore layout keeps rails, details and ticker', async ({ page }) => {
  await page.goto('/?view=map')
  await expect(page.locator('.rail')).toBeVisible()
  await expect(page.locator('.detail')).toContainText('Nothing selected')
  await expect(page.locator('.ticker')).toBeVisible()
  await page.getByTestId('layout-toggle').click()
  await expect(page.locator('.rail')).toHaveCount(0)
})

test('radar view ranks coins, opens the coin drawer, presets and keys work', async ({ page }) => {
  await page.goto('/?view=radar')
  await expect(page.getByTestId('view-radar')).toHaveClass(/on/)
  await page.waitForSelector('.radar-row:not(.head)', { timeout: 20000 })
  const rows = page.locator('.radar-row:not(.head)')
  expect(await rows.count()).toBeGreaterThan(3)
  // scores descend under the default sort
  const s0 = Number(await rows.nth(0).locator('.score b').textContent())
  const s1 = Number(await rows.nth(1).locator('.score b').textContent())
  expect(s0).toBeGreaterThanOrEqual(s1)
  // every row shows inflow, at least one source chip and an age
  await expect(rows.nth(0).locator('.inflow b')).not.toHaveText('')
  expect(await rows.nth(0).locator('.sources em').count()).toBeGreaterThan(0)
  // drawer
  await rows.nth(0).click()
  await expect(page.locator('.coin-drawer h1')).not.toHaveText('Loading…', { timeout: 15000 })
  await expect(page.locator('.coin-drawer')).toContainText('On-chain')
  await expect(page.locator('.coin-drawer')).toContainText('wallets came from')
  await page.keyboard.press('Escape')
  await expect(page.locator('.coin-drawer')).toHaveCount(0)
  // preset switch changes the request (graduating sorts by progress) and the header label
  await page.getByRole('button', { name: 'graduating' }).click()
  await expect(page.locator('.presets button.on')).toHaveText(/graduating/)
  // keys switch views
  await page.keyboard.press('3')
  await expect(page.getByTestId('view-map')).toHaveClass(/on/)
  await page.keyboard.press('1')
  await expect(page.getByTestId('view-radar')).toHaveClass(/on/)
})

test('flow view draws readable ribbons for the top radar coin and links to evidence', async ({ page }) => {
  await page.goto('/?view=radar')
  await page.waitForSelector('.radar-row:not(.head)', { timeout: 20000 })
  await page.locator('.radar-row:not(.head)').nth(0).locator('.actions button').click()
  await expect(page.getByTestId('view-flow')).toHaveClass(/on/)
  await page.waitForSelector('.flow-svg .ribbon', { timeout: 15000 })
  const ribbons = page.locator('.flow-svg .ribbon')
  expect(await ribbons.count()).toBeGreaterThan(0)
  await expect(page.locator('.flow-head')).toContainText('wallets rotated')
  // source labels do not overlap: boxes are stacked with gaps
  const boxes = await page.locator('.flow-svg .src rect').evaluateAll((els) => els.map((e) => e.getBoundingClientRect()).sort((a, b) => a.y - b.y))
  for (let i = 1; i < boxes.length; i++) {
    if (Math.abs(boxes[i].x - boxes[i - 1].x) < 5) expect(boxes[i].y).toBeGreaterThanOrEqual(boxes[i - 1].y + boxes[i - 1].height - 1)
  }
  // clicking a ribbon opens the edge evidence in the map view
  await ribbons.nth(0).locator('path').first().click({ force: true })
  await expect(page.getByTestId('view-map')).toHaveClass(/on/)
  await page.waitForFunction(() => window.__stampede_state().selection?.kind === 'edge')
})
