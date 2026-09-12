// SIGNALS (key 5): the product surface of the realtime engine for a person trading by hand — the live alert feed
// (ENTER verdicts with p / EV / size / plan and the countdown to their +30 / +60 outcome), the paper ledger (open
// positions marked every block, closed ones with the exit reason), the equity sparkline with the statistics and their
// bootstrap CI, and the TRACK RECORD panel. Everything in the ledger is simulated (fills by curve arithmetic at the
// next block's reserves); the view says so in its working row. Dense mono, red only for ENTER / selection / new.
import { useMemo } from 'react'
import { fmtLag } from '../live'
import type { LiveState } from '../useLive'
import { utc } from '../format'
import type { AlertRow, PaperPosition, PaperResponse, PerfResponse, SessionState, TrackRecord } from '../types'

interface Props {
  alerts: AlertRow[]
  paper: PaperResponse | null
  track: TrackRecord | null
  perf: PerfResponse | null
  session: SessionState | null
  live: LiveState
  error: string | null
  selected: string | null
  freshKeys: Set<string>
  onSelect: (address: string) => void
  onFlow: (address: string) => void
}

const UNK = <span className="unk">n/a</span>
const eth = (v: number | null | undefined, d = 4): string => (v === null || v === undefined ? 'n/a' : `${v > 0 ? '+' : ''}${v.toFixed(d)}`)
const pct = (v: number | null | undefined, d = 1): string => (v === null || v === undefined ? 'n/a' : `${v > 0 ? '+' : ''}${(v * 100).toFixed(d)}%`)
const pctRaw = (v: number | null | undefined, d = 1): string => (v === null || v === undefined ? 'n/a' : `${v > 0 ? '+' : ''}${Math.abs(v) >= 1000 ? `${Math.round(v / 100) / 10}k` : v.toFixed(d)}%`)
const usd = (v: number | null | undefined): string => (v === null || v === undefined ? 'n/a' : `${v < 0 ? '−' : '+'}$${Math.abs(v).toFixed(2)}`)
const mmss = (s: number): string => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`
const hold = (s: number | null | undefined): string => (s === null || s === undefined ? 'n/a' : s < 3600 ? `${Math.floor(s / 60)} min ${String(Math.floor(s % 60)).padStart(2, '0')} s` : `${Math.floor(s / 3600)} h ${String(Math.floor((s % 3600) / 60)).padStart(2, '0')} min`)
const px = (v: number | null | undefined): string => (v === null || v === undefined ? 'n/a' : v.toPrecision(3))

/** outcome cell: the settled percent, or the countdown to the settle time on the chain clock */
function Outcome({ value, due, clock }: { value: number | null | undefined; due: number; clock: number | null }) {
  if (value !== null && value !== undefined) return <span className={value > 0 ? 'up' : 'neg'}>{pctRaw(value)}</span>
  if (clock === null) return UNK
  const left = due - clock
  if (left > 0) return <span className="countdown">in {mmss(left)}</span>
  if (left > -300) return <span className="unk">settling…</span>
  return <span className="unk">no price</span> // due long ago and still empty: the coin had no trade to price it with
}

function Spark({ eq }: { eq: PaperResponse['equity'] }) {
  const w = 268
  const h = 44
  if (eq.length < 2) {
    return (
      <svg className="eq-spark" width={w} height={h} aria-label="equity curve">
        <line x1={0} x2={w} y1={h / 2} y2={h / 2} stroke="#351419" />
      </svg>
    )
  }
  const ys = eq.map((e) => e.equity_quote)
  const lo = Math.min(0, ...ys)
  const hi = Math.max(0, ...ys)
  const span = hi - lo || 1
  const x = (i: number) => (i / (eq.length - 1)) * (w - 2) + 1
  const y = (v: number) => h - 2 - ((v - lo) / span) * (h - 4)
  const d = eq.map((e, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(e.equity_quote).toFixed(1)}`).join(' ')
  const last = ys[ys.length - 1]
  return (
    <svg className="eq-spark" width={w} height={h} aria-label="equity curve">
      <line x1={0} x2={w} y1={y(0)} y2={y(0)} stroke="#351419" />
      <path d={d} fill="none" stroke={last >= 0 ? '#F2F2F2' : '#A3A3A3'} strokeWidth={1.2} />
      <circle cx={x(eq.length - 1)} cy={y(last)} r={2} fill="#FF3344" />
    </svg>
  )
}

const planText = (p: PaperPosition): string => (p.plan?.text?.length ? p.plan.text.join(' · ') : 'plan n/a')

