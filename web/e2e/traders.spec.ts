import { expect, test } from '@playwright/test'

// TRADERS view (stage 2). Runs against whatever store the server serves: with `stampede traders` stats the leaderboard
// and the wallet card are checked in full; without them the view must say so (empty state), never show zeros.

const post = (page: import('@playwright/test').Page, body: Record<string, unknown>) => page.request.post('/api/session', { data: body })

test.beforeEach(async ({ page }) => {
  const s = await (await page.request.get('/api/session')).json()
  test.skip(s.mode !== 'replay', 'these tests need the server in replay mode')
  // a server started before stage 2 answers /api/traders with the SPA shell: nothing to test until it is restarted
  const probe = await page.request.get('/api/traders?limit=1')
  test.skip(!(probe.headers()['content-type'] ?? '').includes('json'), 'the running server predates /api/traders (restart it to test the TRADERS view)')
  await post(page, { action: 'pause' })
})

test('key 4 opens TRADERS; the leaderboard is a dense right-aligned table with n/a for unknowns, or an honest empty state', async ({ page }) => {
  await page.goto('/?view=radar')
  await page.keyboard.press('4')
  await expect(page.getByTestId('view-traders')).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByTestId('traders-summary')).toContainText('wallets')
  await expect(page.getByTestId('smart-strip')).toContainText('SMART MONEY NOW BUYING')
  const api = await (await page.request.get('/api/traders?preset=top&min_trades=3&limit=100')).json()
  if (!api.run) {
    await expect(page.getByTestId('traders-empty')).toContainText('No wallet stats in this store')
    await expect(page.getByTestId('traders-empty')).toContainText('stampede traders')
    return
  }
  await page.waitForSelector('.trader-row', { timeout: 20000 })
  const rows = page.locator('.trader-row')
  expect(await rows.count()).toBeGreaterThan(0)
  expect(await rows.count()).toBe(Math.min(100, api.total))
  // the default sort is PnL ETH descending; numbers are right-aligned tabular figures
  if ((await rows.count()) > 1) {
    const p0 = Number((await rows.nth(0).locator('.t-pnl b').textContent())!.replace('+', ''))
    const p1 = Number((await rows.nth(1).locator('.t-pnl b').textContent())!.replace('+', ''))
    expect(p0).toBeGreaterThanOrEqual(p1)
  }
  expect(await rows.nth(0).locator('.t-pnl').evaluate((el) => getComputedStyle(el).textAlign)).toBe('right')
  expect(await page.locator('.traders-table').evaluate((el) => getComputedStyle(el).fontVariantNumeric)).toContain('tabular-nums')
  // every row: a short address, a quality number, and unknown metrics as n/a (never 0)
  await expect(rows.nth(0).locator('.c-coin b')).toContainText('…')
  await expect(rows.nth(0).locator('.t-quality')).not.toHaveText('')
  const r0 = api.rows[0]
  if (r0.win_rate === null) await expect(rows.nth(0).locator('.t-win')).toHaveText('n/a')
  if (r0.exit_quality === null) await expect(rows.nth(0).locator('.t-exit')).toHaveText('n/a')
  // presets change the request and the meta line; smart hides bots and deployer-linked wallets
  await page.getByTestId('tpreset-smart').click()
  await expect(page.locator('.traders .presets button.on')).toHaveText(/smart/)
  await page.waitForTimeout(1200)
  const smart = await (await page.request.get('/api/traders?preset=smart&min_trades=5&limit=100')).json()
  if (smart.rows.length) {
    await expect(page.locator('.trader-row').first()).toBeVisible()
    expect(await page.locator('.trader-row .tag.bot').count()).toBe(0)
  } else {
    await expect(page.getByTestId('traders-empty')).toContainText('No wallets match')
  }
  await page.getByTestId('tpreset-top').click()
  await page.waitForSelector('.trader-row', { timeout: 20000 })
  // the filters row opens with the documented fields
  await page.getByTestId('tfilters-toggle').click()
  await expect(page.locator('#traders-filters')).toContainText('min trades')
  await expect(page.locator('#traders-filters')).toContainText('active within')
})

test('wallet card opens beside the table on click, follows the arrows, Esc closes; the order is held while hovering', async ({ page }) => {
  await page.goto('/?view=traders')
  const api = await (await page.request.get('/api/traders?preset=top&min_trades=3&limit=100')).json()
  test.skip(!api.run || api.rows.length < 2, 'needs wallet_stats with at least two wallets (run `stampede traders` on the served store)')
  await page.waitForSelector('.trader-row', { timeout: 20000 })
  const rows = page.locator('.trader-row')
  const w0 = await rows.nth(0).getAttribute('data-wallet')
  const w1 = await rows.nth(1).getAttribute('data-wallet')
  await rows.nth(0).click()
  await expect(page.locator('.trader-row.sel')).toHaveAttribute('data-wallet', w0!)
  const card = page.getByTestId('wallet-card')
  await expect(card.locator('h1')).not.toHaveText('Loading…', { timeout: 15000 })
  await expect(card).toContainText('PnL · FIFO after fees')
  await expect(card).toContainText('Positions')
  await expect(card).toContainText('Recent trades')
  await expect(card.locator('.sub a[href*="address"]')).toHaveAttribute('href', new RegExp(w0!))
  const detail = await (await page.request.get(`/api/wallet/${w0}`)).json()
  if (detail.trades.length) await expect(card.locator('[data-testid=wallet-trades] a[href*="tx"]').first()).toBeVisible()
  if (detail.positions.length) expect(await card.locator('[data-testid=positions] li').count()).toBe(Math.min(40, detail.positions.length))
  if (detail.stats?.pnl_usd === null) await expect(card).toContainText('n/a') // no fx rate: unknown, never $0
  // the card sits beside the table, not over it
  const tb = await page.locator('.traders-table').boundingBox()
  const cb = await card.boundingBox()
  expect(tb!.x + tb!.width).toBeLessThanOrEqual(cb!.x + 1)
  // the pointer is over the table: the order is held and the note says so
  await rows.nth(1).hover()
  await expect(page.getByTestId('torder-note')).toContainText('order held')
  await page.mouse.move(700, 20)
  await expect(page.getByTestId('torder-note')).toContainText('ranked by')
  // arrows move the selection, the card follows; Esc closes the card, a second Esc clears the selection
  await page.keyboard.press('ArrowDown')
  await expect(page.locator('.trader-row.sel')).toHaveAttribute('data-wallet', w1!)
  await expect(card.locator('.sub a[href*="address"]')).toHaveAttribute('href', new RegExp(w1!), { timeout: 15000 })
  await page.keyboard.press('Escape')
  await expect(page.getByTestId('wallet-card')).toHaveCount(0)
  await expect(page.locator('.trader-row.sel')).toHaveAttribute('data-wallet', w1!)
  await page.keyboard.press('Escape')
  await expect(page.locator('.trader-row.sel')).toHaveCount(0)
  // keys switch views both ways
  await page.keyboard.press('1')
  await expect(page.getByTestId('view-radar')).toHaveAttribute('aria-selected', 'true')
  await page.keyboard.press('4')
  await expect(page.getByTestId('view-traders')).toHaveAttribute('aria-selected', 'true')
})
