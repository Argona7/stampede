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


// ---- radar ----
export interface RadarSource {
  address: string
  symbol: string
  wallets: number
}

export interface RadarRow extends TokenLabel {
  symbol_raw?: string
  inflow_10m: number
  inflow_prev_per_10m: number
  accel: number
  breadth: number
  wallets_range: number
  sequences_range: number
  sequences_10m: number
  sources: RadarSource[]
  quality: number | null
  age_s: number | null
  stage: 'curve' | 'graduated' | 'unknown'
  progress: number | null
  graduated_ts: number | null
  first_inflow_ts: number
  last_inflow_ts: number
  spark: number[]
  mentions_1h: number | null
  mentions_24h: number | null
  score: number
  parts: Record<string, number | boolean>
  price_quote: number | null
  quote_symbol: string | null
  chg_5m: number | null
  chg_1h: number | null
  vol_1h_quote: number
  buyers_1h: number
  market_now?: { price_usd: number | null; fdv_usd: number | null; reserve_usd: number | null; vol_h1: number | null; chg_h1: number | null; url: string; fetched_at: number }
  holders?: { holders: number; top10_share: number; dev_share: number | null; dev_sold_share: number; launch_block_buyers: number; as_of_block: number }
}

export interface RadarResponse {
  clock: number | null
  window_s: number
  span_s: number
  rows: RadarRow[]
  total: number
  presets: Record<string, { label: string } & Record<string, unknown>>
  preset?: string | null
  bots_excluded?: number
  wallet_scores_known?: number
  context?: { policy: string; enabled: boolean; x_enabled: boolean; last_cycle: number | null; budget: Record<string, { calls_today: number }> }
  waiting?: boolean
  session?: SessionState
}

export interface CoinDetail extends TokenLabel {
  symbol_raw?: string
  launch: { deployer: string; pair_token: string; threshold: number | null; block: number; ts: number | null; tx_hash: string; source: string } | null
  age_s: number | null
  progress: { progress: number | null; stage: string; graduated: boolean; curve_trades: number; curve_traders: number; threshold_raw: number | null; net_quote_raw: number; graduation: { ts: number | null; tx_hash: string | null } | null }
  as_of: { price_quote: number | null; quote_symbol: string | null; chg_5m: number | null; chg_1h: number | null; vol_1h_quote: number; trades_1h: number; buyers_1h: number; buys_1h?: number; as_of_ts: number }
  inbound: { token: TokenLabel; wallets_main: number; wallets_all: number }[]
  outbound: { token: TokenLabel; wallets_main: number; wallets_all: number }[]
  buyers: number
  sellers: number
  new_buyers: number
  socials: { twitter: string | null; telegram: string | null; website: string | null; description: string | null; declared_in: string } | null
  market_now: { found?: boolean; price_usd: number | null; fdv_usd: number | null; reserve_usd: number | null; vol_h1: number | null; vol_h24?: number | null; chg_h1: number | null; chg_h24?: number | null; url: string; fetched_at: number; dex?: string } | null
  market_cached: CoinDetail['market_now']
  mentions: { mentions_1h: number; mentions_24h: number; distinct_authors: number; truncated: boolean; top: { url: string | null; author: string | null; followers: number | null; likes: number | null; text: string }[]; query: string; fetched_at: number } | null
  x_account: { handle: string; name: string | null; followers: number | null; created_at: string | null; verified: boolean | null; url: string } | null
  holders: { holders?: number; top10_share?: number; dev_share?: number | null; dev_sold_share?: number; launch_block_buyers?: number; as_of_block?: number; error?: string; top?: { address: string; share: number }[] } | null
  links: { explorer: string; geckoterminal: string; pons: string }
  note: string
}

export interface AlertRow {
  id: number
  created_ts: number
  clock_ts: number
  mode: string
  token: string
  symbol: string
  rule: string
  score: number
  inflow: number
  mentions_1h: number | null
  price: number | null
  detail: { rank: number; sources: RadarSource[]; accel: number; breadth: number; age_s: number | null; stage: string; mentions_known: boolean } | null
  outcome_30m: number | null
  outcome_60m: number | null
  graduated_after: number | null
}

export interface AlertsResponse {
  alerts: AlertRow[]
  rules: Record<string, Record<string, number>>
  track_record: { fired: number; with_outcome_30m: number; up_30m: number; median_30m: number | null; note: string }
  worker: { running: boolean; last_cycle: number | null; last_error: string | null; cycles: number; context_enabled: boolean; alerts_fired: number }
}
