import { useRef } from 'react'
import { duration, utc } from '../format'
import type { SessionState, Status } from '../types'

export type View = 'radar' | 'flow' | 'map'
const VIEWS: View[] = ['radar', 'flow', 'map']
const SPEEDS = [1, 10, 20, 60]

interface Props {
  status: Status | null
  session: SessionState | null
  statusError: string | null
  view: View
  startView: View
  onView: (v: View) => void
  onHome: () => void
  onControl: (action: string, extra?: Record<string, number | undefined>) => void
}

/** Persistent top strip: brand · views · session state · the primary replay controls. Secondary controls live
 *  in each view's working row. */
export default function TopStrip({ status, session, statusError, view, startView, onView, onHome, onControl }: Props) {
  const tabs = useRef<(HTMLButtonElement | null)[]>([])
  const mode = session?.mode ?? status?.mode ?? 'fixture'
  const conn = status?.connection
  const errorText = statusError
    ? `API unreachable · ${statusError}`
    : conn === 'error'
      ? `Provider error · ${status?.live?.last_error ?? 'no connection'} · frozen at the last good block`
      : null
  const paused = mode === 'replay' && !!session && !session.playing
  const badgeClass = mode === 'live' ? (conn === 'stale' ? 'stale' : 'live') : mode === 'replay' ? (paused ? 'paused' : 'replay') : 'fixture'
  const speedTxt = session?.speed ? `${Number.isInteger(session.speed) ? session.speed : session.speed.toFixed(1)}×` : ''
  const badgeText = mode === 'live' ? (conn === 'stale' ? 'LIVE · STALE' : 'LIVE') : mode === 'replay' ? (paused ? `PAUSED · REPLAY ${speedTxt}` : `REPLAY ${speedTxt}`) : 'RECORDED'
  const clock = session?.clock_ts ?? null
  const span = session?.span_s ?? 1800
  const age = status?.data.age_s ?? null
  const speeds = session && !SPEEDS.includes(session.speed) ? [...SPEEDS, session.speed].sort((a, b) => a - b) : SPEEDS

  const onTabKey = (e: React.KeyboardEvent, i: number) => {
    let j = i
    if (e.key === 'ArrowRight') j = (i + 1) % VIEWS.length
    else if (e.key === 'ArrowLeft') j = (i + VIEWS.length - 1) % VIEWS.length
    else if (e.key === 'Home') j = 0
    else if (e.key === 'End') j = VIEWS.length - 1
    else return
    e.preventDefault()
    onView(VIEWS[j])
    tabs.current[j]?.focus()
  }

  return (
    <header className="strip">
      <a
        className="brand"
        href={`?view=${startView}`}
        data-testid="brand"
        aria-label={`STAMPEDE · back to ${startView.toUpperCase()}`}
        title={`Back to ${startView.toUpperCase()} (keeps the clock, filters and selection)`}
        onClick={(e) => {
          e.preventDefault()
          onHome()
        }}
      >
        <img className="mark" src="/brand-bison.png" height={46} alt="" />
        <span className="wordmark">STAMPEDE</span>
        <span className="tagline">wallet rotations · Robinhood Chain</span>
      </a>
      <nav className="views" role="tablist" aria-label="Views">
        {VIEWS.map((v, i) => (
          <button
            key={v}
            ref={(el) => {
              tabs.current[i] = el
            }}
            role="tab"
            aria-selected={view === v}
            tabIndex={view === v ? 0 : -1}
            className={view === v ? 'on' : ''}
            onClick={() => onView(v)}
            onKeyDown={(e) => onTabKey(e, i)}
            data-testid={`view-${v}`}
          >
            <kbd>{i + 1}</kbd>
            {v.toUpperCase()}
          </button>
        ))}
      </nav>
      <div className="status">
        {errorText ? (
          <span className="alert" title={errorText} role="alert">
            {errorText}
          </span>
        ) : (
          <span className={`badge ${badgeClass}`} data-testid="mode">
            {badgeText}
          </span>
        )}
        <span className="clock">
          <span className="k">{mode === 'live' ? 'LAST BLOCK ' : 'CLOCK '}</span>
          <b>{utc(clock)}</b> UTC
        </span>
        {mode === 'live' && age !== null && (
          <span className="clock">
            DATA AGE <b>{duration(age)}</b>
          </span>
        )}
        <span className="clock range">
          RANGE <b>{utc(clock ? clock - span : null)}–{utc(clock)}</b>
        </span>
      </div>
      <div className="right">
        {session?.controls && (
          <>
            <button className={session.playing ? 'on' : 'primary'} onClick={() => onControl('toggle')} aria-label={session.playing ? 'Pause replay' : 'Play replay'} data-testid="play">
              {session.playing ? 'Pause' : 'Play'}
            </button>
            <div className="seg" role="group" aria-label="Replay speed">
              {speeds.map((s) => (
                <button key={s} className={session.speed === s ? 'on' : ''} onClick={() => onControl('speed', { speed: s })} aria-pressed={session.speed === s}>
                  {Number.isInteger(s) ? s : s.toFixed(1)}×
                </button>
              ))}
            </div>
          </>
        )}
      </div>
    </header>
  )
}
