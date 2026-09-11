import { amount, duration, int, shortAddr, utc } from '../format'
import type { EdgeDetail, Selection, Sequence, TokenDetail, Trade } from '../types'

interface Props {
  selection: Selection
  edge: EdgeDetail | null
  token: TokenDetail | null
  loading: boolean
  error: string | null
  onSelect: (s: Selection) => void
  freshRows?: Set<string>
}

const venueName = (v: string) => (v === 'curve' ? 'on the PONS bonding curve' : v === 'v4' ? 'in the Uniswap v4 pool' : 'on the curve and the v4 pool')

function Leg({ t, label }: { t: Trade; label: string }) {
  return (
    <div className="leg">
      <span>{label}</span>
      <span>
        <b>{amount(t.token_amount)}</b> tokens {t.quote_amount === null ? <span className="faint">(quote not attributable, two wallets traded this coin in one tx)</span> : <>for <b>{amount(t.quote_amount, 4)} {t.quote_symbol}</b></>} {venueName(t.venue)}
      </span>
      <span />
      <span className="mono">
        {utc(t.ts)} UTC{t.ts_exact ? '' : ' (approx.)'} · block {t.block} · <a href={t.tx_url} target="_blank" rel="noreferrer">tx {shortAddr(t.tx)}</a>
        {t.tx_from && t.tx_from !== t.wallet ? <span className="faint"> sent by {shortAddr(t.tx_from)}</span> : null}
      </span>
    </div>
  )
}

export function SequenceRow({ s, fresh = false }: { s: Sequence; fresh?: boolean }) {
  return (
    <div className={`seq ${fresh ? 'fresh' : ''}`}>
      <div className="head">
        <span className="mono">
          <a href={s.wallet_url} target="_blank" rel="noreferrer">
            {shortAddr(s.wallet)}
          </a>
          {s.wallet_is_contract ? <span className="tag" style={{ marginLeft: 6 }}>contract</span> : null}
        </span>
        <span>
          <span className={`tag ${s.grade}`}>{s.grade}</span> <span className="muted">gap {duration(s.gap_s)}</span>
        </span>
      </div>
      <Leg t={s.sell} label="sold" />
      <Leg t={s.buy} label="bought" />
      {s.grade === 'ambiguous' && s.candidates && (
        <div className="faint" style={{ marginTop: 6 }}>
          Ambiguous because the wallet also sold {Math.max(0, (s.candidates.sold_in_window_n ?? s.candidates.sold_in_window.length) - 1)} other coin(s) in the window
          {s.candidates.bought_between.length ? ` and bought ${s.candidates.bought_between.length} other coin(s) in between` : ''}. Not counted in the edge weight.
        </div>
      )}
    </div>
  )
}

