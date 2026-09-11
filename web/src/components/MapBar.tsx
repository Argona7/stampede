import { utc, windowName } from '../format'
import type { Selection, SessionState } from '../types'
import Search from './Search'

interface Props {
  session: SessionState | null
  clock: number | null
  spanS: number
  windowS: number
  edgesShown: number
  edgesTotal: number
  renderer: '3d' | '2d'
  selection: Selection
  onRenderer: (r: '3d' | '2d') => void
  onLayout: () => void
  onControl: (action: string, extra?: Record<string, number | undefined>) => void
  onPick: (address: string) => void
  onBack: () => void
}

/** MAP working row (explore layout): the visible range, what is drawn, seek steps, renderer and layout. */
export default function MapBar({ session, clock, spanS, windowS, edgesShown, edgesTotal, renderer, selection, onRenderer, onLayout, onControl, onPick, onBack }: Props) {
  const live = session?.mode === 'live'
  return (
    <div className="workbar" data-testid="map-bar">
      <span className="stat">
        RANGE <b>{utc(clock !== null ? clock - spanS : null)}–{utc(clock)}</b> UTC
      </span>
      <span className="sep" />
      <span className="stat">
        WINDOW <b>{windowName(windowS)}</b>
      </span>
      <span className="sep" />
      <span className="stat" data-testid="edges-shown">
        SHOWING <b>{edgesShown}</b> OF <b>{edgesTotal}</b> FLOWS
      </span>
      {session?.controls && !live && (
        <div className="seg" role="group" aria-label="Seek">
          <button onClick={() => clock !== null && onControl('seek', { ts: clock - 300 })} aria-label="Seek back 5 minutes">
            −5 min
          </button>
          <button onClick={() => clock !== null && onControl('seek', { ts: clock + 300 })} aria-label="Seek forward 5 minutes">
            +5 min
          </button>
          <button onClick={() => session.from_ts !== null && onControl('seek', { ts: session.from_ts + spanS })} title="Seek to the first full range of the sample">
            Start
          </button>
        </div>
      )}
      <span className="grow" />
      {selection && <button onClick={onBack}>Back to overview (Esc)</button>}
      <Search onPick={onPick} placeholder="find a coin" />
      <button className={renderer === '3d' ? 'on' : ''} onClick={() => onRenderer(renderer === '3d' ? '2d' : '3d')} aria-label="Toggle renderer" title="3D WebGL scene or the 2D canvas fallback">
        {renderer === '3d' ? '3D' : '2D'}
      </button>
      <button onClick={onLayout} aria-label="Toggle presentation layout" data-testid="layout-toggle">
        Present (P)
      </button>
    </div>
  )
}
