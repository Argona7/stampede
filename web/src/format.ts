export const shortAddr = (a: string) => (a && a.length > 12 ? `${a.slice(0, 6)}…${a.slice(-4)}` : a)

export function utc(ts: number | null | undefined, withDate = false): string {
  if (!ts) return '?'
  const d = new Date(ts * 1000)
  const hh = String(d.getUTCHours()).padStart(2, '0')
  const mm = String(d.getUTCMinutes()).padStart(2, '0')
  const ss = String(d.getUTCSeconds()).padStart(2, '0')
  const t = `${hh}:${mm}:${ss}`
  if (!withDate) return t
  const y = d.getUTCFullYear()
  const mo = String(d.getUTCMonth() + 1).padStart(2, '0')
  const da = String(d.getUTCDate()).padStart(2, '0')
  return `${y}-${mo}-${da} ${t}`
}

export function duration(s: number | null | undefined): string {
  if (s === null || s === undefined) return '?'
  if (s < 60) return `${Math.round(s)} s`
  if (s < 3600) return `${Math.floor(s / 60)} min ${Math.round(s % 60)} s`
  return `${Math.floor(s / 3600)} h ${Math.round((s % 3600) / 60)} min`
}

export function windowName(s: number): string {
  if (s % 3600 === 0) return `${s / 3600} h`
  if (s % 60 === 0) return `${s / 60} min`
  return `${s} s`
}

export function amount(v: number | null | undefined, digits?: number): string {
  if (v === null || v === undefined) return 'n/a'
  if (v === 0) return '0'
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(2)}M`
  if (v >= 1000) return v.toLocaleString('en-US', { maximumFractionDigits: 0 })
  if (v >= 1) return v.toLocaleString('en-US', { maximumFractionDigits: digits ?? 3 })
  return v.toLocaleString('en-US', { maximumSignificantDigits: 3 })
}

export function int(v: number | null | undefined): string {
  return v === null || v === undefined ? '?' : v.toLocaleString('en-US')
}
