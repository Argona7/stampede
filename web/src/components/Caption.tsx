import { utc, windowName } from '../format'
import type { EdgeDetail, SessionState, TokenDetail } from '../types'

interface Props {
  edge: EdgeDetail | null
  token: TokenDetail | null
  session: SessionState | null
  liveLastTs: number | null
  onOpenEvidence: () => void
  onBack: () => void
  evidenceOpen: boolean
}

/** The big factual phrase of the EVIDENCE state. Every number and name comes from the current data. */
export default function Caption({ edge, token, session, liveLastTs, onOpenEvidence, onBack, evidenceOpen }: Props) {
  const mode = session?.mode ?? 'fixture'
  const clock = mode === 'live' ? liveLastTs : session?.clock_ts ?? null
  const modeLabel = mode === 'live' ? 'LIVE' : mode === 'replay' ? `REPLAY ${session?.speed ?? ''}×${session?.playing ? '' : ' · PAUSED'}` : 'RECORDED SAMPLE'
  if (edge) {
    return (
      <div className="caption" data-testid="caption">
        <div className="n">
          {edge.wallets_main} WALLET{edge.wallets_main === 1 ? '' : 'S'}
        </div>
        <div className="pair">
          SOLD {edge.from.symbol} → <span className="to">BOUGHT {edge.to.symbol}</span>
        </div>
        <div className="meta">
          <b>{edge.wallets_main}</b> distinct wallets within {windowName(edge.window_s)} · <b>{edge.sequences_total}</b> observed sequence rows · ambiguous {edge.wallets_by_grade.ambiguous ?? 0} not counted
        </div>
        <div className="meta">
          {modeLabel} · {utc(clock)} UTC
        </div>
        <div className="hint">
          <button className="primary" onClick={onOpenEvidence}>
            {evidenceOpen ? 'Hide evidence (E)' : 'Open evidence (E)'}
          </button>
          <button onClick={onBack}>Back to overview (Esc)</button>
          <span> Same address, observed order of trades. Not proof of money flow or shared ownership.</span>
        </div>
      </div>
    )
  }
  if (token) {
    return (
      <div className="caption" data-testid="caption">
        <div className="n">{token.symbol}</div>
        <div className="pair">
          coin view
        </div>
        <div className="meta">
          <b>{token.buyers}</b> distinct buyers · <b>{token.sellers}</b> distinct sellers in range · {token.inbound.reduce((a, r) => a + r.wallets_main, 0)} inbound edge-wallets · {token.outbound.reduce((a, r) => a + r.wallets_main, 0)} outbound
        </div>
        <div className="meta">
          {modeLabel} · {utc(clock)} UTC
        </div>
        <div className="hint">
          <button onClick={onBack}>Back to overview (Esc)</button>
          <span> Edge-wallet sums can count one address on several edges.</span>
        </div>
      </div>
    )
  }
  return null
}
