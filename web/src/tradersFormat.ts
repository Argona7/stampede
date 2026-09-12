// Number formatting shared by the TRADERS table and the wallet card. null/undefined = unknown -> null (callers show n/a).
export const hold = (s: number | null | undefined): string | null => {
  if (s === null || s === undefined) return null
  if (s < 60) return `${Math.round(s)} s`
  if (s < 3600) return `${Math.floor(s / 60)} min ${String(Math.round(s % 60)).padStart(2, '0')} s`
  return `${Math.floor(s / 3600)} h ${String(Math.floor((s % 3600) / 60)).padStart(2, '0')} min`
}
export const pct = (v: number | null | undefined, d = 0): string | null => (v === null || v === undefined ? null : `${(v * 100).toFixed(d)}%`)
export const signedPct = (v: number | null | undefined): string | null => (v === null || v === undefined ? null : `${v > 0 ? '+' : ''}${Math.round(v * 100)}%`)
export const eth = (v: number | null | undefined, d = 4): string | null => (v === null || v === undefined ? null : `${v > 0 ? '+' : ''}${v.toFixed(d)}`)
export const usd = (v: number | null | undefined): string | null => (v === null || v === undefined ? null : `${v < 0 ? '-' : ''}$${Math.abs(v).toLocaleString('en-US', { maximumFractionDigits: 0 })}`)
