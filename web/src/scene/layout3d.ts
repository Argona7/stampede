// 3D force layout for the rotation graph. Positions are for navigation only (see the on-screen legend):
// distance between coins is a layout artefact, not an analytical fact.
import { forceCenter, forceCollide, forceLink, forceManyBody, forceSimulation, forceZ } from 'd3-force-3d'
import type { GEdge, GNode } from '../types'

export interface LNode {
  id: string
  symbol: string
  short: string
  r: number
  activity: number
  degree: number
  band: 0 | 1 | 2 // 0 foreground (touches strong edges), 1 midground, 2 background
  x: number
  y: number
  z: number
  vx?: number
  vy?: number
  vz?: number
}

export interface LLink {
  key: string
  from: string
  to: string
  main: number
  amb: number
  source: LNode | string
  target: LNode | string
}

export interface Layout {
  nodes: Map<string, LNode>
  links: LLink[]
  radius: number
}

const SLAB_Z = 260 // total depth of the layout slab

/** Incremental layout: existing nodes keep their position, new ones start near a neighbour. */
export function layout3d(nodes: GNode[], edges: GEdge[], prev: Layout | null, ticks = 240): Layout {
  const degree = new Map<string, number>()
  for (const e of edges) {
    degree.set(e.from, (degree.get(e.from) ?? 0) + e.wallets_main)
    degree.set(e.to, (degree.get(e.to) ?? 0) + e.wallets_main)
  }
  const maxDeg = Math.max(1, ...degree.values())
  const next = new Map<string, LNode>()
  for (const n of nodes) {
    const activity = n.buyers + n.sellers
    const deg = degree.get(n.address) ?? 0
    const band: 0 | 1 | 2 = deg >= maxDeg * 0.25 ? 0 : deg >= 3 ? 1 : 2
    const old = prev?.nodes.get(n.address)
    next.set(n.address, {
      id: n.address,
      symbol: n.symbol,
      short: n.short,
      r: Math.min(12, 3 + 1.5 * Math.log2(1 + activity)),
      activity,
      degree: deg,
      band,
      x: old?.x ?? NaN,
      y: old?.y ?? NaN,
      z: old?.z ?? NaN,
    })
  }
  const links: LLink[] = []
  for (const e of edges) {
    if (!next.has(e.from) || !next.has(e.to)) continue
    links.push({ key: `${e.from}->${e.to}`, from: e.from, to: e.to, main: e.wallets_main, amb: e.wallets_ambiguous, source: e.from, target: e.to })
  }
  // seed positions: new nodes near a neighbour, else on a ring; z by band (foreground closest to camera)
  for (const l of links) {
    const a = next.get(l.from)!
    const b = next.get(l.to)!
    if (Number.isNaN(a.x) && !Number.isNaN(b.x)) {
      a.x = b.x + (Math.random() - 0.5) * 60
      a.y = b.y + (Math.random() - 0.5) * 60
      a.z = b.z + (Math.random() - 0.5) * 40
    }
    if (Number.isNaN(b.x) && !Number.isNaN(a.x)) {
      b.x = a.x + (Math.random() - 0.5) * 60
      b.y = a.y + (Math.random() - 0.5) * 60
      b.z = a.z + (Math.random() - 0.5) * 40
    }
  }
  for (const n of next.values()) {
    if (Number.isNaN(n.x)) {
      const ang = Math.random() * Math.PI * 2
      const rad = 120 + Math.random() * 260
      n.x = Math.cos(ang) * rad
      n.y = Math.sin(ang) * rad
      n.z = (n.band - 1) * -SLAB_Z * 0.4 + (Math.random() - 0.5) * 60
    }
  }
  const arr = [...next.values()]
  const zTarget = (n: LNode) => (n.band === 0 ? SLAB_Z * 0.35 : n.band === 1 ? 0 : -SLAB_Z * 0.45)
  const sim = forceSimulation<LNode, LLink>(arr, 3)
    .force('link', forceLink<LNode, LLink>(links).id((d) => d.id).distance((l) => 80 + 80 / (1 + Math.sqrt(l.main))).strength((l) => Math.min(0.8, 0.2 + 0.1 * Math.sqrt(l.main))))
    .force('charge', forceManyBody<LNode>().strength((d) => -110 - 20 * d.r).distanceMax(700))
    .force('center', forceCenter<LNode>(0, 0, 0).strength(0.04))
    .force('z', forceZ<LNode>((d) => zTarget(d)).strength(0.12))
    .force('collide', forceCollide<LNode>((d) => d.r + 14).iterations(2))
    .alpha(prev ? 0.35 : 1)
    .alphaDecay(0.03)
    .velocityDecay(0.4)
  sim.tick(ticks)
  sim.stop()
  let radius = 1
  for (const n of arr) radius = Math.max(radius, Math.hypot(n.x, n.y, n.z))
  return { nodes: next, links, radius }
}
