import { useEffect, useMemo, useRef, useState } from 'react'
import { duration, utc, windowName } from '../format'
import type { AlertsResponse, RadarResponse, RadarRow, SessionState } from '../types'

import { presetToFilters, type RadarFilters } from '../radarFilters'

interface Props {
  data: RadarResponse | null
  alerts: AlertsResponse | null
  session: SessionState | null
  filters: RadarFilters
  setFilters: (f: RadarFilters) => void
  selected: string | null
  onSelect: (address: string) => void
  onFlow: (address: string) => void
  freshTokens: Map<string, number> // token -> performance.now() of the last inflow bump
}

const SORTS: [string, string][] = [
  ['score', 'score'],
  ['inflow', 'inflow 10m'],
  ['accel', 'acceleration'],
  ['age', 'youngest'],
  ['mentions', 'fewest mentions'],
  ['progress', 'graduation'],
  ['quality', 'wallet quality'],
]

function Spark({ v }: { v: number[] }) {
  const w = 120
  const h = 26
  const max = Math.max(1, ...v)
  const n = v.length || 1
  const bw = w / n
  return (
    <svg className="spark" width={w} height={h} aria-hidden="true">
      {v.map((x, i) => {
        const bh = Math.max(x > 0 ? 2 : 0, (x / max) * (h - 2))
        const last = i >= n - 10
        return <rect key={i} x={i * bw} y={h - bh} width={Math.max(1, bw - 1)} height={bh} fill={last ? '#FF3344' : '#5a5a5a'} />
      })}
    </svg>
  )
}

const fmtPct = (v: number | null | undefined) => (v === null || v === undefined ? '—' : `${v > 0 ? '+' : ''}${v >= 1000 ? `${Math.round(v / 100) / 10}k` : v.toFixed(v > 100 ? 0 : 1)}%`)
const fmtNum = (v: number | null | undefined, d = 0) => (v === null || v === undefined ? '—' : v.toLocaleString('en-US', { maximumFractionDigits: d }))
const fmtUsd = (v: number | null | undefined) => (v === null || v === undefined ? '—' : v >= 1e6 ? `$${(v / 1e6).toFixed(2)}M` : v >= 1e3 ? `$${(v / 1e3).toFixed(1)}k` : `$${v.toFixed(0)}`)

