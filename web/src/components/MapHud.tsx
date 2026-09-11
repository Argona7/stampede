// Live HUD over the 3D scene (presentation): what is moving right now, in numbers that come from the same
// polls the radar uses. Top inflow list flashes when a coin gains wallets; counters are over the visible range.
import { useEffect, useMemo, useState } from 'react'
import { utc } from '../format'
import type { Graph, RadarResponse, Selection, SeqEvent } from '../types'

interface Props {
  radar: RadarResponse | null
  graph: Graph | null
  feed: SeqEvent[]
  fresh: Map<string, number>
  clock: number | null
  selection: Selection
  onPick: (from: string, to: string) => void
}

export default function MapHud({ radar, graph, feed, fresh, clock, selection, onPick }: Props) {
  const [now, setNow] = useState(0)
  useEffect(() => {
    const id = window.setInterval(() => setNow(performance.now()), 400)
    return () => window.clearInterval(id)
  }, [])
  const rows = (radar?.rows ?? []).slice(0, 7)
  const perMin = useMemo(() => {
    if (!clock || !feed.length) return 0
    return feed.filter((e) => e.buy_ts > clock - 60).length
  }, [feed, clock])
  const wallets10 = useMemo(() => {
    if (!clock) return 0
    return new Set(feed.filter((e) => e.buy_ts > clock - 600).map((e) => e.wallet)).size
  }, [feed, clock])
  const coins10 = useMemo(() => (radar?.rows ?? []).filter((r) => r.inflow_10m > 0).length, [radar])
  const selTo = selection?.kind === 'edge' ? selection.to : selection?.kind === 'token' ? selection.address : null
  return (
    <div className="hud" aria-label="Live rotation HUD">
      <div className="hud-counters">
        <div>
          <b>{perMin}</b>
          <span>sequences / last min</span>
        </div>
        <div>
          <b>{wallets10}</b>
          <span>wallets rotating · 10 min</span>
        </div>
        <div>
          <b>{coins10}</b>
          <span>coins with inflow · 10 min</span>
        </div>
        <div>
          <b>{graph?.totals.edges_matching ?? 0}</b>
          <span>flows · {graph ? Math.round(graph.totals.sequences_in_range).toLocaleString('en-US') : 0} seq in range</span>
        </div>
      </div>
      <div className="hud-top">
        <div className="hud-title">TOP INFLOW · LAST 10 MIN · as of {utc(clock)} UTC</div>
        {rows.map((r, i) => {
          const f = fresh.get(r.address)
          const hot = f !== undefined && now - f < 2500
          const src = r.sources[0]
          return (
            <div key={r.address} className={`hud-row ${hot ? 'hot' : ''} ${selTo === r.address ? 'sel' : ''}`} onClick={() => src && onPick(src.address, r.address)} role="button" tabIndex={0}>
              <span className="n">{i + 1}</span>
              <span className="in">
                <b>{r.inflow_10m}</b>
              </span>
              <span className="sym">{r.symbol}</span>
              <span className="from">{src ? `← ${src.symbol} ${src.wallets}${r.sources.length > 1 ? ` +${r.sources.length - 1}` : ''}` : ''}</span>
              <span className="meta">
                {r.age_s !== null ? `${Math.max(1, Math.round(r.age_s / 60))}m` : '?'} · {r.chg_1h !== null ? `${r.chg_1h > 0 ? '+' : ''}${Math.round(r.chg_1h)}% 1h` : ''}
              </span>
            </div>
          )
        })}
        {rows.length === 0 && <div className="hud-row faint">no rotation inflow in range</div>}
      </div>
    </div>
  )
}
