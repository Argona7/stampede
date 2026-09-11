// RADAR: coins ranked by rotation inflow. A dense table: one row per coin, numbers right-aligned in
// tabular figures, units in the headers, details in the drawer (click / D), FLOW on Enter.
// Ordering rule: rows re-rank with every update *except* while the pointer or the keyboard focus is inside
// the table; then the order is held, the numbers update in place and the meta line says how many rows
// would move. ▲n / ▼n mark the movement since the last re-rank; "new" marks a coin that was not listed.
import { useEffect, useMemo, useRef, useState } from 'react'
import { utc, windowName } from '../format'
import type { AlertsResponse, RadarResponse, RadarRow, SessionState } from '../types'
import { presetToFilters, type RadarFilters } from '../radarFilters'
import Search from './Search'

interface Props {
  data: RadarResponse | null
  alerts: AlertsResponse | null
  session: SessionState | null
  error: string | null
  filters: RadarFilters
  setFilters: (f: RadarFilters) => void
  selected: string | null
  active: boolean // the RADAR view is on screen: table keys are live
  onSelect: (address: string) => void // click: select and open the drawer
  onMove: (address: string) => void // keyboard: move the selection, drawer follows if open
  onFlow: (address: string) => void
  onPick: (address: string) => void // search result
  freshTokens: Map<string, number> // token -> performance.now() of the last inflow bump
}

const SORTS: [string, string, string][] = [
  ['score', 'score', 'c-score'],
  ['inflow', 'inflow 10 min', 'c-in'],
  ['accel', 'acceleration', 'c-acc'],
  ['age', 'youngest', 'c-age'],
  ['mentions', 'fewest X mentions', 'c-x'],
  ['progress', 'graduation', 'c-stage'],
  ['quality', 'wallet quality', 'c-score'],
]
const PRESET_ORDER = ['under_radar', 'graduating', 'smart_rotators', 'all']

function Spark({ v }: { v: number[] }) {
  const w = 84
  const h = 14
  const max = Math.max(1, ...v)
  const n = v.length || 1
  const bw = w / n
  return (
    <svg className="spark" width={w} height={h} aria-hidden="true">
      {v.map((x, i) => {
        const bh = Math.max(x > 0 ? 1 : 0, (x / max) * (h - 1))
        const last = i >= n - 10
        return <rect key={i} x={i * bw} y={h - bh} width={Math.max(1, bw - 1)} height={bh} fill={last ? '#FF3344' : '#4a4a4a'} />
      })}
    </svg>
  )
}

const UNK = <span className="unk">—</span>
const fmtPct = (v: number | null | undefined) => (v === null || v === undefined ? null : `${v > 0 ? '+' : ''}${v >= 1000 ? `${Math.round(v / 100) / 10}k` : v.toFixed(Math.abs(v) > 100 ? 0 : 1)}%`)
const ageText = (s: number | null) => {
  if (s === null) return null
  if (s < 60) return `${Math.round(s)} s`
  if (s < 3600) return `${Math.floor(s / 60)} min`
  return `${Math.floor(s / 3600)} h ${String(Math.floor((s % 3600) / 60)).padStart(2, '0')}`
}
const stageText = (r: RadarRow) => (r.stage === 'curve' ? (r.progress !== null ? `curve ${Math.round(r.progress * 100)}%` : 'curve') : r.stage === 'graduated' ? 'grad.' : null)

function scoreHint(r: RadarRow): string {
  const p = r.parts as Record<string, number | boolean>
  const bits = [`inflow ${p.inflow}`, `acceleration ${p.acceleration}`, `breadth ${p.breadth}`]
  if (p.quality_known) bits.push(`wallet quality ${p.wallet_quality}`)
  if ((p.crowding as number) < 0) bits.push(`crowded ${p.crowding}`)
  if ((p.attention_penalty as number) < 0) bits.push(`attention ${p.attention_penalty}`)
  return `score parts: ${bits.join(' · ')}`
}

