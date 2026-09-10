import { useEffect, useRef } from 'react'
import { forceCenter, forceCollide, forceLink, forceManyBody, forceSimulation, forceX, forceY, type Simulation, type SimulationLinkDatum, type SimulationNodeDatum } from 'd3-force'
import { select } from 'd3-selection'
import { zoom, zoomIdentity, type ZoomBehavior, type ZoomTransform } from 'd3-zoom'
import 'd3-transition'
import type { GEdge, GNode, Selection } from '../types'

interface SimNode extends SimulationNodeDatum {
  id: string
  symbol: string
  short: string
  r: number
  degree: number
  activity: number
}

interface SimLink extends SimulationLinkDatum<SimNode> {
  key: string
  from: string
  to: string
  main: number
  amb: number
}

interface Props {
  nodes: GNode[]
  edges: GEdge[]
  selection: Selection
  showAmbiguous: boolean
  fresh: Map<string, number> // edge key -> arrival time (ms); drawn emphasised for ~2.5 s
  onSelect: (s: Selection) => void
  onHover: (s: Selection) => void
  focusNonce: number // increments when the app wants the camera to move to the selection
}

const edgeKey = (a: string, b: string) => `${a}->${b}`
const radiusFor = (r: number, k: number) => r * Math.pow(k, -0.45)
const PULSE_MS = 2500

