// TRADERS: wallets ranked by what they actually earned after fees (FIFO ledgers from `stampede traders`), with the
// same table discipline as RADAR: one row per wallet, tabular figures right-aligned, units in the headers, n/a for
// unknown (never 0), the order held while the pointer or keyboard focus is inside the table. A strip above the table
// lists top-decile wallets that just rotated into a coin (live feed + wallet_stats lookup). The card opens on click or
// Enter; it sits beside the table, never over it.
import { useEffect, useMemo, useRef, useState } from 'react'
import { utc, windowName } from '../format'
import { eth, hold, pct, signedPct, usd } from '../tradersFormat'
import { PRESET_ORDER, TRADER_SORTS, presetDefaults, type LookupEntry, type TraderRow, type TradersFilters, type TradersResponse } from '../tradersTypes'
import type { SeqEvent, SessionState } from '../types'

interface Props {
  data: TradersResponse | null
  error: string | null
  session: SessionState | null
  filters: TradersFilters
  setFilters: (f: TradersFilters) => void
  selected: string | null
  active: boolean
  onSelect: (wallet: string) => void // click / Enter: select and open the card
  onMove: (wallet: string) => void // arrows: move the selection, the card follows if open
  feed: SeqEvent[]
  smart: Map<string, LookupEntry>
  smartRun: { from_ts: number; to_ts: number } | null
}

const UNK = <span className="unk">n/a</span>
const TAG_TITLES: Record<string, string> = {
  bot: '> 60 trades/h over its active span, > 500 trades, or buys of several coins in the same block',
  deployer: 'deployer of a coin it traded (launch event)',
  launcher: 'deployed at least one coin in the range',
  snipe_exempt: 'bought inside the 3-s snipe window without paying snipe tax while later buyers still paid it',
  sniper: '≥ 30% of its entries under 3 s after launch or with snipe tax paid',
  contract: 'the address is a contract',
  fees_unknown: 'curve rows without fee data were skipped',
  fees_partly_unknown: 'some curve rows without fee data were skipped',
  fees_estimated: 'fees estimated at the 1% base fee, creator tax unknown',
}

