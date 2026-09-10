// WebGL scene (three.js): a spatial graph with depth, a directed flight to the selected pair and event pulses.
// Everything drawn maps to data: nodes = coins in range, lines = observed edges, pulses = confirmed sequences
// received since the previous poll. Colour is used for state only (white outline = A · SOLD, red fill = B · BOUGHT).
import { useEffect, useRef, useState } from 'react'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { LineMaterial } from 'three/examples/jsm/lines/LineMaterial.js'
import { LineSegments2 } from 'three/examples/jsm/lines/LineSegments2.js'
import { LineSegmentsGeometry } from 'three/examples/jsm/lines/LineSegmentsGeometry.js'
import type { GEdge, GNode, Selection } from '../types'
import { layout3d, type LNode, type Layout } from './layout3d'

export type ViewState = 'overview' | 'follow' | 'evidence'

export interface Pulse {
  key: string // edge key from->to
  count: number
  at: number // performance.now() when received
}

export interface PerfStats {
  fps: number
  p50_ms: number
  p95_ms: number
  frames: number
  drawn_edges: number
  nodes: number
}

interface Props {
  nodes: GNode[]
  edges: GEdge[]
  selection: Selection
  showAmbiguous: boolean
  pulses: Pulse[]
  viewState: ViewState
  reducedMotion: boolean
  onSelect: (s: Selection) => void
  onHover: (s: Selection) => void
  onViewState: (v: ViewState) => void
  onPerf?: (p: PerfStats) => void
  focusNonce: number
  labelBudget: number // max labelled nodes in overview
  panelOpen?: boolean // evidence panel covers the right side: keep the pair in the left 55%
}

const BG = 0x050505
const RED = new THREE.Color('#FF3344')
const WHITE = new THREE.Color('#F2F2F2')
const GREY = [new THREE.Color('#B4B4B4'), new THREE.Color('#7A7A7A'), new THREE.Color('#4A4A4A')]
const EDGE_THIN = new THREE.Color('#3A3A3A')
const EDGE_MID = new THREE.Color('#6F6F6F')
const EDGE_THICK = new THREE.Color('#A3A3A3')
const PULSE_MS = 2000
const CURVE_SEGMENTS = 10

const ease = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2)

interface Key {
  pos: THREE.Vector3
  look: THREE.Vector3
  dur: number
  onDone?: () => void
}

function curvePoints(a: LNode, b: LNode, segments = CURVE_SEGMENTS): THREE.Vector3[] {
  const A = new THREE.Vector3(a.x, a.y, a.z)
  const B = new THREE.Vector3(b.x, b.y, b.z)
  const dir = new THREE.Vector3().subVectors(B, A)
  const d = dir.length() || 1
  const n = new THREE.Vector3().crossVectors(dir, new THREE.Vector3(0, 0, 1)).normalize()
  if (n.lengthSq() < 0.5) n.set(0, 1, 0)
  const mid = new THREE.Vector3().addVectors(A, B).multiplyScalar(0.5).addScaledVector(n, Math.min(40, 0.18 * d)).add(new THREE.Vector3(0, 0, Math.min(24, 0.08 * d)))
  const c = new THREE.QuadraticBezierCurve3(A, mid, B)
  return c.getPoints(segments)
}

