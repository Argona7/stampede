export type Mode = 'fixture' | 'replay' | 'live'

export interface Status {
  mode: Mode
  mode_label: string
  chain: { id: number; name: string; explorer: string }
  sample: { label: string; from_block: number; to_block: number; from_ts: number | null; to_ts: number | null; trades?: number; wallets?: number; tokens?: number; scope?: string }
  store: { first_ts: number | null; last_ts: number | null; last_block: number | null; trades: number; trades_with_time: number; unknown_time_trades: number; scope: string }
  data: { first_ts: number | null; last_ts: number | null; last_block: number | null; trades: number; age_s: number | null; server_time: number }
  session: SessionState
  source: string | null
  coverage: {
    ingest_path: string | null
    swap_logs: number | null
    transfer_logs: number | null
    swap_txs: number | null
    tokens: number
    pools_resolved: number
    pools_unresolved: number | null
    trades: number
    wallets: number
    not_trades: Record<string, number>
    ts_exact_share: number | null
    verify: { sample: number; completeness: string; reproducibility: string; tx_from_agreement: string } | null
    windows_s: number[]
    venues: string
  }
  live: null | { running: boolean; paused: boolean; ticks: number; last_error: string | null; head_block: number | null; head_lag_s: number | null; last_block: number | null; last_ts: number | null; tick_ms: number | null }
  default_window_s: number
  connection: 'ok' | 'error' | 'static' | 'stale'
}

export interface GNode {
  address: string
  symbol: string
  name: string
  source: string | null
  short: string
  buys: number
  sells: number
  buyers: number
  sellers: number
  last_trade_ts: number | null
  in_edge_wallet_sum: number
  out_edge_wallet_sum: number
  in_unique_wallets: number
  out_unique_wallets: number
}

export interface GEdge {
  from: string
  to: string
  wallets_main: number
  wallets_direct: number
  wallets_clean: number
  wallets_ambiguous: number
  sequences: number
  first_ts: number
  last_ts: number
}

export interface Recent {
  wallet: string
  from: string
  to: string
  grade: string
  buy_ts: number
  gap_s: number
  buy_tx: string
  from_label: string
  to_label: string
}

export interface Graph {
  window_s: number
  from_ts: number | null
  to_ts: number | null
  nodes: GNode[]
  edges: GEdge[]
  recent: Recent[]
  totals: { sequences_in_range: number; wallets_in_range: number; wallets_main_in_range: number; edges_returned: number; edges_matching: number; truncated: boolean }
}

export interface Trade {
  id: number
  tx: string
  tx_url: string
  block: number
  ts: number
  ts_exact: boolean
  token: string
  wallet: string
  side: 'buy' | 'sell'
  token_amount: number
  quote_symbol: string
  quote_amount: number | null
  venue: string
  flags: string[]
  tx_from?: string
  tx_to?: string
}

export interface Sequence {
  id: number
  wallet: string
  wallet_url: string
  wallet_is_contract: boolean
  grade: 'direct' | 'clean' | 'ambiguous'
  gap_s: number
  sell: Trade
  buy: Trade
  candidates: null | { sold_in_window: string[]; sold_in_window_n?: number; bought_between: string[] }
}

export interface TokenLabel {
  address: string
  symbol: string
  name: string
  source: string | null
  short: string
}

export interface EdgeDetail {
  from: TokenLabel
  to: TokenLabel
  window_s: number
  wallets_main: number
  wallets_by_grade: Record<string, number>
  sequences_total: number
  sequences: Sequence[]
  truncated: boolean
  meaning: string
}

export interface TokenDetail {
  address: string
  address_url: string
  symbol: string
  name: string
  short: string
  source: string | null
  curve: string | null
  pair_symbol: string | null
  pools: string[]
  range: { from_ts: number | null; to_ts: number | null }
  buys: number
  sells: number
  buyers: number
  sellers: number
  new_buyers_in_range: number
  repeat_buyers_in_range: number
  contract_wallets: number
  venues: Record<string, number>
  first_trade_ts: number | null
  last_trade_ts: number | null
  coverage: { trades: number; exact_timestamps: number; note: string }
  inbound: { token: TokenLabel; wallets_main: number; wallets_all: number }[]
  outbound: { token: TokenLabel; wallets_main: number; wallets_all: number }[]
}

export type Selection = { kind: 'edge'; from: string; to: string } | { kind: 'token'; address: string } | null

export interface SessionState {
  id: string
  rev: number
  mode: Mode
  label: string
  clock_ts: number | null
  playing: boolean
  speed: number
  span_s: number
  window_s: number
  from_ts: number | null
  to_ts: number | null
  at_end: boolean | null
  server_time: number
  controls: boolean
  live_paused?: boolean
}

export interface SeqEvent {
  id: number
  cursor: string
  buy_ts: number
  buy_ts_exact: boolean
  sell_ts: number
  wallet: string
  from: string
  to: string
  from_symbol: string
  to_symbol: string
  from_short: string
  to_short: string
  grade: 'direct' | 'clean' | 'ambiguous'
  gap_s: number
  buy_tx: string
  sell_tx: string
}

export interface EventsPage {
  kind: 'history' | 'new'
  window_s: number
  until: number
  count: number
  events: SeqEvent[]
  next_cursor: string
  has_more: boolean
  session: SessionState
}
