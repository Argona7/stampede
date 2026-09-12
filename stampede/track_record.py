"""The honest track record of a live paper run: alerts fired and their +30 / +60 outcomes, the paper ledger's statistics
with a bootstrap CI, uptime and gaps from `/api/perf`. Read by `GET /api/track-record` and written to
`docs/TRACK-RECORD.md` by `stampede track-record --db <live store> --out docs/TRACK-RECORD.md [--api http://127.0.0.1:PORT]`.

Every number is recomputed from the store when the command runs; paper fills are simulated (see engine/paper.py) and no
outcome here is a return anyone earned.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .engine import paper as paper_mod
from .store import Store

RULES = ("edge_enter", "under_radar_top5")


def _utc(ts: int | float | None, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    if not ts:
        return "n/a"
    return datetime.fromtimestamp(int(ts), timezone.utc).strftime(fmt)


def _median(xs: list[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    return s[len(s) // 2]


def _pct(v: float | None, digits: int = 1) -> str:
    return "n/a" if v is None else f"{v:+.{digits}f} %"


def _q(v: float | None, digits: int = 4) -> str:
    return "n/a" if v is None else f"{v:+.{digits}f}"


def alerts_summary(store: Store, since_ts: int | None, mode: str = "live", limit: int = 200) -> dict[str, Any]:
    where = "WHERE mode=?" + (" AND clock_ts>=?" if since_ts else "")
    args: tuple = (mode, since_ts) if since_ts else (mode,)
    rows = store.db.execute(f"SELECT id, created_ts, clock_ts, token, symbol, rule, score, inflow, price, detail, outcome_30m, outcome_60m, graduated_after FROM alerts {where} ORDER BY clock_ts", args).fetchall()
    out: dict[str, Any] = {"total": len(rows), "by_rule": {}, "recent": []}
    now = int(time.time())
    for rule in RULES + tuple(sorted({r[5] for r in rows} - set(RULES))):
        rs = [r for r in rows if r[5] == rule]
        if not rs:
            continue
        o30 = [r[10] for r in rs if r[10] is not None]
        o60 = [r[11] for r in rs if r[11] is not None]
        due30 = [r for r in rs if r[2] + 1800 <= now]
        due60 = [r for r in rs if r[2] + 3600 <= now]
        out["by_rule"][rule] = {
            "fired": len(rs),
            "coins": len({r[3] for r in rs}),
            "due_30m": len(due30),
            "with_outcome_30m": len(o30),
            "up_30m": sum(1 for x in o30 if x > 0),
            "ge_2x_30m": sum(1 for x in o30 if x >= 100),
            "median_30m": _median(o30),
            "mean_30m": (sum(o30) / len(o30)) if o30 else None,
            "due_60m": len(due60),
            "with_outcome_60m": len(o60),
            "up_60m": sum(1 for x in o60 if x > 0),
            "median_60m": _median(o60),
            "graduated_after": sum(1 for r in rs if r[12]),
            "engine": sum(1 for r in rs if (json.loads(r[9]) if r[9] else {}).get("engine") == "wss"),
        }
    for r in rows[-limit:]:
        d = json.loads(r[9]) if r[9] else {}
        out["recent"].append({
            "id": r[0], "clock_ts": r[2], "token": r[3], "symbol": r[4], "rule": r[5], "score": r[6], "inflow": r[7], "price": r[8],
            "p_2x_30m": d.get("p_2x_30m"), "size_quote": d.get("size_quote"), "age_s": d.get("age_s"), "rank": d.get("rank"), "engine": d.get("engine"),
            "outcome_30m": r[10], "outcome_60m": r[11], "graduated_after": r[12],
        })
    out["recent"].reverse()
    return out


def perf_summary(perf: dict[str, Any] | None) -> dict[str, Any] | None:
    if not perf or perf.get("engine") != "wss":
        return None
    lat = (perf.get("latency_ms") or {}).get("block_to_emit") or {}
    blocks = perf.get("blocks") or {}
    feed = perf.get("feed") or {}
    writer = perf.get("writer") or {}
    return {
        "started_at": perf.get("started_at"),
        "uptime_s": perf.get("uptime_s"),
        "blocks_processed": blocks.get("processed"),
        "blocks_live": blocks.get("live"),
        "first_block": blocks.get("first"),
        "last_block": blocks.get("last"),
        "gaps_found": blocks.get("gaps_found"),
        "gap_blocks": blocks.get("gap_blocks"),
        "gaps_backfilled_blocks": blocks.get("gaps_backfilled_blocks"),
        "gaps_unfilled_blocks": blocks.get("gaps_unfilled_blocks"),
        "skipped_at_start": blocks.get("skipped_at_start"),
        "latency_p50_ms": lat.get("p50"),
        "latency_p95_ms": lat.get("p95"),
        "latency_p99_ms": lat.get("p99"),
        "latency_max_ms": lat.get("max"),
        "latency_n": lat.get("n"),
        "reconnects": feed.get("reconnects"),
        "failovers": feed.get("failovers"),
        "stalls": feed.get("stalls"),
        "returns_to_preferred": feed.get("returns_to_preferred"),
        "endpoint": ((feed.get("connections") or {}).get("fast") or {}).get("endpoint"),
        "events": (perf.get("events") or {}).get("last_id"),
        "events_per_s_60s": (perf.get("events") or {}).get("per_s_60s"),
        "writer_max_lag_s": writer.get("max_lag_s"),
        "writer_errors": writer.get("errors"),
        "rss_mb": (perf.get("process") or {}).get("rss_mb"),
        "cpu_percent_avg": (perf.get("process") or {}).get("cpu_percent_avg"),
        "paper_errors": (perf.get("state") or {}).get("paper_errors", 0),
        "notifications": (perf.get("counts") or {}).get("notifications", 0),
    }


def build(store: Store, since_ts: int | None = None, perf: dict[str, Any] | None = None, paper: dict[str, Any] | None = None, mode: str = "live", alerts_limit: int = 200) -> dict[str, Any]:
    """The track record as one JSON document. `since_ts` defaults to the latest engine start recorded in `meta`."""
    if since_ts is None:
        since_ts = store.get_meta("engine_started_at")
    if paper is None:
        paper = paper_mod.read_snapshot(store, closed_limit=2000, since_ts=since_ts)
    else:
        paper = dict(paper)
        if since_ts:
            closed = [p for p in paper["positions"]["closed"] if (p.get("opened_ts") or 0) >= since_ts]
            paper["positions"] = {**paper["positions"], "closed": closed}
            paper["stats"] = paper_mod.stats(closed, paper["positions"]["open"], skipped=paper["stats"].get("skipped_by_risk", 0))
    fx_rows = store.db.execute("SELECT COUNT(*), MAX(ts_hour) FROM fx_rates").fetchone()
    return {
        "generated_at": int(time.time()),
        "db": str(store.path),
        "mode": mode,
        "since_ts": since_ts,
        "engine_started_at": store.get_meta("engine_started_at"),
        "alerts": alerts_summary(store, since_ts, mode, limit=alerts_limit),
        "paper": {"stats": paper["stats"], "open": paper["positions"]["open"], "closed": paper["positions"]["closed"], "equity": paper.get("equity") or [], "config": paper.get("config")},
        "perf": perf_summary(perf),
        "fx": {"rows": fx_rows[0], "last_hour": fx_rows[1]},
        "simulated": True,
        "note": "Paper fills are simulated (curve arithmetic at the next block's observed reserves, fees, creator tax, snipe tax, impact; no order sent). Alert outcomes are price changes on indexed trades. Nobody earned these numbers.",
    }


# ---- markdown -----------------------------------------------------------------------------------------------------------
def render_markdown(tr: dict[str, Any], perf_snapshot_path: str | None = None) -> str:
    a = tr["alerts"]
    p = tr["paper"]
    st = p["stats"]
    pf = tr.get("perf")
    since = tr.get("since_ts")
    L: list[str] = []
    L.append("# Live paper run: track record")
    L.append("")
    L.append(f"Generated {_utc(tr['generated_at'])} UTC by `stampede track-record` from `{tr['db']}`" + (f", counting from the engine start at {_utc(since)} UTC" if since else "") + ".")
    L.append("")
    L.append("**Every position below is simulated.** The engine's `edge_enter` verdicts open paper positions filled by the curve arithmetic at the *next* block's observed reserves (1 % fee, the coin's creator tax, snipe tax inside the 3-s window, price impact of our own size); exits follow the plan of docs/RESEARCH-EDGE.md every block. No order was sent, no wallet was funded, nobody earned or lost these amounts. Alert outcomes are price changes measured on indexed trades. MEV, failed transactions and gas are not modelled.")
    L.append("")
    # ---- run
    L.append("## Run (engine `/api/perf`)")
    L.append("")
    if pf:
        up = pf.get("uptime_s") or 0
        L.append("| item | value |")
        L.append("|---|---|")
        L.append(f"| engine started | {_utc(pf.get('started_at'))} UTC |")
        L.append(f"| uptime at the snapshot | {up // 3600} h {(up % 3600) // 60:02d} min ({up:,} s) |")
        L.append(f"| blocks processed | {pf.get('blocks_processed'):,} ({pf.get('blocks_live'):,} live), {pf.get('first_block')} → {pf.get('last_block')} |")
        L.append(f"| gaps found / gap blocks / backfilled / unfilled | {pf.get('gaps_found')} / {pf.get('gap_blocks')} / {pf.get('gaps_backfilled_blocks')} / {pf.get('gaps_unfilled_blocks')} |")
        L.append(f"| skipped at start | {pf.get('skipped_at_start') or 'none'} |")
        L.append(f"| head → last event published, ms (n {pf.get('latency_n')}) | p50 {pf.get('latency_p50_ms')} · p95 {pf.get('latency_p95_ms')} · p99 {pf.get('latency_p99_ms')} · max {pf.get('latency_max_ms')} |")
        L.append(f"| feed | {pf.get('endpoint')} · reconnects {pf.get('reconnects')} · failovers {pf.get('failovers')} · stalls {pf.get('stalls')} · returns to preferred {pf.get('returns_to_preferred')} |")
        L.append(f"| events | {pf.get('events'):,} total · {pf.get('events_per_s_60s')} / s in the last minute |")
        L.append(f"| writer | max lag {pf.get('writer_max_lag_s')} s · errors {pf.get('writer_errors')} |")
        L.append(f"| process | RSS {pf.get('rss_mb')} MB · CPU {pf.get('cpu_percent_avg')} % of one core · ledger errors {pf.get('paper_errors')} · notifications {pf.get('notifications')} |")
        if perf_snapshot_path:
            L.append("")
            L.append(f"Full `/api/perf` snapshot: `{perf_snapshot_path}`.")
    else:
        L.append("No `/api/perf` snapshot: the engine was not reachable when this report was generated (pass `--api http://127.0.0.1:PORT`).")
    L.append("")
    # ---- alerts
    L.append("## Alerts fired and their outcomes")
    L.append("")
    L.append("Outcome = median price of the last 5 trades at +30 / +60 min of chain time vs the price at the alert, in percent; `due` counts the alerts old enough to have one.")
    L.append("")
    L.append("| rule | fired | coins | due +30 | with +30 | up +30 | ≥ 2× +30 | median +30 | mean +30 | due +60 | with +60 | up +60 | median +60 | graduated after |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for rule, r in a["by_rule"].items():
        L.append(f"| `{rule}` | {r['fired']} | {r['coins']} | {r['due_30m']} | {r['with_outcome_30m']} | {r['up_30m']} | {r['ge_2x_30m']} | {_pct(r['median_30m'])} | {_pct(r['mean_30m'])} | {r['due_60m']} | {r['with_outcome_60m']} | {r['up_60m']} | {_pct(r['median_60m'])} | {r['graduated_after']} |")
    if not a["by_rule"]:
        L.append("| — | 0 | | | | | | | | | | | | |")
    L.append("")
    edge = [x for x in a["recent"] if x["rule"] == "edge_enter"]
    L.append(f"### `edge_enter` alerts ({len(edge)} shown, newest first)")
    L.append("")
    if edge:
        L.append("| clock (UTC) | coin | p(2×/30m) | size ETH | age | price | +30 min | +60 min |")
        L.append("|---|---|---|---|---|---|---|---|")
        for x in edge:
            age = x.get("age_s")
            age_s = "n/a" if age is None else (f"{int(age)} s" if age < 60 else f"{int(age // 60)} min")
            L.append(f"| {_utc(x['clock_ts'], '%m-%d %H:%M:%S')} | {x['symbol']} `{x['token'][:10]}` | {x['p_2x_30m'] * 100:.0f} % | {x['size_quote']:.4f} | {age_s} | {x['price']:.3g} | {_pct(x['outcome_30m']) if x['outcome_30m'] is not None else ('pending' if x['clock_ts'] + 1800 > tr['generated_at'] else 'no price')} | {_pct(x['outcome_60m']) if x['outcome_60m'] is not None else ('pending' if x['clock_ts'] + 3600 > tr['generated_at'] else 'no price')} |" if x.get("p_2x_30m") is not None and x.get("size_quote") is not None and x.get("price") is not None else f"| {_utc(x['clock_ts'], '%m-%d %H:%M:%S')} | {x['symbol']} `{x['token'][:10]}` | n/a | n/a | n/a | n/a | {_pct(x['outcome_30m'])} | {_pct(x['outcome_60m'])} |")
    else:
        L.append("none yet.")
    L.append("")
    # ---- paper
    L.append("## Paper ledger (simulated)")
    L.append("")
    cfg = p.get("config") or {}
    plan = cfg.get("plan") or {}
    L.append(f"Size cap {cfg.get('size_cap_quote')} ETH per position, at most {cfg.get('max_concurrent')} open, daily stop −{(cfg.get('daily_stop_frac') or 0) * 100:.0f} % of the bankroll; plan `{plan.get('key') or plan}`; {cfg.get('latency', 'fill at the next block')}; USD via `fx_rates` {'available' if tr.get('fx', {}).get('rows') else 'not loaded (run `stampede fx --db <store>`)'}.")
    L.append("")
    L.append("| statistic | value |")
    L.append("|---|---|")
    L.append(f"| closed trades (coins) | {st['trades']} ({st['coins']}) |")
    L.append(f"| open positions | {st['open']} · unrealized {_q(st['unrealized_quote'])} ETH · exposure {st['exposure_quote']:.4f} ETH |")
    L.append(f"| hit rate | {f'{st['hit_rate'] * 100:.1f} %' if st['hit_rate'] is not None else 'n/a'} ({st['wins']} of {st['trades']}) |")
    L.append(f"| expectancy per trade | {_q(st['expectancy_quote'])} ETH" + (f" ({st['expectancy_pct'] * 100:+.1f} % of the stake)" if st.get("expectancy_pct") is not None else "") + " |")
    L.append(f"| expectancy per trade, USD | {f'${st['expectancy_usd']:+,.2f} ({st['usd_known']} of {st['trades']} with an hourly rate)' if st['expectancy_usd'] is not None else 'n/a (no fx rate for the hour)'} |")
    L.append(f"| 95 % CI of the expectancy | {f'[{st['ci95_quote'][0]:+.4f}, {st['ci95_quote'][1]:+.4f}] ETH' if st.get('ci95_quote') else 'n/a'} — {st['ci_method']} |")
    L.append(f"| total | {_q(st['total_quote'])} ETH" + (f" · ${st['total_usd']:+,.2f}" if st.get("total_usd") is not None else "") + " |")
    L.append(f"| profit factor | {f'{st['profit_factor']:.2f}' if isinstance(st.get('profit_factor'), float) and st['profit_factor'] != float('inf') else ('∞ (no losing trade yet)' if st.get('profit_factor') == float('inf') else 'n/a')} |")
    L.append(f"| max drawdown (sequential equity) | {_q(st['max_drawdown_quote'])} ETH |")
    L.append(f"| average win / loss | {_q(st['avg_win_quote'])} / {_q(st['avg_loss_quote'])} ETH |")
    L.append(f"| median hold | {f'{st['median_hold_s'] // 60} min {st['median_hold_s'] % 60:02d} s' if st.get('median_hold_s') is not None else 'n/a'} |")
    L.append(f"| fees + taxes paid (simulated) | {st['fees_quote']:.4f} ETH |")
    L.append(f"| exits | {', '.join(f'{k} {v}' for k, v in sorted(st['exits'].items(), key=lambda kv: -kv[1])) or 'none'} |")
    L.append(f"| entries skipped by the risk engine | {st.get('skipped_by_risk', 0)} |")
    L.append("")
    if st["per_hour"]:
        L.append("### Per hour (UTC hour the position was opened)")
        L.append("")
        L.append("| hour | trades | wins | pnl ETH |")
        L.append("|---|---|---|---|")
        for h in st["per_hour"]:
            L.append(f"| {h['hour']} | {h['n']} | {h['wins']} | {_q(h['pnl'])} |")
        L.append("")
    closed = p["closed"]
    L.append(f"### Closed positions ({len(closed)}, newest first)")
    L.append("")
    if closed:
        L.append("| opened (UTC) | coin | size ETH | entry px | exit | hold | pnl ETH | pnl USD | peak | fees ETH |")
        L.append("|---|---|---|---|---|---|---|---|---|---|")
        for x in sorted(closed, key=lambda x: -(x.get("closed_ts") or 0)):
            fees = (x.get("fee_quote") or 0) + (x.get("tax_quote") or 0) + (x.get("snipe_quote") or 0) + (x.get("exit_fees_quote") or 0)
            hold = x.get("hold_s") or 0
            L.append(f"| {_utc(x['opened_ts'], '%m-%d %H:%M:%S')} | {x['symbol']} `{x['token'][:10]}` | {x['size_quote']:.4f} | {x['entry_px']:.3g} | {x.get('exit_reason')} | {hold // 60} min {hold % 60:02d} s | {_q(x.get('pnl_quote'))} | {f'${x['pnl_usd']:+,.2f}' if x.get('pnl_usd') is not None else 'n/a'} | {f'{x['peak_ret'] * 100:+.0f} %' if x.get('peak_ret') is not None else 'n/a'} | {fees:.5f} |")
    else:
        L.append("none closed yet.")
    L.append("")
    if p["open"]:
        L.append(f"### Open positions ({len(p['open'])}) at the snapshot")
        L.append("")
        L.append("| opened (UTC) | coin | size ETH | entry px | mark | unrealized ETH | plan |")
        L.append("|---|---|---|---|---|---|---|")
        for x in p["open"]:
            L.append(f"| {_utc(x['opened_ts'], '%m-%d %H:%M:%S')} | {x['symbol']} `{x['token'][:10]}` | {x['size_quote']:.4f} | {x['entry_px']:.3g} | {f'{x['ret'] * 100:+.1f} %' if x.get('ret') is not None else 'n/a'} | {_q(x.get('unrealized_quote'))} | {' · '.join((x.get('plan') or {}).get('text') or [])[:80]} |")
        L.append("")
    L.append("## Reading this honestly")
    L.append("")
    L.append("- The paper ledger buys the coin nobody else was buying at that block: the observed trades are replayed unchanged and our fill moves only our own price. A real order would also pay gas and could fail or be front-run.")
    L.append("- p(2×/30 min) is the out-of-sample rate of docs/RESEARCH-EDGE.md; with a handful of trades the CI above is wide by construction, and the bootstrap resamples coins, not trades, because one coin can alert more than once.")
    L.append("- Outcomes need +30 / +60 min of chain time after the alert; `pending` means not due yet, `no price` means the coin had no trade to price it with.")
    L.append("")
    L.append("## Regenerate")
    L.append("")
    L.append("```sh")
    L.append("uv run stampede track-record --db data/live-engine.sqlite --out docs/TRACK-RECORD.md --api http://127.0.0.1:8812   # --api: the running engine's /api/perf; omit when it is down")
    L.append("uv run stampede fx --db data/live-engine.sqlite --days 4   # optional: hourly ETH/USD so the USD columns are known")
    L.append("```")
    L.append("")
    return "\n".join(L)


def main_track_record(args) -> int:
    import urllib.request

    store = Store(Path(args.db) if args.db else None)
    perf = None
    snap_path = None
    if getattr(args, "perf_json", None):
        perf = json.loads(Path(args.perf_json).read_text())
        snap_path = args.perf_json
    elif getattr(args, "api", None):
        try:
            with urllib.request.urlopen(args.api.rstrip("/") + "/api/perf", timeout=10) as r:
                perf = json.loads(r.read().decode())
            if args.out:
                snap_path = str(Path(args.out).with_suffix("")) + "-perf.json"
                Path(snap_path).write_text(json.dumps(perf, indent=1, default=str))
        except Exception as e:  # noqa: BLE001
            print(f"/api/perf not reachable at {args.api}: {type(e).__name__}: {str(e)[:120]} (report written without the run section)", flush=True)
    tr = build(store, since_ts=getattr(args, "since", None), perf=perf, mode=getattr(args, "mode", None) or "live")
    md = render_markdown(tr, perf_snapshot_path=snap_path)
    if args.out:
        Path(args.out).write_text(md)
        print(f"wrote {args.out}: {tr['alerts']['total']} alerts, {tr['paper']['stats']['trades']} closed paper trades, {tr['paper']['stats']['open']} open" + (f", uptime {tr['perf']['uptime_s']} s" if tr.get("perf") else ""), flush=True)
    else:
        print(md)
    store.close()
    return 0