export default function Radar({ data, alerts, session, filters, setFilters, selected, onSelect, onFlow, freshTokens }: Props) {
  const rows = data?.rows ?? []
  const presets = data?.presets ?? {}
  const [now, setNow] = useState(0)
  useEffect(() => {
    const id = window.setInterval(() => setNow(performance.now()), 500)
    return () => window.clearInterval(id)
  }, [])
  const listRef = useRef<HTMLDivElement>(null)
  const ctx = data?.context
  const clock = data?.clock ?? null
  const mode = session?.mode ?? 'fixture'
  const trackRecord = alerts?.track_record
  const alertRows = useMemo(() => (alerts?.alerts ?? []).slice(0, 12), [alerts])

  return (
    <div className="radar">
      <div className="radar-head">
        <div className="presets" role="tablist" aria-label="Radar presets">
          {Object.entries(presets).map(([k, p]) => (
            <button key={k} className={filters.preset === k ? 'on' : ''} title={p.label} onClick={() => setFilters({ ...filters, preset: k, ...presetToFilters(k) })}>
              {k.replace('_', ' ')}
            </button>
          ))}
        </div>
        <div className="radar-filters">
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
            X 1h ≤
            <input type="number" min={0} placeholder="any" value={filters.mentionsMax ?? ''} onChange={(e) => setFilters({ ...filters, preset: 'custom', mentionsMax: e.target.value === '' ? null : Number(e.target.value) })} />
          </label>
          <label>
            sort
            <select value={filters.sort} onChange={(e) => setFilters({ ...filters, sort: e.target.value })}>
              {SORTS.map(([k, l]) => (
                <option key={k} value={k}>
                  {l}
                </option>
              ))}
            </select>
          </label>
          <label className="check">
            <input type="checkbox" checked={filters.excludeBots} onChange={(e) => setFilters({ ...filters, preset: 'custom', excludeBots: e.target.checked })} /> hide bots
          </label>
        </div>
        <div className="radar-meta">
          <span>
            <b>{data?.total ?? 0}</b> coins with rotation inflow · last {windowName(data?.span_s ?? 1800)} · as of <b>{utc(clock)} UTC</b>
          </span>
          <span className="faint">
            {ctx?.enabled ? 'X / market / holders context on' : mode === 'live' ? 'context warming up' : 'replay: on-chain as-of numbers only, external context off'}
            {ctx?.budget?.twitterapi ? ` · X calls today ${ctx.budget.twitterapi.calls_today}` : ''}
            {data?.wallet_scores_known ? ` · wallet scores ${data.wallet_scores_known.toLocaleString('en-US')}` : ' · wallet scores not loaded'}
          </span>
        </div>
      </div>

      <div className="radar-body">
        <div className="radar-list" ref={listRef}>
          <div className="radar-row head">
            <span>#</span>
            <span>score</span>
            <span>coin</span>
            <span>inflow 10m</span>
            <span>last 30 min</span>
            <span>from</span>
            <span>price</span>
            <span>X</span>
            <span>holders</span>
            <span></span>
          </div>
          {rows.length === 0 && <div className="empty">No coins match. Widen the filters or wait for the clock.</div>}
          {rows.map((r, i) => {
            const fresh = freshTokens.get(r.address)
            const isFresh = fresh !== undefined && now - fresh < 2500
            return (
              <div key={r.address} className={`radar-row ${selected === r.address ? 'sel' : ''} ${isFresh ? 'fresh' : ''}`} onClick={() => onSelect(r.address)} role="button" tabIndex={0}>
                <span className="rank">{i + 1}</span>
                <span className="score">
                  <b>{Math.round(r.score)}</b>
                  <small title={JSON.stringify(r.parts)}>{scoreHint(r)}</small>
                </span>
                <span className="coin">
                  <b>{r.symbol}</b>
                  <small>
                    {r.short} · {r.age_s !== null ? `${duration(r.age_s)} old` : 'age ?'} · {r.stage === 'curve' ? (r.progress !== null ? `curve ${Math.round(r.progress * 100)}%` : 'curve') : r.stage}
                  </small>
                  {r.stage === 'curve' && r.progress !== null && (
                    <span className="bar">
                      <span style={{ width: `${Math.round(r.progress * 100)}%` }} />
                    </span>
                  )}
                </span>
                <span className="inflow">
                  <b>{r.inflow_10m}</b>
                  <small>
                    {r.accel >= 1.5 ? '↑' : r.accel <= 0.7 ? '↓' : '→'} ×{r.accel.toFixed(1)} vs prev · {r.breadth} src
                  </small>
                </span>
                <span>
                  <Spark v={r.spark} />
                </span>
                <span className="sources">
                  {r.sources.map((s) => (
                    <em key={s.address} title={`${s.wallets} wallets sold ${s.symbol} then bought ${r.symbol}`}>
                      {s.symbol} <i>{s.wallets}</i>
                    </em>
                  ))}
                </span>
                <span className="price">
                  <b className={(r.chg_5m ?? 0) < 0 ? 'neg' : ''}>{fmtPct(r.chg_5m)}</b>
                  <small>
                    5m · 1h {fmtPct(r.chg_1h)} · {fmtNum(r.buyers_1h)} buyers
                    {r.market_now ? ` · FDV ${fmtUsd(r.market_now.fdv_usd)}` : ''}
                  </small>
                </span>
                <span className="x">
                  <b>{r.mentions_1h === null ? 'n/a' : r.mentions_1h}</b>
                  <small>{r.mentions_1h === null ? 'not fetched' : `1h · ${r.mentions_24h} / 24h`}</small>
                </span>
                <span className="holders">
                  {r.holders ? (
                    <>
                      <b>{r.holders.holders}</b>
                      <small>
                        top10 {Math.round(r.holders.top10_share * 100)}% · dev {r.holders.dev_share !== null ? `${(r.holders.dev_share * 100).toFixed(1)}%` : '?'}
                        {r.holders.dev_sold_share > 0.01 ? ` · sold ${(r.holders.dev_sold_share * 100).toFixed(0)}%` : ''}
                      </small>
                    </>
                  ) : (
                    <small className="faint">—</small>
                  )}
                </span>
                <span className="actions">
                  <button
                    onClick={(e) => {
                      e.stopPropagation()
                      onFlow(r.address)
                    }}
                  >
                    Flow
                  </button>
                </span>
              </div>
            )
          })}
        </div>
        <aside className="radar-alerts">
          <h2>Alerts · under-radar top-5</h2>
          <p className="faint">
            Fires when a coin enters the top 5 with score ≥ {alerts?.rules?.under_radar_top5?.score_min ?? 60}, inflow {alerts?.rules?.under_radar_top5?.inflow_min ?? 8}–{alerts?.rules?.under_radar_top5?.inflow_max ?? 40} wallets in 10 min, age &lt; {Math.round((alerts?.rules?.under_radar_top5?.age_max_s ?? 3600) / 60)} min, not already +{alerts?.rules?.under_radar_top5?.chg_10m_max ?? 100}% in 10 min, X mentions ≤ {alerts?.rules?.under_radar_top5?.mentions_max ?? 3} (or unknown). Outcome = price 30/60 min later on indexed trades.
          </p>
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
              <li key={a.id} onClick={() => onSelect(a.token)}>
                <span className="t">{utc(a.clock_ts)}</span>
                <b>{a.symbol}</b>
                <span>
                  {a.inflow} in · score {Math.round(a.score)}
                </span>
                <span className={a.outcome_30m === null ? 'faint' : (a.outcome_30m ?? 0) > 0 ? 'up' : 'neg'}>{a.outcome_30m === null ? 'pending' : `${fmtPct(a.outcome_30m)} @30m`}</span>
              </li>
            ))}
            {alertRows.length === 0 && <li className="faint">no alerts yet in this mode</li>}
          </ul>
        </aside>
      </div>
    </div>
  )
}

function scoreHint(r: RadarRow): string {
  const p = r.parts as Record<string, number | boolean>
  const bits = [`in ${p.inflow}`, `acc ${p.acceleration}`, `br ${p.breadth}`]
  if (p.quality_known) bits.push(`q ${p.wallet_quality}`)
  if ((p.crowding as number) < 0) bits.push('crowded')
  if ((p.attention_penalty as number) < 0) bits.push(`x ${p.attention_penalty}`)
  return bits.join(' · ')
}