export default function Signals({ alerts, paper, track, perf, session, live, error, selected, freshKeys, onSelect, onFlow }: Props) {
  const clock = session?.clock_ts ?? null
  const mode = session?.mode ?? 'fixture'
  const st = paper?.stats ?? null
  const open = paper?.positions.open ?? []
  const closed = paper?.positions.closed ?? []
  const enterAlerts = useMemo(() => alerts.filter((a) => a.rule === 'edge_enter'), [alerts])
  const lat = perf?.latency_ms?.block_to_emit
  const conn = mode !== 'live' ? `${mode === 'replay' ? 'REPLAY' : 'RECORDED'} · polling` : live.status === 'live' ? `LIVE · stream${live.clientLagMs !== null ? ` ${fmtLag(live.clientLagMs)}` : ''}` : live.status === 'reconnecting' ? 'RECONNECTING' : live.status === 'connecting' ? 'CONNECTING' : 'LIVE · polling'
  const byRule = track?.alerts.by_rule ?? {}
  const rules = Object.keys(byRule)
  return (
    <div className="signals" data-testid="signals">
      <div className="workbar">
        <span className="stat" data-testid="signals-summary">
          <b>{enterAlerts.length}</b> ENTER · <b>{alerts.length}</b> alerts · paper <b>{open.length}</b> open / <b>{closed.length}</b> closed · realized <b className={(st?.total_quote ?? 0) < 0 ? 'neg' : ''}>{eth(st?.total_quote ?? 0)}</b> ETH
        </span>
        <span className="sep" />
        <span className="stat" title={lat ? `engine head → last event: p50 ${lat.p50} ms · p95 ${lat.p95} ms · n ${lat.n}` : 'no engine latency: the process runs no realtime engine'} data-testid="signals-conn">
          {conn}
          {lat?.p50 !== null && lat?.p50 !== undefined ? <span className="faint"> · engine p50 {fmtLag(lat.p50)}</span> : null}
        </span>
        <span className="grow" />
        <span className="faint">paper fills are simulated: curve math at the next block's reserves, no order sent</span>
      </div>
      {error && !paper ? (
        <div className="state-block err" role="alert">
          <div>
            <b>SIGNALS not loaded</b>
            {error}
          </div>
        </div>
      ) : (
        <div className="signals-body">
          <section className="sig-col sig-alerts" aria-label="Alerts">
            <h2>
              Alerts · newest first <span className="faint">· ENTER = stage-4 verdict · radar = under-radar top-5</span>
            </h2>
            {alerts.length === 0 ? (
              <div className="sig-empty" data-testid="signals-empty">
                no alerts yet in this mode{mode === 'live' ? ' · the engine journals every rule hit here as it fires' : ' · alerts come from the context worker in replay'}
              </div>
            ) : (
              <table className="sig-table alerts" aria-label="Alerts">
                <thead>
                  <tr>
                    <th>clock</th>
                    <th>coin</th>
                    <th>rule</th>
                    <th className="num" title="p(≥ 2× within 30 min) from the runner model or the calibrated rules">p 2×</th>
                    <th className="num c-ev" title="expected value per trade in ETH at the risk-engine size">EV</th>
                    <th className="num" title="risk-engine size in ETH">size</th>
                    <th className="num" title="price change at +30 min of chain time vs the alert; countdown until due">+30</th>
                    <th className="num">+60</th>
                  </tr>
                </thead>
                <tbody>
                  {alerts.slice(0, 120).map((a) => {
                    const key = `${a.token}:${a.clock_ts}`
                    const d = a.detail
                    const enter = a.rule === 'edge_enter'
                    const title = [...(d?.reasons ?? []), ...(d?.exit_plan?.length ? [`plan: ${d.exit_plan.join(' · ')}`] : []), `${a.inflow} wallets in · score ${Math.round(a.score)}${d?.age_s !== null && d?.age_s !== undefined ? ` · age ${Math.round(d.age_s / 60)} min` : ''}`].join('\n')
                    return (
                      <tr key={key} className={`sig-row ${selected === a.token ? 'sel' : ''} ${freshKeys.has(key) ? 'fresh' : ''} ${enter ? 'enter' : ''}`} data-key={key} data-rule={a.rule} title={title} onClick={() => onSelect(a.token)} onDoubleClick={() => onFlow(a.token)}>
                        <td className="t">{utc(a.clock_ts)}</td>
                        <td className="coin">
                          <b>{a.symbol}</b>
                        </td>
                        <td className={`rule ${enter ? 'enter' : ''}`}>{enter ? 'ENTER' : 'radar'}</td>
                        <td className="num">{d?.p_2x_30m !== null && d?.p_2x_30m !== undefined ? `${Math.round(d.p_2x_30m * 100)}%` : UNK}</td>
                        <td className="num c-ev">{d?.ev_per_trade_quote !== null && d?.ev_per_trade_quote !== undefined ? eth(d.ev_per_trade_quote) : UNK}</td>
                        <td className="num">{d?.size_quote !== null && d?.size_quote !== undefined ? d.size_quote.toFixed(4) : UNK}</td>
                        <td className="num">
                          <Outcome value={a.outcome_30m} due={a.clock_ts + 1800} clock={clock} />
                        </td>
                        <td className="num">
                          <Outcome value={a.outcome_60m} due={a.clock_ts + 3600} clock={clock} />
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            )}
          </section>
          <section className="sig-col sig-positions" aria-label="Paper positions">
            <h2>
              Open paper positions · {open.length}
              {paper?.positions.pending.length ? <span className="faint"> · {paper.positions.pending.length} filling at the next block</span> : null}
            </h2>
            {open.length === 0 ? (
              <div className="sig-empty" data-testid="positions-empty">
                none open{st?.skipped_by_risk ? ` · ${st.skipped_by_risk} entr${st.skipped_by_risk === 1 ? 'y' : 'ies'} skipped by the risk engine` : ''}
              </div>
            ) : (
              <table className="sig-table positions" aria-label="Open paper positions">
                <thead>
                  <tr>
                    <th>opened</th>
                    <th>coin</th>
                    <th className="num">size</th>
                    <th className="num" title="entry price in ETH per token, fees included">entry</th>
                    <th className="num" title="what a sell would return now vs the entry (1 % fee and creator tax included)">mark</th>
                    <th className="num">unreal.</th>
                    <th className="num c-peak" title="best mark since the entry">peak</th>
                    <th className="num">age</th>
                    <th title="TP ladder done → trailing 25 % from the high">plan</th>
                  </tr>
                </thead>
                <tbody>
                  {open.map((p) => {
                    const age = clock !== null ? clock - p.opened_ts : null
                    const left = Math.max(0, (p.plan?.time_s ?? 2700) - (age ?? 0))
                    return (
                      <tr key={p.id} className={`sig-row ${selected === p.token ? 'sel' : ''} ${freshKeys.has(`pos:${p.id}`) ? 'fresh' : ''}`} data-position={p.id} title={`${planText(p)}\nfees ${(p.fee_quote + p.tax_quote + p.snipe_quote).toFixed(5)} ETH (tax ${p.tax_bps} bps, ${p.tax_source}) · impact ${p.impact_bps !== null ? `${p.impact_bps.toFixed(0)} bps` : 'n/a'} · alert at block ${p.alert_block}, filled at block ${p.opened_block}`} onClick={() => onSelect(p.token)} onDoubleClick={() => onFlow(p.token)}>
                        <td className="t">{utc(p.opened_ts)}</td>
                        <td className="coin">
                          <b>{p.symbol}</b>
                        </td>
                        <td className="num">{p.size_quote.toFixed(4)}</td>
                        <td className="num">{px(p.entry_px)}</td>
                        <td className={`num ${(p.ret ?? 0) < 0 ? 'neg' : 'up'}`}>{pct(p.ret)}</td>
                        <td className={`num ${(p.unrealized_quote ?? 0) < 0 ? 'neg' : 'up'}`}>{eth(p.unrealized_quote)}</td>
                        <td className="num c-peak">{pct(p.peak_ret)}</td>
                        <td className="num">{age !== null ? `${mmss(age)}` : UNK}</td>
                        <td className="plan">{p.tp_done ? 'TP ✓ · trail' : `TP · out in ${mmss(left)}`}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            )}
            <h2>Closed · {closed.length}</h2>
            {closed.length === 0 ? (
              <div className="sig-empty" data-testid="closed-empty">
                none closed yet
              </div>
            ) : (
              <table className="sig-table positions" aria-label="Closed paper positions">
                <thead>
                  <tr>
                    <th>opened</th>
                    <th>coin</th>
                    <th>exit</th>
                    <th className="num">hold</th>
                    <th className="num">pnl ETH</th>
                    <th className="num">pnl USD</th>
                    <th className="num c-peak">peak</th>
                  </tr>
                </thead>
                <tbody>
                  {closed.slice(0, 80).map((p) => (
                    <tr key={p.id} className={`sig-row ${selected === p.token ? 'sel' : ''}`} data-position={p.id} title={`${planText(p)}\nentry ${px(p.entry_px)} · size ${p.size_quote.toFixed(4)} ETH · fees ${(p.fee_quote + p.tax_quote + p.snipe_quote + p.exit_fees_quote).toFixed(5)} ETH`} onClick={() => onSelect(p.token)} onDoubleClick={() => onFlow(p.token)}>
                      <td className="t">{utc(p.opened_ts)}</td>
                      <td className="coin">
                        <b>{p.symbol}</b>
                      </td>
                      <td className="exit">{p.exit_reason ?? '?'}</td>
                      <td className="num">{hold(p.hold_s)}</td>
                      <td className={`num ${(p.pnl_quote ?? 0) < 0 ? 'neg' : 'up'}`}>{eth(p.pnl_quote)}</td>
                      <td className={`num ${(p.pnl_usd ?? 0) < 0 ? 'neg' : ''}`}>{usd(p.pnl_usd)}</td>
                      <td className="num c-peak">{pct(p.peak_ret)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
          <aside className="sig-col sig-stats" aria-label="Paper statistics and track record">
            <h2>Equity · simulated</h2>
            <Spark eq={paper?.equity ?? []} />
            <div className="kv" data-testid="paper-stats">
              <span>closed trades (coins)</span>
              <span>{st ? `${st.trades} (${st.coins})` : '…'}</span>
              <span>hit rate</span>
              <span>{st?.hit_rate !== null && st?.hit_rate !== undefined ? `${(st.hit_rate * 100).toFixed(0)}% · ${st.wins} of ${st.trades}` : UNK}</span>
              <span>expectancy / trade</span>
              <span>{st?.expectancy_quote !== null && st?.expectancy_quote !== undefined ? `${eth(st.expectancy_quote)} ETH${st.expectancy_pct !== null ? ` (${pct(st.expectancy_pct, 0)})` : ''}` : UNK}</span>
              <span>expectancy, USD</span>
              <span>{st?.expectancy_usd !== null && st?.expectancy_usd !== undefined ? `${usd(st.expectancy_usd)} · ${st.usd_known} rated` : UNK}</span>
              <span title={st?.ci_method ?? ''}>95% CI</span>
              <span>{st?.ci95_quote ? `[${eth(st.ci95_quote[0])}, ${eth(st.ci95_quote[1])}]` : UNK}</span>
              <span>profit factor</span>
              <span>{st?.profit_factor === null || st?.profit_factor === undefined ? UNK : Number.isFinite(st.profit_factor) ? st.profit_factor.toFixed(2) : '∞'}</span>
              <span>max drawdown</span>
              <span>{st ? `${eth(st.max_drawdown_quote)} ETH` : '…'}</span>
              <span>median hold</span>
              <span>{st?.median_hold_s !== null && st?.median_hold_s !== undefined ? hold(st.median_hold_s) : UNK}</span>
              <span>fees + tax (sim.)</span>
              <span>{st ? `${st.fees_quote.toFixed(4)} ETH` : '…'}</span>
              <span>exits</span>
              <span>{st && Object.keys(st.exits).length ? Object.entries(st.exits).map(([k, v]) => `${k} ${v}`).join(' · ') : UNK}</span>
              <span>skipped by risk</span>
              <span>{st?.skipped_by_risk ?? 0}</span>
            </div>
            <h2 className="tr-head">Track record</h2>
            <div className="kv" data-testid="track-record">
              {rules.length === 0 && <span className="faint span2">no alerts in this mode yet</span>}
              {rules.map((r) => {
                const x = byRule[r]
                return [
                  <span key={`${r}-a`} className="span2 rule-head">
                    {r === 'edge_enter' ? 'ENTER' : r}: {x.fired} fired · {x.coins} coins
                  </span>,
                  <span key={`${r}-b`}>+30 known / up / ≥ 2×</span>,
                  <span key={`${r}-c`}>
                    {x.with_outcome_30m} / {x.up_30m} / {x.ge_2x_30m}
                    {x.due_30m > x.with_outcome_30m ? <span className="faint"> · {x.due_30m - x.with_outcome_30m} unpriced</span> : null}
                  </span>,
                  <span key={`${r}-d`}>median +30 / +60</span>,
                  <span key={`${r}-e`}>
                    {pctRaw(x.median_30m)} / {pctRaw(x.median_60m)}
                  </span>,
                ]
              })}
              {track?.perf ? (
                <>
                  <span className="span2 rule-head">run</span>
                  <span>uptime</span>
                  <span>{track.perf.uptime_s !== null ? hold(track.perf.uptime_s) : UNK}</span>
                  <span>blocks · gaps · unfilled</span>
                  <span>
                    {track.perf.blocks_processed?.toLocaleString('en-US')} · {track.perf.gaps_found} · {track.perf.gaps_unfilled_blocks}
                  </span>
                  <span>head → event p50 / p95</span>
                  <span>
                    {fmtLag(track.perf.latency_p50_ms)} / {fmtLag(track.perf.latency_p95_ms)}
                  </span>
                  <span>reconnects · failovers</span>
                  <span>
                    {track.perf.reconnects} · {track.perf.failovers}
                  </span>
                </>
              ) : (
                track && <span className="span2 faint">no engine in this process: uptime and gaps come from the live instance</span>
              )}
              {track?.since_ts ? (
                <span className="span2 faint">counting from the engine start {utc(track.since_ts, true)} UTC</span>
              ) : null}
            </div>
            <p className="note">Alert outcome = price change on indexed trades; paper pnl = simulated fills. Not a return anyone earned.</p>
          </aside>
        </div>
      )}
    </div>
  )
}