export default function MapCanvas({ nodes, edges, selection, showAmbiguous, fresh, onSelect, onHover, focusNonce }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const wrapRef = useRef<HTMLDivElement>(null)
  const simRef = useRef<Simulation<SimNode, SimLink> | null>(null)
  const nodesRef = useRef<Map<string, SimNode>>(new Map())
  const linksRef = useRef<SimLink[]>([])
  const transformRef = useRef<ZoomTransform>(zoomIdentity)
  const zoomRef = useRef<ZoomBehavior<HTMLCanvasElement, unknown> | null>(null)
  const hoverRef = useRef<Selection>(null)
  const selRef = useRef<Selection>(selection)
  const freshRef = useRef(fresh)
  const rafRef = useRef<number | null>(null)
  const sizeRef = useRef({ w: 800, h: 600, dpr: 1 })
  const settledRef = useRef(false)
  const userMovedRef = useRef(false)
  const fittedRef = useRef(false)

  /** Frame every node with a margin. Only used before the user has moved the camera. */
  const fitAll = () => {
    const canvas = canvasRef.current
    const z = zoomRef.current
    if (!canvas || !z) return
    const pts = [...nodesRef.current.values()].filter((n) => n.x !== undefined)
    if (!pts.length) return
    let x0 = Infinity
    let y0 = Infinity
    let x1 = -Infinity
    let y1 = -Infinity
    for (const n of pts) {
      x0 = Math.min(x0, n.x! - n.r)
      y0 = Math.min(y0, n.y! - n.r)
      x1 = Math.max(x1, n.x! + n.r)
      y1 = Math.max(y1, n.y! + n.r)
    }
    const { w, h } = sizeRef.current
    const k = Math.max(0.15, Math.min(2.5, 0.9 * Math.min(w / Math.max(1, x1 - x0), h / Math.max(1, y1 - y0))))
    const t = zoomIdentity.translate(w / 2 - ((x0 + x1) / 2) * k, h / 2 - ((y0 + y1) / 2) * k).scale(k)
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const s = select(canvas)
    if (reduce) s.call(z.transform, t)
    else s.transition().duration(500).call(z.transform, t)
  }

  selRef.current = selection
  freshRef.current = fresh

  // ---- geometry helpers ----
  const curve = (s: SimNode, t: SimNode) => {
    const dx = t.x! - s.x!
    const dy = t.y! - s.y!
    const d = Math.hypot(dx, dy) || 1
    const nx = -dy / d
    const ny = dx / d
    const off = Math.min(28, 0.16 * d)
    const cx = (s.x! + t.x!) / 2 + nx * off
    const cy = (s.y! + t.y!) / 2 + ny * off
    return { cx, cy }
  }
  const pointAt = (s: SimNode, t: SimNode, cx: number, cy: number, u: number) => {
    const a = (1 - u) * (1 - u)
    const b = 2 * (1 - u) * u
    const c = u * u
    return { x: a * s.x! + b * cx + c * t.x!, y: a * s.y! + b * cy + c * t.y! }
  }

  const draw = () => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    const { w, h, dpr } = sizeRef.current
    const tr = transformRef.current
    ctx.setTransform(1, 0, 0, 1, 0, 0)
    ctx.clearRect(0, 0, w * dpr, h * dpr)
    ctx.setTransform(dpr * tr.k, 0, 0, dpr * tr.k, dpr * tr.x, dpr * tr.y)
    const k = tr.k
    const sel = selRef.current
    const hov = hoverRef.current
    const now = performance.now()
    const selNode = sel?.kind === 'token' ? sel.address : null
    const selEdge = sel?.kind === 'edge' ? edgeKey(sel.from, sel.to) : null
    const hovNode = hov?.kind === 'token' ? hov.address : null
    const hovEdge = hov?.kind === 'edge' ? edgeKey(hov.from, hov.to) : null
    const hasFocus = !!sel
    const connected = new Set<string>()
    if (sel?.kind === 'edge') {
      connected.add(sel.from)
      connected.add(sel.to)
    }
    if (selNode) {
      for (const l of linksRef.current) {
        if (l.from === selNode) connected.add(l.to)
        if (l.to === selNode) connected.add(l.from)
      }
      connected.add(selNode)
    }

    // edges
    for (const l of linksRef.current) {
      const s = l.source as SimNode
      const t = l.target as SimNode
      if (s.x === undefined || t.x === undefined) continue
      const ambOnly = l.main === 0
      if (ambOnly && !showAmbiguous) continue
      const isSel = selEdge === l.key
      const isHov = hovEdge === l.key
      const touches = selNode ? l.from === selNode || l.to === selNode : false
      const freshAt = freshRef.current.get(l.key)
      const pulse = freshAt !== undefined ? Math.max(0, 1 - (now - freshAt) / PULSE_MS) : 0
      const alpha = ambOnly ? 0.16 : 0.14 + Math.min(0.72, 0.09 * Math.sqrt(l.main))
      let width = ambOnly ? 0.8 : 0.8 + Math.min(9, 1.6 * Math.sqrt(l.main))
      let color = `rgba(228,232,238,${alpha})`
      if (hasFocus) {
        if (isSel) {
          color = 'rgba(224,184,92,0.95)'
          width += 1.5
        } else if (touches) {
          color = `rgba(228,232,238,${Math.min(0.85, alpha + 0.35)})`
        } else {
          color = `rgba(228,232,238,${alpha * 0.28})`
        }
      } else if (isHov) {
        color = `rgba(228,232,238,${Math.min(0.9, alpha + 0.4)})`
      }
      if (pulse > 0) {
        color = `rgba(224,184,92,${0.35 + 0.6 * pulse})`
        width += 3 * pulse
      }
      const { cx, cy } = curve(s, t)
      ctx.beginPath()
      ctx.moveTo(s.x!, s.y!)
      ctx.quadraticCurveTo(cx, cy, t.x!, t.y!)
      ctx.lineWidth = width / k
      ctx.strokeStyle = color
      ctx.setLineDash(ambOnly ? [3 / k, 4 / k] : [])
      ctx.stroke()
      ctx.setLineDash([])
      // arrow head at the target rim
      let u = 0.98
      let p = pointAt(s, t, cx, cy, u)
      while (u > 0.5 && Math.hypot(p.x - t.x!, p.y - t.y!) < radiusFor(t.r, k) + 3 / k) {
        u -= 0.01
        p = pointAt(s, t, cx, cy, u)
      }
      const p2 = pointAt(s, t, cx, cy, Math.max(0, u - 0.03))
      const ang = Math.atan2(p.y - p2.y, p.x - p2.x)
      const ah = (5 + width * 0.8) / k
      ctx.beginPath()
      ctx.moveTo(p.x, p.y)
      ctx.lineTo(p.x - ah * Math.cos(ang - 0.45), p.y - ah * Math.sin(ang - 0.45))
      ctx.lineTo(p.x - ah * Math.cos(ang + 0.45), p.y - ah * Math.sin(ang + 0.45))
      ctx.closePath()
      ctx.fillStyle = color
      ctx.fill()
    }

    // nodes
    const ranked = [...nodesRef.current.values()].sort((a, b) => b.degree - a.degree)
    const labelSet = new Set(ranked.slice(0, k < 0.7 ? 16 : 40).map((n) => n.id))
    for (const n of nodesRef.current.values()) {
      if (n.x === undefined) continue
      const isSel = selNode === n.id
      const isHov = hovNode === n.id
      const dim = hasFocus && !connected.has(n.id) && !isSel
      ctx.beginPath()
      ctx.arc(n.x!, n.y!, radiusFor(n.r, k), 0, Math.PI * 2)
      ctx.fillStyle = dim ? 'rgba(140,148,160,0.3)' : isSel ? '#e0b85c' : 'rgba(178,187,198,0.92)'
      ctx.fill()
      ctx.lineWidth = (isSel || isHov ? 2 : 1) / k
      ctx.strokeStyle = isSel ? '#e0b85c' : isHov ? '#e4e8ee' : 'rgba(14,17,22,0.9)'
      ctx.stroke()
    }
    // labels (after all nodes so they sit on top)
    ctx.textAlign = 'center'
    ctx.textBaseline = 'top'
    for (const n of nodesRef.current.values()) {
      if (n.x === undefined) continue
      const isSel = selNode === n.id
      const isHov = hovNode === n.id
      const inEdge = sel?.kind === 'edge' && (sel.from === n.id || sel.to === n.id)
      if (!(labelSet.has(n.id) || isSel || isHov || inEdge)) continue
      const dim = hasFocus && !connected.has(n.id) && !isSel && !inEdge
      const fs = (isSel || inEdge ? 13 : 11.5) / k
      ctx.font = `${isSel || inEdge ? 600 : 500} ${fs}px 'Bricolage Grotesque Variable', system-ui, sans-serif`
      ctx.fillStyle = dim ? 'rgba(163,171,182,0.35)' : isSel || inEdge ? '#e4e8ee' : 'rgba(228,232,238,0.82)'
      const rr = radiusFor(n.r, k)
      ctx.fillText(n.symbol, n.x!, n.y! + rr + 3 / k)
      if (isSel || isHov || inEdge) {
        ctx.font = `${10.5 / k}px 'IBM Plex Mono', ui-monospace, monospace`
        ctx.fillStyle = 'rgba(163,171,182,0.9)'
        ctx.fillText(n.short, n.x!, n.y! + rr + 3 / k + fs + 2 / k)
      }
    }
  }

  const schedule = () => {
    if (rafRef.current !== null) return
    rafRef.current = requestAnimationFrame(() => {
      rafRef.current = null
      draw()
      const anyPulse = [...freshRef.current.values()].some((t) => performance.now() - t < PULSE_MS)
      if (anyPulse) schedule()
    })
  }

  // ---- size ----
  useEffect(() => {
    const wrap = wrapRef.current
    const canvas = canvasRef.current
    if (!wrap || !canvas) return
    const ro = new ResizeObserver(() => {
      const r = wrap.getBoundingClientRect()
      const dpr = window.devicePixelRatio || 1
      sizeRef.current = { w: r.width, h: r.height, dpr }
      canvas.width = Math.round(r.width * dpr)
      canvas.height = Math.round(r.height * dpr)
      canvas.style.width = `${r.width}px`
      canvas.style.height = `${r.height}px`
      schedule()
    })
    ro.observe(wrap)
    return () => ro.disconnect()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // ---- zoom ----
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const z = zoom<HTMLCanvasElement, unknown>()
      .scaleExtent([0.15, 8])
      .on('zoom', (ev) => {
        transformRef.current = ev.transform
        if (ev.sourceEvent) userMovedRef.current = true
        schedule()
      })
    zoomRef.current = z
    const sel = select(canvas)
    sel.call(z)
    const { w, h } = sizeRef.current
    sel.call(z.transform, zoomIdentity.translate(w / 2, h / 2).scale(0.9))
    return () => {
      sel.on('.zoom', null)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // ---- data -> simulation ----
  useEffect(() => {
    const prev = nodesRef.current
    const next = new Map<string, SimNode>()
    const degree = new Map<string, number>()
    for (const e of edges) {
      degree.set(e.from, (degree.get(e.from) ?? 0) + e.wallets_main)
      degree.set(e.to, (degree.get(e.to) ?? 0) + e.wallets_main)
    }
    for (const n of nodes) {
      const activity = n.buyers + n.sellers
      const r = Math.min(15, 3 + 1.35 * Math.log2(1 + activity))
      const old = prev.get(n.address)
      next.set(n.address, {
        id: n.address,
        symbol: n.symbol,
        short: n.short,
        r,
        degree: degree.get(n.address) ?? 0,
        activity,
        x: old?.x,
        y: old?.y,
        vx: 0,
        vy: 0,
      })
    }
    // place new nodes near a neighbour so the picture does not explode
    for (const e of edges) {
      const a = next.get(e.from)
      const b = next.get(e.to)
      if (!a || !b) continue
      if (a.x === undefined && b.x !== undefined) {
        a.x = b.x + (Math.random() - 0.5) * 80
        a.y = b.y! + (Math.random() - 0.5) * 80
      }
      if (b.x === undefined && a.x !== undefined) {
        b.x = a.x + (Math.random() - 0.5) * 80
        b.y = a.y! + (Math.random() - 0.5) * 80
      }
    }
    for (const n of next.values()) {
      if (n.x === undefined) {
        const ang = Math.random() * Math.PI * 2
        const rad = 120 + Math.random() * 260
        n.x = Math.cos(ang) * rad
        n.y = Math.sin(ang) * rad
      }
    }
    const links: SimLink[] = edges
      .filter((e) => e.wallets_main > 0 || (showAmbiguous && e.wallets_ambiguous > 0))
      .map((e) => ({ key: edgeKey(e.from, e.to), from: e.from, to: e.to, main: e.wallets_main, amb: e.wallets_ambiguous, source: e.from, target: e.to }))
    // how different is the new picture? small deltas (replay/live ticks) must not reshuffle the layout
    let changed = 0
    for (const id of next.keys()) if (!prev.has(id)) changed++
    for (const id of prev.keys()) if (!next.has(id)) changed++
    const churn = prev.size ? changed / Math.max(1, next.size) : 1
    nodesRef.current = next
    linksRef.current = links
    simRef.current?.stop()
    const alpha = prev.size === 0 ? 1 : churn > 0.5 ? 0.6 : churn > 0.05 ? 0.2 : 0.08
    const sim = forceSimulation<SimNode, SimLink>([...next.values()])
      .force('link', forceLink<SimNode, SimLink>(links).id((d) => d.id).distance((l) => 85 + 70 / (1 + Math.sqrt(l.main))).strength((l) => Math.min(0.8, 0.2 + 0.1 * Math.sqrt(l.main))))
      .force('charge', forceManyBody<SimNode>().strength((d) => -90 - 22 * d.r).distanceMax(600))
      .force('x', forceX(0).strength(0.05))
      .force('y', forceY(0).strength(0.05))
      .force('center', forceCenter(0, 0).strength(0.02))
      .force('collide', forceCollide<SimNode>().radius((d) => d.r + 14).iterations(2))
      .alpha(alpha)
      .alphaDecay(0.035)
      .velocityDecay(0.38)
      .on('tick', () => schedule())
      .on('end', () => {
        settledRef.current = true
        if (!userMovedRef.current && !selRef.current && (!fittedRef.current || churn > 0.5)) {
          fittedRef.current = true
          fitAll()
        }
        schedule()
      })
    settledRef.current = false
    simRef.current = sim
    // live/replay data keeps arriving before the simulation ends: frame the first picture once anyway
    let fitTimer: number | null = null
    if (!fittedRef.current && next.size) {
      fitTimer = window.setTimeout(() => {
        if (!fittedRef.current && !userMovedRef.current && !selRef.current) {
          fittedRef.current = true
          fitAll()
        }
      }, 1500)
    }
    return () => {
      sim.stop()
      if (fitTimer) window.clearTimeout(fitTimer)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodes, edges, showAmbiguous])

  useEffect(() => {
    schedule()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selection, fresh, showAmbiguous])

  // ---- camera focus on selection ----
  useEffect(() => {
    if (!selection || !zoomRef.current || !canvasRef.current) return
    const { w, h } = sizeRef.current
    let x: number | undefined
    let y: number | undefined
    let k = transformRef.current.k
    if (selection.kind === 'token') {
      const n = nodesRef.current.get(selection.address)
      if (!n || n.x === undefined) return
      x = n.x
      y = n.y!
      k = Math.min(1.8, Math.max(k, 1.3))
    } else {
      const a = nodesRef.current.get(selection.from)
      const b = nodesRef.current.get(selection.to)
      if (!a || !b || a.x === undefined || b.x === undefined) return
      x = (a.x + b.x) / 2
      y = (a.y! + b.y!) / 2
      const d = Math.hypot(a.x - b.x, a.y! - b.y!) || 1
      k = Math.min(2.0, Math.max(0.9, (Math.min(w, h) * 0.34) / d))
    }
    const t = zoomIdentity.translate(w / 2 - x * k, h / 2 - y * k).scale(k)
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const s = select(canvasRef.current)
    if (reduce) s.call(zoomRef.current.transform, t)
    else s.transition().duration(650).call(zoomRef.current.transform, t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusNonce])

  // ---- pointer ----
  const hitTest = (sx: number, sy: number): Selection => {
    const tr = transformRef.current
    const [wx, wy] = tr.invert([sx, sy])
    let best: Selection = null
    let bestD = Infinity
    for (const n of nodesRef.current.values()) {
      if (n.x === undefined) continue
      const d = Math.hypot(n.x - wx, n.y! - wy)
      if (d <= radiusFor(n.r, tr.k) + 4 / tr.k && d < bestD) {
        bestD = d
        best = { kind: 'token', address: n.id }
      }
    }
    if (best) return best
    const thr = 7 / tr.k
    for (const l of linksRef.current) {
      if (l.main === 0 && !showAmbiguous) continue
      const s = l.source as SimNode
      const t = l.target as SimNode
      if (s.x === undefined || t.x === undefined) continue
      const { cx, cy } = curve(s, t)
      for (let i = 1; i < 16; i++) {
        const p = pointAt(s, t, cx, cy, i / 16)
        const d = Math.hypot(p.x - wx, p.y - wy)
        if (d < thr && d < bestD) {
          bestD = d
          best = { kind: 'edge', from: l.from, to: l.to }
        }
      }
    }
    return best
  }

  const onMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const r = e.currentTarget.getBoundingClientRect()
    const h = hitTest(e.clientX - r.left, e.clientY - r.top)
    const prev = hoverRef.current
    const same = JSON.stringify(prev) === JSON.stringify(h)
    if (!same) {
      hoverRef.current = h
      onHover(h)
      e.currentTarget.style.cursor = h ? 'pointer' : 'grab'
      schedule()
    }
  }
  const onClick = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const r = e.currentTarget.getBoundingClientRect()
    const h = hitTest(e.clientX - r.left, e.clientY - r.top)
    onSelect(h)
  }

  return (
    <div ref={wrapRef} className="map-wrap" style={{ position: 'absolute', inset: 0 }}>
      <canvas ref={canvasRef} onMouseMove={onMove} onClick={onClick} aria-label="Map of observed wallet rotations between coins" role="img" />
    </div>
  )
}
