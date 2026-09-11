// FLOW: the ego network of one coin. Sources (coins wallets sold before buying this one) on the left,
// the coin in the middle, destinations (where its sellers went next) on the right. Ribbon width = distinct
// wallets; every ribbon carries an arrow in the observed order (sold → bought). Labels are stacked with
// gaps by construction, so they never overlap each other or the centre.
import { useEffect, useMemo, useState } from 'react'
import { duration, windowName } from '../format'
import type { CoinDetail, Selection } from '../types'

interface Props {
  coin: CoinDetail | null
  coinAddr: string | null
  loading: boolean
  drawerOpen: boolean
  onFocus: (address: string) => void
  onEdge: (s: Selection) => void
  onBack: () => void
  onToggleDrawer: () => void
  fresh: Map<string, number> // edge key -> performance.now()
  windowS: number
}

const W = 1200
const H = 720
const LEFT_X = 40
const MID_X = W / 2
const RIGHT_X = W - 40
const COL_W = 250
const NODE_R = 22
const HEAD_Y = 44

export default function Flow({ coin, coinAddr, loading, drawerOpen, onFocus, onEdge, onBack, onToggleDrawer, fresh, windowS }: Props) {
  const [now, setNow] = useState(0)
  useEffect(() => {
    const id = window.setInterval(() => setNow(performance.now()), 400)
    return () => window.clearInterval(id)
  }, [])
  const model = useMemo(() => {
    if (!coin) return null
    const inb = [...coin.inbound].filter((e) => e.wallets_main > 0).sort((a, b) => b.wallets_main - a.wallets_main).slice(0, 12)
    const outb = [...coin.outbound].filter((e) => e.wallets_main > 0).sort((a, b) => b.wallets_main - a.wallets_main).slice(0, 12)
    const maxW = Math.max(1, ...inb.map((e) => e.wallets_main), ...outb.map((e) => e.wallets_main))
    const width = (w: number) => 3 + (w / maxW) * 44
    const place = (list: typeof inb) => {
      const natural = list.reduce((a, e) => a + Math.max(30, width(e.wallets_main)) + 12, 0)
      const scale = Math.min(1, (H - HEAD_Y - 80) / Math.max(1, natural)) // long lists shrink so nothing leaves the frame
      const total = natural * scale
      let y = HEAD_Y + 30 + (H - HEAD_Y - 60 - total) / 2
      return list.map((e) => {
        const wpx = width(e.wallets_main) * scale
        const slot = (Math.max(30, width(e.wallets_main)) + 12) * scale
        const cy = y + slot / 2
        y += slot
        return { ...e, wpx, cy, boxH: Math.min(30, slot - 4) }
      })
    }
    return { inb: place(inb), outb: place(outb), maxW, totalIn: inb.reduce((a, e) => a + e.wallets_main, 0), totalOut: outb.reduce((a, e) => a + e.wallets_main, 0) }
  }, [coin])

  const bar = (summary: React.ReactNode) => (
    <div className="workbar">
      {summary}
      <span className="grow" />
      <button onClick={onBack}>Radar (Esc)</button>
      {coin && (
        <button className={drawerOpen ? 'on' : ''} aria-pressed={drawerOpen} onClick={onToggleDrawer} data-testid="drawer-toggle">
          Coin details (D)
        </button>
      )}
    </div>
  )

  if (!coin) {
    return (
      <div className="flow">
        {bar(<span className="summary">FLOW · where one coin's wallets came from and where they went</span>)}
        <div className="state-block" data-testid="flow-empty">
          <img className="mark" src="/brand-bison.png" height={46} alt="" />
          <div>
            <b>{loading && coinAddr ? `Loading FLOW for ${coinAddr.slice(0, 6)}…${coinAddr.slice(-4)}` : 'No coin selected'}</b>
            {loading && coinAddr ? 'asking /api/coin for its inbound and outbound rotations…' : 'Pick a coin in RADAR (click a row, or ↑↓ then Enter) or click a coin on the MAP. FLOW then shows which coins its buyers sold before, and where its sellers went next.'}
          </div>
        </div>
        <div className="flow-legend" />
      </div>
    )
  }
  const m = model!
  return (
    <div className="flow">
      {bar(
        <span className="summary" data-testid="flow-summary">
          <b>{m.totalIn}</b> wallets rotated <span className="to">into</span> <b>{coin.symbol}</b> from {m.inb.length} coins · <b>{m.totalOut}</b> rotated out to {m.outb.length} coins · pairing window {windowName(windowS)}
        </span>,
      )}
      <div className="flow-stage">
        <svg className="flow-svg" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid meet" role="img" aria-label={`Rotation flow around ${coin.symbol}`}>
          <defs>
            <marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M 0 0 L 10 5 L 0 10 z" fill="#A3A3A3" />
            </marker>
            <marker id="arrow-hot" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M 0 0 L 10 5 L 0 10 z" fill="#FF3344" />
            </marker>
          </defs>
          {/* column headers: the order of trades reads left to right */}
          <text x={LEFT_X} y={HEAD_Y} className="role">
            A · SOLD FIRST · {m.inb.length} COINS
          </text>
          <text x={RIGHT_X} y={HEAD_Y} className="role" textAnchor="end">
            THEN BOUGHT · {m.outb.length} COINS
          </text>
          <text x={MID_X} y={HEAD_Y} className="role b" textAnchor="middle">
            B · BOUGHT
          </text>
          {/* the coin's facts sit in the free band under the header, never in the ribbons' path */}
          <text x={MID_X} y={HEAD_Y + 22} textAnchor="middle" className="n">
            {coin.short}
            {coin.age_s !== null ? ` · ${duration(coin.age_s)} old` : ''} · {coin.progress.stage === 'curve' && coin.progress.progress !== null ? `curve ${Math.round(coin.progress.progress * 100)}%` : coin.progress.stage}
          </text>
          <text x={MID_X} y={HEAD_Y + 42} textAnchor="middle" className="n">
            {coin.buyers} buyers in range · {coin.new_buyers} first-time · price 1 h {coin.as_of.chg_1h !== null ? `${coin.as_of.chg_1h > 0 ? '+' : ''}${coin.as_of.chg_1h.toFixed(0)}%` : '—'}
          </text>
          {/* inbound ribbons: A (left) → this coin */}
          {m.inb.map((e) => {
            const key = `${e.token.address}->${coin.address}`
            const f = fresh.get(key)
            const hot = f !== undefined && now - f < 2500
            const x1 = LEFT_X + COL_W
            const x2 = MID_X - NODE_R - 8
            const d = `M ${x1} ${e.cy} C ${x1 + 170} ${e.cy}, ${x2 - 170} ${H / 2}, ${x2} ${H / 2}`
            return (
              <g key={key} className={`ribbon ${hot ? 'hot' : ''}`} onClick={() => onEdge({ kind: 'edge', from: e.token.address, to: coin.address })}>
                <title>{`${e.wallets_main} wallets sold ${e.token.symbol}, then bought ${coin.symbol} · click for the transactions`}</title>
                <path d={d} fill="none" stroke={hot ? '#FF3344' : '#5a5a5a'} strokeOpacity={hot ? 0.9 : 0.55} strokeWidth={e.wpx} strokeLinecap="butt" />
                <path d={d} fill="none" stroke={hot ? '#FF3344' : '#A3A3A3'} strokeOpacity={0.9} strokeWidth={1} markerEnd={hot ? 'url(#arrow-hot)' : 'url(#arrow)'} />
                <g className="src" onClick={(ev) => (ev.stopPropagation(), onFocus(e.token.address))}>
                  <title>{`make ${e.token.symbol} the centre`}</title>
                  <rect x={LEFT_X} y={e.cy - e.boxH / 2} width={COL_W} height={e.boxH} fill="#0D0A0A" stroke="#351419" />
                  <text x={LEFT_X + 10} y={e.cy + 5} className="sym">
                    {e.token.symbol}
                  </text>
                  <text x={LEFT_X + COL_W - 10} y={e.cy + 5} className="n b" textAnchor="end">
                    {e.wallets_main}
                  </text>
                </g>
              </g>
            )
          })}
          {/* outbound ribbons: this coin → B (right) */}
          {m.outb.map((e) => {
            const key = `${coin.address}->${e.token.address}`
            const f = fresh.get(key)
            const hot = f !== undefined && now - f < 2500
            const x1 = MID_X + NODE_R + 8
            const x2 = RIGHT_X - COL_W
            const d = `M ${x1} ${H / 2} C ${x1 + 170} ${H / 2}, ${x2 - 170} ${e.cy}, ${x2} ${e.cy}`
            return (
              <g key={key} className={`ribbon ${hot ? 'hot' : ''}`} onClick={() => onEdge({ kind: 'edge', from: coin.address, to: e.token.address })}>
                <title>{`${e.wallets_main} wallets sold ${coin.symbol}, then bought ${e.token.symbol} · click for the transactions`}</title>
                <path d={d} fill="none" stroke={hot ? '#FF3344' : '#5a5a5a'} strokeOpacity={hot ? 0.9 : 0.5} strokeWidth={e.wpx} />
                <path d={d} fill="none" stroke={hot ? '#FF3344' : '#A3A3A3'} strokeOpacity={0.9} strokeWidth={1} markerEnd={hot ? 'url(#arrow-hot)' : 'url(#arrow)'} />
                <g className="src" onClick={(ev) => (ev.stopPropagation(), onFocus(e.token.address))}>
                  <title>{`make ${e.token.symbol} the centre`}</title>
                  <rect x={x2} y={e.cy - e.boxH / 2} width={COL_W} height={e.boxH} fill="#0D0A0A" stroke="#351419" />
                  <text x={x2 + 10} y={e.cy + 5} className="sym">
                    {e.token.symbol}
                  </text>
                  <text x={RIGHT_X - 10} y={e.cy + 5} className="n b" textAnchor="end">
                    {e.wallets_main}
                  </text>
                </g>
              </g>
            )
          })}
          {/* centre coin: red = B · BOUGHT, the selected object */}
          <g className="centre">
            <circle cx={MID_X} cy={H / 2} r={NODE_R} fill="#FF3344" />
            <text x={MID_X} y={H / 2 - NODE_R - 18} textAnchor="middle" className="big">
              {coin.symbol}
            </text>
          </g>
          {m.inb.length === 0 && (
            <text x={LEFT_X} y={H / 2} className="n">
              no rotation inflow in this range
            </text>
          )}
          {m.outb.length === 0 && (
            <text x={RIGHT_X} y={H / 2} className="n" textAnchor="end">
              no rotation outflow in this range
            </text>
          )}
        </svg>
      </div>
      <div className="flow-legend">
        Ribbon width = <b>distinct wallets</b> (direct or clean) · arrow = order of trades, sold → bought · click a coin to centre it · click a ribbon for its transactions · same address, observed order; not proof of money flow
      </div>
    </div>
  )
}
