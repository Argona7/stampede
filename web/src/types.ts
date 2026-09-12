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
// Launch quality per coin, precomputed by `stampede launch-intel` from indexed data (docs/RESEARCH-LAUNCHES.md).
// null fields = unknown (the launch predates the indexed range, or the feature is not measurable), never 0.
export interface LaunchIntel {
  observed: boolean // launched inside the indexed trade range: dev buy / bundle / snipe fields are measured
  dev_buy_share: number | null // tokens bought in the launch tx or by the deployer within 5 s, share of supply
  dev_buy_quote: number | null // what the dev paid, in pair-token units (null when the pair's decimals are unknown)
  pair_symbol: string | null
  dev_buy_in_launch_tx: boolean | null
  creator_tax_bps: number | null
  bundle_n: number | null // untaxed buyers inside the 3-s snipe window (proxy for declared exemptions)
  bundle_share: number | null
  exempt_declared_n: number | null // exact SnipeTaxExempted count when indexed
  first_buyers_5s: number | null
  taxed_snipers_3s: number | null
  snipe_tax_paid_quote: number | null
  deployer_prior_launches_30d: number | null
  deployer_prior_graduations_30d: number | null
  deployer_graduation_rate: number | null
  launch_farm: boolean | null
  farm_group_n: number | null
  socials_present: boolean | null
  snipe_window_s: number | null
  snipe_tax_zero_ts: number | null // launch ts + window; lower bound (block.timestamp has 1-s granularity)
  source: string
}

export interface RadarSource {
  address: string
  symbol: string
  wallets: number
}

// Stage 4/5 verdict per coin (signals.verdict): the runner model's probability or the calibrated rules when no model
// file exists (`source`), the exit plan and the risk-engine size. Never a promise: p is a measured out-of-sample rate.
export interface Verdict {
  action: 'ENTER' | 'WAIT' | 'AVOID'
  p_2x_30m: number | null
  p_50_30m?: number | null
  p_minus50_30m: number | null
  ev_per_trade_quote: number | null
  ev_ref_quote?: number | null
  entry_window?: { valid_s: number; note: string }
  exit_plan?: { tp: [number, number][]; sl: number | null; trail: number | null; time_exit_s: number; triggers: string[]; text: string[] }
  size?: { quote: number; allowed: boolean; capped_by: string | null; kelly: number; vol_scale: number }
  reasons: string[]
  source?: 'model' | 'rules'
  thresholds?: { p_enter: number; p_wait: number }
  error?: string
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
  launch?: LaunchIntel | null
  verdict?: Verdict
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
  launch: { deployer: string; pair_token: string; threshold: number | null; block: number; ts: number | null; tx_hash: string; source: string; intel?: LaunchIntel | null } | null
  age_s: number | null
  verdict?: Verdict
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
  detail: { rank: number; sources: RadarSource[]; accel: number; breadth: number; age_s: number | null; stage: string; mentions_known?: boolean; p_2x_30m?: number | null; p_minus50_30m?: number | null; ev_per_trade_quote?: number | null; size_quote?: number | null; exit_plan?: string[] | null; source?: string; engine?: string; reasons?: string[] } | null
  outcome_30m: number | null
  outcome_60m: number | null
  graduated_after: number | null
}

export interface AlertsResponse {
  alerts: AlertRow[]
  rules: Record<string, Record<string, number>>
  track_record: { fired: number; with_outcome_30m: number; up_30m: number; median_30m: number | null; note: string }
  worker: { running: boolean; last_cycle: number | null; last_error: string | null; cycles: number; context_enabled: boolean; alerts_fired: number; alerts_enabled?: boolean }
}

// ---- stage 7: paper ledger (simulated fills at the next block's reserves; engine/paper.py) ----
export interface PaperFill {
  ts: number
  block: number
  side: 'buy' | 'sell'
  tokens: string
  quote: number
  fee: number
  tax: number
  snipe: number
  px: number
  reason: string
  venue: string
}

export interface PaperPosition {
  id: number
  token: string
  symbol: string
  curve: string
  quote_symbol: string | null
  status: 'open' | 'closed'
  alert_ts: number
  alert_block: number
  opened_ts: number
  opened_block: number
  size_quote: number
  tokens: string
  tokens_remaining: string
  tokens_f: number
  entry_px: number
  spot_px_before: number | null
  impact_bps: number | null
  fee_quote: number
  tax_quote: number
  snipe_quote: number
  tax_bps: number
  tax_source: string
  p_2x_30m: number | null
  ev_quote: number | null
  plan: { tp: [number, number][] | null; trail: number | null; sl: number | null; time_s: number; inflow_dies: boolean; text: string[] }
  peak_px: number
  peak_ret: number | null
  tp_done: boolean
  proceeds_quote: number
  exit_fees_quote: number
  mark_px: number | null
  mark_quote: number | null
  unrealized_quote: number | null
  ret: number | null
  closed_ts: number | null
  closed_block: number | null
  exit_reason: string | null
  pnl_quote: number | null
  pnl_usd: number | null
  hold_s: number | null
  updated_ts: number
  fills?: PaperFill[]
}

