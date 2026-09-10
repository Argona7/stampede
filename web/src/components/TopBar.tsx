import { useEffect, useRef, useState } from 'react'
import { api } from '../client'
import { duration, utc } from '../format'
import type { Selection, Status, TokenLabel } from '../types'

interface Props {
  status: Status | null
  clockTs: number | null
  onSelect: (s: Selection) => void
}

export default function TopBar({ status, clockTs, onSelect }: Props) {
  const [text, setText] = useState('')
  const [hits, setHits] = useState<TokenLabel[]>([])
  const [open, setOpen] = useState(false)
  const timer = useRef<number | null>(null)

  useEffect(() => {
    if (timer.current) window.clearTimeout(timer.current)
    if (text.trim().length < 2) {
      setHits([])
      return
    }
    timer.current = window.setTimeout(() => {
      api
        .search(text)
        .then((h) => {
          setHits(h)
          setOpen(true)
        })
        .catch(() => setHits([]))
    }, 180)
  }, [text])

  const mode = status?.mode ?? 'fixture'
  const conn = status?.connection
  const badgeClass = conn === 'error' ? 'error' : mode
  const badgeText = conn === 'error' ? 'CONNECTION ERROR' : mode.toUpperCase()

  let line1 = ''
  let line2 = ''
  if (status) {
    const s = status.sample
    if (mode === 'live') {
      const lv = status.live
      line1 = lv?.last_ts ? `last block ${lv.last_block}, ${utc(lv.last_ts)} UTC, data age ${duration(status.data.age_s)}` : 'waiting for the first block'
      line2 = lv?.paused ? `paused, map frozen at the last good block: ${lv.last_error ?? 'no connection'}` : `Robinhood Chain head ${lv?.head_block ?? '?'}, source Alchemy, tick ${lv?.tick_ms ?? '?'} ms`
    } else if (mode === 'replay') {
      line1 = `${s.label}: ${utc(s.from_ts, true)} to ${utc(s.to_ts)} UTC`
      line2 = clockTs ? `replay clock ${utc(clockTs)} UTC. Recorded data played back, not the current market.` : 'replay paused'
    } else {
      line1 = `${s.label}: ${utc(s.from_ts, true)} to ${utc(s.to_ts)} UTC`
      line2 = `recorded snapshot, ${duration(status.data.age_s)} old. Not live; nothing here updates.`
    }
  }

  return (
    <header className="top">
      <div className="wordmark">
        STAMPEDE
        <small>Watch wallets move between coins on Robinhood Chain</small>
      </div>
      <div className="search">
        <input
          type="text"
          placeholder="Find a coin by symbol, name or address"
          value={text}
          aria-label="Find a coin"
          onChange={(e) => setText(e.target.value)}
          onFocus={() => hits.length && setOpen(true)}
          onBlur={() => window.setTimeout(() => setOpen(false), 150)}
        />
        {open && hits.length > 0 && (
          <ul>
            {hits.map((h) => (
              <li
                key={h.address}
                onMouseDown={() => {
                  onSelect({ kind: 'token', address: h.address })
                  setOpen(false)
                  setText('')
                }}
              >
                <span>
                  <b>{h.symbol}</b> <span className="muted">{h.name}</span>
                </span>
                <span className="mono faint">{h.short}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="mode">
        <span className={`badge ${badgeClass}`}>{badgeText}</span>
        <span className="line">{line1}</span>
        <span />
        <span className="line">{line2}</span>
      </div>
    </header>
  )
}
