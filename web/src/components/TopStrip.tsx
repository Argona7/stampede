import { useEffect, useRef, useState } from 'react'
import { api } from '../client'
import { duration, utc, windowName } from '../format'
import type { Selection, SessionState, Status, TokenLabel } from '../types'

interface Props {
  status: Status | null
  session: SessionState | null
  statusError: string | null
  layout: 'explore' | 'presentation'
  renderer: '3d' | '2d'
  edgesShown: number
  edgesTotal: number
  onLayout: (l: 'explore' | 'presentation') => void
  onRenderer: (r: '3d' | '2d') => void
  onControl: (action: string, extra?: Record<string, number | undefined>) => void
  onSelect: (s: Selection) => void
  view: 'radar' | 'flow' | 'map'
  onView: (v: 'radar' | 'flow' | 'map') => void
}

export default function TopStrip({ status, session, statusError, layout, renderer, edgesShown, edgesTotal, onLayout, onRenderer, onControl, onSelect, view, onView }: Props) {
  const [text, setText] = useState('')
  const [hits, setHits] = useState<TokenLabel[]>([])
  const [open, setOpen] = useState(false)
  const timer = useRef<number | null>(null)
  useEffect(() => {
    if (timer.current) window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => {
      if (text.trim().length < 2) {
        setHits([])
        return
      }
      api
        .search(text)
        .then((h) => {
          setHits(h)
          setOpen(true)
        })
        .catch(() => setHits([]))
    }, 180)
  }, [text])

  const mode = session?.mode ?? status?.mode ?? 'fixture'
  const conn = status?.connection
  const errorText = statusError ? `API UNREACHABLE · ${statusError}` : conn === 'error' ? `PROVIDER ERROR · ${status?.live?.last_error ?? 'no connection'} · frozen at the last good block` : null
  const paused = mode === 'replay' && session && !session.playing
  const badgeClass = mode === 'live' ? (conn === 'stale' ? 'stale' : 'live') : mode === 'replay' ? (paused ? 'paused' : 'replay') : 'fixture'
  const badgeText = mode === 'live' ? (conn === 'stale' ? 'LIVE · STALE' : 'LIVE') : mode === 'replay' ? (paused ? `REPLAY ${session?.speed ?? ''}× · PAUSED` : `REPLAY ${session?.speed ?? ''}×`) : 'FIXTURE'
  const clock = session?.clock_ts ?? null
  const span = session?.span_s ?? 1800
  const age = status?.data.age_s ?? null

  return (
    <header className="strip">
      <div className="wordmark">
        STAMPEDE
        <small>wallet rotations · Robinhood Chain</small>
      </div>
      <nav className="views" aria-label="Views">
        {(['radar', 'flow', 'map'] as const).map((v, i) => (
          <button key={v} className={view === v ? 'on' : ''} onClick={() => onView(v)} data-testid={`view-${v}`}>
            {i + 1} {v.toUpperCase()}
          </button>
        ))}
      </nav>
      <div className="mid">
        {errorText ? <span className="alert">{errorText}</span> : <span className={`badge ${badgeClass}`}>{badgeText}</span>}
        <span>
          {mode === 'live' ? 'LAST BLOCK' : 'CLOCK'} <b>{utc(clock)} UTC</b>
        </span>
        {mode === 'live' && age !== null && <span>DATA AGE <b>{duration(age)}</b></span>}
        <span>
          RANGE <b>{utc(clock ? clock - span : null)}–{utc(clock)}</b>
        </span>
        <span>
          WINDOW <b>{windowName(session?.window_s ?? 1800)}</b>
        </span>
        {view === 'map' && (
          <span>
            SHOWING <b>{edgesShown}</b> OF <b>{edgesTotal}</b> EDGES
          </span>
        )}
        {session?.id && <span className="faint">SESSION {session.id}</span>}
        <div className="search">
          <input
            type="text"
            placeholder="find a coin: symbol, name or address"
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
                  <span className="faint">{h.short}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
      <div className="right">
        {session?.controls && (
          <>
            <button onClick={() => onControl('toggle')} aria-label={session.playing ? 'Pause replay' : 'Play replay'}>
              {session.playing ? 'Pause' : 'Play'}
            </button>
            {[1, 10, 60].map((s) => (
              <button key={s} className={session.speed === s ? 'on' : ''} onClick={() => onControl('speed', { speed: s })}>
                {s}×
              </button>
            ))}
            <button onClick={() => clock !== null && onControl('seek', { ts: clock - 300 })} aria-label="Seek back 5 minutes">
              −5m
            </button>
            <button onClick={() => clock !== null && onControl('seek', { ts: clock + 300 })} aria-label="Seek forward 5 minutes">
              +5m
            </button>
          </>
        )}
        {view === 'map' && (
          <>
            <button className={renderer === '3d' ? 'on' : ''} onClick={() => onRenderer(renderer === '3d' ? '2d' : '3d')} aria-label="Toggle renderer">
              {renderer === '3d' ? '3D' : '2D'}
            </button>
            <button className={layout === 'presentation' ? 'on' : ''} onClick={() => onLayout(layout === 'presentation' ? 'explore' : 'presentation')} aria-label="Toggle presentation layout" data-testid="layout-toggle">
              {layout === 'presentation' ? 'Explore (P)' : 'Present (P)'}
            </button>
          </>
        )}
      </div>
    </header>
  )
}
