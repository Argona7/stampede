import { duration, utc } from '../format'
import type { CoinDetail, SessionState } from '../types'

interface Props {
  coin: CoinDetail | null
  loading: boolean
  error: string | null
  session: SessionState | null
  onClose: () => void
  onRefresh: () => void
  onFlow: (address: string) => void
  onFocus: (address: string) => void
}

const pct = (v: number | null | undefined, d = 1) => (v === null || v === undefined ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(d)}%`)
const usd = (v: number | null | undefined) => (v === null || v === undefined ? '—' : v >= 1e6 ? `$${(v / 1e6).toFixed(2)}M` : v >= 1e3 ? `$${(v / 1e3).toFixed(1)}k` : `$${v.toFixed(2)}`)
const ago = (ts: number | null | undefined) => (ts ? `${Math.max(0, Math.round((Date.now() / 1000 - ts) / 60))} min ago` : '')

export default function CoinDrawer({ coin, loading, error, session, onClose, onRefresh, onFlow, onFocus }: Props) {
  return (
    <aside className="coin-drawer" aria-label="Coin details">
      <div className="row between">
        <h1>{coin ? coin.symbol : loading ? 'Loading…' : 'Coin'}</h1>
        <div className="row">
          {coin && <button onClick={() => onFlow(coin.address)}>Flow</button>}
          <button onClick={onRefresh} title="Fetch X mentions, market, holders and declared socials now (paid/rate-limited calls)">
            Refresh context
          </button>
          <button onClick={onClose}>Close (Esc)</button>
        </div>
      </div>
      {error && <div className="state err">{error}</div>}
      {coin && (
        <>
          <div className="sub">
            {coin.name ? `${coin.name} · ` : ''}
            <a href={coin.links.explorer} target="_blank" rel="noreferrer">
              {coin.short}
            </a>{' '}
            · <a href={coin.links.geckoterminal} target="_blank" rel="noreferrer">GeckoTerminal</a> · <a href={coin.links.pons} target="_blank" rel="noreferrer">PONS</a>
          </div>

          <h2>On-chain · as of {utc(coin.as_of.as_of_ts)} UTC</h2>
          <div className="kv">
            <span>age</span>
            <span>{coin.age_s !== null ? duration(coin.age_s) : '?'}</span>
            <span>stage</span>
            <span>{coin.progress.stage}{coin.progress.stage === 'curve' && coin.progress.progress !== null ? ` · ${Math.round(coin.progress.progress * 100)}% to graduation` : ''}</span>
            {coin.launch && (
              <>
                <span>launched</span>
                <span>{coin.launch.ts ? `${utc(coin.launch.ts)} UTC` : `block ${coin.launch.block}`}</span>
                <span>deployer</span>
                <span title={coin.launch.deployer}>{coin.launch.deployer.slice(0, 8)}…{coin.launch.deployer.slice(-4)}</span>
              </>
            )}
            <span>price change 5m / 1h</span>
            <span>
              {pct(coin.as_of.chg_5m)} / {pct(coin.as_of.chg_1h)}
            </span>
            <span>trades / buyers 1h</span>
            <span>
              {coin.as_of.trades_1h} / {coin.as_of.buyers_1h}
            </span>
            <span>volume 1h</span>
            <span>
              {coin.as_of.vol_1h_quote.toLocaleString('en-US', { maximumFractionDigits: 2 })} {coin.as_of.quote_symbol ?? ''}
            </span>
            <span>buyers in range · new</span>
            <span>
              {coin.buyers} · {coin.new_buyers}
            </span>
          </div>

          <h2>Rotation · pairing window {duration(session?.window_s ?? 1800)}</h2>
          <div className="flows">
            <div>
              <div className="lbl">wallets came from</div>
              {coin.inbound.filter((e) => e.wallets_main > 0).slice(0, 6).map((e) => (
                <div key={e.token.address} className="flow-line" onClick={() => onFocus(e.token.address)}>
                  <b>{e.wallets_main}</b> <span>{e.token.symbol}</span> <i>→</i>
                </div>
              ))}
              {coin.inbound.filter((e) => e.wallets_main > 0).length === 0 && <div className="faint">none in range</div>}
            </div>
            <div>
              <div className="lbl">then went to</div>
              {coin.outbound.filter((e) => e.wallets_main > 0).slice(0, 6).map((e) => (
                <div key={e.token.address} className="flow-line" onClick={() => onFocus(e.token.address)}>
                  <i>→</i> <span>{e.token.symbol}</span> <b>{e.wallets_main}</b>
                </div>
              ))}
              {coin.outbound.filter((e) => e.wallets_main > 0).length === 0 && <div className="faint">none in range</div>}
            </div>
          </div>

          <h2>Holders {coin.holders?.as_of_block ? `· block ${coin.holders.as_of_block}` : ''}</h2>
          {coin.holders && !coin.holders.error ? (
            <div className="kv">
              <span>holders</span>
              <span>{coin.holders.holders}</span>
              <span>top-10 share of supply</span>
              <span>{((coin.holders.top10_share ?? 0) * 100).toFixed(1)}%</span>
              <span>dev holds / sold</span>
              <span>
                {coin.holders.dev_share !== null && coin.holders.dev_share !== undefined ? `${(coin.holders.dev_share * 100).toFixed(2)}%` : '?'} / {((coin.holders.dev_sold_share ?? 0) * 100).toFixed(1)}%
              </span>
              <span>bought in launch block</span>
              <span>{coin.holders.launch_block_buyers ?? '—'} wallets</span>
            </div>
          ) : (
            <div className="faint">{coin.holders?.error ? `holders unavailable: ${coin.holders.error}` : 'not fetched · Refresh context'}</div>
          )}

          <h2>Attention · X</h2>
          {coin.mentions ? (
            <div className="kv">
              <span>mentions 1h / 24h</span>
              <span>
                {coin.mentions.mentions_1h} / {coin.mentions.mentions_24h}
                {coin.mentions.truncated ? '+' : ''}
              </span>
              <span>distinct authors</span>
              <span>{coin.mentions.distinct_authors}</span>
              <span>fetched</span>
              <span>{ago(coin.mentions.fetched_at)}</span>
            </div>
          ) : (
            <div className="faint">not fetched · Refresh context</div>
          )}
          {coin.mentions?.top?.length ? (
            <ul className="tweets">
              {coin.mentions.top.map((t, i) => (
                <li key={i}>
                  {t.url ? (
                    <a href={t.url} target="_blank" rel="noreferrer">
                      @{t.author}
                    </a>
                  ) : (
                    `@${t.author}`
                  )}{' '}
                  <span className="faint">{t.followers?.toLocaleString('en-US')} followers · {t.likes} likes</span>
                  <div>{t.text}</div>
                </li>
              ))}
            </ul>
          ) : null}
          {coin.mentions && <div className="faint tiny">search: {coin.mentions.query}</div>}

          <h2>Declared at launch</h2>
          {coin.socials ? (
            <div className="kv">
              <span>X account</span>
              <span>
                {coin.socials.twitter ? (
                  <a href={`https://x.com/${coin.socials.twitter}`} target="_blank" rel="noreferrer">
                    @{coin.socials.twitter}
                  </a>
                ) : (
                  '—'
                )}
                {coin.x_account?.followers !== null && coin.x_account?.followers !== undefined ? ` · ${coin.x_account.followers.toLocaleString('en-US')} followers` : ''}
              </span>
              <span>telegram</span>
              <span>{coin.socials.telegram ? <a href={`https://t.me/${coin.socials.telegram}`} target="_blank" rel="noreferrer">t.me/{coin.socials.telegram}</a> : '—'}</span>
              <span>website</span>
              <span>{coin.socials.website ? <a href={coin.socials.website} target="_blank" rel="noreferrer">{coin.socials.website.replace(/^https?:\/\//, '').slice(0, 40)}</a> : '—'}</span>
              {coin.socials.description && (
                <>
                  <span>description</span>
                  <span>{coin.socials.description.slice(0, 160)}</span>
                </>
              )}
            </div>
          ) : (
            <div className="faint">not fetched · Refresh context reads the launch transaction</div>
          )}

          <h2>Market now {coin.market_now || coin.market_cached ? `· GeckoTerminal ${ago((coin.market_now ?? coin.market_cached)?.fetched_at)}` : ''}</h2>
          {coin.market_now || coin.market_cached ? (
            <div className="kv">
              <span>price</span>
              <span>{(coin.market_now ?? coin.market_cached)?.price_usd?.toExponential(2) ?? '—'} USD</span>
              <span>FDV / liquidity</span>
              <span>
                {usd((coin.market_now ?? coin.market_cached)?.fdv_usd)} / {usd((coin.market_now ?? coin.market_cached)?.reserve_usd)}
              </span>
              <span>volume 1h / 24h</span>
              <span>
                {usd((coin.market_now ?? coin.market_cached)?.vol_h1)} / {usd((coin.market_now ?? coin.market_cached)?.vol_h24)}
              </span>
              <span>change 1h / 24h</span>
              <span>
                {pct((coin.market_now ?? coin.market_cached)?.chg_h1)} / {pct((coin.market_now ?? coin.market_cached)?.chg_h24)}
              </span>
            </div>
          ) : (
            <div className="faint">not fetched</div>
          )}
          {session?.mode !== 'live' && (coin.market_now || coin.market_cached) && <div className="faint tiny">"Market now" is the present, not the replay clock.</div>}
          <div className="meaning">{coin.note}</div>
        </>
      )}
    </aside>
  )
}
