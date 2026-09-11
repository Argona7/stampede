import { duration, shortAddr, utc } from '../format'
import type { Recent, Selection, SessionState, Status } from '../types'

interface Props {
  status: Status | null
  session: SessionState | null
  recent: (Recent & { id?: number })[]
  freshKeys: Set<string>
  onSelect: (s: Selection) => void
}

export default function Ticker({ status, session, recent, freshKeys, onSelect }: Props) {
  const mode = session?.mode ?? status?.mode ?? 'fixture'
  const title = mode === 'live' ? 'Sequences · newest first' : mode === 'replay' ? 'Sequences at the clock' : 'Latest sequences'
  return (
    <footer className="ticker" aria-label="Recent sequences">
      <div className="label">
        <b>{title}</b>
        {mode === 'fixture' ? 'static list: the sample is a recording' : 'time · wallet · sold → bought · gap · red = since the last poll'}
      </div>
      <ol>
        {recent.length === 0 && <li className="faint">No direct or clean sequences reached in this range yet — press Play or seek forward.</li>}
        {recent.map((r) => {
          const key = `${r.from}->${r.to}`
          return (
            <li key={`${r.id ?? r.buy_tx}-${r.from}`} className={r.id !== undefined && freshKeys.has(String(r.id)) ? 'fresh' : ''} onClick={() => onSelect({ kind: 'edge', from: r.from, to: r.to })} title={key}>
              <span className="t">{utc(r.buy_ts)}</span>
              <span className="mono faint">{shortAddr(r.wallet)}</span>
              <span>
                {r.from_label} → {r.to_label}
              </span>
              <span className="faint">{r.grade === 'direct' ? 'same tx' : `after ${duration(r.gap_s)}`}</span>
            </li>
          )
        })}
      </ol>
    </footer>
  )
}
