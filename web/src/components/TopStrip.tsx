import { useRef } from 'react'
import { duration, utc } from '../format'
import { fmtLag, type LiveStatus } from '../live'
import type { SessionState, Status } from '../types'

export type View = 'radar' | 'flow' | 'map' | 'traders' | 'signals'
const VIEWS: View[] = ['radar', 'flow', 'map', 'traders', 'signals']
const SPEEDS = [1, 10, 20, 60]

export interface LiveBadge {
  status: LiveStatus
  /** engine head → last event published, p50 ms from /api/perf (the programme number); null without an engine */
  lagMs: number | null
  /** publish stamp → arrival of the newest frame at this client, ms */
  clientLagMs: number | null
}

interface Props {
  status: Status | null
  session: SessionState | null
  statusError: string | null
  view: View
  startView: View
  live?: LiveBadge
  onView: (v: View) => void
  onHome: () => void
  onControl: (action: string, extra?: Record<string, number | undefined>) => void
}

/** Persistent top strip: brand · views · session state · the primary replay controls. Secondary controls live
 *  in each view's working row. In live mode the badge carries the stream state: `LIVE · 120 ms` (engine head → event
 *  latency, one small number), `LIVE · RECONNECTING`, `LIVE · STALE`; without an engine the modes read as before. */
export default function TopStrip({ status, session, statusError, view, startView, live, onView, onHome, onControl }: Props) {
  const tabs = useRef<(HTMLButtonElement | null)[]>([])
  const mode = session?.mode ?? status?.mode ?? 'fixture'
  const conn = status?.connection
  const errorText = statusError
    ? `API unreachable · ${statusError}`
    : conn === 'error'
      ? `Provider error · ${status?.live?.last_error ?? 'no connection'} · frozen at the last good block`
      : null
  const paused = mode === 'replay' && !!session && !session.playing
  const reconnecting = mode === 'live' && live?.status === 'reconnecting'
  const badgeClass = mode === 'live' ? (conn === 'stale' ? 'stale' : reconnecting ? 'reconnecting' : 'live') : mode === 'replay' ? (paused ? 'paused' : 'replay') : 'fixture'
  const speedTxt = session?.speed ? `${Number.isInteger(session.speed) ? session.speed : session.speed.toFixed(1)}×` : ''
  const lag = live?.lagMs ?? live?.clientLagMs ?? null
  const liveText = conn === 'stale' ? 'LIVE · STALE' : reconnecting ? 'LIVE · RECONNECTING' : live?.status === 'live' && lag !== null ? `LIVE · ${fmtLag(lag)}` : 'LIVE'
  const badgeText = mode === 'live' ? liveText : mode === 'replay' ? (paused ? `PAUSED · REPLAY ${speedTxt}` : `REPLAY ${speedTxt}`) : 'RECORDED'
  const badgeTitle =
    mode === 'live'
      ? live?.status === 'live'
        ? `stream connected · engine head → last event p50 ${live.lagMs !== null ? fmtLag(live.lagMs) : 'n/a'} · this client receives frames ${live.clientLagMs !== null ? fmtLag(live.clientLagMs) : 'n/a'} after publish`
        : reconnecting
          ? 'the event stream dropped; the browser is reconnecting with Last-Event-ID, missed events replay from the ring'
          : live?.status === 'unavailable'
            ? 'no event stream in this process (polling tail): views poll'
            : 'connecting to /api/stream'
      : mode === 'replay'
        ? 'recorded sample played back on the shared session clock; views poll'
        : 'recorded sample, not live'
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
          <span className={`badge ${badgeClass}`} data-testid="mode" title={badgeTitle} data-live={mode === 'live' ? live?.status ?? 'off' : 'n/a'}>
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
