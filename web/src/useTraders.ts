// State of the TRADERS view: the leaderboard poll (while the view is on screen), the selected wallet's card, and
// the lookup that marks top-decile wallets in the live rotation feed. Kept out of App.tsx so the view is one hook call.
import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from './client'
import { DEFAULT_TRADERS, type LookupEntry, type TradersFilters, type TradersResponse, type WalletCardData } from './tradersTypes'
import type { SeqEvent, SessionState } from './types'

const POLL_MS = 6000

export function useTraders(active: boolean, session: SessionState | null, feed: SeqEvent[], initialWallet: string | null) {
  const [filters, setFilters] = useState<TradersFilters>(DEFAULT_TRADERS)
  const [data, setData] = useState<TradersResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [walletAddr, setWalletAddr] = useState<string | null>(initialWallet)
  const [wallet, setWallet] = useState<WalletCardData | null>(null)
  const [walletError, setWalletError] = useState<string | null>(null)
  const [cardOpen, setCardOpen] = useState(!!initialWallet)
  const [smart, setSmart] = useState<Map<string, LookupEntry>>(new Map())
  const [smartRun, setSmartRun] = useState<{ from_ts: number; to_ts: number } | null>(null)
  const nonce = useRef(0)
  const known = useRef<Set<string>>(new Set()) // wallets already asked for in the lookup
  const clock = session?.clock_ts ?? null
  const tick = clock === null ? 0 : Math.floor(clock / 60)

  // ---- leaderboard: poll while the view is visible; refetch on filter change and once a minute of session time ----
  useEffect(() => {
    if (!active) return
    let alive = true
    let busy = false
    const f = filters
    const run = () => {
      if (busy) return
      busy = true
      api
        .traders({ preset: f.preset, min_trades: f.minTrades, min_roi: f.minRoi, min_win_rate: f.minWinRate, active_within_s: f.activeWithinS, sort: f.sort, limit: 100 })
        .then((d) => {
          if (!alive) return
          setData(d)
          setError(null)
        })
        .catch((e) => alive && setError(String(e.message ?? e)))
        .finally(() => {
          busy = false
        })
    }
    run()
    const id = window.setInterval(run, POLL_MS)
    return () => {
      alive = false
      window.clearInterval(id)
    }
  }, [active, filters, tick])

  // ---- one wallet's card: fetched for the selected address (again once a minute of session time); state changes only
  // when the answer arrives, "loading" is derived from the selection vs the card on hand ----
  useEffect(() => {
    if (!walletAddr) return
    const n = ++nonce.current
    let alive = true
    api
      .wallet(walletAddr)
      .then((c) => {
        if (!alive || nonce.current !== n) return
        setWallet(c)
        setWalletError(null)
      })
      .catch((e) => alive && nonce.current === n && setWalletError(String(e.message ?? e)))
    return () => {
      alive = false
    }
  }, [walletAddr, tick])
  const card = wallet && wallet.address === walletAddr ? wallet : null
  const walletLoading = walletAddr !== null && card === null && walletError === null
  // selection changes come from events (click, arrows, Esc): clearing the card there keeps the effect fetch-only
  const selectWallet = useCallback((addr: string | null) => {
    setWalletAddr(addr)
    setWalletError(null)
    if (!addr) setWallet(null)
  }, [])
  const openWallet = useCallback((addr: string) => {
    setWalletAddr(addr)
    setWalletError(null)
    setCardOpen(true)
  }, [])

  // ---- smart money in the live feed: look up every wallet the feed shows once per wallet ----
  useEffect(() => {
    const fresh = [...new Set(feed.map((e) => e.wallet))].filter((w) => !known.current.has(w))
    if (!fresh.length) return
    fresh.forEach((w) => known.current.add(w))
    let alive = true
    api
      .tradersLookup(fresh)
      .then((r) => {
        if (!alive) return
        setSmart((m) => {
          const next = new Map(m)
          for (const [w, e] of Object.entries(r.wallets)) next.set(w, e)
          return next
        })
        if (r.run) setSmartRun({ from_ts: r.run.from_ts, to_ts: r.run.to_ts })
      })
      .catch(() => {
        fresh.forEach((w) => known.current.delete(w)) // ask again next time
      })
    return () => {
      alive = false
    }
  }, [feed])

  return { filters, setFilters, data, error, walletAddr, setWalletAddr: selectWallet, wallet: card, walletLoading, walletError, cardOpen, setCardOpen, openWallet, smart, smartRun }
}
