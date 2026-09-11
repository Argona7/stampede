// Canvas tape of real observed sequences. On a (re)load the whole visible history streams through at high
// speed with a live counter; afterwards newly observed rows push in from the bottom. Rows are the API's
// events, nothing synthetic. Drawn on a canvas so thousands of rows never touch the DOM.
import { useEffect, useRef } from 'react'
import { utc } from '../format'
import type { SeqEvent } from '../types'

export interface TapeApi {
  boot: (rows: SeqEvent[], label: string) => void
  push: (rows: SeqEvent[]) => void
  clear: () => void
}

interface Props {
  onReady: (api: TapeApi) => void
  width?: number
}

const BOOT_MS = 2600
const ROW_H = 18
const easeOut = (t: number) => 1 - Math.pow(1 - t, 3)
const short = (a: string) => `${a.slice(0, 6)}…${a.slice(-4)}`

export default function Tape({ onReady, width = 470 }: Props) {
  const ref = useRef<HTMLCanvasElement>(null)
  useEffect(() => {
    const canvas = ref.current!
    const ctx = canvas.getContext('2d')!
    let rows: SeqEvent[] = []
    let label = ''
    let bootStart = 0
    let bootTotal = 0
    let shown = 0
    let fresh = new Map<number, number>() // id -> time received
    let raf: number | null = null
    let lastPush = 0
    const dpr = Math.min(2, window.devicePixelRatio || 1)

    const size = () => {
      const r = canvas.parentElement!.getBoundingClientRect()
      canvas.width = Math.round(width * dpr)
      canvas.height = Math.round(r.height * dpr)
      canvas.style.width = `${width}px`
      canvas.style.height = `${r.height}px`
    }
    const ro = new ResizeObserver(() => {
      size()
      draw(performance.now())
    })
    ro.observe(canvas.parentElement!)
    size()

    const draw = (now: number) => {
      const W = canvas.width / dpr
      const H = canvas.height / dpr
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      ctx.clearRect(0, 0, W, H)
      ctx.fillStyle = 'rgba(5,5,5,0.62)'
      ctx.fillRect(0, 0, W, H)
      ctx.fillStyle = '#351419'
      ctx.fillRect(0, 0, 1, H)
      let animating = false
      let count: number
      if (bootStart) {
        const t = Math.min(1, (now - bootStart) / BOOT_MS)
        shown = easeOut(t) * bootTotal
        count = Math.floor(shown)
        animating = t < 1
        if (t >= 1) bootStart = 0
      } else count = rows.length
      // header: what is streaming and how much of it
      ctx.font = '600 12px "IBM Plex Mono", ui-monospace, monospace'
      ctx.fillStyle = '#F2F2F2'
      ctx.textBaseline = 'top'
      const total = bootTotal || rows.length
      const head = bootStart ? `STREAMING ${count.toLocaleString('en-US')} / ${total.toLocaleString('en-US')} OBSERVED SEQUENCES` : `TAPE · ${rows.length.toLocaleString('en-US')} OBSERVED SEQUENCES IN RANGE`
      ctx.fillText(head, 14, 12)
      ctx.font = '11px "IBM Plex Mono", ui-monospace, monospace'
      ctx.fillStyle = '#A3A3A3'
      ctx.fillText(label, 14, 30)
      ctx.fillText('time · wallet · SOLD A → BOUGHT B · grade', 14, 46)
      // progress hairline during boot
      if (bootStart || animating) {
        ctx.fillStyle = '#351419'
        ctx.fillRect(14, 62, W - 28, 1)
        ctx.fillStyle = '#FF3344'
        ctx.fillRect(14, 62, (W - 28) * (count / Math.max(1, total)), 1)
      }
      // rows, newest at the bottom
      const top = 72
      const nRows = Math.floor((H - top - 10) / ROW_H)
      const start = Math.max(0, count - nRows)
      const slideT = lastPush ? Math.min(1, (now - lastPush) / 420) : 1
      const slide = (1 - easeOut(slideT)) * ROW_H
      if (slideT < 1) animating = true
      const baseY = H - 10 - ROW_H
      for (let i = count - 1, k = 0; i >= start; i--, k++) {
        const e = rows[i]
        if (!e) continue
        const y = baseY - k * ROW_H + slide
        if (y < top) break
        const age = k / Math.max(1, nRows)
        const alpha = Math.max(0.25, 1 - age * 0.9)
        const isFresh = fresh.has(e.id) && now - (fresh.get(e.id) ?? 0) < 2500
        if (isFresh) {
          ctx.fillStyle = '#FF3344'
          ctx.fillRect(6, y + 3, 2, ROW_H - 6)
        }
        ctx.globalAlpha = alpha
        ctx.font = '11.5px "IBM Plex Mono", ui-monospace, monospace'
        ctx.fillStyle = '#A3A3A3'
        ctx.fillText(utc(e.buy_ts) + (e.buy_ts_exact ? ' ' : '≈'), 14, y + 3)
        ctx.fillText(short(e.wallet), 86, y + 3)
        ctx.fillStyle = '#F2F2F2'
        ctx.fillText(e.from_symbol.slice(0, 15), 172, y + 3)
        ctx.fillStyle = isFresh ? '#FF3344' : '#747474'
        ctx.fillText('→', 282, y + 3)
        ctx.fillStyle = '#F2F2F2'
        ctx.fillText(e.to_symbol.slice(0, 15), 298, y + 3)
        ctx.fillStyle = e.grade === 'ambiguous' ? '#747474' : '#A3A3A3'
        ctx.fillText(e.grade === 'direct' ? 'direct' : e.grade === 'clean' ? 'clean' : 'amb.', 412, y + 3)
        ctx.globalAlpha = 1
      }
      if (animating) raf = requestAnimationFrame(draw)
      else raf = null
    }
    const kick = () => {
      if (raf === null) raf = requestAnimationFrame(draw)
    }
    onReady({
      boot: (r, l) => {
        rows = r
        label = l
        bootTotal = r.length
        bootStart = performance.now()
        fresh = new Map()
        lastPush = 0
        kick()
      },
      push: (r) => {
        if (!r.length) return
        const now = performance.now()
        for (const e of r) fresh.set(e.id, now)
        rows = [...rows, ...r].slice(-4000)
        lastPush = now
        if (fresh.size > 5000) fresh = new Map([...fresh].slice(-1000))
        kick()
      },
      clear: () => {
        rows = []
        bootTotal = 0
        bootStart = 0
        kick()
      },
    })
    return () => {
      ro.disconnect()
      if (raf) cancelAnimationFrame(raf)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  return <canvas ref={ref} className="tape" aria-hidden="true" />
}
