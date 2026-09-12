import type { LookupResponse, TradersResponse, WalletCardData } from './tradersTypes'
import type { AlertsResponse, CoinDetail, EdgeDetail, Graph, RadarResponse, Status, TokenDetail, TokenLabel } from './types'

async function get<T>(url: string): Promise<T> {
  const r = await fetch(url)
  if (!r.ok) throw new Error(`${r.status} ${url}`)
  return (await r.json()) as T
}

const q = (params: Record<string, string | number | null | undefined>) =>
  Object.entries(params)
    .filter(([, v]) => v !== null && v !== undefined && v !== '')
    .map(([k, v]) => `${k}=${encodeURIComponent(typeof v === 'number' ? String(Math.round(v)) : String(v))}`)
    .join('&')

export const api = {
  status: () => get<Status>('/api/status'),
  graph: (window_s: number, from: number | null, to: number | null, minWallets: number, limit = 250) =>
    get<Graph>(`/api/graph?${q({ window: `${window_s}s`, from, to, min_wallets: minWallets, limit })}`),
  edge: (a: string, b: string, window_s: number, from: number | null, to: number | null, limit = 60, exact = true) =>
    get<EdgeDetail>(`/api/edge/${a}/${b}?${q({ window: `${window_s}s`, from, to, limit, exact: exact ? 1 : 0 })}`),
  token: (addr: string, window_s: number, from: number | null, to: number | null) =>
    get<TokenDetail>(`/api/token/${addr}?${q({ window: `${window_s}s`, from, to })}`),
  search: (text: string) => get<TokenLabel[]>(`/api/search?${q({ q: text })}`),
  radar: (p: Record<string, string | number | null | undefined>) => get<RadarResponse>(`/api/radar?${q(p)}`),
  coin: (addr: string, refresh = false) => get<CoinDetail>(`/api/coin/${addr}?${q({ refresh: refresh ? 1 : 0 })}`),
  alerts: () => get<AlertsResponse>('/api/alerts'),
  traders: (p: Record<string, string | number | null | undefined>) => get<TradersResponse>(`/api/traders?${q(p)}`),
  wallet: (addr: string, clock: number | null = null) => get<WalletCardData>(`/api/wallet/${addr}?${q({ clock })}`),
  tradersLookup: (wallets: string[]) => get<LookupResponse>(`/api/traders/lookup?${q({ wallets: wallets.join(',') })}`),
}
