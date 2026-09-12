"""Paper-trading ledger of the realtime engine (stage 7): every engine `edge_enter` verdict opens a simulated position.

Nothing here touches a wallet. The ledger is the honest record of what the stage-4/5 policy would have done, filled by
the curve arithmetic that `CurveReserve` validated exactly on real trades, at the reserves of the block *after* the
decision (our order cannot land in the block that produced the signal):

- entry: `spent` = the risk-engine size (default cap 0.02 ETH); fee 1 % of spent, creator tax at the coin's observed
  rate (from the curve's CurveBuy/CurveSell events, else the launch intel, else 100 bps flagged as estimated), snipe tax
  when the fill is inside the 3-s window; `net = spent − fee − tax − snipe`, `tokensOut = net·T/(Q+net)` on the
  observed reserves; impact = effective price vs the spot price before the fill;
- mark every block: `gross = tokensIn·Q/(T+tokensIn)`, `out = gross − 1 % − tax` for the remaining tokens (what a sell
  would return now); `ret = out/tokens ÷ entry price − 1` with the entry price including the fees;
- exits per the plan of docs/RESEARCH-EDGE.md (`edge-config.json: exit_plan`): TP ladder (sell 50 % at +100 %, then a
  25 % trailing stop from the high), stop −30 %, time exit 45 min, inflow-dies (no rotation inflow in 10 min after 5 min
  in the trade), the risk engine's exit-now triggers (sell pressure, deployer / exempt wallet selling, liquidity drop);
  graduation closes the position at the observed pool price (median of the last 5 trades) minus 1 % + tax, or at the
  last curve state when no pool trade is seen yet;
- the observed trades are replayed unchanged: our fills never move the reserves other traders saw (same convention as
  the research simulator). MEV, failed transactions and gas are not modelled.

Tables (created here with CREATE TABLE IF NOT EXISTS): `paper_positions`, `paper_fills`, `paper_equity`. Rows are
written through the engine's `WriteBatch.sql` (one transaction per 250 ms with everything else). Events `position`
(`kind`: opened / fill / mark / closed / skipped) go to the SSE bus. `stats()` gives trades, hit rate, expectancy per
trade in quote and USD (when `fx_rates` has the hour), profit factor, max drawdown of the sequential equity curve, the
per-hour distribution and a bootstrap 95 % CI by coin. `GET /api/paper` and `stampede track-record` read the same
numbers, from memory when the engine runs in the process and from the tables otherwise.
"""
from __future__ import annotations

import json
import random
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from .. import chain
from ..signals import risk
from ..signals import verdict as verdict_mod

SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_positions (
  id INTEGER PRIMARY KEY, token TEXT, symbol TEXT, curve TEXT, quote_token TEXT, mode TEXT,
  alert_ts INTEGER, alert_block INTEGER, opened_ts INTEGER, opened_block INTEGER,
  size_quote REAL, size_wei TEXT, tokens TEXT, tokens_remaining TEXT, entry_px REAL, spot_px_before REAL, impact_bps REAL,
  fee_wei TEXT, tax_wei TEXT, snipe_wei TEXT, tax_bps INTEGER, tax_source TEXT,
  p_2x_30m REAL, ev_quote REAL, plan TEXT, status TEXT, peak_px REAL, tp_done INTEGER,
  proceeds_wei TEXT, exit_fees_wei TEXT, mark_px REAL, mark_quote REAL, unrealized_quote REAL,
  closed_ts INTEGER, closed_block INTEGER, exit_reason TEXT, pnl_quote REAL, pnl_usd REAL, hold_s INTEGER, updated_ts INTEGER);
CREATE INDEX IF NOT EXISTS paper_positions_token ON paper_positions(token, opened_ts);
CREATE TABLE IF NOT EXISTS paper_fills (
  id INTEGER PRIMARY KEY AUTOINCREMENT, position_id INTEGER, ts INTEGER, block INTEGER, side TEXT, tokens TEXT, quote_wei TEXT,
  fee_wei TEXT, tax_wei TEXT, snipe_wei TEXT, px REAL, reason TEXT, reserve_quote TEXT, reserve_tokens TEXT, venue TEXT);
CREATE INDEX IF NOT EXISTS paper_fills_pos ON paper_fills(position_id);
CREATE TABLE IF NOT EXISTS paper_equity (
  ts INTEGER PRIMARY KEY, block INTEGER, realized_quote REAL, unrealized_quote REAL, equity_quote REAL, open_positions INTEGER, closed_positions INTEGER, realized_usd REAL);
