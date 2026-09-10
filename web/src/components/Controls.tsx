import { int, utc, windowName } from '../format'
import type { Graph, Status } from '../types'

export interface Filters {
  windowS: number
  spanS: number // visible range length; the range ends at `toTs`
  toTs: number | null
  minWallets: number
  showAmbiguous: boolean
}

interface Props {
  status: Status | null
  graph: Graph | null
  filters: Filters
  setFilters: (f: Filters) => void
  bounds: { min: number; max: number } | null
  replay: { playing: boolean; speed: number; setPlaying: (p: boolean) => void; setSpeed: (s: number) => void; reset: () => void } | null
}

const SPANS = [300, 900, 1800, 3600]

export default function Controls({ status, graph, filters, setFilters, bounds, replay }: Props) {
  const windows = status?.coverage.windows_s?.length ? status.coverage.windows_s : [1800]
  const cov = status?.coverage
  const fromTs = filters.toTs !== null ? filters.toTs - filters.spanS : null
  const live = status?.mode === 'live'
  return (
    <aside className="rail">
      <section>
        <h2>Pairing window</h2>
        <div className="row">
          {windows.map((w) => (
            <button key={w} className={filters.windowS === w ? 'on' : ''} onClick={() => setFilters({ ...filters, windowS: w })}>
              {windowName(w)}
            </button>
          ))}
        </div>
        <p className="faint" style={{ margin: '6px 0 0', fontSize: 12 }}>
          A sell and a later buy by the same wallet pair up only if the buy happens within this window.
        </p>
      </section>

      <section>
        <h2>Time range</h2>
        <div className="row">
          {SPANS.map((s) => (
            <button key={s} className={filters.spanS === s ? 'on' : ''} onClick={() => setFilters({ ...filters, spanS: s })}>
              {windowName(s)}
            </button>
          ))}
        </div>
        {bounds && !live && (
          <>
            <input
              type="range"
              min={bounds.min}
              max={bounds.max}
              step={30}
              value={filters.toTs ?? bounds.max}
              aria-label="End of the visible range"
              onChange={(e) => setFilters({ ...filters, toTs: Number(e.target.value) })}
              disabled={!!replay?.playing}
            />
            <div className="kv">
              <span>showing buys from</span>
              <span>{utc(fromTs)} UTC</span>
              <span>to</span>
              <span>{utc(filters.toTs ?? bounds.max)} UTC</span>
            </div>
          </>
        )}
        {live && (
          <p className="faint" style={{ margin: '6px 0 0', fontSize: 12 }}>
            Range ends at the last indexed block and moves with the chain.
          </p>
        )}
      </section>

      {replay && (
        <section>
          <h2>Replay</h2>
          <div className="row">
            <button className={replay.playing ? 'on' : ''} onClick={() => replay.setPlaying(!replay.playing)}>
              {replay.playing ? 'Pause' : 'Play'}
            </button>
            {[1, 10, 60].map((s) => (
              <button key={s} className={replay.speed === s ? 'on' : ''} onClick={() => replay.setSpeed(s)}>
                {s}x
              </button>
            ))}
            <button onClick={replay.reset}>Start</button>
          </div>
          <p className="faint" style={{ margin: '6px 0 0', fontSize: 12 }}>
            Recorded sample played back at the chosen speed. Edges appear when their buy transaction is reached.
          </p>
        </section>
      )}

      <section>
        <h2>Edges</h2>
        <div className="kv">
          <span>min wallets on an edge</span>
          <span>{filters.minWallets}</span>
        </div>
        <input type="range" min={1} max={20} value={filters.minWallets} aria-label="Minimum wallets per edge" onChange={(e) => setFilters({ ...filters, minWallets: Number(e.target.value) })} />
        <label className="row" style={{ fontSize: 12.5, marginTop: 6 }}>
          <input type="checkbox" checked={filters.showAmbiguous} onChange={(e) => setFilters({ ...filters, showAmbiguous: e.target.checked })} />
          draw ambiguous-only edges (dashed)
        </label>
      </section>

      {graph && (
        <section>
          <h2>In view</h2>
          <div className="kv">
            <span>edges drawn</span>
            <span>{int(graph.totals.edges_returned)}{graph.totals.truncated ? '+' : ''}</span>
            <span>coins</span>
            <span>{int(graph.nodes.length)}</span>
            <span>sequences in range</span>
            <span>{int(graph.totals.sequences_in_range)}</span>
            <span>wallets in range</span>
            <span>{int(graph.totals.wallets_in_range)}</span>
          </div>
        </section>
      )}

      {cov && (
        <section>
          <h2>Coverage</h2>
          <div className="kv">
            <span>venues</span>
            <span style={{ whiteSpace: 'normal', textAlign: 'right', fontFamily: 'inherit', color: 'var(--text-2)' }}>PONS v2 curves and their v4 pools</span>
            <span>coins</span>
            <span>{int(cov.tokens)}</span>
            <span>trades</span>
            <span>{int(cov.trades)}</span>
            <span>wallets</span>
            <span>{int(cov.wallets)}</span>
            <span>v4 pools not covered</span>
            <span>{int(cov.pools_unresolved)}</span>
            <span>exact timestamps</span>
            <span>{cov.ts_exact_share !== null ? `${Math.round(cov.ts_exact_share * 100)}%` : '?'}</span>
          </div>
          {cov.verify && (
            <p className="faint" style={{ margin: '8px 0 0', fontSize: 12 }}>
              Receipt cross-check: {cov.verify.completeness}; {cov.verify.reproducibility}.
            </p>
          )}
          <p className="faint" style={{ margin: '8px 0 0', fontSize: 12 }}>
            Other launchpads and DEXes on this chain are not indexed. Timestamps between anchor blocks are interpolated until an edge is opened.
          </p>
        </section>
      )}
    </aside>
  )
}
