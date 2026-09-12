// Live client for GET /api/stream (Server-Sent Events, docs/ENGINE.md).
//
// One EventSource per page. The browser resends `Last-Event-ID` on its own reconnects, so a hiccup replays the
// missed events from the server's ring; when the hello says `replay_gap`, the owner refetches /api/radar and
// /api/alerts before trusting deltas again. The first frame of every connection is a `session` hello: when it says
// the process runs no engine (fixture, replay, `--feed alchemy`), the client closes and reports `unavailable`, and the
// views keep polling exactly as before this file existed. Nothing here touches React: `useLive` wraps it.
import type { RadarDelta, RadarRow, StreamEvent, StreamType } from './types'
import type { RadarFilters } from './radarFilters'

export type LiveStatus = 'off' | 'connecting' | 'live' | 'reconnecting' | 'unavailable'

export interface LiveHandlers {
  onEvent: (ev: StreamEvent) => void
  onStatus: (status: LiveStatus, detail?: string) => void
  onGap?: () => void
}

export const STREAM_TYPES: StreamType[] = ['block', 'trade', 'sequence', 'radar_delta', 'alert', 'verdict', 'position', 'session']

export interface LiveClient {
  close: () => void
  lastId: () => number | null
  /** seconds between the server's publish stamp and the arrival of the newest frame (same host: clock skew ~0) */
  clientLagS: () => number | null
}

export function openLive(handlers: LiveHandlers, types: StreamType[] | null = null, url = '/api/stream'): LiveClient {
  let lastId: number | null = null
  let lastLag: number | null = null
  let closed = false
  let sawHello = false
  const q = types ? `?types=${types.join(',')}` : ''
  const es = new EventSource(url + q)
  handlers.onStatus('connecting')
  const onFrame = (e: MessageEvent) => {
    let ev: StreamEvent
    try {
      ev = JSON.parse(e.data as string) as StreamEvent
    } catch {
      return
    }
    if (ev.id !== null && ev.id !== undefined) lastId = ev.id
    if (typeof ev.ts_emit === 'number') lastLag = Date.now() / 1000 - ev.ts_emit
    if (ev.type === 'session') {
      const d = ev.data as { hello?: boolean; engine?: string; replay_gap?: boolean }
      if (d.hello) {
        sawHello = true
        if (d.engine !== 'wss') {
          // no realtime engine in this process: the stream only carries hellos and keepalives, polling stays in charge
          es.close()
          closed = true
          handlers.onStatus('unavailable', `engine ${d.engine ?? 'none'}`)
          return
        }
        if (d.replay_gap) handlers.onGap?.()
      }
      handlers.onStatus('live')
    }
    handlers.onEvent(ev)
  }
  for (const t of STREAM_TYPES) es.addEventListener(t, onFrame as EventListener)
  es.onopen = () => {
    if (!closed) handlers.onStatus(sawHello ? 'live' : 'connecting')
  }
  es.onerror = () => {
    if (closed) return
    // CONNECTING: the browser is retrying with Last-Event-ID; CLOSED: it gave up (server down for good, or a fatal response)
    if (es.readyState === EventSource.CLOSED) {
      closed = true
      handlers.onStatus('unavailable', 'stream closed')
    } else handlers.onStatus('reconnecting')
  }
  return {
    close: () => {
      closed = true
      es.close()
      handlers.onStatus('off')
    },
    lastId: () => lastId,
    clientLagS: () => lastLag,
  }
}

// ---- RADAR rows from radar_delta ----------------------------------------------------------------------------------
/** Rows keyed by address; `apply` folds one delta in and returns the ranked order (the server's `top`, first 50). */
export class LiveRadar {
  rows = new Map<string, RadarRow>()
  top: string[] = []
  clock: number | null = null
  spanS = 1800
  deltas = 0

  apply(d: RadarDelta): void {
    if (d.full) this.rows.clear()
    for (const r of d.rows) this.rows.set(r.address, r)
    for (const a of d.removed) this.rows.delete(a)
    if (d.top) this.top = d.top
    this.clock = d.clock
    this.spanS = d.span_s
    this.deltas++
  }

  ranked(): RadarRow[] {
    const out: RadarRow[] = []
    for (const a of this.top) {
      const r = this.rows.get(a)
      if (r) out.push(r)
    }
    return out
  }

  reset(): void {
    this.rows.clear()
    this.top = []
    this.deltas = 0
  }
}

/** The preset / filter semantics of /api/radar (stampede/api/radar.py PRESETS) applied to streamed rows. */
export function filterLiveRows(rows: RadarRow[], f: RadarFilters): RadarRow[] {
  let out = rows.filter((r) => r.wallets_range >= f.minWallets)
  if (f.stage) out = out.filter((r) => r.stage === f.stage)
  if (f.ageMax !== null) out = out.filter((r) => r.age_s !== null && r.age_s <= f.ageMax!)
  if (f.mentionsMax !== null) out = out.filter((r) => r.mentions_1h === null || r.mentions_1h <= f.mentionsMax!)
  if (f.qualityMin !== null) out = out.filter((r) => r.quality !== null && r.quality >= f.qualityMin!)
  if (f.preset === 'graduating') out = out.filter((r) => r.stage === 'curve' && (r.progress ?? 0) >= 0.6)
  if (f.preset === 'clean_launch') out = out.filter((r) => !!r.launch?.observed && (r.launch.bundle_n ?? 99) <= 2 && (r.launch.dev_buy_share ?? 1) <= 0.05 && !r.launch.launch_farm)
  const key = f.sort
  const val = (r: RadarRow): number => {
    switch (key) {
      case 'inflow':
        return r.inflow_10m
      case 'accel':
        return r.accel
      case 'age':
        return r.age_s === null ? -Infinity : -r.age_s
      case 'mentions':
        return r.mentions_1h === null ? -Infinity : -r.mentions_1h
      case 'progress':
        return r.progress ?? -Infinity
      case 'quality':
        return r.quality ?? -Infinity
      default:
        return r.score
    }
  }
  if (key !== 'score') out = [...out].sort((a, b) => val(b) - val(a) || b.score - a.score)
  return out
}

export const fmtLag = (ms: number | null | undefined): string => (ms === null || ms === undefined ? '' : ms >= 10_000 ? `${(ms / 1000).toFixed(0)} s` : ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms)} ms`)