"""

POSITION_COLUMNS = (
    "id", "token", "symbol", "curve", "quote_token", "mode", "alert_ts", "alert_block", "opened_ts", "opened_block", "size_quote", "size_wei", "tokens", "tokens_remaining",
    "entry_px", "spot_px_before", "impact_bps", "fee_wei", "tax_wei", "snipe_wei", "tax_bps", "tax_source", "p_2x_30m", "ev_quote", "plan", "status", "peak_px", "tp_done",
    "proceeds_wei", "exit_fees_wei", "mark_px", "mark_quote", "unrealized_quote", "closed_ts", "closed_block", "exit_reason", "pnl_quote", "pnl_usd", "hold_s", "updated_ts",
)
POSITION_INSERT = f"INSERT OR REPLACE INTO paper_positions({','.join(POSITION_COLUMNS)}) VALUES({','.join('?' * len(POSITION_COLUMNS))})"
FILL_INSERT = "INSERT INTO paper_fills(position_id, ts, block, side, tokens, quote_wei, fee_wei, tax_wei, snipe_wei, px, reason, reserve_quote, reserve_tokens, venue) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
EQUITY_INSERT = "INSERT OR REPLACE INTO paper_equity(ts, block, realized_quote, unrealized_quote, equity_quote, open_positions, closed_positions, realized_usd) VALUES(?,?,?,?,?,?,?,?)"
FEE_BPS = chain.CURVE_FEE_BPS
DEFAULT_TAX_BPS = 100
MARK_EVERY_S = 5  # chain seconds between `mark` events / row updates of one open position
EQUITY_EVERY_S = 60
INFLOW_DIES_AFTER_S = 300
WEI = 10**18


def ensure_tables(db) -> None:
    db.executescript(SCHEMA)


# ---- fills ------------------------------------------------------------------------------------------------------------
def buy_fill(reserve, spent: int, tax_bps: int, elapsed_s: int | None) -> dict[str, int]:
    """A buy of `spent` raw quote on `reserve` (CurveReserve): fee 1 %, snipe tax inside the window, creator tax, then
    tokensOut = net·T/(Q+net) on the virtual reserves. Returns raw integers."""
    from ..context.launch_intel import snipe_tax_bps

    snipe_bps = snipe_tax_bps(int(elapsed_s), creator_tax_bps=tax_bps) if elapsed_s is not None and elapsed_s < chain.SNIPE_TAX_SECONDS else 0
    fee = spent * FEE_BPS // 10_000
    snipe = spent * snipe_bps // 10_000
    tax = spent * tax_bps // 10_000
    net = spent - fee - snipe - tax
    out = reserve.tokens_out(net) if net > 0 else 0
    return {"tokens_out": out, "fee": fee, "snipe": snipe, "tax": tax, "net": net}


def sell_fill(reserve, tokens_in: int, tax_bps: int) -> dict[str, int]:
    """A sell of `tokens_in` raw tokens: gross = tokensIn·Q/(T+tokensIn); out = gross − 1 % − creator tax."""
    gross = reserve.quote_out(tokens_in) if tokens_in > 0 else 0
    fee = gross * FEE_BPS // 10_000
    tax = gross * tax_bps // 10_000
    return {"quote_out": gross - fee - tax, "gross": gross, "fee": fee, "tax": tax}


def pool_sell(tokens_in: int, pool_px: float, tax_bps: int) -> dict[str, int]:
    """After graduation: the observed pool price (quote per token, decimals applied) less 1 % + tax."""
    gross = int(tokens_in * pool_px)
    fee = gross * FEE_BPS // 10_000
    tax = gross * tax_bps // 10_000
    return {"quote_out": gross - fee - tax, "gross": gross, "fee": fee, "tax": tax}


def _short_trigger(reason: str) -> str:
    """`risk.exit_now` reasons -> the short keys the tables and the stats use."""
    r = reason.lower()
    if r.startswith("sell pressure"):
        return "sell pressure"
    if r.startswith("deployer"):
        return "insider selling"
    if r.startswith("liquidity"):
        return "liquidity drop"
    return r.split(":")[0][:24]


# ---- positions ----------------------------------------------------------------------------------------------------------
@dataclass
class PendingEntry:
    token: str
    symbol: str
    curve: str
    alert_ts: int
    alert_block: int
    size_wei: int
    verdict: dict[str, Any]
    features: dict[str, Any]
    tax_hint: int | None


@dataclass
class Position:
    id: int
    token: str
    symbol: str
    curve: str
    quote_token: str
    alert_ts: int
    alert_block: int
    opened_ts: int
    opened_block: int
    size_wei: int
    tokens: int
    tokens_remaining: int
    entry_px: float
    spot_px_before: float | None
    impact_bps: float | None
    fee_wei: int
    tax_wei: int
    snipe_wei: int
    tax_bps: int
    tax_source: str
    p_2x_30m: float | None
    ev_quote: float | None
    plan: dict[str, Any]
    status: str = "open"
    peak_px: float = 0.0
    tp_done: bool = False
    proceeds_wei: int = 0
    exit_fees_wei: int = 0
    mark_px: float | None = None
    mark_wei: int | None = None
    closed_ts: int | None = None
    closed_block: int | None = None
    exit_reason: str | None = None
    pnl_usd: float | None = None
    updated_ts: int = 0
    last_mark_emit: int = 0
    fills: list[dict[str, Any]] = field(default_factory=list)
    progress_hist: deque = field(default_factory=lambda: deque(maxlen=400))

    @property
    def size_quote(self) -> float:
        return self.size_wei / WEI

    @property
    def unrealized_quote(self) -> float | None:
        if self.status != "open" or self.mark_wei is None:
            return None
        cost_open = self.size_wei * (self.tokens_remaining / self.tokens) if self.tokens else 0
        return (self.mark_wei - cost_open) / WEI

    @property
    def pnl_quote(self) -> float | None:
        if self.status == "closed":
            return (self.proceeds_wei - self.size_wei) / WEI
        if self.mark_wei is None:
            return None
        return (self.proceeds_wei + self.mark_wei - self.size_wei) / WEI

    @property
    def ret(self) -> float | None:
        if self.mark_px is None or not self.entry_px:
            return None
        return self.mark_px / self.entry_px - 1

    @property
    def hold_s(self) -> int | None:
        return (self.closed_ts - self.opened_ts) if self.closed_ts else None

    def to_dict(self, clock: int | None = None, fills: bool = True) -> dict[str, Any]:
        d = {
            "id": self.id, "token": self.token, "symbol": self.symbol, "curve": self.curve, "quote_token": self.quote_token, "quote_symbol": "ETH" if self.quote_token == chain.NATIVE else None,
            "status": self.status, "alert_ts": self.alert_ts, "alert_block": self.alert_block, "opened_ts": self.opened_ts, "opened_block": self.opened_block,
            "size_quote": self.size_quote, "tokens": str(self.tokens), "tokens_remaining": str(self.tokens_remaining), "tokens_f": self.tokens / WEI,
            "entry_px": self.entry_px, "spot_px_before": self.spot_px_before, "impact_bps": self.impact_bps,
            "fee_quote": self.fee_wei / WEI, "tax_quote": self.tax_wei / WEI, "snipe_quote": self.snipe_wei / WEI, "tax_bps": self.tax_bps, "tax_source": self.tax_source,
            "p_2x_30m": self.p_2x_30m, "ev_quote": self.ev_quote, "plan": self.plan, "peak_px": self.peak_px, "tp_done": self.tp_done,
            "proceeds_quote": self.proceeds_wei / WEI, "exit_fees_quote": self.exit_fees_wei / WEI, "mark_px": self.mark_px, "mark_quote": (self.mark_wei / WEI) if self.mark_wei is not None else None,
            "unrealized_quote": self.unrealized_quote, "ret": self.ret, "closed_ts": self.closed_ts, "closed_block": self.closed_block, "exit_reason": self.exit_reason,
            "pnl_quote": self.pnl_quote, "pnl_usd": self.pnl_usd, "hold_s": self.hold_s if self.closed_ts else ((clock - self.opened_ts) if clock else None), "updated_ts": self.updated_ts,
            "peak_ret": (self.peak_px / self.entry_px - 1) if self.entry_px and self.peak_px else None,
        }
        if fills:
            d["fills"] = list(self.fills)
        return d

    def row(self) -> tuple:
        return (
            self.id, self.token, self.symbol, self.curve, self.quote_token, "live", self.alert_ts, self.alert_block, self.opened_ts, self.opened_block, self.size_quote, str(self.size_wei), str(self.tokens), str(self.tokens_remaining),
            self.entry_px, self.spot_px_before, self.impact_bps, str(self.fee_wei), str(self.tax_wei), str(self.snipe_wei), self.tax_bps, self.tax_source, self.p_2x_30m, self.ev_quote, json.dumps(self.plan), self.status, self.peak_px, int(self.tp_done),
            str(self.proceeds_wei), str(self.exit_fees_wei), self.mark_px, (self.mark_wei / WEI) if self.mark_wei is not None else None, self.unrealized_quote, self.closed_ts, self.closed_block, self.exit_reason, self.pnl_quote if self.status == "closed" else None, self.pnl_usd, self.hold_s, self.updated_ts,
        )


def position_from_row(r: dict[str, Any]) -> Position:
    p = Position(
        id=int(r["id"]), token=r["token"], symbol=r["symbol"] or "?", curve=r["curve"] or "", quote_token=r["quote_token"] or chain.NATIVE, alert_ts=int(r["alert_ts"] or 0), alert_block=int(r["alert_block"] or 0),
        opened_ts=int(r["opened_ts"] or 0), opened_block=int(r["opened_block"] or 0), size_wei=int(r["size_wei"] or 0), tokens=int(r["tokens"] or 0), tokens_remaining=int(r["tokens_remaining"] or 0),
        entry_px=float(r["entry_px"] or 0.0), spot_px_before=r["spot_px_before"], impact_bps=r["impact_bps"], fee_wei=int(r["fee_wei"] or 0), tax_wei=int(r["tax_wei"] or 0), snipe_wei=int(r["snipe_wei"] or 0),
        tax_bps=int(r["tax_bps"] or 0), tax_source=r["tax_source"] or "?", p_2x_30m=r["p_2x_30m"], ev_quote=r["ev_quote"], plan=json.loads(r["plan"]) if r.get("plan") else {},
        status=r["status"] or "open", peak_px=float(r["peak_px"] or 0.0), tp_done=bool(r["tp_done"]), proceeds_wei=int(r["proceeds_wei"] or 0), exit_fees_wei=int(r["exit_fees_wei"] or 0),
        mark_px=r["mark_px"], mark_wei=int(float(r["mark_quote"]) * WEI) if r.get("mark_quote") is not None else None, closed_ts=r["closed_ts"], closed_block=r["closed_block"], exit_reason=r["exit_reason"], pnl_usd=r["pnl_usd"], updated_ts=int(r["updated_ts"] or 0),
    )
    return p


# ---- statistics ------------------------------------------------------------------------------------------------------------
def bootstrap_ci(groups: list[list[float]], n_boot: int = 1000, seed: int = 7, level: float = 0.95) -> tuple[float, float] | None:
    """95 % interval of the mean pnl per trade, resampling coins (groups of trades) with replacement."""
    groups = [g for g in groups if g]
    if len(groups) < 2:
        return None
    rng = random.Random(seed)
    means: list[float] = []
    k = len(groups)
    for _ in range(n_boot):
        pick = [groups[rng.randrange(k)] for _ in range(k)]
        vals = [x for g in pick for x in g]
        means.append(sum(vals) / len(vals))
    means.sort()
    a = (1 - level) / 2
    return means[int(a * (n_boot - 1))], means[int((1 - a) * (n_boot - 1))]


def max_drawdown(pnls: list[float]) -> float:
    eq = peak = mdd = 0.0
    for x in pnls:
        eq += x
        peak = max(peak, eq)
        mdd = min(mdd, eq - peak)
    return mdd


def stats(closed: list[dict[str, Any]], open_: list[dict[str, Any]] | None = None, skipped: int = 0) -> dict[str, Any]:
    """Trades, hit rate, expectancy (quote + USD when known), profit factor, max drawdown, exits, per-hour distribution,
    bootstrap 95 % CI by coin. `closed` / `open_` are position dicts (`Position.to_dict`)."""
    closed = sorted(closed, key=lambda p: (p.get("closed_ts") or 0, p["id"]))
    pnls = [float(p["pnl_quote"]) for p in closed if p.get("pnl_quote") is not None]
    n = len(pnls)
    wins = [x for x in pnls if x > 0]
    losses = [x for x in pnls if x <= 0]
    usd = [float(p["pnl_usd"]) for p in closed if p.get("pnl_usd") is not None]
    by_coin: dict[str, list[float]] = {}
    for p in closed:
        if p.get("pnl_quote") is not None:
            by_coin.setdefault(p["token"], []).append(float(p["pnl_quote"]))
    ci = bootstrap_ci(list(by_coin.values()))
    per_hour: dict[str, dict[str, Any]] = {}
    by_hod: dict[int, dict[str, Any]] = {}
    for p, x in zip([p for p in closed if p.get("pnl_quote") is not None], pnls):
        h = datetime.fromtimestamp(int(p["opened_ts"]), timezone.utc)
        key = h.strftime("%Y-%m-%d %H")
        d = per_hour.setdefault(key, {"hour": key, "n": 0, "wins": 0, "pnl": 0.0})
        d["n"] += 1
        d["wins"] += 1 if x > 0 else 0
        d["pnl"] += x
        e = by_hod.setdefault(h.hour, {"hour": h.hour, "n": 0, "wins": 0, "pnl": 0.0})
        e["n"] += 1
        e["wins"] += 1 if x > 0 else 0
        e["pnl"] += x
    size_mean = (sum(float(p["size_quote"]) for p in closed) / n) if n else None
    open_ = open_ or []
    unreal = [float(p["unrealized_quote"]) for p in open_ if p.get("unrealized_quote") is not None]
    return {
        "trades": n,
        "coins": len(by_coin),
        "wins": len(wins),
        "hit_rate": (len(wins) / n) if n else None,
        "expectancy_quote": (sum(pnls) / n) if n else None,
        "expectancy_pct": (sum(pnls) / n / size_mean) if n and size_mean else None,
        "expectancy_usd": (sum(usd) / len(usd)) if usd else None,
        "usd_known": len(usd),
        "total_quote": sum(pnls),
        "total_usd": sum(usd) if usd and len(usd) == n else None,
        "profit_factor": (sum(wins) / abs(sum(losses))) if losses and sum(losses) < 0 else (None if not wins else float("inf")),
        "max_drawdown_quote": max_drawdown(pnls),
        "avg_win_quote": (sum(wins) / len(wins)) if wins else None,
        "avg_loss_quote": (sum(losses) / len(losses)) if losses else None,
        "median_hold_s": sorted(int(p["hold_s"]) for p in closed if p.get("hold_s") is not None)[n // 2] if n else None,
        "fees_quote": sum(float(p.get("fee_quote") or 0) + float(p.get("tax_quote") or 0) + float(p.get("snipe_quote") or 0) + float(p.get("exit_fees_quote") or 0) for p in closed),
        "size_mean_quote": size_mean,
        "exits": dict(Counter(p.get("exit_reason") or "?" for p in closed)),
        "per_hour": sorted(per_hour.values(), key=lambda d: d["hour"]),
        "by_hour_of_day": [by_hod[h] for h in sorted(by_hod)],
        "ci95_quote": list(ci) if ci else None,
        "ci_method": "bootstrap of the mean pnl per trade, resampling coins with replacement, 1000 draws, seed 7" if ci else "needs closed trades on at least 2 coins",
        "open": len(open_),
        "unrealized_quote": sum(unreal) if unreal else 0.0,
        "exposure_quote": sum(float(p["size_quote"]) * (float(p["tokens_remaining"]) / float(p["tokens"]) if float(p["tokens"] or 0) else 0.0) for p in open_),
        "skipped_by_risk": skipped,
        "simulated": True,
        "note": "Paper fills are simulated by the curve arithmetic at the next block's observed reserves; no order was sent, nobody earned these numbers.",
    }


# ---- the ledger --------------------------------------------------------------------------------------------------------------
class PaperLedger:
    """Owned by `EngineState`: `on_enter()` at an engine `edge_enter` verdict, `on_block()` once per block after the
    reserves and rows are updated. Persists through `WriteBatch.sql`."""

    def __init__(self, store=None, *, config: dict[str, Any] | None = None, risk_cfg: dict[str, Any] | None = None, fx=None, latency_s: int = 0, qdec: dict[str, int] | None = None):
        self.cfg = config if config is not None else verdict_mod.load_config()
        self.rcfg = risk_cfg or risk.load_config()
        self.plan: dict[str, Any] = dict(self.cfg.get("exit_plan") or verdict_mod.DEFAULT_EXIT)
        payoff = self.cfg.get("payoff") or verdict_mod.DEFAULT_PAYOFF
        self.payoff = {"win_mean": float(payoff.get("runner_mean", 0.0)), "loss_mean": float(payoff.get("other_mean", 0.0)), "size_eth": float(payoff.get("size_eth") or 0.02)}
        self.fx = fx
        self.latency_s = latency_s
        self.qdec = qdec or {chain.NATIVE: 18}
        self.pending: list[PendingEntry] = []
        self.open: dict[int, Position] = {}
        self.closed: list[Position] = []
        self.features: dict[str, dict[str, Any]] = {}  # token -> last verdict feature dict of an open/pending coin
        self.next_id = 1
        self.stats: Counter = Counter()
        self.last_equity_ts = 0
        self.realized_wei = 0
        self.realized_usd: float | None = None
        self.realized_usd_known = 0
        self.equity_curve: deque = deque(maxlen=5000)  # (ts, realized_quote, unrealized_quote, equity_quote)
        if store is not None:
            self.load(store)

    # ---- persistence ----
    def load(self, store) -> None:
        ensure_tables(store.db)
        r = store.db.execute("SELECT MAX(id) FROM paper_positions").fetchone()
        self.next_id = int(r[0] or 0) + 1
        cols = [c[1] for c in store.db.execute("PRAGMA table_info(paper_positions)")]
        for row in store.db.execute("SELECT * FROM paper_positions WHERE status='open' ORDER BY id"):
            p = position_from_row(dict(zip(cols, row)))
            self.open[p.id] = p
        for row in store.db.execute("SELECT * FROM paper_positions WHERE status='closed' ORDER BY closed_ts, id"):
            p = position_from_row(dict(zip(cols, row)))
            self.closed.append(p)
            self.realized_wei += p.proceeds_wei - p.size_wei
            if p.pnl_usd is not None:
                self.realized_usd = (self.realized_usd or 0.0) + p.pnl_usd
                self.realized_usd_known += 1
        for row in store.db.execute("SELECT ts, realized_quote, unrealized_quote, equity_quote FROM paper_equity ORDER BY ts DESC LIMIT 5000"):
            self.equity_curve.appendleft(tuple(row))
        if self.fx is None:
            try:
                from ..context.fx import Fx

                fx = Fx(store)
                self.fx = fx if fx.rates else None
            except Exception:  # noqa: BLE001
                self.fx = None
        self.stats["loaded_open"] = len(self.open)
        self.stats["loaded_closed"] = len(self.closed)

    def tracked_tokens(self) -> set[str]:
        return {p.token for p in self.open.values()} | {e.token for e in self.pending}

    def _usd(self, wei: int, ts: int) -> float | None:
        if self.fx is None:
            return None
        rate = self.fx.usd(chain.NATIVE, ts)
        return None if rate is None else wei / WEI * rate

    def day_pnl_quote(self, clock: int) -> float:
        midnight = clock - clock % 86400
        return sum((p.proceeds_wei - p.size_wei) for p in self.closed if (p.closed_ts or 0) >= midnight) / WEI

    # ---- entries ----
    def on_enter(self, token: str, symbol: str, curve: str | None, verdict: dict[str, Any], features: dict[str, Any] | None, clock: int, block: int, tax_hint: int | None = None) -> dict[str, Any] | None:
        """An engine `edge_enter` alert: queue the entry for the next block (the fill uses that block's reserves). Returns a
        `skipped` event when the risk engine says no (position cap, daily stop, no size); the alert itself still stands."""
        if not curve:
            self.stats["skipped_no_curve"] += 1
            return {"kind": "skipped", "token": token, "symbol": symbol, "clock": clock, "block": block, "reason": "no curve known for the coin"}
        if any(p.token == token for p in self.open.values()) or any(e.token == token for e in self.pending):
            self.stats["skipped_open"] += 1
            return {"kind": "skipped", "token": token, "symbol": symbol, "clock": clock, "block": block, "reason": "position already open"}
        f = features or {}
        p = verdict.get("p_2x_30m")
        sz = risk.size(p, self.payoff, self.rcfg, range_5m=f.get("range_5m"), open_positions=len(self.open) + len(self.pending), day_pnl_quote=self.day_pnl_quote(clock))
        if not sz["allowed"] or not sz["quote"]:
            self.stats["skipped_by_risk"] += 1
            return {"kind": "skipped", "token": token, "symbol": symbol, "clock": clock, "block": block, "reason": "; ".join(sz.get("reasons") or ["risk engine: no size"]), "capped_by": sz.get("capped_by")}
        quote = float(sz["quote"])
        vq = (verdict.get("size") or {}).get("quote")
        if vq:  # never larger than the size the alert showed (same inputs; the verdict may have seen a wider 5-min range)
            quote = min(quote, float(vq))
        self.pending.append(PendingEntry(token, symbol, curve, clock, block, int(round(quote * WEI)), verdict, dict(f), tax_hint))
        self.features[token] = dict(f)
        self.stats["queued"] += 1
        return None

    def _tax_for(self, reserve, hint: int | None) -> tuple[int, str]:
        t = getattr(reserve, "tax_bps", None)
        if t is not None:
            return int(t), "curve events"
        if hint is not None:
            return int(hint), "launch intel"
        return DEFAULT_TAX_BPS, "estimated (100 bps default)"

    def _fill_pending(self, clock: int, block: int, reserves: dict, launches: dict, labels: Callable[[str], str], wb) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        keep: list[PendingEntry] = []
        for e in self.pending:
            if block <= e.alert_block or clock < e.alert_ts + self.latency_s:
                keep.append(e)
                continue
            rs = reserves.get(e.curve)
            sym = labels(e.token) or e.symbol
            if rs is None or rs.price() is None or rs.phantom is None:
                self.stats["skipped_reserve_unknown"] += 1
                out.append({"kind": "skipped", "token": e.token, "symbol": sym, "clock": clock, "block": block, "reason": "curve reserve not known at the fill block (snapshot pending)"})
                continue
            if rs.graduated:
                self.stats["skipped_graduated"] += 1
                out.append({"kind": "skipped", "token": e.token, "symbol": sym, "clock": clock, "block": block, "reason": "graduated before the fill"})
                continue
            if rs.pair_token != chain.NATIVE:
                self.stats["skipped_non_eth"] += 1
                out.append({"kind": "skipped", "token": e.token, "symbol": sym, "clock": clock, "block": block, "reason": "non-ETH quote: the policy was measured on ETH curves"})
                continue
            tax_bps, tax_src = self._tax_for(rs, e.tax_hint)
            la = launches.get(e.token) or {}
            elapsed = (clock - la["ts"]) if la.get("ts") else None
            spot = rs.price()
            f = buy_fill(rs, e.size_wei, tax_bps, elapsed)
            if f["tokens_out"] <= 0:
                self.stats["skipped_no_tokens"] += 1
                out.append({"kind": "skipped", "token": e.token, "symbol": sym, "clock": clock, "block": block, "reason": "fill returned no tokens"})
                continue
            entry_px = e.size_wei / f["tokens_out"]
            pos = Position(
                id=self.next_id, token=e.token, symbol=sym, curve=e.curve, quote_token=chain.NATIVE, alert_ts=e.alert_ts, alert_block=e.alert_block, opened_ts=clock, opened_block=block,
                size_wei=e.size_wei, tokens=f["tokens_out"], tokens_remaining=f["tokens_out"], entry_px=entry_px, spot_px_before=spot, impact_bps=((f["net"] / f["tokens_out"]) / spot - 1) * 10_000 if spot else None,
                fee_wei=f["fee"], tax_wei=f["tax"], snipe_wei=f["snipe"], tax_bps=tax_bps, tax_source=tax_src, p_2x_30m=e.verdict.get("p_2x_30m"), ev_quote=e.verdict.get("ev_per_trade_quote"),
                plan={"tp": self.plan.get("tp"), "trail": self.plan.get("trail"), "sl": self.plan.get("sl"), "time_s": self.plan.get("time_s") or self.plan.get("time_exit_s") or 1800, "inflow_dies": bool(self.plan.get("inflow_dies")), "text": ((e.verdict.get("exit_plan") or {}).get("text") or [])},
                peak_px=entry_px, updated_ts=clock,
            )
            self.next_id += 1
            m = sell_fill(rs, pos.tokens_remaining, tax_bps)
            pos.mark_wei, pos.mark_px = m["quote_out"], (m["quote_out"] / pos.tokens_remaining if pos.tokens_remaining else None)
            pos.progress_hist.append((clock, rs.progress()))
            fill = {"ts": clock, "block": block, "side": "buy", "tokens": str(pos.tokens), "quote": e.size_wei / WEI, "fee": f["fee"] / WEI, "tax": f["tax"] / WEI, "snipe": f["snipe"] / WEI, "px": entry_px, "reason": "entry", "venue": "curve"}
            pos.fills.append(fill)
            self.open[pos.id] = pos
            self.stats["opened"] += 1
            if wb is not None:
                wb.sql.append((POSITION_INSERT, pos.row()))
                wb.sql.append((FILL_INSERT, (pos.id, clock, block, "buy", str(pos.tokens), str(e.size_wei), str(f["fee"]), str(f["tax"]), str(f["snipe"]), entry_px, "entry", str(rs.quote), str(rs.tokens), "curve")))
            out.append({"kind": "opened", **pos.to_dict(clock)})
        self.pending = keep
        return out

    # ---- exits ----
    def _sell(self, pos: Position, frac: float, clock: int, block: int, reason: str, rs, pool_px: float | None, wb) -> dict[str, Any]:
        amt = pos.tokens_remaining if frac >= 1 else pos.tokens_remaining * int(round(frac * 10_000)) // 10_000  # integer math: no float rounding on 1e24-scale token amounts
        if amt <= 0:
            return {}
        venue = "curve"
        if rs is not None and not rs.graduated and rs.price() is not None:
            f = sell_fill(rs, amt, pos.tax_bps)
        elif pool_px is not None:
            f = pool_sell(amt, pool_px, pos.tax_bps)
            venue = "pool"
        elif rs is not None and rs.phantom is not None:
            f = sell_fill(rs, amt, pos.tax_bps)  # graduated, no pool trade seen: the last curve state
            venue = "curve (last state)"
        else:
            f = {"quote_out": 0, "gross": 0, "fee": 0, "tax": 0}
            venue = "unknown"
        pos.tokens_remaining -= amt
        pos.proceeds_wei += f["quote_out"]
        pos.exit_fees_wei += f["fee"] + f["tax"]
        px = f["quote_out"] / amt if amt else 0.0
        fill = {"ts": clock, "block": block, "side": "sell", "tokens": str(amt), "quote": f["quote_out"] / WEI, "fee": f["fee"] / WEI, "tax": f["tax"] / WEI, "snipe": 0.0, "px": px, "reason": reason, "venue": venue}
        pos.fills.append(fill)
        if wb is not None:
            wb.sql.append((FILL_INSERT, (pos.id, clock, block, "sell", str(amt), str(f["quote_out"]), str(f["fee"]), str(f["tax"]), "0", px, reason, str(rs.quote) if rs is not None else None, str(rs.tokens) if rs is not None else None, venue)))
        return fill

    @staticmethod
    def _mark_sql(pos: Position, clock: int) -> tuple[str, tuple]:
        return ("UPDATE paper_positions SET mark_px=?, mark_quote=?, unrealized_quote=?, peak_px=?, tokens_remaining=?, tp_done=?, proceeds_wei=?, exit_fees_wei=?, updated_ts=? WHERE id=?", (pos.mark_px, (pos.mark_wei or 0) / WEI, pos.unrealized_quote, pos.peak_px, str(pos.tokens_remaining), int(pos.tp_done), str(pos.proceeds_wei), str(pos.exit_fees_wei), clock, pos.id))

    def _close(self, pos: Position, clock: int, block: int, reason: str, wb) -> dict[str, Any]:
        pos.status, pos.closed_ts, pos.closed_block, pos.exit_reason, pos.updated_ts = "closed", clock, block, reason, clock
        pos.mark_wei, pos.mark_px = 0, (pos.fills[-1]["px"] if pos.fills else pos.mark_px)
        pos.pnl_usd = self._usd(pos.proceeds_wei - pos.size_wei, clock)
        self.open.pop(pos.id, None)
        self.closed.append(pos)
        self.realized_wei += pos.proceeds_wei - pos.size_wei
        if pos.pnl_usd is not None:
            self.realized_usd = (self.realized_usd or 0.0) + pos.pnl_usd
            self.realized_usd_known += 1
        self.features.pop(pos.token, None)
        self.stats["closed"] += 1
        self.stats[f"exit_{reason.split(':')[0]}"] += 1
        if wb is not None:
            wb.sql.append((POSITION_INSERT, pos.row()))
        return {"kind": "closed", **pos.to_dict(clock)}

    def _exit_reasons(self, pos: Position, clock: int, row: dict[str, Any] | None) -> list[str]:
        """Exit-now triggers of the risk engine from the coin's latest features (+ the ledger's own progress_5m)."""
        f = dict(self.features.get(pos.token) or {})
        if pos.progress_hist:
            now_p = pos.progress_hist[-1][1]
            old = [p for t, p in pos.progress_hist if t <= clock - 300]
            if now_p is not None and old and old[-1] is not None:
                f["progress_5m"] = now_p - old[-1]
        f.setdefault("stage", "curve")
        if row is not None:
            f.setdefault("progress", row.get("progress"))
        reasons = [r for r in risk.exit_now(f, self.rcfg) if not r.startswith("graduation at") and not r.startswith("graduated")]
        return reasons

    def on_block(self, clock: int, block: int, reserves: dict, rows: dict[str, dict[str, Any]], launches: dict, graduations: dict, price_now: Callable[[str], float | None], labels: Callable[[str], str], wb, is_fragment: bool = False) -> list[dict[str, Any]]:
        """Fills the pending entries at this block's reserves, then marks every open position and applies the exit plan."""
        events: list[dict[str, Any]] = []
        if is_fragment:
            return events
        events.extend(self._fill_pending(clock, block, reserves, launches, labels, wb))
        opened_now = {e["id"] for e in events if e.get("kind") == "opened"}
        for pos in list(self.open.values()):
            if pos.id in opened_now:
                continue
            rs = reserves.get(pos.curve)
            row = rows.get(pos.token)
            grad = graduations.get(pos.token)
            graduated = bool((rs is not None and rs.graduated) or (grad and (grad.get("ts") is None or grad["ts"] <= clock)))
            pool_px = price_now(pos.token) if graduated else None
            if graduated:
                if pos.tokens_remaining > 0:
                    self._sell(pos, 1.0, clock, block, "graduation", rs, pool_px, wb)
                events.append(self._close(pos, clock, block, "graduation", wb))
                continue
            if rs is None or rs.price() is None or rs.phantom is None:
                continue  # reserve not trustworthy yet (snapshot pending): no mark, no exit decision
            pos.progress_hist.append((clock, rs.progress()))
            m = sell_fill(rs, pos.tokens_remaining, pos.tax_bps)
            pos.mark_wei = m["quote_out"]
            pos.mark_px = m["quote_out"] / pos.tokens_remaining if pos.tokens_remaining else 0.0
            if pos.mark_px > pos.peak_px:
                pos.peak_px = pos.mark_px
            ret = pos.mark_px / pos.entry_px - 1 if pos.entry_px else 0.0
            plan = pos.plan
            reason: str | None = None
            sl, trail, tp = plan.get("sl"), plan.get("trail"), plan.get("tp")
            tps = tp if (tp and isinstance(tp[0], (list, tuple))) else ([tp] if tp else [])
            if sl is not None and ret <= -float(sl):
                reason = "stop"
            elif not pos.tp_done and tps and ret >= float(tps[0][0]):
                self._sell(pos, float(tps[0][1]), clock, block, "tp", rs, None, wb)
                pos.tp_done = True
                pos.peak_px = pos.mark_px
                self.stats["tp_fills"] += 1
                if pos.tokens_remaining <= 0:
                    reason = "tp"
                else:
                    pos.updated_ts = pos.last_mark_emit = clock
                    events.append({"kind": "fill", **pos.to_dict(clock)})
                    if wb is not None:
                        wb.sql.append(self._mark_sql(pos, clock))
                    continue
            elif trail is not None and (pos.tp_done or not tps) and pos.mark_px <= pos.peak_px * (1 - float(trail)):
                reason = "trail"
            if reason is None and clock >= pos.opened_ts + int(plan.get("time_s") or 1800):
                reason = "time"
            if reason is None and plan.get("inflow_dies") and clock - pos.opened_ts >= INFLOW_DIES_AFTER_S:
                inflow = row.get("inflow_10m") if row is not None else 0
                if not inflow:
                    reason = "inflow_dies"
            if reason is None:
                trig = self._exit_reasons(pos, clock, row)
                if trig:
                    reason = "trigger: " + _short_trigger(trig[0])
            if reason is not None:
                if pos.tokens_remaining > 0:
                    self._sell(pos, 1.0, clock, block, reason, rs, None, wb)
                events.append(self._close(pos, clock, block, reason, wb))
                continue
            pos.updated_ts = clock
            if clock - pos.last_mark_emit >= MARK_EVERY_S:
                pos.last_mark_emit = clock
                events.append({"kind": "mark", **pos.to_dict(clock, fills=False)})
                if wb is not None:
                    wb.sql.append(self._mark_sql(pos, clock))
        if ((self.open or events) and clock - self.last_equity_ts >= EQUITY_EVERY_S) or any(e.get("kind") == "closed" for e in events):
            self.last_equity_ts = clock
            unreal = sum((p.unrealized_quote or 0.0) for p in self.open.values())
            realized = self.realized_wei / WEI
            self.equity_curve.append((clock, realized, unreal, realized + unreal))
            if wb is not None:
                wb.sql.append((EQUITY_INSERT, (clock, block, realized, unreal, realized + unreal, len(self.open), len(self.closed), self.realized_usd if self.realized_usd_known == len(self.closed) else None)))
        return events

    # ---- read side ----
    def snapshot(self, clock: int | None = None, closed_limit: int = 200) -> dict[str, Any]:
        open_ = [p.to_dict(clock) for p in sorted(self.open.values(), key=lambda p: p.id)]
        closed_all = [p.to_dict(clock, fills=False) for p in self.closed]
        st = stats(closed_all, open_, skipped=self.stats.get("skipped_by_risk", 0))
        return {
            "positions": {"open": open_, "closed": [p.to_dict(clock) for p in self.closed[-closed_limit:]][::-1], "pending": [{"token": e.token, "symbol": e.symbol, "alert_ts": e.alert_ts, "alert_block": e.alert_block, "size_quote": e.size_wei / WEI} for e in self.pending]},
            "equity": [{"ts": t, "realized_quote": r, "unrealized_quote": u, "equity_quote": q} for t, r, u, q in self.equity_curve],
            "stats": st,
            "config": {"size_cap_quote": self.rcfg.get("cap_per_trade_quote"), "max_concurrent": self.rcfg.get("max_concurrent"), "daily_stop_frac": self.rcfg.get("daily_stop_frac"), "plan": self.plan, "latency": "fill at the first block after the decision block" if not self.latency_s else f"fill at the first block ≥ {self.latency_s} s after the decision", "fx": self.fx is not None},
            "counters": dict(self.stats),
            "clock": clock,
            "simulated": True,
        }

    def summary(self) -> dict[str, Any]:
        return {"open": len(self.open), "closed": len(self.closed), "pending": len(self.pending), "realized_quote": self.realized_wei / WEI, **{f"paper_{k}": v for k, v in self.stats.items()}}


