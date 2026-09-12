// One wallet: FIFO stats after fees, tags, the positions timeline over the computed range, coins, recent trades with
// tx links, and the copy-test note. A column beside the leaderboard (same shell as the coin drawer), never over it.
import { utc } from '../format'
import { hold } from '../tradersFormat'
import type { WalletCardData } from '../tradersTypes'
import type { SessionState } from '../types'

interface Props {
  card: WalletCardData | null
  loading: boolean
  error: string | null
  session: SessionState | null
  onClose: () => void
  onCoin: (address: string) => void
}

const pct = (v: number | null | undefined, d = 0) => (v === null || v === undefined ? 'n/a' : `${(v * 100).toFixed(d)}%`)
const signed = (v: number | null | undefined, d = 4) => (v === null || v === undefined ? 'n/a' : `${v > 0 ? '+' : ''}${v.toFixed(d)}`)
const usd = (v: number | null | undefined) => (v === null || v === undefined ? 'n/a' : `${v < 0 ? '-' : ''}$${Math.abs(v).toLocaleString('en-US', { maximumFractionDigits: 0 })}`)

export default function WalletCard({ card, loading, error, session, onClose, onCoin }: Props) {
  const st = card?.stats ?? null
  const run = card?.run ?? null
  const span = run ? Math.max(1, run.to_ts - run.from_ts) : 1
  const clock = session?.clock_ts ?? null
  return (
    <aside className="coin-drawer wallet-card" aria-label="Wallet details" data-testid="wallet-card">
      <div className="row between">
        <h1>{card ? card.short : loading ? 'Loading…' : 'Wallet'}</h1>
        <div className="row">
          <button onClick={onClose} aria-label="Close wallet details">
            Close (Esc)
          </button>
        </div>
      </div>
      {error && (
        <div className="state err" role="alert">
          Could not load this wallet: {error}
        </div>
      )}
      {!card && loading && (
        <div className="state-block" style={{ padding: '14px 0' }}>
          <img className="mark" src="/brand-bison.png" height={46} alt="" />
          <div>
            <b>Loading wallet</b>
            FIFO stats, positions and recent trades…
          </div>
        </div>
      )}
      {card && (
        <>
          <div className="sub">
            <a href={card.explorer} target="_blank" rel="noreferrer">
              {card.address}
            </a>
            {card.is_contract ? ' · contract' : ''}
            {card.launched_coins ? ` · deployed ${card.launched_coins} coin${card.launched_coins === 1 ? '' : 's'}` : ''}
          </div>
          {st ? (
            <>
              <div className="tags">
                {st.tags.length ? st.tags.map((t) => <span key={t} className={`tag ${t}`}>{t.replace(/_/g, ' ')}</span>) : <span className="faint">no tags</span>}
              </div>
              <h2>PnL · FIFO after fees · {run ? `${utc(run.from_ts)}–${utc(run.to_ts)} UTC` : 'range n/a'}</h2>
              <div className="kv">
                <span>realized + unrealized</span>
                <span className={st.pnl_eth < 0 ? 'neg' : ''}>
                  <b>{signed(st.pnl_eth)} ETH</b> · {usd(st.pnl_usd)}
                </span>
                <span>realized</span>
                <span>{signed(st.realized_eth)} ETH</span>
                <span>unrealized at last price</span>
                <span>{signed(st.unrealized_eth)} ETH</span>
                <span>deployed · fees paid</span>
                <span>
                  {st.cost_eth.toFixed(4)} · {st.fees_eth.toFixed(4)} ETH
                </span>
                <span>ROI · realized ROI</span>
                <span>
                  {st.roi === null ? 'n/a' : `${st.roi > 0 ? '+' : ''}${Math.round(st.roi * 100)}%`} · {st.roi_realized === null ? 'n/a' : `${st.roi_realized > 0 ? '+' : ''}${Math.round(st.roi_realized * 100)}%`}
                </span>
                {Object.keys(st.pnl_by_quote).filter((q) => q !== '0x0000000000000000000000000000000000000000').length > 0 && (
                  <>
                    <span>other quote assets</span>
                    <span className="faint">
                      {Object.entries(st.pnl_by_quote)
                        .filter(([q]) => q !== '0x0000000000000000000000000000000000000000')
                        .map(([q, v]) => `${q.slice(0, 6)}… ${signed(v.realized + v.unrealized, 4)}`)
                        .join(' · ')}
                    </span>
                  </>
                )}
              </div>
              <h2>Behaviour</h2>
              <div className="kv">
                <span>trades · coins</span>
                <span>
                  {st.trades} · {st.coins}
                </span>
                <span>positions closed · open · won</span>
                <span>
                  {st.positions_closed} · {st.positions_open} · {pct(st.win_rate)}
                </span>
                <span>median hold · trades/h</span>
                <span>
                  {hold(st.median_hold_s) ?? 'n/a'} · {st.trades_per_hour.toFixed(1)}
                </span>
                <span>median buyer rank · sniper entries</span>
                <span>
                  {st.buyer_rank_median === null ? 'n/a' : Math.round(st.buyer_rank_median)} · {pct(st.sniper_share)}
                </span>
                <span>exit quality · rug avoided</span>
                <span>
                  {st.exit_quality === null ? 'n/a' : st.exit_quality.toFixed(2)} · {pct(st.rug_avoid)}
                </span>
                <span>consistency (weeks positive)</span>
                <span>{st.consistency === null ? 'n/a' : `${pct(st.consistency)} of ${st.weeks_active}`}</span>
                <span>quality</span>
                <span>
                  <b>{st.quality.toFixed(3)}</b>
                </span>
              </div>
            </>
          ) : (
            <div className="state-block" style={{ padding: '14px 0' }}>
              <div>
                <b>No wallet stats for this address</b>
                {card.run ? 'it did not trade inside the computed range' : 'run `stampede traders --db <store>` and reload'} · the trades below are read live from the store.
              </div>
            </div>
          )}

          <h2>Positions · {card.positions.length} in range · newest first</h2>
          {card.positions.length ? (
            <ul className="positions" data-testid="positions">
              {card.positions.slice(0, 40).map((p, i) => {
                const left = run ? Math.max(0, Math.min(100, ((p.entry_ts - run.from_ts) / span) * 100)) : 0
                const end = p.exit_ts ?? clock ?? run?.to_ts ?? p.entry_ts
                const width = run ? Math.max(0.6, Math.min(100 - left, ((end - p.entry_ts) / span) * 100)) : 0
                const pnl = p.closed ? p.pnl_quote : p.unrealized_quote
                return (
                  <li key={i} className={`${p.closed ? 'closed' : 'open'} ${(pnl ?? 0) < 0 ? 'neg' : ''}`}>
                    <div className="line">
                      <span className="t">{utc(p.entry_ts)}</span>
                      <button className="coin" onClick={() => onCoin(p.token.address)} title={`open ${p.token.symbol}`}>
                        {p.token.symbol}
                      </button>
                      <span className="pnl num">{pnl === null || pnl === undefined ? <span className="unk">n/a</span> : `${signed(pnl)} ${p.quote_symbol}`}</span>
                      <span className="meta" title={`${p.buys} buys · ${p.sells} sells · cost ${p.cost_quote.toFixed(4)} · proceeds ${p.proceeds_quote.toFixed(4)} · fees ${p.fees_quote.toFixed(5)} ${p.quote_symbol}${p.since_launch_s !== null ? ` · entered ${Math.round(p.since_launch_s)} s after launch` : ''}`}>
                        {p.closed ? `closed · ${hold(p.hold_s) ?? 'n/a'}` : 'open'} · rank {p.buyer_rank ?? 'n/a'}
                        {p.sniper ? ' · sniper' : ''}
                        {p.snipe_paid ? ' · snipe tax paid' : ''} · exit q {p.exit_quality === null ? 'n/a' : p.exit_quality.toFixed(2)} · rug {p.rug_after === null ? 'n/a' : p.rug_after ? 'yes' : 'no'}
                      </span>
                      <span className="tx">
                        {p.entry_tx_url && (
                          <a href={p.entry_tx_url} target="_blank" rel="noreferrer">
                            in
                          </a>
                        )}
                        {p.exit_tx_url && (
                          <a href={p.exit_tx_url} target="_blank" rel="noreferrer">
                            out
                          </a>
                        )}
                      </span>
                    </div>
                    <div className="bar" aria-hidden="true">
                      <span style={{ left: `${left}%`, width: `${width}%` }} />
                    </div>
                  </li>
                )
              })}
            </ul>
          ) : (
            <div className="faint">no positions in the computed range</div>
          )}

          <h2>Coins · {card.coins.length}</h2>
          <div className="coins">
            {card.coins.slice(0, 24).map((c) => (
              <button key={c.address} className="coin-chip" onClick={() => onCoin(c.address)} title={`${c.buys} buys · ${c.sells} sells · last ${utc(c.last_ts)} UTC`}>
                {c.symbol} <b>{c.trades}</b>
              </button>
            ))}
            {card.coins.length === 0 && <span className="faint">none</span>}
          </div>

          <h2>Recent trades · {card.trades.length} of {card.trades_total}</h2>
          <ul className="wtrades" data-testid="wallet-trades">
            {card.trades.slice(0, 30).map((t) => (
              <li key={t.id}>
                <span className="t">{utc(t.ts)}{t.ts_exact ? '' : '≈'}</span>
                <span className={`side ${t.side}`}>{t.side}</span>
                <button className="coin" onClick={() => onCoin(t.token.address)}>
                  {t.token.symbol}
                </button>
                <span className="num amt">{t.quote_amount === null ? <span className="unk">quote n/a</span> : `${t.quote_amount.toFixed(4)} ${t.quote_symbol}`}</span>
                <span className="fee faint">{t.fee_quote === null ? (t.venue === 'v4' ? 'pool' : 'fee n/a') : `fee ${(t.fee_quote + (t.tax_quote ?? 0)).toFixed(5)}${t.snipe_paid ? ' · snipe' : ''}`}</span>
                <a href={t.tx_url} target="_blank" rel="noreferrer">
                  tx
                </a>
              </li>
            ))}
            {card.trades.length === 0 && <li className="faint">no trades for this address in the store</li>}
          </ul>

          {card.copy_test && (
            <>
              <h2>Copy-test</h2>
              <div className="meaning" data-testid="copy-note">{card.copy_test}</div>
            </>
          )}
          <div className="meaning">{card.note}</div>
        </>
      )}
    </aside>
  )
}
