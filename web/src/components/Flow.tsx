// FLOW: the ego network of one coin. Sources (coins wallets sold before buying this one) on the left,
// the coin in the middle, destinations (where its sellers went next) on the right. Ribbon width = distinct
// wallets. Readable by construction: no overlaps, labels always next to their ribbon.
import { useEffect, useMemo, useState } from 'react'
import { duration, windowName } from '../format'
import type { CoinDetail, Selection } from '../types'

interface Props {
  coin: CoinDetail | null
  loading: boolean
  onFocus: (address: string) => void
  onEdge: (s: Selection) => void
  fresh: Map<string, number> // edge key -> performance.now()
  windowS: number
}

const W = 1200
const H = 720
const LEFT_X = 40
const MID_X = W / 2
const RIGHT_X = W - 40
const COL_W = 260

export default function Flow({ coin, loading, onFocus, onEdge, fresh, windowS }: Props) {
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
    const width = (w: number) => 3 + (w / maxW) * 46
    const place = (list: typeof inb) => {
      const total = list.reduce((a, e) => a + width(e.wallets_main) + 18, 0)
      let y = H / 2 - total / 2
      return list.map((e) => {
        const wpx = width(e.wallets_main)
        const cy = y + wpx / 2
        y += wpx + 18
        return { ...e, wpx, cy }
      })
    }
    return { inb: place(inb), outb: place(outb), maxW, totalIn: inb.reduce((a, e) => a + e.wallets_main, 0), totalOut: outb.reduce((a, e) => a + e.wallets_main, 0) }
  }, [coin])

  if (!coin) {
    return <div className="flow-empty">{loading ? 'Loading coin…' : 'Pick a coin in the radar (or click a node in the map) to see where its wallets came from and where they went.'}</div>
  }
  const m = model!
  const nodeR = 46
  return (
    <div className="flow">
      <div className="flow-head">
        <div>
          <b>{m.totalIn}</b> wallets rotated <span className="to">into</span> <b>{coin.symbol}</b> from {m.inb.length} coins · then <b>{m.totalOut}</b> rotated out to {m.outb.length} coins · pairing window {windowName(windowS)}
        </div>
        <div className="faint">Ribbon width = distinct wallets (direct/clean). Click a coin to make it the centre; click a ribbon for the transactions. Same address, observed order of trades; not proof of money flow.</div>
      </div>
      <svg className="flow-svg" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid meet" role="img" aria-label={`Rotation flow around ${coin.symbol}`}>
        <defs>
          <marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="#FF3344" />
          </marker>
        </defs>
        {/* inbound ribbons */}
        {m.inb.map((e) => {
          const key = `${e.token.address}->${coin.address}`
          const f = fresh.get(key)
          const hot = f !== undefined && now - f < 2500
          const x1 = LEFT_X + COL_W
          const x2 = MID_X - nodeR - 6
          const d = `M ${x1} ${e.cy} C ${x1 + 160} ${e.cy}, ${x2 - 160} ${H / 2}, ${x2} ${H / 2}`
          return (
            <g key={key} className={`ribbon ${hot ? 'hot' : ''}`} onClick={() => onEdge({ kind: 'edge', from: e.token.address, to: coin.address })}>
              <path d={d} fill="none" stroke={hot ? '#FF3344' : '#7a7a7a'} strokeOpacity={hot ? 0.95 : 0.55} strokeWidth={e.wpx} strokeLinecap="butt" />
              <path d={d} fill="none" stroke="#FF3344" strokeOpacity={0.9} strokeWidth={1.2} markerEnd="url(#arrow)" />
              <g className="src" onClick={(ev) => (ev.stopPropagation(), onFocus(e.token.address))}>
                <rect x={LEFT_X} y={e.cy - 17} width={COL_W} height={34} fill="#0D0A0A" stroke="#351419" />
                <text x={LEFT_X + 12} y={e.cy + 5} className="sym">
                  {e.token.symbol}
                </text>
                <text x={LEFT_X + COL_W - 12} y={e.cy + 5} className="n" textAnchor="end">
                  {e.wallets_main} wallets
                </text>
              </g>
            </g>
          )
        })}
        {/* outbound ribbons */}
        {m.outb.map((e) => {
          const key = `${coin.address}->${e.token.address}`
          const f = fresh.get(key)
          const hot = f !== undefined && now - f < 2500
          const x1 = MID_X + nodeR + 6
          const x2 = RIGHT_X - COL_W
          const d = `M ${x1} ${H / 2} C ${x1 + 160} ${H / 2}, ${x2 - 160} ${e.cy}, ${x2} ${e.cy}`
          return (
            <g key={key} className={`ribbon ${hot ? 'hot' : ''}`} onClick={() => onEdge({ kind: 'edge', from: coin.address, to: e.token.address })}>
              <path d={d} fill="none" stroke={hot ? '#FF3344' : '#5a5a5a'} strokeOpacity={hot ? 0.95 : 0.5} strokeWidth={e.wpx} />
              <path d={d} fill="none" stroke="#A3A3A3" strokeOpacity={0.8} strokeWidth={1} markerEnd="url(#arrow)" />
              <g className="src" onClick={(ev) => (ev.stopPropagation(), onFocus(e.token.address))}>
                <rect x={x2} y={e.cy - 17} width={COL_W} height={34} fill="#0D0A0A" stroke="#351419" />
                <text x={x2 + 12} y={e.cy + 5} className="sym">
                  {e.token.symbol}
                </text>
                <text x={RIGHT_X - 12} y={e.cy + 5} className="n" textAnchor="end">
                  {e.wallets_main} wallets
                </text>
              </g>
            </g>
          )
        })}
        {/* centre coin */}
        <g className="centre">
          <circle cx={MID_X} cy={H / 2} r={nodeR} fill="#FF3344" />
          <text x={MID_X} y={H / 2 - nodeR - 46} textAnchor="middle" className="role">
            B · BOUGHT
          </text>
          <text x={MID_X} y={H / 2 - nodeR - 14} textAnchor="middle" className="big">
            {coin.symbol}
          </text>
          <text x={MID_X} y={H / 2 + nodeR + 26} textAnchor="middle" className="n">
            {coin.short} · {coin.age_s !== null ? `${duration(coin.age_s)} old` : ''} · {coin.progress.stage === 'curve' && coin.progress.progress !== null ? `curve ${Math.round(coin.progress.progress * 100)}%` : coin.progress.stage}
          </text>
          <text x={MID_X} y={H / 2 + nodeR + 48} textAnchor="middle" className="n">
            {coin.buyers} buyers · {coin.new_buyers} new · price {coin.as_of.chg_1h !== null ? `${coin.as_of.chg_1h > 0 ? '+' : ''}${coin.as_of.chg_1h.toFixed(0)}% 1h` : '—'}
          </text>
        </g>
        {m.inb.length === 0 && (
          <text x={LEFT_X + 20} y={H / 2} className="n">
            no rotation inflow in range
          </text>
        )}
        {m.outb.length === 0 && (
          <text x={RIGHT_X - COL_W + 20} y={H / 2} className="n">
            no rotation outflow in range
          </text>
        )}
      </svg>
    </div>
  )
}