def read_snapshot(store, closed_limit: int = 200, since_ts: int | None = None) -> dict[str, Any]:
    """`/api/paper` without the engine in the process: the same shape from the tables."""
    ensure_tables(store.db)
    cols = [c[1] for c in store.db.execute("PRAGMA table_info(paper_positions)")]
    fills: dict[int, list[dict[str, Any]]] = {}
    for pid, ts, block, side, tokens, quote_wei, fee, tax, snipe, px, reason, venue in store.db.execute("SELECT position_id, ts, block, side, tokens, quote_wei, fee_wei, tax_wei, snipe_wei, px, reason, venue FROM paper_fills ORDER BY id"):
        fills.setdefault(pid, []).append({"ts": ts, "block": block, "side": side, "tokens": tokens, "quote": int(quote_wei or 0) / WEI, "fee": int(fee or 0) / WEI, "tax": int(tax or 0) / WEI, "snipe": int(snipe or 0) / WEI, "px": px, "reason": reason, "venue": venue})
    where = " WHERE opened_ts>=?" if since_ts else ""
    args: tuple = (since_ts,) if since_ts else ()
    open_, closed = [], []
    for row in store.db.execute(f"SELECT * FROM paper_positions{where} ORDER BY id", args):
        p = position_from_row(dict(zip(cols, row)))
        p.fills = fills.get(p.id, [])
        (open_ if p.status == "open" else closed).append(p)
    open_d = [p.to_dict() for p in open_]
    closed_d = [p.to_dict(fills=False) for p in closed]
    eq = [{"ts": r[0], "realized_quote": r[1], "unrealized_quote": r[2], "equity_quote": r[3]} for r in store.db.execute("SELECT ts, realized_quote, unrealized_quote, equity_quote FROM paper_equity" + (" WHERE ts>=?" if since_ts else "") + " ORDER BY ts", args)]
    cfg = verdict_mod.load_config()
    rcfg = risk.load_config()
    return {
        "positions": {"open": open_d, "closed": [p.to_dict() for p in closed[-closed_limit:]][::-1], "pending": []},
        "equity": eq,
        "stats": stats(closed_d, open_d),
        "config": {"size_cap_quote": rcfg.get("cap_per_trade_quote"), "max_concurrent": rcfg.get("max_concurrent"), "daily_stop_frac": rcfg.get("daily_stop_frac"), "plan": cfg.get("exit_plan") or verdict_mod.DEFAULT_EXIT, "latency": "fill at the first block after the decision block", "fx": bool(store.db.execute("SELECT 1 FROM fx_rates LIMIT 1").fetchone())},
        "counters": {},
        "clock": None,
        "simulated": True,
        "source": "tables",
    }


def now_ts() -> int:
    return int(time.time())
