import { duration, shortAddr, utc } from '../format'
import type { Recent, Selection, Status } from '../types'

interface Props {
  status: Status | null
  recent: Recent[]
  freshKeys: Set<string>
  onSelect: (s: Selection) => void
}

export default function Ticker({ status, recent, freshKeys, onSelect }: Props) {
  const mode = status?.mode ?? 'fixture'
  const title = mode === 'live' ? 'Confirmed sequences, newest first' : mode === 'replay' ? 'Sequences reached by the replay clock' : 'Latest sequences in the recorded range'
  return (
    <footer className="ticker">
      <div className="label">
        <b>{title}</b>
        {mode === 'fixture' ? 'Static list. Nothing arrives here because the sample is a recording.' : 'A row appears when a buy transaction confirms and pairs with an earlier sell by the same wallet.'}
      </div>
      <ol>
        {recent.length === 0 && <li className="faint">No direct or clean sequences in the selected range.</li>}
        {recent.map((r) => {
          const key = `${r.from}->${r.to}`
          return (
            <li key={`${r.buy_tx}-${r.from}`} className={freshKeys.has(`${r.buy_tx}-${r.from}`) ? 'fresh' : ''} onClick={() => onSelect({ kind: 'edge', from: r.from, to: r.to })} title={key}>
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