export default function Scene3D(p: Props) {
  const [layoutVersion, setLayoutVersion] = useState(0)
  const firstFlightDone = useRef(false)
  const wrapRef = useRef<HTMLDivElement>(null)
  const labelsRef = useRef<HTMLDivElement>(null)
  const st = useRef<{
    renderer: THREE.WebGLRenderer
    scene: THREE.Scene
    camera: THREE.PerspectiveCamera
    controls: OrbitControls
    layout: Layout | null
    nodeMesh: THREE.InstancedMesh | null
    nodeIndex: string[]
    lineGroup: THREE.Group
    lineMaterials: LineMaterial[]
    selGroup: THREE.Group
    pulseGroup: THREE.Group
    edgePoints: Map<string, THREE.Vector3[]>
    keys: Key[]
    keyStart: number
    keyFrom: { pos: THREE.Vector3; look: THREE.Vector3 } | null
    look: THREE.Vector3
    activePulses: { key: string; count: number; start: number; mesh: THREE.Mesh; line: THREE.Line; label: HTMLDivElement }[]
    seenPulse: Set<string>
    raf: number | null
    needs: boolean
    frameTimes: number[]
    lastFrame: number
    frames: number
    labelEls: Map<string, HTMLDivElement>
    hover: Selection
    drawnEdges: number
  } | null>(null)
  const propsRef = useRef(p)
  propsRef.current = p
  const lastSelRef = useRef<Selection | undefined>(undefined)
  const lastNonceRef = useRef<number>(-1)
  const framedRadius = useRef(1)
  const userMoved = useRef(false)
  const lastPanelRef = useRef(false)

  // ---- init ----
  useEffect(() => {
    const wrap = wrapRef.current!
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false, powerPreference: 'high-performance' })
    renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1))
    renderer.setClearColor(BG, 1)
    wrap.appendChild(renderer.domElement)
    const scene = new THREE.Scene()
    scene.fog = new THREE.Fog(BG, 900, 2600)
    const camera = new THREE.PerspectiveCamera(42, 1, 1, 8000)
    camera.position.set(120, -380, 1300)
    const controls = new OrbitControls(camera, renderer.domElement)
    controls.enableDamping = true
    controls.dampingFactor = 0.12
    controls.autoRotate = false
    controls.minDistance = 40
    controls.maxDistance = 4000
    controls.addEventListener('start', () => {
      // manual control interrupts any automatic flight and pins the camera until the next selection
      userMoved.current = true
      if (st.current) {
        st.current.keys = []
        st.current.keyFrom = null
      }
    })
    controls.addEventListener('change', () => {
      if (st.current) st.current.needs = true
    })
    scene.add(new THREE.HemisphereLight(0xffffff, 0x222222, 1.1))
    const dl = new THREE.DirectionalLight(0xffffff, 1.2)
    dl.position.set(300, -200, 900)
    scene.add(dl)
    const lineGroup = new THREE.Group()
    const selGroup = new THREE.Group()
    const pulseGroup = new THREE.Group()
    scene.add(lineGroup, selGroup, pulseGroup)
    st.current = {
      renderer,
      scene,
      camera,
      controls,
      layout: null,
      nodeMesh: null,
      nodeIndex: [],
      lineGroup,
      lineMaterials: [],
      selGroup,
      pulseGroup,
      edgePoints: new Map(),
      keys: [],
      keyStart: 0,
      keyFrom: null,
      look: new THREE.Vector3(0, 0, 0),
      activePulses: [],
      seenPulse: new Set(),
      raf: null,
      needs: true,
      frameTimes: [],
      lastFrame: 0,
      frames: 0,
      labelEls: new Map(),
      hover: null,
      drawnEdges: 0,
    }
    const ro = new ResizeObserver(() => {
      const r = wrap.getBoundingClientRect()
      renderer.setSize(r.width, r.height, false)
      renderer.domElement.style.width = `${r.width}px`
      renderer.domElement.style.height = `${r.height}px`
      camera.aspect = r.width / Math.max(1, r.height)
      camera.updateProjectionMatrix()
      for (const m of st.current!.lineMaterials) m.resolution.set(r.width, r.height)
      st.current!.needs = true
      loop()
    })
    ro.observe(wrap)
    const loop = () => {
      const s = st.current
      if (!s || s.raf !== null) return
      s.raf = requestAnimationFrame(frame)
    }
    const frame = (now: number) => {
      const s = st.current
      if (!s) return
      s.raf = null
      let animating = false
      // camera keyframes
      if (s.keys.length) {
        const k = s.keys[0]
        if (!s.keyFrom) {
          s.keyFrom = { pos: s.camera.position.clone(), look: s.look.clone() }
          s.keyStart = now
        }
        const t = Math.min(1, (now - s.keyStart) / Math.max(1, k.dur))
        const e = ease(t)
        s.camera.position.lerpVectors(s.keyFrom.pos, k.pos, e)
        s.look.lerpVectors(s.keyFrom.look, k.look, e)
        s.controls.target.copy(s.look)
        s.camera.lookAt(s.look)
        if (t >= 1) {
          s.keys.shift()
          s.keyFrom = null
          k.onDone?.()
        }
        animating = true
      } else {
        s.controls.update()
        s.look.copy(s.controls.target)
      }
      // pulses
      const live: typeof s.activePulses = []
      for (const pu of s.activePulses) {
        const t = (now - pu.start) / PULSE_MS
        const pts = s.edgePoints.get(pu.key)
        if (t >= 1 || !pts) {
          s.pulseGroup.remove(pu.mesh)
          s.pulseGroup.remove(pu.line)
          pu.label.remove()
          continue
        }
        const i = Math.min(pts.length - 2, Math.floor(t * (pts.length - 1)))
        const f = t * (pts.length - 1) - i
        pu.mesh.position.lerpVectors(pts[i], pts[i + 1], f)
        // marker keeps a screen-constant size (~7 px) so it reads at overview distance too
        const dist = s.camera.position.distanceTo(pu.mesh.position)
        pu.mesh.scale.setScalar(Math.max(1, dist * 0.0032) * (1 + 0.5 * Math.sin(Math.PI * t)))
        ;(pu.line.material as THREE.LineBasicMaterial).opacity = 0.9 * (1 - t * 0.6)
        live.push(pu)
        animating = true
      }
      s.activePulses = live
      if (s.controls.enableDamping && !s.keys.length) animating = animating || false
      // frame timing (only while rendering continuously)
      if (s.lastFrame) {
        const dt = now - s.lastFrame
        if (dt < 500) {
          s.frameTimes.push(dt)
          if (s.frameTimes.length > 180) s.frameTimes.shift()
        }
      }
      s.lastFrame = now
      s.frames++
      s.renderer.render(s.scene, s.camera)
      placeLabels()
      if (s.frames % 30 === 0 && propsRef.current.onPerf && s.frameTimes.length > 10) {
        const arr = [...s.frameTimes].sort((a, b) => a - b)
        const p50 = arr[Math.floor(arr.length * 0.5)]
        const p95 = arr[Math.floor(arr.length * 0.95)]
        propsRef.current.onPerf({ fps: Math.round(1000 / p50), p50_ms: Math.round(p50 * 10) / 10, p95_ms: Math.round(p95 * 10) / 10, frames: s.frames, drawn_edges: s.drawnEdges, nodes: s.nodeIndex.length })
      }
      s.needs = false
      if (animating || s.needs) loop()
      else {
        // keep damping settled: one more frame later if controls still moving
        s.lastFrame = 0
      }
    }
    const placeLabels = () => {
      const s = st.current!
      const cont = labelsRef.current!
      const r = s.renderer.domElement.getBoundingClientRect()
      const W = r.width
      const H = r.height
      const v = new THREE.Vector3()
      const sel = propsRef.current.selection
      const selNodes = new Set<string>()
      if (sel?.kind === 'edge') {
        selNodes.add(sel.from)
        selNodes.add(sel.to)
      } else if (sel?.kind === 'token') selNodes.add(sel.address)
      const hov = s.hover?.kind === 'token' ? s.hover.address : null
      const vs = propsRef.current.viewState
      // which nodes get a label
      const want = new Map<string, 'big' | 'small'>()
      for (const id of selNodes) want.set(id, 'big')
      if (hov && !want.has(hov)) want.set(hov, 'small')
      if (vs === 'overview' && s.layout) {
        const budget = propsRef.current.labelBudget
        const ranked = [...s.layout.nodes.values()].sort((a, b) => b.degree - a.degree)
        for (const n of ranked.slice(0, budget)) if (!want.has(n.id)) want.set(n.id, 'small')
      }
      // remove stale
      for (const [id, el] of s.labelEls) {
        if (!want.has(id)) {
          el.remove()
          s.labelEls.delete(id)
        }
      }
      for (const [id, kind] of want) {
        const n = s.layout?.nodes.get(id)
        if (!n) continue
        let el = s.labelEls.get(id)
        if (!el) {
          el = document.createElement('div')
          el.className = 'lbl'
          cont.appendChild(el)
          s.labelEls.set(id, el)
        }
        v.set(n.x, n.y, n.z).project(s.camera)
        const behind = v.z > 1
        const x = ((v.x + 1) / 2) * W
        const y = ((1 - v.y) / 2) * H
        const dist = s.camera.position.distanceTo(new THREE.Vector3(n.x, n.y, n.z))
        const fade = Math.max(0.25, Math.min(1, 1 - (dist - 900) / 1700))
        // projected radius in px so the label clears the sphere at any zoom
        const rpx = (n.r * (H / 2)) / (dist * Math.tan((s.camera.fov * Math.PI) / 360))
        el.style.transform = `translate(-50%, 0) translate(${x.toFixed(1)}px, ${(y + rpx * (kind === 'big' ? 1.4 : 1.1) + 6).toFixed(1)}px)`
        el.style.display = behind || x < -50 || x > W + 50 || y < -20 || y > H + 20 ? 'none' : 'block'
        el.style.opacity = kind === 'big' ? '1' : String(fade)
        const role = sel?.kind === 'edge' ? (id === sel.from ? 'A · SOLD' : id === sel.to ? 'B · BOUGHT' : '') : ''
        const cls = `lbl ${kind}` + (role.startsWith('A') ? ' a' : role.startsWith('B') ? ' b' : '')
        if (el.className !== cls) el.className = cls
        const txt = kind === 'big' ? `<span class="role">${role}</span><span class="sym">${n.symbol}</span><span class="addr">${n.short}</span>` : `<span class="sym">${n.symbol}</span>`
        if (el.innerHTML !== txt) el.innerHTML = txt
      }
    }
    ;(st.current as unknown as { loop: () => void }).loop = loop
    // pointer
    const el = renderer.domElement
    const pick = (ev: MouseEvent): Selection => {
      const s = st.current!
      const r = el.getBoundingClientRect()
      const mx = ev.clientX - r.left
      const my = ev.clientY - r.top
      const nd = new THREE.Vector2((mx / r.width) * 2 - 1, -(my / r.height) * 2 + 1)
      if (s.nodeMesh) {
        const rc = new THREE.Raycaster()
        rc.setFromCamera(nd, s.camera)
        const hit = rc.intersectObject(s.nodeMesh, false)[0]
        if (hit && hit.instanceId !== undefined) return { kind: 'token', address: s.nodeIndex[hit.instanceId] }
      }
      // edges: screen-space distance to sampled curve points (drawn edges only)
      let best: Selection = null
      let bestD = 9
      const v = new THREE.Vector3()
      for (const [key, pts] of s.edgePoints) {
        for (const pt of pts) {
          v.copy(pt).project(s.camera)
          if (v.z > 1) continue
          const x = ((v.x + 1) / 2) * r.width
          const y = ((1 - v.y) / 2) * r.height
          const d = Math.hypot(x - mx, y - my)
          if (d < bestD) {
            bestD = d
            const [from, to] = key.split('->')
            best = { kind: 'edge', from, to }
          }
        }
      }
      return best
    }
    let moved = false
    let downAt = 0
    el.addEventListener('pointerdown', () => {
      moved = false
      downAt = performance.now()
    })
    el.addEventListener('pointermove', (ev) => {
      if (performance.now() - downAt < 400 && (ev.buttons & 1) === 1) moved = true
      const h = pick(ev)
      const s = st.current!
      if (JSON.stringify(h) !== JSON.stringify(s.hover)) {
        s.hover = h
        propsRef.current.onHover(h)
        el.style.cursor = h ? 'pointer' : 'grab'
        s.needs = true
        loop()
      }
    })
    el.addEventListener('click', (ev) => {
      if (moved) return
      propsRef.current.onSelect(pick(ev))
    })
    loop()
    return () => {
      ro.disconnect()
      if (st.current?.raf) cancelAnimationFrame(st.current.raf)
      controls.dispose()
      renderer.dispose()
      wrap.removeChild(renderer.domElement)
      st.current = null
    }
  }, [])

  // ---- data -> layout + meshes ----
  useEffect(() => {
    const s = st.current
    if (!s) return
    if (p.nodes.length === 0 && !s.layout) return
    const layout = layout3d(p.nodes, p.edges, s.layout)
    s.layout = layout
    // nodes
    if (s.nodeMesh) {
      s.scene.remove(s.nodeMesh)
      s.nodeMesh.geometry.dispose()
    }
    const arr = [...layout.nodes.values()]
    const geo = new THREE.SphereGeometry(1, 14, 10)
    const mat = new THREE.MeshLambertMaterial({ color: 0xffffff })
    const mesh = new THREE.InstancedMesh(geo, mat, Math.max(1, arr.length))
    const m = new THREE.Matrix4()
    const sel = propsRef.current.selection
    arr.forEach((n, i) => {
      m.makeScale(n.r, n.r, n.r).setPosition(n.x, n.y, n.z)
      mesh.setMatrixAt(i, m)
      mesh.setColorAt(i, GREY[n.band])
    })
    if (sel?.kind === 'edge') {
      const ia = arr.findIndex((n) => n.id === sel.from)
      const ib = arr.findIndex((n) => n.id === sel.to)
      if (ia >= 0) mesh.setColorAt(ia, WHITE)
      if (ib >= 0) mesh.setColorAt(ib, RED)
    } else if (sel?.kind === 'token') {
      const i = arr.findIndex((n) => n.id === sel.address)
      if (i >= 0) mesh.setColorAt(i, RED)
    }
    mesh.instanceMatrix.needsUpdate = true
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true
    mesh.count = arr.length
    s.scene.add(mesh)
    s.nodeMesh = mesh
    s.nodeIndex = arr.map((n) => n.id)
    // edges in three weight buckets + ambiguous dashed
    s.lineGroup.clear()
    for (const lm of s.lineMaterials) lm.dispose()
    s.lineMaterials = []
    s.edgePoints.clear()
    const r = s.renderer.domElement.getBoundingClientRect()
    const links = layout.links.filter((l) => l.main > 0 || (p.showAmbiguous && l.amb > 0))
    const sorted = [...links].sort((a, b) => b.main - a.main)
    const thickSet = new Set(sorted.slice(0, 24).map((l) => l.key))
    const thin: number[] = []
    const thinCol: number[] = []
    const mid: number[] = []
    const thick: number[] = []
    const amb: number[] = []
    const pushSeg = (arr: number[], pts: THREE.Vector3[]) => {
      for (let i = 0; i < pts.length - 1; i++) arr.push(pts[i].x, pts[i].y, pts[i].z, pts[i + 1].x, pts[i + 1].y, pts[i + 1].z)
    }
    for (const l of links) {
      const a = layout.nodes.get(l.from)!
      const b = layout.nodes.get(l.to)!
      const pts = curvePoints(a, b)
      s.edgePoints.set(l.key, pts)
      if (l.main === 0) pushSeg(amb, pts)
      else if (thickSet.has(l.key) && l.main >= 3) pushSeg(thick, pts)
      else if (l.main >= 3) pushSeg(mid, pts)
      else {
        pushSeg(thin, pts)
        for (let i = 0; i < (pts.length - 1) * 2; i++) thinCol.push(EDGE_THIN.r, EDGE_THIN.g, EDGE_THIN.b)
      }
    }
    if (thin.length) {
      const g = new THREE.BufferGeometry()
      g.setAttribute('position', new THREE.Float32BufferAttribute(thin, 3))
      g.setAttribute('color', new THREE.Float32BufferAttribute(thinCol, 3))
      s.lineGroup.add(new THREE.LineSegments(g, new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: 0.9 })))
    }
    if (amb.length) {
      const g = new THREE.BufferGeometry()
      g.setAttribute('position', new THREE.Float32BufferAttribute(amb, 3))
      const dm = new THREE.LineDashedMaterial({ color: 0x4a4a4a, dashSize: 6, gapSize: 6, transparent: true, opacity: 0.8 })
      const ls = new THREE.LineSegments(g, dm)
      ls.computeLineDistances()
      s.lineGroup.add(ls)
    }
    const fat = (segs: number[], width: number, color: THREE.Color) => {
      if (!segs.length) return
      const g = new LineSegmentsGeometry()
      g.setPositions(segs)
      const lm = new LineMaterial({ color: color.getHex(), linewidth: width, worldUnits: false, transparent: true, opacity: 0.95 })
      lm.resolution.set(r.width || 1440, r.height || 900)
      s.lineMaterials.push(lm)
      s.lineGroup.add(new LineSegments2(g, lm))
    }
    fat(mid, 2, EDGE_MID)
    fat(thick, 3.5, EDGE_THICK)
    s.drawnEdges = links.length
    // fog around the layout size
    ;(s.scene.fog as THREE.Fog).near = layout.radius * 2.6 + 300
    ;(s.scene.fog as THREE.Fog).far = layout.radius * 5.5 + 600
    rebuildSelection()
    s.needs = true
    setLayoutVersion((v) => v + 1)
    ;(s as unknown as { loop: () => void }).loop()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [p.nodes, p.edges, p.showAmbiguous])

  const rebuildSelection = () => {
    const s = st.current
    if (!s || !s.layout) return
    s.selGroup.clear()
    const sel = propsRef.current.selection
    if (!sel) return
    if (sel.kind === 'edge') {
      const a = s.layout.nodes.get(sel.from)
      const b = s.layout.nodes.get(sel.to)
      if (!a || !b) return
      const pts = curvePoints(a, b, 40)
      const curve = new THREE.CatmullRomCurve3(pts)
      const tube = new THREE.Mesh(new THREE.TubeGeometry(curve, 48, Math.max(1.2, Math.min(2.6, 0.8 + 0.25 * Math.sqrt(a.degree + b.degree) * 0.5)), 8, false), new THREE.MeshBasicMaterial({ color: RED }))
      s.selGroup.add(tube)
      // arrow head at B
      const end = pts[pts.length - 1]
      const before = pts[pts.length - 4]
      const dir = new THREE.Vector3().subVectors(end, before).normalize()
      const cone = new THREE.Mesh(new THREE.ConeGeometry(b.r * 0.35 + 2, b.r * 0.9 + 6, 12), new THREE.MeshBasicMaterial({ color: RED }))
      cone.position.copy(end).addScaledVector(dir, -(b.r + 3))
      cone.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir)
      s.selGroup.add(cone)
      // A: white outline ring
      const ring = new THREE.Mesh(new THREE.TorusGeometry(a.r + 4, 1.2, 8, 40), new THREE.MeshBasicMaterial({ color: WHITE }))
      ring.position.set(a.x, a.y, a.z)
      ring.lookAt(s.camera.position)
      s.selGroup.add(ring)
      // B: red filled sphere on top
      const bs = new THREE.Mesh(new THREE.SphereGeometry(b.r + 1.5, 20, 14), new THREE.MeshLambertMaterial({ color: RED }))
      bs.position.set(b.x, b.y, b.z)
      s.selGroup.add(bs)
    } else {
      const n = s.layout.nodes.get(sel.address)
      if (!n) return
      const ring = new THREE.Mesh(new THREE.TorusGeometry(n.r + 4, 1.2, 8, 40), new THREE.MeshBasicMaterial({ color: RED }))
      ring.position.set(n.x, n.y, n.z)
      ring.lookAt(s.camera.position)
      s.selGroup.add(ring)
    }
  }

  // ---- selection -> recolour + flight ----
  useEffect(() => {
    const s = st.current
    if (!s || !s.layout) return
    const arr = [...s.layout.nodes.values()]
    if (s.nodeMesh) {
      arr.forEach((n, i) => s.nodeMesh!.setColorAt(i, GREY[n.band]))
      const sel = p.selection
      if (sel?.kind === 'edge') {
        const ia = arr.findIndex((n) => n.id === sel.from)
        const ib = arr.findIndex((n) => n.id === sel.to)
        if (ia >= 0) s.nodeMesh.setColorAt(ia, WHITE)
        if (ib >= 0) s.nodeMesh.setColorAt(ib, RED)
      } else if (sel?.kind === 'token') {
        const i = arr.findIndex((n) => n.id === sel.address)
        if (i >= 0) s.nodeMesh.setColorAt(i, RED)
      }
      if (s.nodeMesh.instanceColor) s.nodeMesh.instanceColor.needsUpdate = true
    }
    rebuildSelection()
    s.needs = true
    ;(s as unknown as { loop: () => void }).loop()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [p.selection, p.focusNonce])

  useEffect(() => {
    const s = st.current
    if (!s || !s.layout) return
    const R = s.layout.radius
    if (s.layout.nodes.size === 0) return
    const selectionChanged = lastSelRef.current !== p.selection || lastNonceRef.current !== p.focusNonce
    const panelChanged = lastPanelRef.current !== !!p.panelOpen
    lastPanelRef.current = !!p.panelOpen
    const radiusJump = Math.abs(R - framedRadius.current) / Math.max(1, framedRadius.current) > 0.6
    if (!selectionChanged && !panelChanged && firstFlightDone.current && (!radiusJump || userMoved.current)) return // data refresh: keep the camera where the user left it
    const onlyReframe = !selectionChanged && panelChanged && p.selection?.kind === 'edge' // panel toggled: short reframe, no full flight
    lastSelRef.current = p.selection
    lastNonceRef.current = p.focusNonce
    firstFlightDone.current = true
    framedRadius.current = R
    if (selectionChanged) userMoved.current = false
    const instant = p.reducedMotion
    const fly = (keys: Key[]) => {
      if (instant) {
        const last = keys[keys.length - 1]
        s.camera.position.copy(last.pos)
        s.look.copy(last.look)
        s.controls.target.copy(last.look)
        s.camera.lookAt(last.look)
        keys.forEach((k) => k.onDone?.())
        s.keys = []
      } else {
        s.keys = keys
        s.keyFrom = null
      }
      s.needs = true
      ;(s as unknown as { loop: () => void }).loop()
    }
    if (!p.selection) {
      // OVERVIEW: the whole slab from a 3/4 angle; no orbit, no rotation
      fly([{ pos: new THREE.Vector3(0.18 * R, -0.5 * R, 1.75 * R + 200), look: new THREE.Vector3(0, 0, 0), dur: 900, onDone: () => propsRef.current.onViewState('overview') }])
      return
    }
    if (p.selection.kind === 'edge') {
      const a = s.layout.nodes.get(p.selection.from)
      const b = s.layout.nodes.get(p.selection.to)
      if (!a || !b) return
      const A = new THREE.Vector3(a.x, a.y, a.z)
      const B = new THREE.Vector3(b.x, b.y, b.z)
      const d = Math.max(90, A.distanceTo(B))
      const ab = new THREE.Vector3().subVectors(B, A).normalize()
      const up = new THREE.Vector3(0, 0, 1)
      const side = new THREE.Vector3().crossVectors(ab, up).normalize()
      if (side.lengthSq() < 0.5) side.set(0, 1, 0)
      const mid = new THREE.Vector3().addVectors(A, B).multiplyScalar(0.5)
      const near = Math.max(420, 1.7 * d)
      // FOLLOW 1: come down to A (A large, neighbours pass by in the foreground)
      const k1: Key = { pos: A.clone().add(side.clone().multiplyScalar(0.45 * near)).add(new THREE.Vector3(0, -0.35 * near, 0.85 * near)), look: A.clone(), dur: 800, onDone: () => propsRef.current.onViewState('follow') }
      // FOLLOW 2: travel along the route to B, B in front
      const k2: Key = { pos: B.clone().add(side.clone().multiplyScalar(0.4 * near)).add(new THREE.Vector3(0, -0.3 * near, 0.75 * near)), look: B.clone(), dur: 1000 }
      // EVIDENCE: settle where both are visible, apart, with the route between them and room for the caption
      const far = Math.max(560, 2.2 * d + 260)
      // framing in camera terms: the caption owns the bottom-left, the evidence panel the right 45%.
      // Without the panel the pair sits upper-right of centre; with it, upper-left-of-centre.
      const dir = new THREE.Vector3(0.12, -0.3, 0.9).normalize() // from the look point towards the camera
      const viewDir = dir.clone().negate()
      const camRight = new THREE.Vector3().crossVectors(viewDir, s.camera.up).normalize() // screen-right in world space
      const camUp = new THREE.Vector3().crossVectors(camRight, viewDir).normalize() // screen-up in world space
      const shiftX = p.panelOpen ? 0.12 : -0.3
      const look = mid.clone().addScaledVector(camRight, shiftX * far).addScaledVector(camUp, -0.2 * far)
      const k3: Key = {
        pos: look.clone().addScaledVector(dir, far),
        look,
        dur: onlyReframe ? 500 : 700,
        onDone: () => propsRef.current.onViewState('evidence'),
      }
      fly(onlyReframe ? [k3] : [k1, k2, k3])
      return
    }
    const n = s.layout.nodes.get(p.selection.address)
    if (!n) return
    const N = new THREE.Vector3(n.x, n.y, n.z)
    fly([{ pos: N.clone().add(new THREE.Vector3(90, -220, 520)), look: N.clone(), dur: 1100, onDone: () => propsRef.current.onViewState('evidence') }])
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [p.selection, p.focusNonce, p.reducedMotion, layoutVersion, p.panelOpen])

  // ---- pulses: one travelling marker per newly observed batch on an edge ----
  useEffect(() => {
    const s = st.current
    if (!s) return
    const now = performance.now()
    for (const pu of p.pulses) {
      const id = `${pu.key}@${pu.at}`
      if (s.seenPulse.has(id)) continue
      s.seenPulse.add(id)
      if (!s.edgePoints.has(pu.key)) continue
      if (s.activePulses.length >= 40) break
      const mesh = new THREE.Mesh(new THREE.SphereGeometry(2.2, 12, 8), new THREE.MeshBasicMaterial({ color: RED }))
      s.pulseGroup.add(mesh)
      // the whole edge lights up red for the duration of the pulse (one observed batch, not money)
      const lg = new THREE.BufferGeometry().setFromPoints(s.edgePoints.get(pu.key)!)
      const line = new THREE.Line(lg, new THREE.LineBasicMaterial({ color: RED, transparent: true, opacity: 0.9 }))
      s.pulseGroup.add(line)
      const label = document.createElement('div')
      label.className = 'pulse-lbl'
      label.textContent = `+${pu.count} sequence${pu.count === 1 ? '' : 's'}`
      labelsRef.current?.appendChild(label)
      s.activePulses.push({ key: pu.key, count: pu.count, start: p.reducedMotion ? now - PULSE_MS * 0.6 : now, mesh, line, label })
      // label follows the marker: cheap approach, place at B end
      const pts = s.edgePoints.get(pu.key)!
      const end = pts[pts.length - 1].clone().project(s.camera)
      const r = s.renderer.domElement.getBoundingClientRect()
      label.style.transform = `translate(${(((end.x + 1) / 2) * r.width + 10).toFixed(0)}px, ${(((1 - end.y) / 2) * r.height - 24).toFixed(0)}px)`
      window.setTimeout(() => label.remove(), PULSE_MS + 700)
    }
    if (s.seenPulse.size > 5000) s.seenPulse.clear()
    s.needs = true
    ;(s as unknown as { loop: () => void }).loop()
  }, [p.pulses, p.reducedMotion])

  return (
    <div ref={wrapRef} className="scene3d" style={{ position: 'absolute', inset: 0 }}>
      <div ref={labelsRef} className="scene-labels" />
    </div>
  )
}
