import { int, utc, windowName } from '../format'
import type { Graph, SessionState, Status } from '../types'

export interface Filters {
  windowS: number
  spanS: number // visible range length; the range ends at the shared clock
  toTs: number | null
  minWallets: number
  showAmbiguous: boolean
  limit: number // top-N edges drawn on the map (by distinct wallets)
}

interface Props {
  status: Status | null
  graph: Graph | null
  filters: Filters
  setFilters: (f: Filters) => void
  bounds: { min: number; max: number } | null
  session: SessionState | null
  onControl: (action: string, extra?: Record<string, number | undefined>) => void
}

const SPANS = [300, 900, 1800, 3600]
const WINDOWS = [300, 1800, 7200]

/** MAP left rail: what is drawn (range, pairing window, edge filters) and what the data covers. Play/pause,
 *  speed and seeking live in the strip and the working row, not here. */
export default function Controls({ status, graph, filters, setFilters, bounds, session, onControl }: Props) {
  const cov = status?.coverage
  const live = session?.mode === 'live'
  const clock = session?.clock_ts ?? null
  const windows = cov?.windows_s?.length ? cov.windows_s : WINDOWS
  return (
    <aside className="rail" aria-label="Map controls">
      <section>
        <h2>Pairing window</h2>
        <div className="seg" role="group" aria-label="Pairing window" title="A sell and a later buy by the same wallet pair up only inside this window; shared with the terminal">
          {windows.map((w) => (
            <button key={w} className={filters.windowS === w ? 'on' : ''} aria-pressed={filters.windowS === w} onClick={() => onControl('window', { window_s: w })}>
              {windowName(w)}
            </button>
          ))}
        </div>
      </section>

      <section>
        <h2>Visible range</h2>
        <div className="seg" role="group" aria-label="Visible range">
          {SPANS.map((s) => (
            <button key={s} className={filters.spanS === s ? 'on' : ''} aria-pressed={filters.spanS === s} onClick={() => onControl('span', { span_s: s })}>
              {windowName(s)}
            </button>
          ))}
        </div>
        {bounds && !live && session?.controls && (
          <>
            <input type="range" min={bounds.min} max={bounds.max} step={30} value={clock ?? bounds.max} aria-label="Replay clock" onChange={(e) => onControl('seek', { ts: Number(e.target.value) })} />
            <div className="kv">
              <span>buys from</span>
              <span>{utc(clock !== null ? clock - filters.spanS : null)} UTC</span>
              <span>to (clock)</span>
              <span>{utc(clock)} UTC</span>
            </div>
          </>
        )}
        {live && <p className="note">Range ends at the last indexed block and moves with the chain.</p>}
      </section>

      <section>
        <h2>Edges drawn</h2>
        <div className="kv">
          <span>min wallets on an edge</span>
          <span>{filters.minWallets}</span>
        </div>
        <input type="range" min={1} max={20} value={filters.minWallets} aria-label="Minimum wallets per edge" onChange={(e) => setFilters({ ...filters, minWallets: Number(e.target.value) })} />
        <div className="kv">
          <span>top flows</span>
          <span>{filters.limit}</span>
        </div>
        <div className="seg" role="group" aria-label="Top flows drawn" style={{ marginTop: 6 }}>
          {[25, 50, 100, 300].map((n) => (
            <button key={n} className={filters.limit === n ? 'on' : ''} aria-pressed={filters.limit === n} onClick={() => setFilters({ ...filters, limit: n })}>
              {n}
            </button>
          ))}
        </div>
        <label className="row" style={{ marginTop: 8, flexWrap: 'nowrap' }} title="edges whose only sequences are ambiguous are drawn dashed">
          <input type="checkbox" checked={filters.showAmbiguous} onChange={(e) => setFilters({ ...filters, showAmbiguous: e.target.checked })} />
          show ambiguous edges
        </label>
      </section>

      {graph && (
        <section>
          <h2>In this range</h2>
          <div className="kv">
            <span>drawn / matching</span>
            <span>
              {int(graph.totals.edges_returned)} / {int(graph.totals.edges_matching)}
            </span>
            <span>coins</span>
            <span>{int(graph.nodes.length)}</span>
            <span>sequence rows</span>
            <span>{int(graph.totals.sequences_in_range)}</span>
            <span>distinct wallets (main)</span>
            <span>{int(graph.totals.wallets_main_in_range)}</span>
          </div>
        </section>
      )}

      {cov && status && (
        <section>
          <h2>Coverage</h2>
          <div className="kv">
            <span>sample trades</span>
            <span>{int(status.sample.trades ?? cov.trades)}</span>
            <span>whole store trades</span>
            <span>{int(status.store?.trades ?? cov.trades)}</span>
            <span>unknown-time trades</span>
            <span>{int(status.store?.unknown_time_trades ?? 0)}</span>
            <span>v4 pools not covered</span>
            <span>{int(cov.pools_unresolved)}</span>
            <span>exact timestamps</span>
            <span>{cov.ts_exact_share !== null ? `${Math.round(cov.ts_exact_share * 100)}%` : '?'}</span>
          </div>
          <p className="note">PONS v2 curves and their v4 pools only; other DEXes are not indexed. Interpolated block times are marked “approx.” in evidence.</p>
        </section>
      )}
    </aside>
  )
}