export default function Details({ selection, edge, token, loading, error, onSelect, freshRows }: Props) {
  if (!selection) {
    return (
      <aside className="detail" data-testid="detail-empty">
        <div className="state-block" style={{ padding: '10px 0' }}>
          <img className="mark" src="/brand-mark.svg" width={32} height={32} alt="" />
          <div>
            <b>Nothing selected</b>
            Click a line (A → B) for the wallets that sold A and then bought B, with their transactions; click a coin for its buyers, sellers and edges.
          </div>
        </div>
      </aside>
    )
  }
  if (error) {
    return (
      <aside className="detail">
        <div className="state-block err" role="alert" style={{ margin: 0 }}>
          <div>
            <b>Could not load the selection</b>
            {error}
          </div>
        </div>
      </aside>
    )
  }
  if (selection.kind === 'edge') {
    if (!edge || loading)
      return (
        <aside className="detail">
          <div className="state-block" style={{ padding: '10px 0' }}>
            <img className="mark" src="/brand-mark.svg" width={32} height={32} alt="" />
            <div>
              <b>Loading evidence</b>
              sequence rows first, then exact block times and tx senders…
            </div>
          </div>
        </aside>
      )
    const g = edge.wallets_by_grade
    return (
      <aside className="detail">
        <h1>
          <span role="button" tabIndex={0} style={{ cursor: 'pointer' }} onClick={() => onSelect({ kind: 'token', address: edge.from.address })}>{edge.from.symbol}</span>
          {' → '}
          <span role="button" tabIndex={0} style={{ cursor: 'pointer' }} onClick={() => onSelect({ kind: 'token', address: edge.to.address })}>{edge.to.symbol}</span>
        </h1>
        <div className="sub mono">
          A {edge.from.short} → B {edge.to.short} · window {edge.window_s / 60} min
        </div>
        <div className="grades">
          <span>
            <b>{edge.wallets_main}</b>distinct wallets
          </span>
          <span>
            <b>{g.direct ?? 0}</b>in one tx
          </span>
          <span>
            <b>{g.clean ?? 0}</b>clean sequence
          </span>
          <span>
            <b>{g.ambiguous ?? 0}</b>ambiguous, not counted
          </span>
        </div>
        <div className="meaning">{edge.meaning}</div>
        <h2>
          {edge.sequences_total} sequence rows
          {edge.truncated ? ` · ${edge.sequences.length} most recent shown` : ''} · a wallet appears once per sequence, so rows can exceed wallets
        </h2>
        {edge.sequences.map((s) => (
          <SequenceRow key={s.id} s={s} fresh={!!freshRows?.has(String(s.id))} />
        ))}
      </aside>
    )
  }
  if (!token || loading)
    return (
      <aside className="detail">
        <div className="state-block" style={{ padding: '10px 0' }}>
          <img className="mark" src="/brand-mark.svg" width={32} height={32} alt="" />
          <div>
            <b>Loading coin</b>
            buyers, sellers and the edges touching it in this range…
          </div>
        </div>
      </aside>
    )
  return (
    <aside className="detail">
      <h1>{token.symbol}</h1>
      <div className="sub">
        {token.name} · <a className="mono" href={token.address_url} target="_blank" rel="noreferrer">{token.address}</a>
      </div>
      <div className="kv" style={{ marginBottom: 12 }}>
        <span>venue</span>
        <span>{token.source === 'curve' ? `PONS v2 curve, paired with ${token.pair_symbol ?? '?'}` : 'graduated v4 pool'}</span>
        <span>buys / sells in range</span>
        <span>{int(token.buys)} / {int(token.sells)}</span>
        <span>distinct buyers / sellers</span>
        <span>{int(token.buyers)} / {int(token.sellers)}</span>
        <span>first-time buyers in range</span>
        <span>{int(token.new_buyers_in_range)}</span>
        <span>repeat buyers in range</span>
        <span>{int(token.repeat_buyers_in_range)}</span>
        <span>contract wallets among them</span>
        <span>{int(token.contract_wallets)}</span>
        <span>first / last trade</span>
        <span>{utc(token.first_trade_ts)} / {utc(token.last_trade_ts)}</span>
      </div>
      <div className="meaning">{token.coverage.note} Trades with exact block time: {token.coverage.exact_timestamps} of {token.coverage.trades}.</div>
      <h2>Wallets came from</h2>
      {token.inbound.length === 0 ? <div className="empty">No inbound sequences in range.</div> : (
        <ul className="list">
          {token.inbound.map((r) => (
            <li key={r.token.address} onClick={() => onSelect({ kind: 'edge', from: r.token.address, to: token.address })}>
              <span>
                {r.token.symbol} <span className="mono faint">{r.token.short}</span>
              </span>
              <span className="num">{r.wallets_main} <span className="faint">({r.wallets_all} incl. ambiguous)</span></span>
            </li>
          ))}
        </ul>
      )}
      <h2 style={{ marginTop: 16 }}>Wallets went to</h2>
      {token.outbound.length === 0 ? <div className="empty">No outbound sequences in range.</div> : (
        <ul className="list">
          {token.outbound.map((r) => (
            <li key={r.token.address} onClick={() => onSelect({ kind: 'edge', from: token.address, to: r.token.address })}>
              <span>
                {r.token.symbol} <span className="mono faint">{r.token.short}</span>
              </span>
              <span className="num">{r.wallets_main} <span className="faint">({r.wallets_all} incl. ambiguous)</span></span>
            </li>
          ))}
        </ul>
      )}
    </aside>
  )
}