export default function Traders({ data, error, session, filters, setFilters, selected, active, onSelect, onMove, feed, smart, smartRun }: Props) {
  const presets = data?.presets ?? {}
  const presetKeys = Object.keys(presets).length ? PRESET_ORDER.filter((k) => k in presets) : PRESET_ORDER
  const listRef = useRef<HTMLDivElement>(null)
  const [filtersOpen, setFiltersOpen] = useState(false)
  const range = data?.range ?? null

  // ---- displayed order: held while the pointer / focus is inside the table ----
  const [pointerIn, setPointerIn] = useState(false)
  const [focusIn, setFocusIn] = useState(false)
  const holdOrder = pointerIn || focusIn
  const [order, setOrder] = useState<string[]>([])
  const [pending, setPending] = useState(0)
  const orderRef = useRef<string[]>([])
  const filtersRef = useRef(filters)
  useEffect(() => {
    const incoming = (data?.rows ?? []).map((r) => r.wallet)
    if (filtersRef.current !== filters) {
      filtersRef.current = filters
      orderRef.current = []
    }
    if (holdOrder && orderRef.current.length) {
      const present = new Set(incoming)
      const kept = orderRef.current.filter((a) => present.has(a))
      const keptSet = new Set(kept)
      const next = [...kept, ...incoming.filter((a) => !keptSet.has(a))]
      let moved = 0
      next.forEach((a, i) => {
        if (incoming.indexOf(a) !== i) moved++
      })
      orderRef.current = next
      setOrder(next)
      setPending(moved)
      return
    }
    orderRef.current = incoming
    setOrder(incoming)
    setPending(0)
  }, [data, holdOrder, filters])
  const byAddr = useMemo(() => new Map((data?.rows ?? []).map((r) => [r.wallet, r])), [data])
  const shown = useMemo(() => order.map((a) => byAddr.get(a)).filter((r): r is TraderRow => !!r), [order, byAddr])

  // ---- keyboard: arrows move the selection, Enter opens the card ----
  useEffect(() => {
    if (!active) return
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null
      const tag = t?.tagName
      if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA' || tag === 'BUTTON' || tag === 'A') return
      const list = orderRef.current
      if (e.key === 'Enter') {
        if (selected && list.includes(selected)) {
          e.preventDefault()
          onSelect(selected)
        }
        return
      }
      if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp' && e.key !== 'Home' && e.key !== 'End') return
      if (!list.length) return
      e.preventDefault()
      const i = selected ? list.indexOf(selected) : -1
      const j = e.key === 'ArrowDown' ? Math.min(list.length - 1, i + 1) : e.key === 'ArrowUp' ? (i <= 0 ? 0 : i - 1) : e.key === 'Home' ? 0 : list.length - 1
      onMove(list[j])
      const el = listRef.current?.querySelector<HTMLElement>(`tr[data-wallet="${list[j]}"]`)
      el?.focus({ preventScroll: true })
      el?.scrollIntoView({ block: 'nearest' })
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [active, selected, onMove, onSelect])

  // ---- smart money now buying: feed rows whose wallet is in the top decile, newest first ----
  const smartRows = useMemo(() => {
    const out: (SeqEvent & { info: LookupEntry })[] = []
    for (let i = feed.length - 1; i >= 0 && out.length < 8; i--) {
      const info = smart.get(feed[i].wallet)
      if (info?.smart) out.push({ ...feed[i], info })
    }
    return out
  }, [feed, smart])
  const looked = feed.length ? [...new Set(feed.map((e) => e.wallet))].filter((w) => smart.has(w)).length : 0

  const sort = TRADER_SORTS.find((s) => s[0] === filters.sort) ?? TRADER_SORTS[0]
  const emptyCause = () => {
    const bits = [`≥ ${filters.minTrades} trades`]
    if (filters.minRoi !== null) bits.push(`ROI ≥ ${Math.round(filters.minRoi * 100)}%`)
    if (filters.minWinRate !== null) bits.push(`win rate ≥ ${Math.round(filters.minWinRate * 100)}%`)
    if (filters.activeWithinS !== null) bits.push(`active in the last ${windowName(filters.activeWithinS)}`)
    return bits.join(', ')
  }

  return (
    <div className="traders">
      <div className="workbar">
        <div className="seg presets" role="group" aria-label="Trader presets">
          {presetKeys.map((k) => (
            <button key={k} className={filters.preset === k ? 'on' : ''} title={presets[k]?.label} aria-pressed={filters.preset === k} onClick={() => setFilters({ ...filters, preset: k, ...presetDefaults(k) })} data-testid={`tpreset-${k}`}>
              {k}
            </button>
          ))}
        </div>
        <button className="disclosure" aria-expanded={filtersOpen} aria-controls="traders-filters" onClick={() => setFiltersOpen((v) => !v)} data-testid="tfilters-toggle">
          Filters
        </button>
        <label>
          sort
          <select value={filters.sort} aria-label="Sort traders" onChange={(e) => setFilters({ ...filters, sort: e.target.value })}>
            {TRADER_SORTS.map(([k, l]) => (
              <option key={k} value={k}>
                {l}
              </option>
            ))}
          </select>
        </label>
        <span className="grow" />
        <span className="stat summary" data-testid="traders-summary">
          <b>{data ? data.total.toLocaleString('en-US') : '…'}</b> wallets · stats {range ? `${utc(range.from_ts)}–${utc(range.to_ts)} UTC` : 'n/a'} · FIFO after fees
        </span>
      </div>
      {filtersOpen && (
        <div className="filters" id="traders-filters">
          <label>
            min trades
            <input type="number" min={1} max={1000} value={filters.minTrades} onChange={(e) => setFilters({ ...filters, minTrades: Math.max(1, Number(e.target.value) || 1) })} />
          </label>
          <label>
            ROI ≥ %
            <input type="number" placeholder="any" value={filters.minRoi === null ? '' : Math.round(filters.minRoi * 100)} onChange={(e) => setFilters({ ...filters, minRoi: e.target.value === '' ? null : Number(e.target.value) / 100 })} />
          </label>
          <label>
            win rate ≥ %
            <input type="number" min={0} max={100} placeholder="any" value={filters.minWinRate === null ? '' : Math.round(filters.minWinRate * 100)} onChange={(e) => setFilters({ ...filters, minWinRate: e.target.value === '' ? null : Number(e.target.value) / 100 })} />
          </label>
          <label>
            active within
            <select value={filters.activeWithinS ?? ''} onChange={(e) => setFilters({ ...filters, activeWithinS: e.target.value ? Number(e.target.value) : null })}>
              <option value="">any</option>
              <option value={600}>10 min</option>
              <option value={3600}>1 h</option>
              <option value={6 * 3600}>6 h</option>
              <option value={86400}>24 h</option>
            </select>
          </label>
          <span className="faint">recency = the wallet's last trade in the computed range lies within the window before the clock · stats are FIFO with the fees the curve charged · docs/RESEARCH-TRADERS.md</span>
        </div>
      )}
      <div className="smart-strip" data-testid="smart-strip" aria-label="Smart money now buying">
        <span className="h">SMART MONEY NOW BUYING</span>
        {smartRows.length ? (
          smartRows.map((e) => (
            <button key={e.id} className="smart-item" onClick={() => onSelect(e.wallet)} title={`quality ${e.info.quality.toFixed(2)} · ${e.info.trades} trades · PnL ${eth(e.info.pnl_eth)} ETH · sold ${e.from_symbol} then bought ${e.to_symbol}`}>
              <span className="t">{utc(e.buy_ts)}</span>
              <span className="w">{e.wallet.slice(0, 6)}…{e.wallet.slice(-4)}</span>
              <span className="q">q {e.info.quality.toFixed(2)}</span>
              <span className="arrow">→</span>
              <b>{e.to_symbol}</b>
              <span className="from">from {e.from_symbol}</span>
            </button>
          ))
        ) : (
          <span className="faint">{!smartRun && data && !data.run ? 'no wallet stats in this store · run stampede traders' : feed.length ? `none of the ${looked || feed.length} feed wallets in the last ${windowName(session?.span_s ?? 1800)} is a top-decile trader` : 'waiting for the rotation feed'}</span>
        )}
      </div>
      <div className="traders-body">
        <div
          className="traders-list radar-list"
          ref={listRef}
          onPointerEnter={() => setPointerIn(true)}
          onPointerLeave={() => setPointerIn(false)}
          onFocus={(e) => {
            const t = e.target as HTMLElement
            setFocusIn(typeof t.matches === 'function' ? t.matches(':focus-visible') : true)
          }}
          onBlur={(e) => {
            if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setFocusIn(false)
          }}
        >
          {error && !data ? (
            <div className="state-block err" role="alert">
              <div>
                <b>TRADERS not loaded</b>
                {error} · this page asks its own origin for /api/traders.
              </div>
            </div>
          ) : !data ? (
            <div className="state-block" data-testid="traders-loading">
              <img className="mark" src="/brand-bison.png" height={46} alt="" />
              <div>
                <b>Loading TRADERS</b>
                asking /api/traders for the wallet leaderboard…
              </div>
            </div>
          ) : (
            <>
              <div className="radar-meta">
                <span>{presets[filters.preset]?.label ?? 'wallet leaderboard'}</span>
                <span className={pending ? 'hold' : ''} data-testid="torder-note">
                  {holdOrder ? (pending ? `order held · ${pending} row${pending === 1 ? '' : 's'} would move · leave the table to re-rank` : 'order held while the pointer or keyboard focus is here') : `ranked by ${sort[1]} · ↑↓ select · Enter → wallet card`}
                </span>
              </div>
              {shown.length === 0 ? (
                <div className="state-block" data-testid="traders-empty">
                  <img className="mark" src="/brand-bison.png" height={46} alt="" />
                  <div>
                    <b>{data.run ? `No wallets match` : 'No wallet stats in this store'}</b>
                    {data.run ? `0 wallets pass preset ${filters.preset} (${emptyCause()}). Next: lower min trades in Filters, or switch the preset to TOP.` : data.empty_reason ?? 'run `stampede traders --db <store>` and reload.'}
                  </div>
                </div>
              ) : (
                <table className="radar-table traders-table" aria-label="Wallets ranked by PnL after fees">
                  <thead>
                    <tr>
                      <th className="num c-rank">#</th>
                      <th className="c-coin">wallet · tags</th>
                      <th className={`num t-trades ${sort[0] === 'trades' ? 'sorted' : ''}`}>trades</th>
                      <th className="num t-coins">coins</th>
                      <th className={`num t-win ${sort[0] === 'win_rate' ? 'sorted' : ''}`} title="closed positions with PnL > 0 / closed positions">
                        win
                      </th>
                      <th className={`num t-pnl ${sort[0] === 'pnl' || sort[0] === 'realized' ? 'sorted' : ''}`} title="FIFO PnL after the 1% fee, snipe tax and creator tax; realized + unrealized at the last minute-median price">
                        PnL ETH · real + unreal
                      </th>
                      <th className={`num t-roi ${sort[0] === 'roi' ? 'sorted' : ''}`} title="(realized + unrealized) / cost of every buy">
                        ROI
                      </th>
                      <th className="num t-hold" title="median(last sell − first buy) over closed positions">
                        hold
                      </th>
                      <th className="num t-exit" title="exit price / max price in the following 15 min; 1 = sold the top">
                        exit q
                      </th>
                      <th className="num t-rug" title="share of exits after which the coin fell ≥ 80% within 60 min">
                        rug avoid
                      </th>
                      <th className="num t-sniper" title="share of entries under 3 s after launch or with snipe tax paid">
                        sniper
                      </th>
                      <th className={`num t-quality ${sort[0] === 'quality' ? 'sorted' : ''}`} title="0–1, 0.5 = no evidence: win rate, ROI, exit quality, rug avoidance, shrunk by sample size">
                        quality
                      </th>
                      <th className={`num t-last ${sort[0] === 'recent' ? 'sorted' : ''}`}>last UTC</th>
                    </tr>
                  </thead>
                  <tbody>
                    {shown.map((r, i) => {
                      const isSel = selected === r.wallet
                      return (
                        <tr key={r.wallet} data-wallet={r.wallet} className={`radar-row trader-row ${isSel ? 'sel' : ''}`} aria-selected={isSel} tabIndex={isSel || (!selected && i === 0) ? 0 : -1} onClick={() => onSelect(r.wallet)}>
                          <td className="num c-rank">{i + 1}</td>
                          <td className="c-coin">
                            <b>{r.short}</b>
                            {r.tags.map((t) => (
                              <span key={t} className={`tag ${t}`} title={TAG_TITLES[t] ?? t}>
                                {t.replace(/_/g, ' ')}
                              </span>
                            ))}
                          </td>
                          <td className="num t-trades">{r.trades}</td>
                          <td className="num t-coins">{r.coins}</td>
                          <td className="num t-win" title={`${r.wins} of ${r.positions_closed} closed positions`}>
                            {pct(r.win_rate) ?? UNK}
                          </td>
                          <td className={`num t-pnl ${r.pnl_eth < 0 ? 'neg' : ''}`} title={`realized ${eth(r.realized_eth)} · unrealized ${eth(r.unrealized_eth)} · fees ${r.fees_eth.toFixed(4)} ETH · ${usd(r.pnl_usd) ?? 'USD n/a (no hourly rate)'}`}>
                            <b>{eth(r.pnl_eth)}</b>
                            <span className="sub">
                              {eth(r.realized_eth)} + {eth(r.unrealized_eth)}
                            </span>
                          </td>
                          <td className={`num t-roi ${(r.roi ?? 0) < 0 ? 'neg' : ''}`}>{signedPct(r.roi) ?? UNK}</td>
                          <td className="num t-hold">{hold(r.median_hold_s) ?? UNK}</td>
                          <td className="num t-exit">{r.exit_quality === null ? UNK : r.exit_quality.toFixed(2)}</td>
                          <td className="num t-rug">{pct(r.rug_avoid) ?? UNK}</td>
                          <td className="num t-sniper">{pct(r.sniper_share) ?? UNK}</td>
                          <td className="num t-quality">
                            <b>{r.quality.toFixed(2)}</b>
                          </td>
                          <td className="num t-last">{utc(r.last_ts)}</td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}
