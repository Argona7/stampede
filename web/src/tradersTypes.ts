// TRADERS view types: rows of `wallet_stats` (the latest full-range run of `stampede traders`), one wallet's card,
// and the batch lookup that marks top-decile wallets in the live feed. null = unknown, never 0.
import type { SessionState, TokenLabel } from './types'

export interface TraderRow {
  wallet: string
  short: string
  explorer: string
  label: string
  range_from: number
  range_to: number
  trades: number
  buys: number
  sells: number
  coins: number
  positions_closed: number
  positions_open: number
  wins: number
  realized_eth: number
  unrealized_eth: number
  cost_eth: number
  fees_eth: number
  pnl_eth: number
  realized_usd: number | null
  unrealized_usd: number | null
  cost_usd: number | null
  pnl_usd: number | null
  usd_complete: boolean
  roi: number | null
  roi_realized: number | null
  win_rate: number | null
  median_hold_s: number | null
  trades_per_hour: number
  buyer_rank_median: number | null
  sniper_share: number | null
  exit_quality: number | null
  rug_avoid: number | null
  consistency: number | null
  weeks_active: number
  weeks_positive: number
  multi_coin_blocks: number
  is_bot: boolean
  deployer_linked: boolean
  quality: number
  tags: string[]
  pnl_by_quote: Record<string, { realized: number; unrealized: number; cost: number; proceeds: number; fees: number }>
  first_ts: number | null
  last_ts: number | null
  fees_unknown: number
  unpriced: number
  unmatched_sells: number
}

export interface TradersResponse {
  preset: string | null
  presets: Record<string, { label: string } & Record<string, unknown>>
  rows: TraderRow[]
  total: number
  run: { run_ts: number; from_ts: number; to_ts: number; wallets: number } | null
  range: { from_ts: number; to_ts: number } | null
  sort?: string
  min_trades?: number
  empty_reason?: string
  session?: SessionState
}

export interface WalletPosition {
  token: TokenLabel
  quote_symbol: string
  entry_ts: number
  exit_ts: number | null
  closed: boolean
  buys: number
  sells: number
  cost_quote: number
  proceeds_quote: number
  pnl_quote: number
  pnl_usd: number | null
  fees_quote: number
  unrealized_quote: number | null
  hold_s: number | null
  buyer_rank: number | null
  since_launch_s: number | null
  sniper: boolean
  snipe_paid: boolean
  exit_quality: number | null
  rug_after: boolean | null
  entry_tx: string | null
  entry_tx_url: string | null
  exit_tx: string | null
  exit_tx_url: string | null
}

export interface WalletTrade {
  id: number
  tx: string
  tx_url: string
  block: number
  ts: number
  ts_exact: boolean
  token: TokenLabel
  side: 'buy' | 'sell'
  token_amount: number
  quote_symbol: string
  quote_amount: number | null
  venue: string
  fee_quote: number | null
  tax_quote: number | null
  snipe_paid: boolean
}

export interface WalletCardData {
  address: string
  short: string
  explorer: string
  run: { run_ts: number; from_ts: number; to_ts: number; wallets: number } | null
  stats: TraderRow | null
  positions: WalletPosition[]
  trades: WalletTrade[]
  trades_total: number
  coins: (TokenLabel & { trades: number; buys: number; sells: number; first_ts: number; last_ts: number })[]
  is_contract: boolean | null
  launched_coins: number
  copy_test: string | null
  note: string
}

export interface LookupEntry {
  quality: number
  pnl_eth: number
  realized_eth: number
  trades: number
  positions_closed: number
  win_rate: number | null
  is_bot: boolean
  deployer_linked: boolean
  tags: string[]
  last_ts: number | null
  sniper_share: number | null
  smart: boolean
}

export interface LookupResponse {
  run: { run_ts: number; from_ts: number; to_ts: number; wallets: number } | null
  wallets: Record<string, LookupEntry>
  top_decile_min_quality: number | null
}

export interface TradersFilters {
  preset: string
  minTrades: number
  minRoi: number | null
  minWinRate: number | null
  activeWithinS: number | null
  sort: string
}

export const DEFAULT_TRADERS: TradersFilters = { preset: 'top', minTrades: 3, minRoi: null, minWinRate: null, activeWithinS: null, sort: 'pnl' }
export const TRADER_SORTS: [string, string][] = [
  ['pnl', 'PnL ETH (real + unreal)'],
  ['realized', 'realized PnL'],
  ['quality', 'quality'],
  ['roi', 'ROI'],
  ['win_rate', 'win rate'],
  ['trades', 'trades'],
  ['recent', 'most recent'],
]
export const PRESET_ORDER = ['top', 'smart', 'snipers', 'bots']

export function presetDefaults(k: string): Partial<TradersFilters> {
  switch (k) {
    case 'smart':
      return { sort: 'quality', minTrades: 5 }
    case 'snipers':
      return { sort: 'pnl', minTrades: 1 }
    case 'bots':
      return { sort: 'trades', minTrades: 1 }
    default:
      return { sort: 'pnl', minTrades: 3 }
  }
}