export interface PaperStats {
  trades: number
  coins: number
  wins: number
  hit_rate: number | null
  expectancy_quote: number | null
  expectancy_pct: number | null
  expectancy_usd: number | null
  usd_known: number
  total_quote: number
  total_usd: number | null
  profit_factor: number | null
  max_drawdown_quote: number
  avg_win_quote: number | null
  avg_loss_quote: number | null
  median_hold_s: number | null
  fees_quote: number
  size_mean_quote: number | null
  exits: Record<string, number>
  per_hour: { hour: string; n: number; wins: number; pnl: number }[]
  by_hour_of_day: { hour: number; n: number; wins: number; pnl: number }[]
  ci95_quote: [number, number] | null
  ci_method: string
  open: number
  unrealized_quote: number
  exposure_quote: number
  skipped_by_risk: number
  simulated: true
  note: string
}

export interface PaperResponse {
  positions: { open: PaperPosition[]; closed: PaperPosition[]; pending: { token: string; symbol: string; alert_ts: number; alert_block: number; size_quote: number }[] }
  equity: { ts: number; realized_quote: number; unrealized_quote: number; equity_quote: number }[]
  stats: PaperStats
  config: { size_cap_quote: number; max_concurrent: number; daily_stop_frac: number; plan: Record<string, unknown>; latency: string; fx: boolean }
  counters: Record<string, number>
  clock: number | null
  simulated: true
  source: 'engine' | 'tables'
  mode: Mode
}

export interface TrackRecordRule {
  fired: number
  coins: number
  due_30m: number
  with_outcome_30m: number
  up_30m: number
  ge_2x_30m: number
  median_30m: number | null
  mean_30m: number | null
  due_60m: number
  with_outcome_60m: number
  up_60m: number
  median_60m: number | null
  graduated_after: number
  engine: number
}

export interface TrackRecord {
  generated_at: number
  since_ts: number | null
  engine_started_at: number | null
  mode: Mode
  alerts: { total: number; by_rule: Record<string, TrackRecordRule>; recent: unknown[] }
  paper: { stats: PaperStats; open: PaperPosition[]; closed: PaperPosition[] }
  perf: null | {
    started_at: number | null
    uptime_s: number | null
    blocks_processed: number | null
    gaps_found: number | null
    gaps_unfilled_blocks: number | null
    latency_p50_ms: number | null
    latency_p95_ms: number | null
    reconnects: number | null
    failovers: number | null
    stalls: number | null
    endpoint: string | null
    events: number | null
    writer_max_lag_s: number | null
  }
  fx: { rows: number; last_hour: number | null }
  simulated: true
  note: string
}

export interface PerfResponse {
  engine: string | null
  uptime_s?: number | null
  blocks?: { processed?: number; gaps_found?: number; gaps_unfilled_blocks?: number }
  latency_ms: Record<string, { n: number; p50: number | null; p90?: number | null; p95: number | null; p99?: number | null; max?: number | null }>
  events?: { last_id: number; per_s_10s?: number }
  note?: string
}

// ---- GET /api/stream frames (docs/ENGINE.md) ----
export type StreamType = 'block' | 'trade' | 'sequence' | 'radar_delta' | 'alert' | 'verdict' | 'position' | 'session'

export interface StreamEvent<T = unknown> {
  id: number | null
  type: StreamType
  ts_emit: number
  block: number | null
  data: T
}

export interface RadarDelta {
  full: boolean
  reason: 'block' | 'tick' | 'snapshot'
  clock: number
  window_s: number
  span_s: number
  rows: (RadarRow & { rank: number | null; price_spot: number | null; reserve_quote: string | null; block: number; updated_ts: number })[]
  removed: string[]
  top: string[]
}

export interface StreamAlert extends Partial<AlertRow> {
  kind: 'fired' | 'outcome'
  key: string
  token: string
  symbol: string
  clock_ts: number
  rule?: string
  price: number | null
  outcome_30m?: number | null
  outcome_60m?: number | null
  graduated_after?: number | null
}

export type StreamPosition = PaperPosition & { kind: 'opened' | 'fill' | 'mark' | 'closed' } | { kind: 'skipped'; token: string; symbol: string; clock: number; block: number; reason: string; capped_by?: string | null }

export interface StreamSession extends Partial<SessionState> {
  hello?: boolean
  engine?: string
  head_block?: number | null
  last_block?: number | null
  head_lag_s?: number | null
  paused?: boolean
  feed?: { endpoint: string | null; connected: boolean; reconnects: number | null; failovers: number | null; last_head_age_s: number | null; pending_blocks: number | null } | null
  last_event_id?: number
  replay_gap?: boolean
  lag_ms?: number | null
  paper?: { open: number; closed: number; pending: number; realized_quote: number } | null
  events_per_s?: number
}
