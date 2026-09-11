"""Does rotation inflow precede runners? A walk-forward backtest on indexed PONS trades.

    .venv/bin/python -m stampede.research.backtest --db data/research-12h.sqlite --out docs/RESEARCH-RUNNERS.md

Signal time = every minute at which a coin has rotation inflow (distinct wallets that sold another coin and
then bought this one, direct/clean grade) in the previous 10 minutes. At each signal minute we record
features that were knowable at that minute, then look forward `--horizon` seconds for the outcome.

Outcome "runner" = the coin's median trade price reaches >= (1 + runner_gain) x the price at the signal
minute within the horizon, or the coin graduates within the horizon. Base rate = the same outcome measured
at every coin-minute that had at least one trade (no signal condition), so lift is signal vs. everything.

Wallet quality is walk-forward: a wallet's score at time t uses only its rotations whose outcome was already
known at t (buy time + horizon <= t). No future information leaks into a feature.
"""
from __future__ import annotations

import argparse
import bisect
import json
import math
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..store import Store

MAIN = ("direct", "clean")


def pct(x: float | None, d: int = 1) -> str:
    return "—" if x is None else f"{100 * x:.{d}f}%"


def median(xs: list[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    return s[len(s) // 2]


class Series:
    """Per-token minute-level price (median of trade prices in the minute) plus buyers per minute."""

    def __init__(self):
        self.minutes: list[int] = []  # sorted minute stamps (unix // 60)
        self.price: list[float] = []
        self.first_ts: int | None = None

    def price_at(self, m: int) -> float | None:
        i = bisect.bisect_right(self.minutes, m) - 1
        return self.price[i] if i >= 0 else None

    def max_after(self, m: int, horizon_min: int) -> float | None:
        i = bisect.bisect_right(self.minutes, m)
        j = bisect.bisect_right(self.minutes, m + horizon_min)
        return max(self.price[i:j]) if j > i else None


def build_series(store: Store) -> dict[str, Series]:
    q = store.db.execute
    per: dict[str, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))
    first: dict[str, int] = {}
    for tok, ts, ta, qa in q("SELECT token, ts, CAST(token_amount AS REAL), CAST(quote_amount AS REAL) FROM trades WHERE ts>0 AND CAST(token_amount AS REAL)>0 AND CAST(quote_amount AS REAL)>0"):
        per[tok][ts // 60].append(qa / ta)
        if tok not in first or ts < first[tok]:
            first[tok] = ts
    out: dict[str, Series] = {}
    for tok, mins in per.items():
        s = Series()
        for m in sorted(mins):
            s.minutes.append(m)
            s.price.append(median(mins[m]) or 0.0)
        s.first_ts = first.get(tok)
        out[tok] = s
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/research-12h.sqlite")
    ap.add_argument("--window", type=int, default=1800, help="rotation pairing window used for sequences")
    ap.add_argument("--horizon", type=int, default=1800, help="seconds to look forward for the outcome")
    ap.add_argument("--runner-gain", type=float, default=1.0, help="price multiple minus one: 1.0 = +100%%")
    ap.add_argument("--out", default="")
    ap.add_argument("--scores-out", default="", help="write wallet scores (json) for import into another store")
    ap.add_argument("--write-scores", action="store_true", help="write wallet_scores into --db")
    a = ap.parse_args()
    t0 = time.time()
    store = Store(a.db)
    q = store.db.execute
    H = a.horizon
    Hm = H // 60
    series = build_series(store)
    grad = {r[0]: r[1] for r in q("SELECT token, ts FROM graduations WHERE ts IS NOT NULL")}
    lo, hi = q("SELECT MIN(ts), MAX(ts) FROM trades WHERE ts>0").fetchone()
    seqs = q("SELECT buy_token, sell_token, wallet, buy_ts FROM sequences WHERE window_s=? AND grade IN ('direct','clean') ORDER BY buy_ts", (a.window,)).fetchall()
    buyers_by_tok_min: dict[str, dict[int, set[str]]] = defaultdict(lambda: defaultdict(set))
    for tok, ts, w in q("SELECT token, ts, wallet FROM trades WHERE side='buy' AND ts>0"):
        buyers_by_tok_min[tok][ts // 60].add(w)
    trades_per_wallet = {r[0]: r[1] for r in q("SELECT wallet, COUNT(*) FROM trades GROUP BY wallet")}
    hours = max(1.0, (hi - lo) / 3600)
    bots = {w for w, n in trades_per_wallet.items() if n / hours > 60 or n > 500}

    def outcome(tok: str, m: int) -> tuple[bool | None, float | None, bool]:
        s = series.get(tok)
        if not s:
            return None, None, False
        p0 = s.price_at(m)
        if not p0:
            return None, None, False
        if (m + Hm) * 60 > hi:  # horizon not fully observed: skip
            return None, None, False
        mx = s.max_after(m, Hm)
        gain = (mx / p0 - 1) if mx else 0.0
        g = grad.get(tok)
        graduated = bool(g and m * 60 < g <= (m + Hm) * 60)
        return (gain >= a.runner_gain) or graduated, gain, graduated

    # ---- base rate: every coin-minute with a trade ----
    base_n = base_hits = 0
    base_gains: list[float] = []
    for tok, s in series.items():
        for m in s.minutes:
            r, gain, _ = outcome(tok, m)
            if r is None:
                continue
            base_n += 1
            base_hits += int(r)
            base_gains.append(gain or 0.0)

    # ---- signals: per token-minute with inflow in the last 10 min ----
    by_tok: dict[str, list[tuple[int, str, str]]] = defaultdict(list)  # buy_ts, wallet, src
    for tok, src, w, ts in seqs:
        if w in bots:
            continue
        by_tok[tok].append((ts, w, src))
    # walk-forward wallet scores: rotation outcomes known once buy_ts + H <= now
    seq_outcomes: list[tuple[int, str, bool]] = []  # (known_at, wallet, hit)
    for tok, src, w, ts in seqs:
        r, _, _ = outcome(tok, ts // 60)
        if r is None:
            continue
        seq_outcomes.append((ts + H, w, r))
    seq_outcomes.sort()
    signals: list[dict[str, Any]] = []
    for tok, lst in by_tok.items():
        lst.sort()
        times = [x[0] for x in lst]
        s = series.get(tok)
        if not s:
            continue
        minutes = sorted({ts // 60 for ts, _, _ in lst})
        for m in minutes:
            now = (m + 1) * 60
            i10 = bisect.bisect_left(times, now - 600)
            i30 = bisect.bisect_left(times, now - 1800)
            iN = bisect.bisect_left(times, now)
            w10 = {lst[k][1] for k in range(i10, iN)}
            if not w10:
                continue
            wprev = {lst[k][1] for k in range(i30, i10)}
            src10 = {lst[k][2] for k in range(i10, iN)}
            prev_rate = len(wprev) / 2.0
            accel = len(w10) / prev_rate if prev_rate > 0 else float(len(w10))
            buyers10 = set()
            for mm in range(m - 9, m + 1):
                buyers10 |= buyers_by_tok_min[tok].get(mm, set())
            p0 = s.price_at(m)
            p10 = s.price_at(m - 10)
            momentum = (p0 / p10 - 1) if p0 and p10 else None
            age = now - (s.first_ts or now)
            r, gain, graduated = outcome(tok, m)
            if r is None:
                continue
            signals.append({"tok": tok, "m": m, "now": now, "inflow10": len(w10), "accel": accel, "breadth": len(src10), "buyers10": len(buyers10), "rot_share": len(w10) / max(1, len(buyers10)), "momentum": momentum, "age": age, "runner": r, "gain": gain or 0.0, "graduated": graduated, "wallets": w10})
    signals.sort(key=lambda x: x["now"])
    # wallet quality feature (walk-forward)
    hits: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    k = 0
    for sg in signals:
        while k < len(seq_outcomes) and seq_outcomes[k][0] <= sg["now"]:
            _, w, hit = seq_outcomes[k]
            hits[w][0] += 1
            hits[w][1] += int(hit)
            k += 1
        qs = []
        for w in sg["wallets"]:
            n, h = hits.get(w, (0, 0))
            if n:
                qs.append((h + 1) / (n + 2))
        sg["quality"] = (sum(qs) / len(qs)) if qs else None
        sg["quality_cov"] = len(qs) / max(1, len(sg["wallets"]))

    def table(name: str, groups: list[tuple[str, list[dict[str, Any]]]]) -> list[str]:
        lines = [f"### {name}", "", "| bucket | signals | coins | runner rate | lift vs base | median max gain | graduated |", "|---|---|---|---|---|---|---|"]
        for label, rows in groups:
            if not rows:
                lines.append(f"| {label} | 0 | 0 | — | — | — | — |")
                continue
            rate = sum(r["runner"] for r in rows) / len(rows)
            lines.append(f"| {label} | {len(rows)} | {len({r['tok'] for r in rows})} | {pct(rate)} | {rate / base_rate:.2f}× | {pct(median([r['gain'] for r in rows]))} | {sum(r['graduated'] for r in rows)} |")
        lines.append("")
        return lines

    base_rate = base_hits / base_n if base_n else float("nan")
    inflow_bins = [("1–2", lambda r: r["inflow10"] <= 2), ("3–4", lambda r: 3 <= r["inflow10"] <= 4), ("5–7", lambda r: 5 <= r["inflow10"] <= 7), ("8–11", lambda r: 8 <= r["inflow10"] <= 11), ("12–19", lambda r: 12 <= r["inflow10"] <= 19), ("20–39", lambda r: 20 <= r["inflow10"] <= 39), ("40+", lambda r: r["inflow10"] >= 40)]
    accel_bins = [("< 1 (slowing)", lambda r: r["accel"] < 1), ("1–2", lambda r: 1 <= r["accel"] < 2), ("2–4", lambda r: 2 <= r["accel"] < 4), ("4+ (new burst)", lambda r: r["accel"] >= 4)]
    age_bins = [("< 15 min", lambda r: r["age"] < 900), ("15–60 min", lambda r: 900 <= r["age"] < 3600), ("1–4 h", lambda r: 3600 <= r["age"] < 4 * 3600), ("4 h+", lambda r: r["age"] >= 4 * 3600)]
    mom_bins = [("already +100% in 10 min", lambda r: r["momentum"] is not None and r["momentum"] >= 1.0), ("+20..100%", lambda r: r["momentum"] is not None and 0.2 <= r["momentum"] < 1.0), ("flat -20..+20%", lambda r: r["momentum"] is not None and -0.2 <= r["momentum"] < 0.2), ("falling", lambda r: r["momentum"] is not None and r["momentum"] < -0.2), ("no price 10 min ago", lambda r: r["momentum"] is None)]
    q_bins = [("unknown", lambda r: r["quality"] is None), ("< 0.35", lambda r: r["quality"] is not None and r["quality"] < 0.35), ("0.35–0.5", lambda r: r["quality"] is not None and 0.35 <= r["quality"] < 0.5), ("0.5–0.65", lambda r: r["quality"] is not None and 0.5 <= r["quality"] < 0.65), ("0.65+", lambda r: r["quality"] is not None and r["quality"] >= 0.65)]
    breadth_bins = [("1 source", lambda r: r["breadth"] == 1), ("2–3", lambda r: 2 <= r["breadth"] <= 3), ("4–7", lambda r: 4 <= r["breadth"] <= 7), ("8+", lambda r: r["breadth"] >= 8)]

    def grp(bins):
        return [(lbl, [r for r in signals if f(r)]) for lbl, f in bins]

    strong = [r for r in signals if r["inflow10"] >= 8]
    under = [r for r in strong if r["age"] < 3600 and (r["momentum"] is None or r["momentum"] < 1.0)]
    under_q = [r for r in under if r["quality"] is not None and r["quality"] >= 0.5]
    combos = [("inflow ≥ 8", strong), ("inflow ≥ 8 & age < 1 h & not already +100% in 10 min (under radar)", under), ("… & wallet quality ≥ 0.5", under_q), ("inflow ≥ 8 & accel ≥ 4 (fresh burst)", [r for r in strong if r["accel"] >= 4]), ("inflow ≥ 20", [r for r in signals if r["inflow10"] >= 20])]

    # first-signal-per-coin view: when a coin first crosses inflow >= 8, what happens?
    first_cross: dict[str, dict[str, Any]] = {}
    for r in signals:
        if r["inflow10"] >= 8 and r["tok"] not in first_cross:
            first_cross[r["tok"]] = r
    fc = list(first_cross.values())

    # wallet scores for export
    final_scores = []
    for w, (n, h) in hits.items():
        if n >= 3:
            final_scores.append({"wallet": w, "rotations": n, "runner_hits": h, "score": (h + 1) / (n + 2), "is_bot": int(w in bots), "trades_per_hour": trades_per_wallet.get(w, 0) / hours})
    for w in bots:
        if w not in hits:
            final_scores.append({"wallet": w, "rotations": 0, "runner_hits": 0, "score": None, "is_bot": 1, "trades_per_hour": trades_per_wallet.get(w, 0) / hours})

    md: list[str] = []
    md.append("# Do rotations precede runners? Backtest on indexed PONS trades")
    md.append("")
    md.append(f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} by `stampede.research.backtest` from `{a.db}`.")
    md.append("")
    md.append("## Setup")
    md.append("")
    md.append(f"- Data: {q('SELECT COUNT(*) FROM trades').fetchone()[0]:,} trades, {len(series):,} coins with a price series, {len(seqs):,} direct/clean sequences (pairing window {a.window // 60} min), range {datetime.fromtimestamp(lo, timezone.utc):%Y-%m-%d %H:%M}–{datetime.fromtimestamp(hi, timezone.utc):%H:%M} UTC, {len(grad)} graduations recorded.")
    md.append(f"- Signal minute: a coin had ≥ 1 rotation inflow wallet in the previous 10 minutes; features use only data up to that minute. {len(signals):,} signal minutes on {len({r['tok'] for r in signals}):,} coins (bots excluded: {len(bots):,} wallets with > 60 trades/hour or > 500 trades).")
    md.append(f"- Outcome: median trade price reaches ≥ {1 + a.runner_gain:.0f}× the signal-minute price within {H // 60} min, or the coin graduates within {H // 60} min. Signals whose horizon runs past the end of the data are dropped.")
    md.append(f"- Base rate: the same outcome at every coin-minute with at least one trade: **{pct(base_rate, 2)}** of {base_n:,} coin-minutes (median max gain {pct(median(base_gains))}).")
    md.append("")
    md.append("## Results")
    md.append("")
    md += table("By rotation inflow (distinct wallets in the last 10 min)", grp(inflow_bins))
    md += table("By acceleration (inflow last 10 min vs the 20 min before)", grp(accel_bins))
    md += table("By coin age at the signal", grp(age_bins))
    md += table("By price momentum already in the last 10 min", grp(mom_bins))
    md += table("By breadth (number of source coins)", grp(breadth_bins))
    md += table("By walk-forward wallet quality (mean past runner rate of the inflow wallets)", grp(q_bins))
    md += table("Combined rules", combos)
    md += table("First time a coin crosses inflow ≥ 8 (one row per coin)", [("all coins", fc), ("age < 1 h", [r for r in fc if r["age"] < 3600]), ("age < 1 h and not already +100%", [r for r in fc if r["age"] < 3600 and (r["momentum"] is None or r["momentum"] < 1.0)])])
    md.append("## Reading this honestly")
    md.append("")
    md.append("- Signal minutes are autocorrelated (a coin with inflow for 20 minutes contributes 20 rows). The per-coin table is the fairer count.")
    md.append("- 'Runner' is a price path on the PONS curve, with 1% fees and creator taxes ignored; nobody earned these numbers, and many of these coins go to zero afterwards (see the FDV of yesterday's TruffleHog: $3k).")
    md.append("- Rotation inflow is observed order of trades by the same address. It says nothing about who those addresses are or why they moved.")
    md.append(f"- The sample is {hours:.1f} hours of one chain on one launchpad. Treat lifts as a first measurement, not a law; the numbers are recomputed whenever this script runs on a new range.")
    md.append("")
    md.append(f"Wallet scores written: {len(final_scores):,} wallets with ≥ 3 known rotation outcomes or flagged as bots. Compute time {time.time() - t0:.0f} s.")
    text = "\n".join(md)
    if a.out:
        Path(a.out).write_text(text)
        print(f"written {a.out}")
    else:
        print(text)
    if a.write_scores:
        store.db.executemany("INSERT OR REPLACE INTO wallet_scores(wallet, rotations, runner_hits, score, is_bot, trades_per_hour, updated_at) VALUES(?,?,?,?,?,?,?)", [(x["wallet"], x["rotations"], x["runner_hits"], x["score"], x["is_bot"], x["trades_per_hour"], time.time()) for x in final_scores])
        store.commit()
        print(f"wallet_scores: {len(final_scores)} rows written into {a.db}")
    if a.scores_out:
        Path(a.scores_out).write_text(json.dumps(final_scores))
        print(f"scores json: {a.scores_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
