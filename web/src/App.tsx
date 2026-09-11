import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from './client'
import Caption from './components/Caption'
import CoinDrawer from './components/CoinDrawer'
import Flow from './components/Flow'
import MapHud from './components/MapHud'
import Radar from './components/Radar'
import { DEFAULT_RADAR, type RadarFilters } from './radarFilters'
import Controls, { type Filters } from './components/Controls'
import Details, { SequenceRow } from './components/Details'
import Tape, { type TapeApi } from './components/Tape'
import MapCanvas from './components/MapCanvas'
import Ticker from './components/Ticker'
import TopStrip from './components/TopStrip'
import Scene3D, { type PerfStats, type Pulse, type ViewState } from './scene/Scene3D'
import { sessionApi } from './session'
import { utc, windowName } from './format'
import type { AlertsResponse, CoinDetail, EdgeDetail, Graph, RadarResponse, Recent, Selection, SeqEvent, SessionState, Status, TokenDetail } from './types'

const SESSION_POLL_MS = 1000
const EVENTS_POLL_MS = 1500
const STATUS_POLL_MS = 5000
const TOUR_MS = 7000

const prefersReducedMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches

export default function App() {
  const params = new URLSearchParams(window.location.search)
  const [status, setStatus] = useState<Status | null>(null)
  const [statusError, setStatusError] = useState<string | null>(null)
  const [session, setSession] = useState<SessionState | null>(null)
  const [graph, setGraph] = useState<Graph | null>(null)
  const [filters, setFilters] = useState<Filters>({ windowS: 1800, spanS: 1800, toTs: null, minWallets: 3, showAmbiguous: false, limit: Number(params.get('top') ?? 300) })
  const [selection, setSelection] = useState<Selection>(null)
  const [hover, setHover] = useState<Selection>(null)
  const [edge, setEdge] = useState<EdgeDetail | null>(null)
  const [token, setToken] = useState<TokenDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState<string | null>(null)
  const [focusNonce, setFocusNonce] = useState(0)
  const [pulses, setPulses] = useState<Pulse[]>([])
  const [freshRows, setFreshRows] = useState<Set<string>>(new Set())
  const [feed, setFeed] = useState<SeqEvent[]>([])
  const [layout, setLayout] = useState<'explore' | 'presentation'>(params.get('layout') === 'presentation' ? 'presentation' : 'explore')
  const [renderer, setRenderer] = useState<'3d' | '2d'>(params.get('renderer') === '2d' ? '2d' : '3d')
  const [viewState, setViewState] = useState<ViewState>('overview')
  const [evidenceOpen, setEvidenceOpen] = useState(false)
  const [perf, setPerf] = useState<PerfStats | null>(null)
  const [reduced] = useState(prefersReducedMotion() || params.get('motion') === 'reduce')
  const cursorRef = useRef<string | null>(null)
  const tapeRef = useRef<TapeApi | null>(null)
  const [revealAt, setRevealAt] = useState<number | null>(null)
  const [showTape, setShowTape] = useState(params.get('tape') !== '0')
  const lastClockRef = useRef<{ clock: number; wall: number; id: string } | null>(null)
  const inFlight = useRef(false)
  const showPerf = params.get('perf') === '1'
  // ---- radar / flow / coin ----
  const initialView = (params.get('view') as 'radar' | 'flow' | 'map' | null) ?? 'radar'
  const [view, setView] = useState<'radar' | 'flow' | 'map'>(initialView === 'flow' || initialView === 'map' ? initialView : 'radar')
  const [radarFilters, setRadarFilters] = useState<RadarFilters>(DEFAULT_RADAR)
  const [radarData, setRadarData] = useState<RadarResponse | null>(null)
  const [alerts, setAlerts] = useState<AlertsResponse | null>(null)
  const [coinAddr, setCoinAddr] = useState<string | null>(params.get('coin'))
  const [coin, setCoin] = useState<CoinDetail | null>(null)
  const [coinLoading, setCoinLoading] = useState(false)
  const [coinError, setCoinError] = useState<string | null>(null)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [freshTokens, setFreshTokens] = useState<Map<string, number>>(new Map())
  const [freshEdges, setFreshEdges] = useState<Map<string, number>>(new Map())
  const prevInflow = useRef<Map<string, number>>(new Map())
  const coinNonce = useRef(0)

  const mode = session?.mode ?? status?.mode ?? 'fixture'

  // ---- status (coverage, live health) ----
  useEffect(() => {
    let alive = true
    const tick = () =>
      api
        .status()
        .then((s) => {
          if (!alive) return
          setStatus(s)
          setStatusError(null)
        })
        .catch((e) => alive && setStatusError(String(e.message ?? e)))
    tick()
    const id = window.setInterval(tick, STATUS_POLL_MS)
    return () => {
      alive = false
      window.clearInterval(id)
    }
  }, [])

  // ---- shared session clock (server-side, same for TUI and web) ----
  useEffect(() => {
    let alive = true
    const tick = () =>
      sessionApi
        .get()
        .then((s) => {
          if (!alive) return
          setSession(s)
          setStatusError(null)
          setFilters((f) => (f.windowS !== s.window_s || f.spanS !== s.span_s ? { ...f, windowS: s.window_s, spanS: s.span_s } : f))
        })
        .catch((e) => alive && setStatusError(String(e.message ?? e)))
    tick()
    const id = window.setInterval(tick, SESSION_POLL_MS)
    return () => {
      alive = false
      window.clearInterval(id)
    }
  }, [])

  const control = useCallback((action: string, extra: Record<string, number | undefined> = {}) => {
    sessionApi
      .control(action, extra)
      .then(setSession)
      .catch((e) => setStatusError(String(e.message ?? e)))
  }, [])

  // the visible range ends at the shared clock: replay/fixture clock, or in live mode the last block the tail
  // indexed (null until the first live block, so an old sample is never shown as if it were live)
  const liveLastTs = mode === 'live' ? session?.clock_ts ?? null : status?.data.last_ts ?? null
  const toTs = mode === 'live' ? liveLastTs : session?.clock_ts ?? null
  const fromTs = toTs !== null ? toTs - filters.spanS : null

  // ---- graph snapshot for the range ----
  useEffect(() => {
    if (toTs === null) return
    let alive = true
    const t = window.setTimeout(() => {
      if (inFlight.current) return
      inFlight.current = true
      api
        .graph(filters.windowS, fromTs, toTs, filters.minWallets, filters.limit)
        .then((g) => alive && setGraph(g))
        .catch(() => {})
        .finally(() => {
          inFlight.current = false
        })
    }, 150)
    return () => {
      alive = false
      window.clearTimeout(t)
    }
  }, [filters.windowS, filters.minWallets, filters.limit, fromTs, toTs])

  // ---- event stream: history after a seek, new events otherwise (pulses only for new) ----
  useEffect(() => {
    if (!session) return
    let alive = true
    const poll = async () => {
      const clock = mode === 'live' ? liveLastTs : session.clock_ts
      if (clock === null) return
      const last = lastClockRef.current
      const seek = !last || last.id !== session.id || clock < last.clock - 1 || clock > last.clock + ((Date.now() - last.wall) / 1000 + 2 * EVENTS_POLL_MS / 1000) * (session.speed || 1) + 5
      try {
        const after = seek ? null : cursorRef.current
        let page = await sessionApi.events(session.window_s, clock, after, 2000, session.span_s)
        const pages = [page]
        // page to the end: an unfinished history would otherwise be continued as "new" on the next poll
        while (page.has_more && pages.length < 25) {
          page = await sessionApi.events(session.window_s, clock, page.next_cursor, 2000, session.span_s)
          pages.push(page)
        }
        const historyOnly = seek || pages[0].kind === 'history'
        if (!alive) return
        lastClockRef.current = { clock, wall: Date.now(), id: session.id }
        cursorRef.current = pages[pages.length - 1].next_cursor
        const rows = pages.flatMap((p) => p.events)
        if (historyOnly) {
          setFeed(rows.slice(-200))
          setFreshRows(new Set())
          // boot: the whole visible history streams through the tape and the scene builds up in time order
          const lo = clock - session.span_s
          tapeRef.current?.boot(rows, `${utc(lo)} → ${utc(clock)} UTC · ${session.mode === 'live' ? 'LIVE' : `REPLAY ${session.speed}×`} · window ${windowName(session.window_s)}`)
          setRevealAt(performance.now())
        } else if (rows.length) {
          setFeed((f) => [...f, ...rows].slice(-200))
          setFreshRows(new Set(rows.map((r) => String(r.id))))
          tapeRef.current?.push(rows)
          // one pulse per edge per batch: N = accepted rows on that edge in this poll
          const perEdge = new Map<string, number>()
          for (const r of rows) perEdge.set(`${r.from}->${r.to}`, (perEdge.get(`${r.from}->${r.to}`) ?? 0) + 1)
          const at = performance.now()
          setPulses([...perEdge.entries()].map(([key, count]) => ({ key, count, at })))
          setFreshEdges((m) => new Map([...m, ...[...perEdge.keys()].map((k) => [k, at] as [string, number])]))
          window.setTimeout(() => alive && setFreshRows(new Set()), 2500)
        }
      } catch {
        /* status poll reports the outage */
      }
    }
    poll()
    const id = window.setInterval(poll, EVENTS_POLL_MS)
    return () => {
      alive = false
      window.clearInterval(id)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session?.id, session?.rev, session?.clock_ts, session?.playing, mode, liveLastTs])

  // ---- radar (ranked coins) and alerts: poll while the radar or flow view is visible ----
  const radarClock = session?.clock_ts ?? null
  const radarTick = radarClock === null ? 0 : Math.floor(radarClock / 30)
  const coinTick = radarClock === null ? 0 : Math.floor(radarClock / 60)
  useEffect(() => {
    let alive = true
    let busy = false
    const tick = () => {
      if (busy) return
      busy = true
      const f = radarFilters
      api
        .radar({ preset: f.preset !== 'custom' ? f.preset : null, min_wallets: f.minWallets, age_max: f.ageMax, stage: f.stage, exclude_bots: f.excludeBots ? 1 : 0, mentions_max: f.mentionsMax, quality_min: f.qualityMin, sort: f.sort, limit: 60 })
        .then((d) => {
          if (!alive) return
          const bumps = new Map<string, number>()
          const now = performance.now()
          for (const r of d.rows) {
            const prev = prevInflow.current.get(r.address)
            if (prev !== undefined && r.inflow_10m > prev) bumps.set(r.address, now)
            prevInflow.current.set(r.address, r.inflow_10m)
          }
          if (bumps.size) setFreshTokens((m) => new Map([...m, ...bumps]))
          setRadarData(d)
        })
        .catch(() => {})
        .finally(() => {
          busy = false
        })
      api.alerts().then((a) => alive && setAlerts(a)).catch(() => {})
    }
    tick()
    const id = window.setInterval(tick, 4000)
    return () => {
      alive = false
      window.clearInterval(id)
    }
  }, [view, radarFilters, radarTick])

  // ---- one coin: on-chain as-of numbers plus whatever context is cached; refresh fetches live context ----
  const loadCoin = useCallback((addr: string, refresh = false) => {
    const n = ++coinNonce.current
    setCoinLoading(true)
    setCoinError(null)
    api
      .coin(addr, refresh)
      .then((c) => {
        if (coinNonce.current !== n) return
        setCoin(c)
      })
      .catch((e) => coinNonce.current === n && setCoinError(String(e.message ?? e)))
      .finally(() => coinNonce.current === n && setCoinLoading(false))
  }, [])
  useEffect(() => {
    if (!coinAddr) {
      setCoin(null)
      return
    }
    loadCoin(coinAddr)
  }, [coinAddr, loadCoin, coinTick])

  const openCoin = useCallback((addr: string) => {
    setCoinAddr(addr)
    setDrawerOpen(true)
  }, [])
  const openFlow = useCallback((addr: string) => {
    setCoinAddr(addr)
    setView('flow')
  }, [])

  // ---- autopilot: in MAP presentation, tour the top radar coins (fly -> caption -> next); any manual
  // interaction stops it. Every stop is a real edge with its computed count, in the order the radar ranks them.
  const [autopilot, setAutopilot] = useState(params.get('autopilot') === '1')
  const tourIdx = useRef(0)
  const tourTimer = useRef<number | null>(null)
  const graphRef = useRef<Graph | null>(null)
  graphRef.current = graph
  const visited = useRef<Set<string>>(new Set())
  useEffect(() => {
    if (!autopilot || view !== 'map' || !graph?.edges.length) return
    const step = () => {
      // the strongest observed flows in the range *as of now*, biggest first, each pair visited once per round
      const g = graphRef.current
      if (!g) return
      const flows = [...g.edges].filter((e) => e.wallets_main >= 3).sort((a, b) => b.wallets_main - a.wallets_main).slice(0, 10)
      let e = flows.find((x) => !visited.current.has(`${x.from}->${x.to}`))
      if (!e) {
        visited.current.clear()
        e = flows[0]
      }
      if (!e) return
      visited.current.add(`${e.from}->${e.to}`)
      tourIdx.current += 1
      setSelection({ kind: 'edge', from: e.from, to: e.to })
      setFocusNonce((n) => n + 1)
    }
    if (tourTimer.current === null) {
      // first stop soon after the overview settles, then one stop every TOUR_MS
      tourTimer.current = window.setTimeout(function tick() {
        step()
        tourTimer.current = window.setTimeout(tick, TOUR_MS)
      }, 2600)
    }
    return () => {
      if (tourTimer.current !== null) {
        window.clearTimeout(tourTimer.current)
        tourTimer.current = null
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autopilot, view, graph === null])
  const stopAutopilot = useCallback(() => setAutopilot(false), [])
  useEffect(() => {
    if (!autopilot) return
    const off = () => stopAutopilot()
    window.addEventListener('pointerdown', off)
    window.addEventListener('wheel', off, { passive: true })
    return () => {
      window.removeEventListener('pointerdown', off)
      window.removeEventListener('wheel', off)
    }
  }, [autopilot, stopAutopilot])

  // ---- details for the selection ----
  useEffect(() => {
    if (!selection) {
      setEdge(null)
      setToken(null)
      setEvidenceOpen(false)
      return
    }
    let alive = true
    setDetailLoading(true)
    setDetailError(null)
    const p =
      selection.kind === 'edge'
        ? api
            .edge(selection.from, selection.to, filters.windowS, fromTs, toTs, 60, false) // fast: counts + rows as stored
            .then((d) => {
              if (!alive) return
              setEdge(d)
              setToken(null)
              // then upgrade the same rows with exact block times and tx.from (Alchemy lookups)
              api.edge(selection.from, selection.to, filters.windowS, fromTs, toTs, 60, true).then((d2) => alive && setEdge(d2)).catch(() => {})
            })
        : api.token(selection.address, filters.windowS, fromTs, toTs).then((d) => alive && (setToken(d), setEdge(null)))
    p.catch((e) => alive && setDetailError(String(e))).finally(() => alive && setDetailLoading(false))
    return () => {
      alive = false
    }
  }, [selection, filters.windowS, fromTs, toTs])

  const select = useCallback((s: Selection) => {
    setSelection(s)
    setFocusNonce((n) => n + 1)
    if (!s) setViewState('overview')
  }, [])
  // automation hook (demo recording, tests): same code path as a click
  useEffect(() => {
    ;(window as unknown as { __stampede_select?: (s: Selection) => void }).__stampede_select = select
    ;(window as unknown as { __stampede_state?: () => unknown }).__stampede_state = () => ({ viewState, selection, layout, renderer, view, coin: coinAddr, radarRows: radarData?.rows.length ?? 0, edges: graph?.edges.length, sessionClock: session?.clock_ts, playing: session?.playing, autopilot, tourIdx: tourIdx.current })
    ;(window as unknown as { __stampede_view?: (v: 'radar' | 'flow' | 'map') => void }).__stampede_view = setView
    ;(window as unknown as { __stampede_coin?: (a: string) => void }).__stampede_coin = openFlow
  }, [select, viewState, selection, layout, renderer, graph, session, view, coinAddr, radarData, openFlow, autopilot])

  // keys: E evidence, Esc back, P presentation, space play/pause
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.tagName === 'INPUT') return
      if (e.key === 'Escape') {
        if (drawerOpen) setDrawerOpen(false)
        else select(null)
      } else if (e.key === 'a' || e.key === 'A') setAutopilot((v) => !v)
      else if (e.key === '1') setView('radar')
      else if (e.key === '2') setView('flow')
      else if (e.key === '3') setView('map')
      else if (e.key === 'e' || e.key === 'E') setEvidenceOpen((v) => !v)
      else if (e.key === 'p' || e.key === 'P') setLayout((l) => (l === 'presentation' ? 'explore' : 'presentation'))
      else if (e.key === 't' || e.key === 'T') setShowTape((v) => !v)
      else if ((e.key === 'd' || e.key === 'D') && coinAddr) setDrawerOpen((v) => !v)
      else if (e.key === ' ' && session?.controls) {
        e.preventDefault()
        control('toggle')
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [select, control, session?.controls, drawerOpen, coinAddr])

  const recent: Recent[] = useMemo(
    () =>
      [...feed]
        .reverse()
        .slice(0, 30)
        .map((r) => ({ wallet: r.wallet, from: r.from, to: r.to, grade: r.grade, buy_ts: r.buy_ts, gap_s: r.gap_s, buy_tx: r.buy_tx, from_label: r.from_symbol, to_label: r.to_symbol, id: r.id })),
    [feed],
  )

  const bounds = useMemo(() => {
    if (!session) return null
    if (mode === 'live') return liveLastTs ? { min: liveLastTs - 6 * 3600, max: liveLastTs } : null
    return session.from_ts && session.to_ts ? { min: session.from_ts, max: session.to_ts } : null
  }, [session, mode, liveLastTs])

  const hotCoins = useMemo(() => new Map((radarData?.rows ?? []).slice(0, 12).map((r) => [r.address, r.inflow_10m] as [string, number])), [radarData])
  const edgesShown = graph?.edges.length ?? 0
  const edgesTotal = graph?.totals.edges_matching ?? 0
  const presentation = layout === 'presentation'
  const stale = status?.connection === 'stale'

  const mapView = view === 'map'
  return (
    <div className={`app ${presentation || !mapView ? 'presentation' : ''} view-${view}`}>
      <TopStrip status={status} session={session} statusError={statusError} layout={layout} renderer={renderer} edgesShown={edgesShown} edgesTotal={edgesTotal} onLayout={setLayout} onRenderer={setRenderer} onControl={control} onSelect={(s) => (s?.kind === 'token' ? openCoin(s.address) : select(s))} view={view} onView={setView} />
      {view === 'radar' && (
        <main className="main radar-main">
          <Radar data={radarData} alerts={alerts} session={session} filters={radarFilters} setFilters={setRadarFilters} selected={coinAddr} onSelect={openCoin} onFlow={openFlow} freshTokens={freshTokens} />
          {drawerOpen && <CoinDrawer coin={coin} loading={coinLoading} error={coinError} session={session} onClose={() => setDrawerOpen(false)} onRefresh={() => coinAddr && loadCoin(coinAddr, true)} onFlow={openFlow} onFocus={openCoin} />}
        </main>
      )}
      {view === 'flow' && (
        <main className="main flow-main">
          <Flow coin={coin} loading={coinLoading} onFocus={(a) => setCoinAddr(a)} onEdge={(s) => { select(s); setView('map'); setLayout('presentation') }} fresh={freshEdges} windowS={session?.window_s ?? 1800} />
          {drawerOpen && <CoinDrawer coin={coin} loading={coinLoading} error={coinError} session={session} onClose={() => setDrawerOpen(false)} onRefresh={() => coinAddr && loadCoin(coinAddr, true)} onFlow={openFlow} onFocus={(a) => setCoinAddr(a)} />}
          {!drawerOpen && coin && (
            <button className="drawer-toggle" onClick={() => setDrawerOpen(true)}>
              Coin details (D)
            </button>
          )}
        </main>
      )}
      {view === 'map' && (
      <main className="main">
        {!presentation && <Controls status={status} graph={graph} filters={filters} setFilters={setFilters} bounds={bounds} session={session} onControl={control} />}
        <section className={`map ${presentation && (edge || token) ? 'has-caption' : ''} ${presentation && showTape && !evidenceOpen ? 'has-tape' : ''}`} aria-label="Rotation map">
          {renderer === '3d' ? (
            <Scene3D
              nodes={graph?.nodes ?? []}
              edges={graph?.edges ?? []}
              selection={selection}
              showAmbiguous={filters.showAmbiguous}
              pulses={pulses}
              viewState={viewState}
              reducedMotion={reduced}
              onSelect={select}
              onHover={setHover}
              onViewState={setViewState}
              onPerf={setPerf}
              focusNonce={focusNonce}
              labelBudget={presentation ? 14 : 22}
              panelOpen={presentation && evidenceOpen}
              revealAt={revealAt}
              revealRange={fromTs !== null && toTs !== null ? { from: fromTs, to: toTs } : null}
              hudRight={presentation && showTape && !evidenceOpen ? 470 : 0}
              hudLeft={presentation ? 384 : 0}
              hot={hotCoins}
            />
          ) : (
            <MapCanvas nodes={graph?.nodes ?? []} edges={graph?.edges ?? []} selection={selection} showAmbiguous={filters.showAmbiguous} fresh={new Map(pulses.map((p) => [p.key, p.at]))} onSelect={select} onHover={setHover} focusNonce={focusNonce} />
          )}
          {presentation && showTape && !evidenceOpen && (
            <div className="tape-wrap" aria-label="Observed sequences tape">
              <Tape onReady={(api) => (tapeRef.current = api)} />
            </div>
          )}
          <div className="corner">
            <span>
              <b>{viewState.toUpperCase()}</b>
            </span>
            {mode !== 'live' && status && <span>{mode === 'replay' ? 'REPLAY' : 'RECORDED'} · {status.sample.label}</span>}
            {stale && <span className="badge stale">STALE · data age {status?.data.age_s ?? '?'} s</span>}
            {presentation && (
              <button className={autopilot ? 'on' : ''} onClick={() => setAutopilot((v) => !v)} style={{ pointerEvents: 'auto' }} data-testid="autopilot">
                {autopilot ? 'Autopilot on (A)' : 'Autopilot (A)'}
              </button>
            )}
          </div>
          {presentation && <MapHud radar={radarData} graph={graph} feed={feed} fresh={freshTokens} clock={toTs} selection={selection} onPick={(from, to) => { setAutopilot(false); select({ kind: 'edge', from, to }) }} />}
          {showPerf && perf && (
            <div className="perf">
              {perf.fps} fps · p50 {perf.p50_ms} ms · p95 {perf.p95_ms} ms · {perf.drawn_edges} edges · {perf.nodes} nodes
            </div>
          )}
          {statusError && <div className="overlay-msg">API not reachable: {statusError}. Nothing here is live.</div>}
          {!statusError && graph && graph.edges.length === 0 && <div className="overlay-msg">No edges with at least {filters.minWallets} wallets in this range. Lower the minimum or move the clock.</div>}
          {!statusError && mode === 'live' && toTs === null && <div className="overlay-msg">Waiting for the first live block. Nothing is drawn until the tail has indexed real data.</div>}
          {presentation && (viewState === 'evidence' || selection) && (edge || token) && (
            <Caption edge={edge} token={token} session={session} liveLastTs={liveLastTs} onOpenEvidence={() => setEvidenceOpen((v) => !v)} onBack={() => select(null)} evidenceOpen={evidenceOpen} />
          )}
          {presentation && evidenceOpen && edge && (
            <aside className="evidence-panel" aria-label="Evidence rows">
              <h1>
                {edge.from.symbol} → {edge.to.symbol}
              </h1>
              <div className="sub">
                {edge.from.short} → {edge.to.short} · {edge.wallets_main} wallets · {edge.sequences_total} sequences · ≈ marks interpolated block time
              </div>
              {edge.sequences.map((s) => (
                <SequenceRow key={s.id} s={s} fresh={freshRows.has(String(s.id))} />
              ))}
            </aside>
          )}
          <div className="legend">
            <span>
              <b>Edge A → B</b>: distinct wallets that sold A, then bought B within {filters.windowS / 60} min. Width = wallets; dashed = ambiguous only. Distance on the map is layout, not a fact.
            </span>
            <span>
              {hover?.kind === 'edge' ? `${graph?.nodes.find((n) => n.address === hover.from)?.symbol ?? ''} → ${graph?.nodes.find((n) => n.address === hover.to)?.symbol ?? ''}` : hover?.kind === 'token' ? graph?.nodes.find((n) => n.address === hover.address)?.symbol : renderer === '3d' ? 'drag to orbit · wheel to zoom · click a coin or a line' : 'scroll to zoom · drag to pan'}
            </span>
          </div>
        </section>
        {!presentation && <Details selection={selection} edge={edge} token={token} loading={detailLoading} error={detailError} onSelect={select} freshRows={freshRows} />}
      </main>
      )}
      {view === 'map' && !presentation && <Ticker status={status} session={session} recent={recent} freshKeys={freshRows} onSelect={select} />}
    </div>
  )
}