export default function Radar({ data, alerts, session, error, filters, setFilters, selected, active, onSelect, onMove, onFlow, onPick, freshTokens }: Props) {
  const presets = data?.presets ?? {}
  const presetKeys = Object.keys(presets).length ? Object.keys(presets) : PRESET_ORDER
  const [now, setNow] = useState(0)
  useEffect(() => {
    const id = window.setInterval(() => setNow(performance.now()), 500)
    return () => window.clearInterval(id)
  }, [])
  const listRef = useRef<HTMLDivElement>(null)
  const ctx = data?.context
  const clock = data?.clock ?? session?.clock_ts ?? null
  const mode = session?.mode ?? 'fixture'
  const paused = mode === 'replay' && !!session && !session.playing
  const trackRecord = alerts?.track_record
  const alertRows = useMemo(() => (alerts?.alerts ?? []).slice(0, 12), [alerts])
  const [filtersOpen, setFiltersOpen] = useState(false)
  const [alertsOpen, setAlertsOpen] = useState(false)

  // ---- displayed order: held while the pointer / focus is inside the table ----
  const [pointerIn, setPointerIn] = useState(false)
  const [focusIn, setFocusIn] = useState(false)
  const hold = pointerIn || focusIn
  const [order, setOrder] = useState<string[]>([])
  const [deltas, setDeltas] = useState<Map<string, number | 'new'>>(new Map())
  const [pending, setPending] = useState(0)
  const orderRef = useRef<string[]>([])
  const prevRank = useRef<Map<string, number>>(new Map()) // ranks at the last re-rank
  const filtersRef = useRef(filters)
  const staleData = useRef<RadarResponse | null>(null) // data fetched before the current filters
  useEffect(() => {
    const incoming = (data?.rows ?? []).map((r) => r.address)
    if (filtersRef.current !== filters) {
      // a new preset / filter set is a new ranking: no deltas against the old one, no hold-over
      filtersRef.current = filters
      prevRank.current = new Map()
      staleData.current = data
      orderRef.current = []
    }
    if (hold && orderRef.current.length) {
      // held: keep the displayed order, append coins that are new to the list, count what would move
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
    const base = prevRank.current
    const d = new Map<string, number | 'new'>()
    incoming.forEach((a, i) => {
      const p = base.get(a)
      if (p === undefined) {
        if (base.size) d.set(a, 'new')
      } else if (p !== i) d.set(a, p - i)
    })
    if (data !== staleData.current) prevRank.current = new Map(incoming.map((a, i) => [a, i]))
    orderRef.current = incoming
    setOrder(incoming)
    setDeltas(d)
    setPending(0)
  }, [data, hold, filters])
  const byAddr = useMemo(() => new Map((data?.rows ?? []).map((r) => [r.address, r])), [data])
  const shown = useMemo(() => order.map((a) => byAddr.get(a)).filter((r): r is RadarRow => !!r), [order, byAddr])

  // ---- keyboard: arrows move the selection, Enter opens FLOW ----
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
          onFlow(selected)
        }
        return
      }
      if (e.key === 'Escape') {
        if (t && t.classList.contains('radar-row')) t.blur()
        return
      }
      if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp' && e.key !== 'Home' && e.key !== 'End') return
      if (!list.length) return
      e.preventDefault()
      const i = selected ? list.indexOf(selected) : -1
      const j = e.key === 'ArrowDown' ? Math.min(list.length - 1, i + 1) : e.key === 'ArrowUp' ? (i <= 0 ? 0 : i - 1) : e.key === 'Home' ? 0 : list.length - 1
      onMove(list[j])
      const el = listRef.current?.querySelector<HTMLElement>(`tr[data-address="${list[j]}"]`)
      el?.focus({ preventScroll: true })
      el?.scrollIntoView({ block: 'nearest' })
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [active, selected, onMove, onFlow])

  const sort = SORTS.find((s) => s[0] === filters.sort) ?? SORTS[0]
  const emptyCause = () => {
    const bits: string[] = []
    if (filters.stage) bits.push(`stage ${filters.stage}`)
    if (filters.ageMax !== null) bits.push(`age < ${windowName(filters.ageMax)}`)
    bits.push(`≥ ${filters.minWallets} rotating wallets`)
    if (filters.mentionsMax !== null) bits.push(`≤ ${filters.mentionsMax} X mentions`)
    if (filters.excludeBots) bits.push('bots hidden')
    return bits.join(', ')
  }
  const contextNote = ctx?.enabled ? 'X / market / holders context on' : mode === 'live' ? 'context warming up: X, holders and market fill in later' : 'replay · on-chain as-of numbers · X mentions and holders not fetched: n/a = unknown, not 0'

  return (
    <div className={`radar ${alertsOpen ? 'alerts-open' : ''}`}>
      <div className="workbar">
        <div className="seg presets" role="group" aria-label="Radar presets">
          {presetKeys.map((k) => (
            <button key={k} className={filters.preset === k ? 'on' : ''} title={presets[k]?.label} aria-pressed={filters.preset === k} onClick={() => setFilters({ ...filters, preset: k, ...presetToFilters(k) })}>
              {k.replace('_', ' ')}
            </button>
          ))}
        </div>
        <button className="disclosure" aria-expanded={filtersOpen} aria-controls="radar-filters" onClick={() => setFiltersOpen((v) => !v)} data-testid="filters-toggle">
          Filters{filters.preset === 'custom' ? ' · custom' : ''}
        </button>
        <label>
          sort
          <select value={filters.sort} aria-label="Sort" onChange={(e) => setFilters({ ...filters, sort: e.target.value })}>
            {SORTS.map(([k, l]) => (
              <option key={k} value={k}>
                {l}
              </option>
            ))}
          </select>
        </label>
        <span className="grow" />
        <Search onPick={onPick} placeholder="find a coin" />
        <span className="stat summary" data-testid="radar-summary">
          <b>{data ? data.total.toLocaleString('en-US') : '…'}</b> coins with rotation inflow · last {windowName(data?.span_s ?? session?.span_s ?? 1800)} · as of <b>{utc(clock)}</b> UTC{paused ? ' · paused' : ''}
        </span>
        <button className="alerts-toggle" aria-pressed={alertsOpen} onClick={() => setAlertsOpen((v) => !v)}>
          Alerts {alerts?.alerts.length ?? ''}
        </button>
      </div>
      {filtersOpen && (
        <div className="filters" id="radar-filters">
          <label>
            min wallets
            <input type="number" min={1} max={50} value={filters.minWallets} onChange={(e) => setFilters({ ...filters, preset: 'custom', minWallets: Number(e.target.value) })} />
          </label>
          <label>
            age
            <select value={filters.ageMax ?? ''} onChange={(e) => setFilters({ ...filters, preset: 'custom', ageMax: e.target.value ? Number(e.target.value) : null })}>
              <option value="">any</option>
              <option value={1800}>&lt; 30 min</option>
              <option value={7200}>&lt; 2 h</option>
              <option value={14400}>&lt; 4 h</option>
              <option value={86400}>&lt; 24 h</option>
            </select>
          </label>
          <label>
            stage
            <select value={filters.stage ?? ''} onChange={(e) => setFilters({ ...filters, preset: 'custom', stage: (e.target.value || null) as RadarFilters['stage'] })}>
              <option value="">any</option>
              <option value="curve">on the curve</option>
              <option value="graduated">graduated</option>
            </select>
          </label>
          <label>
            X mentions 1 h ≤
            <input type="number" min={0} placeholder="any" value={filters.mentionsMax ?? ''} onChange={(e) => setFilters({ ...filters, preset: 'custom', mentionsMax: e.target.value === '' ? null : Number(e.target.value) })} />
          </label>
          <label>
            <input type="checkbox" checked={filters.excludeBots} onChange={(e) => setFilters({ ...filters, preset: 'custom', excludeBots: e.target.checked })} /> hide bots
          </label>
          <span className="faint">{data?.bots_excluded ? `${data.bots_excluded} bot wallets excluded · ` : ''}{data?.wallet_scores_known ? `wallet scores for ${data.wallet_scores_known.toLocaleString('en-US')} addresses` : 'wallet scores not loaded'}</span>
        </div>
      )}

      <div className="radar-body">
        <div
          className="radar-list"
          ref={listRef}
          onPointerEnter={() => setPointerIn(true)}
          onPointerLeave={() => setPointerIn(false)}
          onFocus={(e) => {
            // keyboard focus holds the order; a mouse click that happens to focus a row does not
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
                <b>RADAR not loaded</b>
                {error} · this page asks its own origin for /api/radar; if you started the server on another port, open that address.
              </div>
            </div>
          ) : !data ? (
            <div className="state-block" data-testid="radar-loading">
              <img className="mark" src="/brand-bison.png" height={46} alt="" />
              <div>
                <b>Loading RADAR</b>
                asking /api/radar for coins with rotation inflow in the last {windowName(session?.span_s ?? 1800)}…
              </div>
            </div>
          ) : (
            <>
              <div className="radar-meta">
                <span>{contextNote}</span>
                <span className={pending ? 'hold' : ''} data-testid="order-note">
                  {hold ? (pending ? `order held · ${pending} row${pending === 1 ? '' : 's'} would move · leave the table to re-rank` : 'order held while the pointer or keyboard focus is here') : `ranked by ${sort[1]} · ▲▼ = moved since the last re-rank · ↑↓ select · Enter → FLOW`}
                </span>
              </div>
              {shown.length === 0 ? (
                <div className="state-block" data-testid="radar-empty">
                  <img className="mark" src="/brand-bison.png" height={46} alt="" />
                  <div>
                    <b>No coins match at {utc(clock)} UTC</b>
                    0 coins pass {filters.preset === 'custom' ? 'the custom filters' : `preset ${filters.preset.replace('_', ' ')}`} ({emptyCause()}) in the last {windowName(data.span_s)}. Next: switch the preset to ALL, or open Filters and set age to any.
                  </div>
                </div>
              ) : (
                <table className="radar-table" aria-label="Coins ranked by rotation inflow">
                  <thead>
                    <tr>
                      <th className="num c-rank">#</th>
                      <th className="c-coin">coin · address</th>
                      <th className={`num c-age ${sort[2] === 'c-age' ? 'sorted' : ''}`}>age</th>
                      <th className={`c-stage ${sort[2] === 'c-stage' ? 'sorted' : ''}`}>stage</th>
                      <th className={`num c-in ${sort[2] === 'c-in' ? 'sorted' : ''}`} title="distinct wallets that sold another coin and bought this one in the last 10 min">
                        in · 10 min
                      </th>
                      <th className={`num c-acc ${sort[2] === 'c-acc' ? 'sorted' : ''}`} title="inflow now versus the previous 10 min">
                        × prev
                      </th>
                      <th className="c-from" title="coins the wallets sold before buying this one, with distinct-wallet counts">
                        from · wallets
                      </th>
                      <th className="c-spark" title="wallets rotating in per minute; red = last 10 min">
                        last 30 min
                      </th>
                      <th className="num c-p5" title="price change over the last 5 min at the clock, from indexed trades">
                        Δ 5 min
                      </th>
                      <th className={`num c-x ${sort[2] === 'c-x' ? 'sorted' : ''}`} title="X mentions in the last hour; n/a = not fetched">
                        X · 1 h
                      </th>
                      <th className="num c-hold" title="holders; — = not fetched">
                        holders
                      </th>
                      <th className={`num c-score ${sort[2] === 'c-score' ? 'sorted' : ''}`}>score</th>
                    </tr>
                  </thead>
                  <tbody>
                    {shown.map((r, i) => {
                      const fresh = freshTokens.get(r.address)
                      const isFresh = fresh !== undefined && now - fresh < 2500
                      const isSel = selected === r.address
                      const d = deltas.get(r.address)
                      const age = ageText(r.age_s)
                      const stage = stageText(r)
                      const p5 = fmtPct(r.chg_5m)
                      const top = r.sources.slice(0, 3)
                      return (
                        <tr
                          key={r.address}
                          data-address={r.address}
                          className={`radar-row ${isSel ? 'sel' : ''} ${isFresh ? 'fresh' : ''}`}
                          aria-selected={isSel}
                          tabIndex={isSel || (!selected && i === 0) ? 0 : -1}
                          onClick={() => onSelect(r.address)}
                        >
                          <td className="num c-rank">
                            {i + 1}
                            <span className={`d ${d === 'new' ? 'new' : ''}`}>{d === 'new' ? 'new' : d === undefined ? '' : d > 0 ? `▲${d}` : `▼${-d}`}</span>
                          </td>
                          <td className="c-coin">
                            <b>{r.symbol}</b>
                            <span className="addr">{r.short}</span>
                          </td>
                          <td className="num c-age">{age ?? UNK}</td>
                          <td className="c-stage">{stage ?? UNK}</td>
                          <td className="num c-in">{r.inflow_10m}</td>
                          <td className="num c-acc">{r.inflow_prev_per_10m > 0 ? `×${r.accel.toFixed(1)}` : <span className="unk">new</span>}</td>
                          <td className="c-from" title={r.sources.map((s) => `${s.wallets} wallets sold ${s.symbol} then bought ${r.symbol}`).join('\n')}>
                            {r.breadth} src
                            {top.map((s) => (
                              <span key={s.address}>
                                {' · '}
                                {s.symbol} <b>{s.wallets}</b>
                              </span>
                            ))}
                            {r.sources.length > 3 ? ` · +${r.sources.length - 3}` : ''}
                          </td>
                          <td className="c-spark">
                            <Spark v={r.spark} />
                          </td>
                          <td className={`num c-p5 ${(r.chg_5m ?? 0) < 0 ? 'neg' : ''}`}>{p5 ?? UNK}</td>
                          <td className="num c-x" title={r.mentions_1h === null ? 'X mentions not fetched: external context is off in this mode' : `${r.mentions_24h} in 24 h`}>
                            {r.mentions_1h === null ? <span className="unk">n/a</span> : r.mentions_1h}
                          </td>
                          <td className="num c-hold" title={r.holders ? `top-10 hold ${Math.round(r.holders.top10_share * 100)}%` : 'holders not fetched'}>
                            {r.holders ? r.holders.holders.toLocaleString('en-US') : UNK}
                          </td>
                          <td className="num c-score" title={scoreHint(r)}>
                            {Math.round(r.score)}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              )}
            </>
          )}
        </div>
        <aside className="radar-alerts" aria-label="Alerts">
          <h2>Alerts · under-radar top-5</h2>
          <details>
            <summary>rule and outcome</summary>
            Fires when a coin enters the top 5 with score ≥ {alerts?.rules?.under_radar_top5?.score_min ?? 60}, inflow {alerts?.rules?.under_radar_top5?.inflow_min ?? 8}–{alerts?.rules?.under_radar_top5?.inflow_max ?? 40} wallets in 10 min, age &lt; {Math.round((alerts?.rules?.under_radar_top5?.age_max_s ?? 3600) / 60)} min, not already +{alerts?.rules?.under_radar_top5?.chg_10m_max ?? 100}% in 10 min, X mentions ≤ {alerts?.rules?.under_radar_top5?.mentions_max ?? 3} or unknown. Outcome = price 30/60 min later on indexed trades; not a return anyone earned.
          </details>
          {trackRecord && (
            <div className="kv">
              <span>fired</span>
              <span>{trackRecord.fired}</span>
              <span>with 30-min outcome</span>
              <span>{trackRecord.with_outcome_30m}</span>
              <span>up after 30 min</span>
              <span>{trackRecord.with_outcome_30m ? `${trackRecord.up_30m} / ${trackRecord.with_outcome_30m}` : '—'}</span>
              <span>median 30-min move</span>
              <span>{trackRecord.median_30m !== null && trackRecord.median_30m !== undefined ? fmtPct(trackRecord.median_30m) : '—'}</span>
            </div>
          )}
          <ul className="alert-list">
            {alertRows.map((a) => (
              <li key={a.id} className={selected === a.token ? 'sel' : ''} onClick={() => onSelect(a.token)}>
                <span className="t">{utc(a.clock_ts)}</span>
                <b>{a.symbol}</b>
                <span className={`out ${a.outcome_30m === null ? 'faint' : (a.outcome_30m ?? 0) > 0 ? 'up' : 'neg'}`}>{a.outcome_30m === null ? 'pending' : `${fmtPct(a.outcome_30m)} @30m`}</span>
                <span className="what">
                  {a.inflow} in · score {Math.round(a.score)}
                </span>
              </li>
            ))}
            {alertRows.length === 0 && <li className="faint">no alerts yet in this mode: none of the ranked coins met the rule so far</li>}
          </ul>
        </aside>
      </div>
    </div>
  )
}
