import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from './client'
import Controls, { type Filters } from './components/Controls'
import Details from './components/Details'
import MapCanvas from './components/MapCanvas'
import Ticker from './components/Ticker'
import TopBar from './components/TopBar'
import type { EdgeDetail, Graph, Selection, Status, TokenDetail } from './types'

const POLL_MS = 3000

export default function App() {
  const [status, setStatus] = useState<Status | null>(null)
  const [statusError, setStatusError] = useState<string | null>(null)
  const [graph, setGraph] = useState<Graph | null>(null)
  const [filters, setFilters] = useState<Filters>({ windowS: 1800, spanS: 1800, toTs: null, minWallets: 3, showAmbiguous: false })
  const [selection, setSelection] = useState<Selection>(null)
  const [hover, setHover] = useState<Selection>(null)
  const [edge, setEdge] = useState<EdgeDetail | null>(null)
  const [token, setToken] = useState<TokenDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState<string | null>(null)
  const [focusNonce, setFocusNonce] = useState(0)
  const [fresh, setFresh] = useState<Map<string, number>>(new Map())
  const [freshRows, setFreshRows] = useState<Set<string>>(new Set())
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState(10)
  const seenRows = useRef<Set<string> | null>(null)
  const clockRef = useRef<number | null>(null)
  const inFlight = useRef(false)
  const pendingRef = useRef(false)

  const mode = status?.mode ?? 'fixture'

  // ---- status polling ----
  useEffect(() => {
    let alive = true
    const tick = () =>
      api
        .status()
        .then((s) => {
          if (!alive) return
          setStatus(s)
          setStatusError(null)
          setFilters((f) => (f.windowS === 1800 && s.default_window_s && s.default_window_s !== 1800 ? { ...f, windowS: s.default_window_s } : f))
        })
        .catch((e) => alive && setStatusError(String(e)))
    tick()
    const id = window.setInterval(tick, mode === 'live' ? POLL_MS : 15000)
    return () => {
      alive = false
      window.clearInterval(id)
    }
  }, [mode])

  // sample bounds for the range slider
  const bounds = useMemo(() => {
    if (!status) return null
    const min = status.sample.from_ts ?? status.data.first_ts
    const max = mode === 'live' ? status.data.last_ts : status.sample.to_ts ?? status.data.last_ts
    if (!min || !max) return null
    return { min, max }
  }, [status, mode])

  // initial `to`: end of sample (fixture), start + span (replay), moving head (live)
  useEffect(() => {
    if (!bounds) return
    setFilters((f) => {
      if (mode === 'live') return { ...f, toTs: bounds.max }
      if (f.toTs !== null) return f
      return { ...f, toTs: mode === 'replay' ? Math.min(bounds.max, bounds.min + f.spanS) : bounds.max }
    })
  }, [bounds, mode])

  // ---- replay clock ----
  useEffect(() => {
    if (mode !== 'replay' || !playing || !bounds) return
    let last = performance.now()
    const id = window.setInterval(() => {
      const now = performance.now()
      const dt = ((now - last) / 1000) * speed
      last = now
      setFilters((f) => {
        const cur = f.toTs ?? bounds.min
        const next = Math.min(bounds.max, Math.round(cur + dt))
        if (next >= bounds.max) setPlaying(false)
        return { ...f, toTs: next }
      })
    }, 1000)
    return () => window.clearInterval(id)
  }, [mode, playing, speed, bounds])

  // ---- graph fetch ----
  const toTs = filters.toTs
  const fromTs = toTs !== null ? toTs - filters.spanS : null
  clockRef.current = toTs
  useEffect(() => {
    if (toTs === null && mode !== 'live') return
    let alive = true
    let timer: number | null = null
    const load = () => {
      if (inFlight.current) {
        // one request at a time; the newest range wins when it finishes
        pendingRef.current = true
        return
      }
      inFlight.current = true
      api
        .graph(filters.windowS, fromTs, toTs, filters.minWallets, 260)
        .then((g) => {
          if (!alive) return
          setGraph(g)
          // fresh rows: sequences not seen in the previous fetch (replay/live only)
          const ids = new Set(g.recent.map((r) => `${r.buy_tx}-${r.from}`))
          if (seenRows.current && mode !== 'fixture') {
            const newRows = new Set<string>()
            const newEdges = new Map(fresh)
            const now = performance.now()
            for (const r of g.recent) {
              const id = `${r.buy_tx}-${r.from}`
              if (!seenRows.current.has(id)) {
                newRows.add(id)
                newEdges.set(`${r.from}->${r.to}`, now)
              }
            }
            if (newRows.size) {
              setFreshRows(newRows)
              setFresh(newEdges)
              window.setTimeout(() => setFreshRows(new Set()), 2500)
            }
          }
          seenRows.current = ids
        })
        .catch(() => {})
        .finally(() => {
          inFlight.current = false
          if (pendingRef.current && alive) {
            pendingRef.current = false
            load()
          }
        })
    }
    // debounce: the range slider and the replay clock change `to` often
    timer = window.setTimeout(load, mode === 'replay' ? 250 : 120)
    if (mode === 'live') {
      const id = window.setInterval(load, POLL_MS)
      return () => {
        alive = false
        if (timer) window.clearTimeout(timer)
        window.clearInterval(id)
      }
    }
    return () => {
      alive = false
      if (timer) window.clearTimeout(timer)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters.windowS, filters.minWallets, fromTs, toTs, mode, status?.data.last_block])

  // ---- details ----
  useEffect(() => {
    if (!selection) {
      setEdge(null)
      setToken(null)
      return
    }
    let alive = true
    setDetailLoading(true)
    setDetailError(null)
    const p =
      selection.kind === 'edge'
        ? api.edge(selection.from, selection.to, filters.windowS, fromTs, toTs).then((d) => alive && (setEdge(d), setToken(null)))
        : api.token(selection.address, filters.windowS, fromTs, toTs).then((d) => alive && (setToken(d), setEdge(null)))
    p.catch((e) => alive && setDetailError(String(e))).finally(() => alive && setDetailLoading(false))
    return () => {
      alive = false
    }
  }, [selection, filters.windowS, fromTs, toTs])

  const select = useCallback((s: Selection) => {
    setSelection(s)
    if (s) setFocusNonce((n) => n + 1)
  }, [])

  const replay =
    mode === 'replay' && bounds
      ? {
          playing,
          speed,
          setPlaying,
          setSpeed,
          reset: () => {
            setPlaying(false)
            seenRows.current = null
            setFilters((f) => ({ ...f, toTs: Math.min(bounds.max, bounds.min + f.spanS) }))
          },
        }
      : null

  const stale = mode !== 'live'
  const connError = status?.connection === 'error'

  return (
    <div className="app">
      <TopBar status={status} clockTs={mode === 'replay' ? toTs : null} onSelect={select} />
      <main className="main">
        <Controls status={status} graph={graph} filters={filters} setFilters={setFilters} bounds={bounds} replay={replay} />
        <section className="map" aria-label="Rotation map">
          <MapCanvas nodes={graph?.nodes ?? []} edges={graph?.edges ?? []} selection={selection} showAmbiguous={filters.showAmbiguous} fresh={fresh} onSelect={select} onHover={setHover} focusNonce={focusNonce} />
          {stale && status && (
            <div className="stale">{mode === 'replay' ? `REPLAY · recorded ${status.sample.label}` : `RECORDED · ${status.sample.label}`}</div>
          )}
          {connError && <div className="stale" style={{ color: 'var(--err)', top: 32 }}>Connection error: the map shows the last good block and is not updating.</div>}
          {statusError && <div className="overlay">API not reachable: {statusError}</div>}
          {!statusError && graph && graph.edges.length === 0 && <div className="overlay">No edges with at least {filters.minWallets} wallets in this range. Lower the minimum or widen the range.</div>}
          <div className="legend">
            <span>
              <b>Edge A → B</b>: distinct wallets that sold A, then bought B within {filters.windowS / 60} min. Line width = number of wallets.
              {filters.showAmbiguous ? ' Dashed lines have only ambiguous sequences.' : ''}
            </span>
            <span>
              {hover?.kind === 'edge' ? `${graph?.nodes.find((n) => n.address === hover.from)?.symbol ?? ''} → ${graph?.nodes.find((n) => n.address === hover.to)?.symbol ?? ''}` : hover?.kind === 'token' ? graph?.nodes.find((n) => n.address === hover.address)?.symbol : 'Scroll to zoom, drag to pan'}
            </span>
          </div>
        </section>
        <Details selection={selection} edge={edge} token={token} loading={detailLoading} error={detailError} onSelect={select} />
      </main>
      <Ticker status={status} recent={graph?.recent ?? []} freshKeys={freshRows} onSelect={select} />
    </div>
  )
}
