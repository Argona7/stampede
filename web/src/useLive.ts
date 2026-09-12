// React side of the SSE client: one connection while `enabled`, status + lag as state, events to a handler ref
// (handlers can change every render without reconnecting). A page that gets `unavailable` keeps polling as before.
import { useEffect, useRef, useState } from 'react'
import { openLive, type LiveClient, type LiveStatus } from './live'
import type { StreamEvent } from './types'

export interface LiveState {
  status: LiveStatus
  detail: string | null
  /** client-observed lag of the newest frame, ms (server publish stamp → arrival), refreshed once a second */
  clientLagMs: number | null
  gapNonce: number // bumps when the server reported a replay gap: refetch snapshots
}

const OFF: LiveState = { status: 'off', detail: null, clientLagMs: null, gapNonce: 0 }

export function useLive(enabled: boolean, onEvent: (ev: StreamEvent) => void): LiveState {
  const handler = useRef(onEvent)
  useEffect(() => {
    handler.current = onEvent
  }, [onEvent])
  const [state, setState] = useState<LiveState>(OFF)
  useEffect(() => {
    if (!enabled) return
    let alive = true
    const client: LiveClient = openLive({
      onEvent: (ev) => {
        if (alive) handler.current(ev)
      },
      onStatus: (status, detail) => {
        if (alive) setState((s) => (s.status === status && (detail ?? null) === s.detail ? s : { ...s, status, detail: detail ?? null }))
      },
      onGap: () => {
        if (alive) setState((s) => ({ ...s, gapNonce: s.gapNonce + 1 }))
      },
    })
    const id = window.setInterval(() => {
      if (!alive) return
      const lag = client.clientLagS()
      setState((s) => {
        const ms = lag === null ? null : Math.max(0, Math.round(lag * 1000))
        return s.clientLagMs === ms ? s : { ...s, clientLagMs: ms }
      })
    }, 1000)
    return () => {
      alive = false
      window.clearInterval(id)
      client.close()
    }
  }, [enabled])
  return enabled ? state : { ...OFF, gapNonce: state.gapNonce }
}
