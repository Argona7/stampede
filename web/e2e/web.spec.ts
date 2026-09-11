import { expect, test, type Page } from '@playwright/test'

type Sel = { kind: 'edge'; from: string; to: string } | { kind: 'token'; address: string } | null
declare global {
  interface Window {
    __stampede_select: (s: Sel) => void
    __stampede_state: () => { viewState: string; selection: Sel; layout: string; renderer: string; view: string; startView: string; coin: string | null; drawerOpen: boolean; edges: number; sessionClock: number | null; playing: boolean; reduced: boolean }
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

test('strip shows the brand mark, the shared session state, and the scene renders', async ({ page }) => {
  await page.goto('/?layout=presentation&view=map')
  await expect(page.locator('.badge')).toContainText(/REPLAY/)
  await expect(page.locator('.badge')).toContainText(/PAUSED/)
  const mark = page.locator('.brand img.mark')
  await expect(mark).toBeVisible()
  const box = await mark.boundingBox()
  expect(box!.height).toBeGreaterThanOrEqual(24) // 16x16 pixel mark at an integer factor
  expect(box!.height % 16).toBe(0)
  await expect(page.locator('.scene3d canvas')).toBeVisible()
  await page.waitForFunction(() => window.__stampede_state && (window.__stampede_state().edges ?? 0) > 0)
  const st = await page.evaluate(() => window.__stampede_state())
  expect(st.layout).toBe('presentation')
  expect(st.viewState).toBe('overview')
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
  await expect(cap).toContainText(`${detail.sequences_total} observed sequence rows`)
  // the pair labels exist and do not overlap
  const a = await page.locator('.lbl.big.a').boundingBox()
  const b = await page.locator('.lbl.big.b').boundingBox()
  expect(a && b).toBeTruthy()
  const overlap = !(a!.x + a!.width < b!.x || b!.x + b!.width < a!.x || a!.y + a!.height < b!.y || b!.y + b!.height < a!.y)
  expect(overlap).toBe(false)
  // E opens the evidence rows with the same counts and readable tx links, Esc returns to the overview
  await page.keyboard.press('e')
  const panel = page.locator('.evidence-panel')
  await expect(panel.locator('.seq').first()).toBeVisible()
  await expect(panel).toContainText(`${detail.wallets_main} distinct wallets`)
  await expect(panel).toContainText(`${detail.sequences_total} sequence rows`)
  await expect(panel.locator('.seq a[href*="tx"]').first()).toBeVisible()
  // the legend hint is not under the panel
  const hint = await page.locator('.legend span:last-child').boundingBox()
  const pb = await panel.boundingBox()
  expect(hint!.x + hint!.width).toBeLessThanOrEqual(pb!.x + 1)
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

test('explore layout keeps the working row, rail, details and ticker; the map does not drift on its own', async ({ page }) => {
  await page.goto('/?view=map')
  await expect(page.getByTestId('edges-shown')).toContainText('SHOWING')
  await expect(page.locator('.rail')).toBeVisible()
  await expect(page.getByTestId('detail-empty')).toContainText('Nothing selected')
  await expect(page.locator('.ticker')).toBeVisible()
  await page.waitForFunction(() => window.__stampede_state && (window.__stampede_state().edges ?? 0) > 0)
  // after the arrival flight the camera stays put: two frames 3 s apart are identical (unless another client
  // of the shared clock moved it in between, which redraws the data)
  await page.waitForTimeout(4500)
  const c1 = (await page.evaluate(() => window.__stampede_state())).sessionClock
  const shot1 = await page.locator('.scene3d canvas').screenshot()
  await page.waitForTimeout(3000)
  const shot2 = await page.locator('.scene3d canvas').screenshot()
  const c2 = (await page.evaluate(() => window.__stampede_state())).sessionClock
  if (c1 === c2) expect(shot1.equals(shot2)).toBe(true)
  await page.getByTestId('layout-toggle').click()
  await expect(page.locator('.rail')).toHaveCount(0)
})

test('radar view ranks coins in a table, opens the coin drawer, presets and keys work', async ({ page }) => {
  await page.goto('/?view=radar')
  await expect(page.getByTestId('view-radar')).toHaveAttribute('aria-selected', 'true')
  await page.waitForSelector('.radar-row', { timeout: 20000 })
  const rows = page.locator('.radar-row')
  expect(await rows.count()).toBeGreaterThan(3)
  // scores descend under the default sort; numbers are right-aligned tabular figures
  const s0 = Number(await rows.nth(0).locator('.c-score').textContent())
  const s1 = Number(await rows.nth(1).locator('.c-score').textContent())
  expect(s0).toBeGreaterThanOrEqual(s1)
  expect(await rows.nth(0).locator('.c-in').evaluate((el) => getComputedStyle(el).textAlign)).toBe('right')
  expect(await page.locator('.radar-table').evaluate((el) => getComputedStyle(el).fontVariantNumeric)).toContain('tabular-nums')
  // every row shows inflow, its sources and a short address; unknown context is n/a, never 0
  await expect(rows.nth(0).locator('.c-in')).not.toHaveText('')
  await expect(rows.nth(0).locator('.c-from')).toContainText('src')
  await expect(rows.nth(0).locator('.c-coin .addr')).toContainText('…')
  const ctx = await (await page.request.get('/api/radar?limit=1')).json()
  if (!ctx.context?.enabled) await expect(rows.nth(0).locator('.c-x')).toHaveText('n/a')
  // drawer opens next to the table, not over it
  await rows.nth(0).click()
  await expect(page.locator('.coin-drawer h1')).not.toHaveText('Loading…', { timeout: 15000 })
  await expect(page.locator('.coin-drawer')).toContainText('On-chain')
  await expect(page.locator('.coin-drawer')).toContainText('wallets came from')
  const tb = await page.locator('.radar-table').boundingBox()
  const db = await page.locator('.coin-drawer').boundingBox()
  expect(tb!.x + tb!.width).toBeLessThanOrEqual(db!.x + 1)
  await page.keyboard.press('Escape')
  await expect(page.locator('.coin-drawer')).toHaveCount(0)
  // preset switch changes the request (graduating sorts by progress) and the header label
  await page.getByRole('button', { name: 'graduating' }).click()
  await expect(page.locator('.presets button.on')).toHaveText(/graduating/)
  // keys switch views
  await page.keyboard.press('3')
  await expect(page.getByTestId('view-map')).toHaveAttribute('aria-selected', 'true')
  await page.keyboard.press('1')
  await expect(page.getByTestId('view-radar')).toHaveAttribute('aria-selected', 'true')
})

test('radar selection is keyed by address, survives polling, moves with the arrows; Enter opens FLOW, Esc returns', async ({ page }) => {
  await page.goto('/?view=radar')
  await page.waitForSelector('.radar-row', { timeout: 20000 })
  const rows = page.locator('.radar-row')
  const a0 = await rows.nth(0).getAttribute('data-address')
  const a1 = await rows.nth(1).getAttribute('data-address')
  await rows.nth(0).click()
  await expect(page.locator('.radar-row.sel')).toHaveAttribute('data-address', a0!)
  await page.keyboard.press('d') // close the drawer, keep the selection
  await expect(page.locator('.coin-drawer')).toHaveCount(0)
  // the pointer is over the table: the order is held and the note says so
  await rows.nth(2).hover()
  await expect(page.getByTestId('order-note')).toContainText('order held')
  await page.mouse.move(700, 20)
  await expect(page.getByTestId('order-note')).toContainText('ranked by')
  // two radar polls later the same coin is still selected
  await page.waitForTimeout(5000)
  await expect(page.locator('.radar-row.sel')).toHaveAttribute('data-address', a0!)
  await expect(page.locator('.radar-row.sel')).toHaveCount(1)
  // keyboard
  await page.keyboard.press('ArrowDown')
  await expect(page.locator('.radar-row.sel')).toHaveAttribute('data-address', a1!)
  expect((await page.evaluate(() => window.__stampede_state())).coin).toBe(a1)
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('view-flow')).toHaveAttribute('aria-selected', 'true')
  await page.waitForSelector('.flow-svg .ribbon', { timeout: 15000 })
  await page.keyboard.press('Escape')
  await expect(page.getByTestId('view-radar')).toHaveAttribute('aria-selected', 'true')
  await expect(page.locator('.radar-row.sel')).toHaveAttribute('data-address', a1!)
})

test('brand click returns to the start view without resetting the clock, filters or selection', async ({ page }) => {
  await page.goto('/?view=radar')
  await page.waitForSelector('.radar-row', { timeout: 20000 })
  await page.getByRole('button', { name: 'all' }).click()
  await expect(page.locator('.presets button.on')).toHaveText(/all/)
  await page.waitForSelector('.radar-row', { timeout: 20000 })
  const addr = await page.locator('.radar-row').nth(0).getAttribute('data-address')
  await page.locator('.radar-row').nth(0).click()
  await page.keyboard.press('d')
  const clock = (await page.evaluate(() => window.__stampede_state())).sessionClock
  await page.keyboard.press('3')
  await expect(page.getByTestId('view-map')).toHaveAttribute('aria-selected', 'true')
  await page.getByTestId('brand').click()
  await expect(page.getByTestId('view-radar')).toHaveAttribute('aria-selected', 'true')
  const st = await page.evaluate(() => window.__stampede_state())
  expect(st.startView).toBe('radar')
  expect(st.coin).toBe(addr)
  expect(st.sessionClock).toBe(clock)
  await expect(page.locator('.presets button.on')).toHaveText(/all/)
  await expect(page.locator('.radar-row.sel')).toHaveAttribute('data-address', addr!)
})

test('view tabs are keyboard operable with distinct active and focus states', async ({ page }) => {
  await page.goto('/?view=radar')
  await page.getByTestId('view-radar').focus()
  await page.keyboard.press('ArrowRight')
  await expect(page.getByTestId('view-flow')).toHaveAttribute('aria-selected', 'true')
  expect(await page.evaluate(() => document.activeElement?.getAttribute('data-testid'))).toBe('view-flow')
  // active = red underline (inset shadow); focus = light outline
  const active = await page.getByTestId('view-flow').evaluate((el) => ({ shadow: getComputedStyle(el).boxShadow, outline: getComputedStyle(el).outlineStyle }))
  expect(active.shadow).toContain('rgb(255, 51, 68)')
  expect(active.outline).toBe('solid')
  const idle = await page.getByTestId('view-map').evaluate((el) => getComputedStyle(el).boxShadow)
  expect(idle).not.toContain('rgb(255, 51, 68)')
  await page.keyboard.press('End')
  await expect(page.getByTestId('view-map')).toHaveAttribute('aria-selected', 'true')
})

test('flow view draws readable ribbons for the top radar coin and links to evidence', async ({ page }) => {
  await page.goto('/?view=radar')
  await page.waitForSelector('.radar-row', { timeout: 20000 })
  await page.locator('.radar-row').nth(0).click()
  await page.getByTestId('drawer-flow').click()
  await expect(page.getByTestId('view-flow')).toHaveAttribute('aria-selected', 'true')
  await page.waitForSelector('.flow-svg .ribbon', { timeout: 15000 })
  const ribbons = page.locator('.flow-svg .ribbon')
  expect(await ribbons.count()).toBeGreaterThan(0)
  await expect(page.getByTestId('flow-summary')).toContainText('wallets rotated')
  // source labels do not overlap: boxes are stacked with gaps, and none touches the centre label
  const boxes = await page.locator('.flow-svg .src rect').evaluateAll((els) => els.map((e) => e.getBoundingClientRect()).sort((a, b) => a.y - b.y))
  for (let i = 1; i < boxes.length; i++) {
    if (Math.abs(boxes[i].x - boxes[i - 1].x) < 5) expect(boxes[i].y).toBeGreaterThanOrEqual(boxes[i - 1].y + boxes[i - 1].height - 1)
  }
  const centre = await page.locator('.flow-svg text.big').boundingBox()
  for (const b of boxes) expect(b.x + b.width < centre!.x || b.x > centre!.x + centre!.width).toBe(true)
  // the drawer sits beside the diagram
  const svg = await page.locator('.flow-svg').boundingBox()
  const drawer = await page.locator('.coin-drawer').boundingBox()
  expect(svg!.x + svg!.width).toBeLessThanOrEqual(drawer!.x + 1)
  // clicking a ribbon opens the edge evidence in the map view
  await ribbons.nth(0).locator('path').first().click({ force: true })
  await expect(page.getByTestId('view-map')).toHaveAttribute('aria-selected', 'true')
  await page.waitForFunction(() => window.__stampede_state().selection?.kind === 'edge')
})

test.describe('narrow viewport', () => {
  test.use({ viewport: { width: 900, height: 800 } })
  test('the single Play action and the brand stay visible; the table drops columns instead of clipping', async ({ page }) => {
    await page.goto('/?view=radar')
    await page.waitForSelector('.radar-row', { timeout: 20000 })
    const play = page.getByTestId('play')
    await expect(play).toBeVisible()
    const pb = await play.boundingBox()
    expect(pb!.x + pb!.width).toBeLessThanOrEqual(900)
    await expect(page.locator('.brand img.mark')).toBeVisible()
    const list = await page.locator('.radar-list').evaluate((el) => ({ scroll: el.scrollWidth, client: el.clientWidth }))
    expect(list.scroll).toBeLessThanOrEqual(list.client)
    await expect(page.locator('.radar-row').nth(0).locator('.c-score')).toBeVisible()
  })
})

test.describe('reduced motion', () => {
  test.use({ reducedMotion: 'reduce' })
  test('flights are instant, nothing pulses or transitions', async ({ page }) => {
    await page.goto('/?layout=presentation&view=map')
    await page.waitForFunction(() => window.__stampede_state && (window.__stampede_state().edges ?? 0) > 0)
    expect((await page.evaluate(() => window.__stampede_state())).reduced).toBe(true)
    await expect(page.locator('.app')).toHaveClass(/reduced/)
    const { edge } = await topEdge(page)
    const t0 = Date.now()
    await page.evaluate((e) => window.__stampede_select({ kind: 'edge', from: e.from, to: e.to }), edge)
    await page.waitForFunction(() => window.__stampede_state().viewState === 'evidence', null, { timeout: 2000 })
    expect(Date.now() - t0).toBeLessThan(2000)
    expect(await page.locator('.views button').first().evaluate((el) => getComputedStyle(el).transitionDuration)).toBe('0s')
    await page.keyboard.press('1')
    await page.waitForSelector('.radar-row', { timeout: 20000 })
    expect(await page.locator('.radar-row').first().evaluate((el) => getComputedStyle(el).animationName)).toBe('none')
  })
})
