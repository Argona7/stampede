"""In-memory state of the realtime engine: registry, curve reserves, per-wallet rings, rolling per-coin windows.

One block goes through `apply_block` in one call: lifecycle events update the registry (curve -> token/pair,
pool -> token) and the reserves; swap transactions are normalized with the same `trades_from_tx` as every other
path; new buys are paired with recent sells of the same wallet (`sequences_for_wallet` on the wallet's ring, only
sequences ending in this block's buys are new); the radar rows of the touched coins are recomputed with the same
`radar.score()`; the alert rule is evaluated on the new top of the table; outcomes of earlier alerts are settled
from in-memory prices. The function returns the events to publish, the rows to persist and per-stage timings.

Curve reserves are reconstructed from CurveBuy / CurveSell (validated on 5,202 real trades: 4,903 exact, the rest
two-sided transactions where the trade row has no per-event amounts - the engine works from the events, so those
are exact too): buy `quote += quoteIn - fee - tax`, `tokens -= tokensOut`; sell `quote -= quoteOut + fee + tax`,
`tokens += tokensIn`; spot price `(phantom + quote) / tokens`, progress `quote / threshold`. A curve first seen
mid-life starts `complete=False` until the resolver snapshots `trackedQuote` / `trackedTokens` on chain.
"""
from __future__ import annotations

import json
import time
from collections import Counter, OrderedDict, defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

from .. import chain
from ..api import radar as radar_mod
from ..api.queries import short
from ..normalize import TRADE_COLUMNS, Context, Trade, trades_from_tx
from ..rotation import T, sequences_for_wallet
from ..store import Store
from .feed import SWAP_KINDS, Block

MAIN = ("direct", "clean")
TRADE_INSERT_ID = f"INSERT OR IGNORE INTO trades(id,{TRADE_COLUMNS}) VALUES({','.join('?' * (len(TRADE_COLUMNS.split(',')) + 1))})"
SEQ_INSERT = "INSERT OR IGNORE INTO sequences(window_s,wallet,sell_token,buy_token,sell_trade,buy_trade,sell_ts,buy_ts,gap_s,grade,candidates) VALUES(?,?,?,?,?,?,?,?,?,?,?)"
LOG_INSERT = "INSERT OR IGNORE INTO logs(tx_hash,log_index,block,address,topic0,topic1,topic2,topic3,data,kind,source) VALUES(?,?,?,?,?,?,?,?,?,?,?)"
LAUNCH_INSERT = "INSERT INTO launches(token,curve,deployer,pair_token,threshold,launch_config,block,ts,tx_hash,source) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(token) DO UPDATE SET ts=COALESCE(excluded.ts, launches.ts)"
GRAD_POOL_INSERT = "INSERT INTO graduations(token,stage,block,ts,tx_hash,quote_token,creator) VALUES(?,?,?,?,?,?,?) ON CONFLICT(token) DO UPDATE SET stage='pool', block=excluded.block, ts=excluded.ts, tx_hash=excluded.tx_hash"
GRAD_IGNORE_INSERT = "INSERT OR IGNORE INTO graduations(token,stage,block,ts,tx_hash,quote_token,creator) VALUES(?,?,?,?,?,?,?)"
CURVE_INSERT = "INSERT OR IGNORE INTO curves(curve,token,pair_token,resolved_via) VALUES(?,?,?,?)"
TOKEN_INSERT = "INSERT OR IGNORE INTO tokens(address,symbol,name,source,curve,first_seen_block) VALUES(?,?,?,?,?,?)"
POOL_INSERT = "INSERT OR IGNORE INTO pools(pool_id,currency0,currency1,fee,tick_spacing,hooks,init_block,init_tx) VALUES(?,?,?,?,?,?,?,?)"
INFRA_INSERT = "INSERT OR IGNORE INTO infra(address,kind,source) VALUES(?,?,?)"
ALERT_INSERT = "INSERT INTO alerts(created_ts, clock_ts, mode, token, symbol, rule, score, inflow, mentions_1h, price, detail) VALUES(?,?,?,?,?,?,?,?,?,?,?)"
ALERT_UPDATE = "UPDATE alerts SET outcome_30m=COALESCE(?, outcome_30m), outcome_60m=COALESCE(?, outcome_60m), graduated_after=COALESCE(?, graduated_after), outcome_checked_ts=? WHERE mode='live' AND token=? AND clock_ts=?"
WALLET_UPSERT = "INSERT INTO wallets(address,is_contract,trades) VALUES(?,NULL,?) ON CONFLICT(address) DO UPDATE SET trades=wallets.trades+excluded.trades"
# the alert rule of the context worker (docs/RADAR.md); the same parameters, evaluated per block here
RULES = {"under_radar_top5": {"score_min": 60, "mentions_max": 3, "rank_max": 5, "inflow_min": 8, "inflow_max": 40, "age_max_s": 3600, "chg_10m_max": 100}}


@dataclass
class CurveReserve:
    token: str
    pair_token: str
    quote: int = 0  # real quote reserve (raw units), fees excluded (`trackedQuote` on chain)
    tokens: int = chain.CURVE_SUPPLY_WEI  # tokens still on the curve (`trackedTokens`)
    phantom: int | None = chain.CURVE_PHANTOM_QUOTE_WEI  # virtual quote; None for non-ETH launch configs until snapshotted
    threshold: int = chain.CURVE_GRADUATION_THRESHOLD_WEI
    complete: bool = True  # False when the engine did not see the curve from its first trade (snapshot pending)
    synced_block: int | None = None  # events at or before this block are already inside a chain snapshot
    buys: int = 0
    sells: int = 0
    last_ts: int = 0
    graduated: bool = False

    def apply_buy(self, quote_in: int, tokens_out: int, fee: int, tax: int, block: int = 0, ts: int = 0) -> None:
        if self.synced_block is not None and block <= self.synced_block:
            return
        self.quote += quote_in - fee - tax
        self.tokens -= tokens_out
        self.buys += 1
        self.last_ts = ts

    def apply_sell(self, tokens_in: int, quote_out: int, fee: int, tax: int, block: int = 0, ts: int = 0) -> None:
        if self.synced_block is not None and block <= self.synced_block:
            return
        self.quote -= quote_out + fee + tax
        self.tokens += tokens_in
        self.sells += 1
        self.last_ts = ts

    def tokens_out(self, net_quote: int) -> int:
        """Constant product on the virtual reserves: tokens a buy of `net_quote` (after fee and tax) gets."""
        if self.phantom is None:
            return 0
        d = self.phantom + self.quote + net_quote
        return net_quote * self.tokens // d if d > 0 else 0

    def quote_out(self, tokens_in: int) -> int:
        if self.phantom is None:
            return 0
        d = self.tokens + tokens_in
        return tokens_in * (self.phantom + self.quote) // d if d > 0 else 0

    def price(self) -> float | None:
        """Spot price in raw quote units per raw token unit, None until the reserve is trustworthy."""
        if not self.complete or self.phantom is None or self.tokens <= 0:
            return None
        return (self.phantom + self.quote) / self.tokens

    def progress(self) -> float | None:
        if self.graduated:
            return 1.0
        if not self.complete or not self.threshold:
            return None
        return max(0.0, min(1.0, self.quote / self.threshold))

    @property
    def needs_snapshot(self) -> bool:
        return (not self.complete or self.phantom is None) and not self.graduated

    def snapshot(self, quote: int, tokens: int, block: int, phantom: int | None = None, threshold: int | None = None) -> None:
        """Chain state (`trackedQuote`, `trackedTokens`, `phantomQuote`, `graduationThreshold`) as of `block`."""
        self.quote, self.tokens, self.synced_block, self.complete = quote, tokens, block, True
        if phantom is not None:
            self.phantom = phantom
        if threshold:
            self.threshold = threshold


@dataclass
class WriteBatch:
    trades: list[tuple] = field(default_factory=list)
    sequences: list[tuple] = field(default_factory=list)
    blocks: list[tuple] = field(default_factory=list)
    logs: list[tuple] = field(default_factory=list)
    launches: list[tuple] = field(default_factory=list)
    graduations_pool: list[tuple] = field(default_factory=list)
    graduations_ignore: list[tuple] = field(default_factory=list)
    curves: list[tuple] = field(default_factory=list)
    tokens: list[tuple] = field(default_factory=list)
    pools: list[tuple] = field(default_factory=list)
    infra: list[tuple] = field(default_factory=list)
    alerts: list[tuple] = field(default_factory=list)
    alert_updates: list[tuple] = field(default_factory=list)
    token_meta: list[tuple] = field(default_factory=list)  # (symbol, name, address)
    wallets: Counter = field(default_factory=Counter)
    meta: dict[str, Any] = field(default_factory=dict)
    created: float = field(default_factory=time.time)

    def extend(self, o: "WriteBatch") -> None:
        for k in ("trades", "sequences", "blocks", "logs", "launches", "graduations_pool", "graduations_ignore", "curves", "tokens", "pools", "infra", "alerts", "alert_updates", "token_meta"):
            getattr(self, k).extend(getattr(o, k))
        self.wallets.update(o.wallets)
        self.meta.update(o.meta)
        self.created = min(self.created, o.created)

    def rows(self) -> int:
        return sum(len(getattr(self, k)) for k in ("trades", "sequences", "blocks", "logs", "launches", "graduations_pool", "graduations_ignore", "curves", "tokens", "pools", "infra", "alerts", "alert_updates", "token_meta")) + len(self.wallets)


@dataclass
class PendingAlert:
    token: str
    clock_ts: int
    price: float | None
    symbol: str
    outcome_30m: float | None = None
    outcome_60m: float | None = None
    done_30: bool = False
    done_60: bool = False


class EngineState:
    def __init__(self, store: Store, window_s: int = 1800, span_s: int = 1800, ring_s: int = 7200, rules: dict[str, dict[str, Any]] | None = None, persist_logs: bool = True):
        self.window_s, self.span_s, self.ring_s = window_s, span_s, ring_s
        self.rules = rules or RULES
        self.persist_logs = persist_logs
        self.stats: Counter = Counter()
        self.notes: Counter = Counter()
        self.note_samples: dict[str, deque] = defaultdict(lambda: deque(maxlen=20))
        # ---- registry (seeded from the store) ----
        curves = store.curves()
        tokens = store.tokens()
        universe = set(tokens) | {c["token"] for c in curves.values()}
        pool_token: dict[str, dict] = {}
        for pid, p in store.pools().items():
            self._classify_pool(pool_token, pid, p["currency0"], p["currency1"], universe)
        infra = set(store.infra()) | set(curves) | set(chain.KNOWN_INFRA) | {chain.NATIVE}
        self.ctx = Context(curves=curves, pool_token=pool_token, universe=universe, infra=infra)
        self.labels: dict[str, tuple[str, str]] = {a: (t["symbol"], t.get("name") or "") for a, t in tokens.items() if t.get("symbol")}
        self.symbol_count: Counter = Counter(s for s, _ in self.labels.values())
        self.known_tokens: set[str] = set(tokens)
        self.launches: dict[str, dict[str, Any]] = {}
        for r in store.db.execute("SELECT token, curve, deployer, pair_token, threshold, block, ts FROM launches"):
            self.launches[r[0]] = {"curve": r[1], "deployer": r[2], "pair_token": r[3], "threshold": int(r[4]) if r[4] else None, "block": r[5], "ts": r[6]}
        self.graduations: dict[str, dict[str, Any]] = {}
        for r in store.db.execute("SELECT token, stage, block, ts, tx_hash FROM graduations"):
            self.graduations[r[0]] = {"stage": r[1], "block": r[2], "ts": r[3], "tx_hash": r[4]}
        self.qdec: dict[str, int] = {r[0]: r[1] for r in store.db.execute("SELECT address, decimals FROM quotes")}
        self.qsym: dict[str, str] = {r[0]: r[1] for r in store.db.execute("SELECT address, symbol FROM quotes")}
        self.qdec.setdefault(chain.NATIVE, 18)
        self.qsym.setdefault(chain.NATIVE, "ETH")
        self.bots: set[str] = {r[0] for r in store.db.execute("SELECT wallet FROM wallet_scores WHERE is_bot=1")}
        self.wscores: dict[str, float] = {r[0]: r[1] for r in store.db.execute("SELECT wallet, score FROM wallet_scores WHERE score IS NOT NULL")}
        self.reserves: dict[str, CurveReserve] = {}
        self.mentions: dict[str, dict[str, Any]] = {}
        self.context: dict[str, dict[str, Any]] = {"market": {}, "holders": {}}
        # ---- rolling state ----
        r = store.db.execute("SELECT MAX(id) FROM trades").fetchone()
        self.next_trade_id = int(r[0] or 0) + 1
        self.seen_trades: OrderedDict[tuple[str, str, str], int] = OrderedDict()
        self.applied_curve_events: OrderedDict[tuple[str, int], None] = OrderedDict()
        self.recent_tx_logs: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
        self.wallet_ring: dict[str, deque[T]] = defaultdict(deque)
        self.coin_trades: dict[str, deque[tuple]] = defaultdict(deque)  # (ts, side, token_amount, quote_amount, quote_token, wallet)
        self.coin_seqs: dict[str, deque[tuple[int, str, str]]] = defaultdict(deque)  # (buy_ts, wallet, sell_token), main grades only
        self.first_trade_ts: dict[str, int] = {}
        self.curve_traded: set[str] = set()
        self.cs_cache: dict[str, tuple[int, int, dict[str, Any]]] = {}
        self.rows: dict[str, dict[str, Any]] = {}
        self.ranked: list[dict[str, Any]] = []
        self.block_ts: OrderedDict[int, int] = OrderedDict()
        self.clock: int = 0
        self.last_tick: int = 0
        self.last_sweep: float = 0.0
        self.unresolved_curves: set[str] = set()
        self.tokens_needing_meta: set[str] = set()
        self.alert_recent: dict[str, int] = {}
        self.alert_pending: list[PendingAlert] = []
        self.load_alerts(store)
        self.stats["curves_seeded"] = len(curves)
        self.stats["pools_seeded"] = len(pool_token)
        self.stats["launches_seeded"] = len(self.launches)

    # ---- registry helpers ----
    @staticmethod
    def _classify_pool(pool_token: dict[str, dict], pid: str, c0: str, c1: str, universe: set[str]) -> None:
        if c1 in universe and c0 not in universe:
            pool_token[pid] = {"token": c1, "quote": c0, "currency0": c0, "currency1": c1}
        elif c0 in universe and c1 not in universe:
            pool_token[pid] = {"token": c0, "quote": c1, "currency0": c0, "currency1": c1}
        elif c0 in universe and c1 in universe:
            pool_token[pid] = {"token": None, "quote": None, "currency0": c0, "currency1": c1, "both_universe": True}

    def is_pons_pool(self, pool_id: str) -> bool:
        return pool_id in self.ctx.pool_token

    def add_curve(self, curve: str, token: str, pair_token: str, via: str = "engine") -> tuple:
        self.ctx.curves[curve] = {"token": token, "pair_token": pair_token}
        self.ctx.universe.add(token)
        self.ctx.infra.add(curve)
        self.unresolved_curves.discard(curve)
        return (curve, token, pair_token, via)

    def set_label(self, token: str, symbol: str, name: str) -> None:
        old = self.labels.get(token)
        if old:
            self.symbol_count[old[0]] -= 1
        self.labels[token] = (symbol, name)
        self.symbol_count[symbol] += 1
        self.tokens_needing_meta.discard(token)

    def label(self, token: str) -> dict[str, Any]:
        sym, name = self.labels.get(token, ("?", ""))
        return {"address": token, "symbol": f"{sym}·{token[-4:]}" if self.symbol_count.get(sym, 0) > 1 else sym, "symbol_raw": sym, "name": name, "short": short(token), "source": "engine", "ambiguous_symbol": self.symbol_count.get(sym, 0) > 1}

    def load_alerts(self, store: Store) -> None:
        now = int(time.time())
        for tok, cts in store.db.execute("SELECT token, MAX(clock_ts) FROM alerts WHERE mode='live' AND clock_ts>? GROUP BY token", (now - 1800,)):
            self.alert_recent[tok] = cts
        known = {(p.token, p.clock_ts) for p in self.alert_pending}
        for tok, cts, price, sym, o30, o60 in store.db.execute("SELECT token, clock_ts, price, symbol, outcome_30m, outcome_60m FROM alerts WHERE mode='live' AND (outcome_30m IS NULL OR outcome_60m IS NULL) AND clock_ts>?", (now - 3 * 3600,)):
            if (tok, cts) in known:
                continue
            self.alert_pending.append(PendingAlert(tok, cts, price, sym or "?", o30, o60, o30 is not None, o60 is not None))

    def reserve_for(self, curve: str, block: int, complete: bool) -> CurveReserve | None:
        rs = self.reserves.get(curve)
        if rs is not None:
            return rs
        c = self.ctx.curves.get(curve)
        if not c:
            return None
        la = self.launches.get(c["token"])
        thr = (la or {}).get("threshold") or chain.CURVE_GRADUATION_THRESHOLD_WEI
        rs = self.reserves[curve] = CurveReserve(c["token"], c["pair_token"], threshold=thr, complete=complete)
        if not complete:
            self.stats["reserves_incomplete"] += 1
        return rs

    # ---- the block ----
    def apply_block(self, block: Block) -> tuple[list[tuple[str, dict[str, Any]]], WriteBatch, dict[str, float]]:
        t_start = time.time()
        wb = WriteBatch()
        events: list[tuple[str, dict[str, Any]]] = []
        ts = block.ts if block.ts is not None else self.block_ts.get(block.number)
        ts_estimated = False
        if ts is None:
            ts, ts_estimated = (self.clock or int(t_start)), True
        if not block.is_fragment:
            self.clock = max(self.clock, ts)
            self.block_ts[block.number] = ts
            while len(self.block_ts) > 20000:
                self.block_ts.popitem(last=False)
            if not ts_estimated:
                wb.blocks.append((block.number, ts, 1))
        clock = self.clock
        by_tx: dict[str, list[dict[str, Any]]] = {}
        for l in block.logs:
            by_tx.setdefault(l["tx_hash"], []).append(l)
        if block.is_fragment:
            for tx, logs in by_tx.items():
                prev = self.recent_tx_logs.get(tx)
                if prev:
                    seen = {l["log_index"] for l in logs}
                    logs.extend(l for l in prev if l["log_index"] not in seen)
                    logs.sort(key=lambda l: l["log_index"])
        # 1. lifecycle + reserves
        n_launch = n_grad = 0
        for tx, logs in by_tx.items():
            for l in logs:
                k = l["kind"]
                if k == "token_launched":
                    n_launch += self._on_launch(l, block.number, ts, tx, wb)
                elif k == "pool_registered":
                    n_grad += self._on_pool(l, block.number, ts, tx, wb)
                elif k == "curve_completed":
                    self._on_completed(l, block.number, ts, tx, wb)
                elif k == "curve_buy" or k == "curve_sell":
                    self._on_curve_event(l, block.number, ts)
        # 2. trades
        new_trades: list[tuple[int, Trade]] = []
        n_swap_tx = 0
        for tx, logs in by_tx.items():
            has_swap = False
            for l in logs:
                if l["kind"] in SWAP_KINDS:
                    has_swap = True
                    break
            if not has_swap:
                continue
            n_swap_tx += 1
            if len(self.recent_tx_logs) >= 4000:
                self.recent_tx_logs.popitem(last=False)
            self.recent_tx_logs[tx] = logs
            trades, notes = trades_from_tx(tx, block.number, logs, self.ctx)
            for _tok, note, detail in notes:
                self.notes[note] += 1
                if note == "unresolved_curve" and detail:
                    self.unresolved_curves.add(detail)
                elif note != "non_universe_pool":
                    self.note_samples[note].append((block.number, tx, block.source, sorted(block.flags)))
            for t in trades:
                key = (t.tx_hash, t.token, t.wallet)
                if key in self.seen_trades:
                    self.stats["dup_trades"] += 1
                    continue
                tid = self.next_trade_id
                self.next_trade_id += 1
                self.seen_trades[key] = tid
                new_trades.append((tid, t))
            if self.persist_logs and trades:
                wb.logs.extend((l["tx_hash"], l["log_index"], block.number, l["address"], l["topic0"], l["topic1"], l["topic2"], l["topic3"], l["data"], l["kind"], "wss") for l in logs)
        while len(self.seen_trades) > 300000:
            self.seen_trades.popitem(last=False)
        t_trades = time.time()
        # 3. rings and coin windows
        new_buys: dict[str, set[int]] = defaultdict(set)
        touched: set[str] = set()
        for tid, t in new_trades:
            wb.trades.append((tid, *t.row(ts, 0 if ts_estimated else 1)))
            wb.wallets[t.wallet] += 1
            ring = self.wallet_ring[t.wallet]
            ring.append(T(tid, t.tx_hash, t.block, ts, t.token, t.side))
            if t.token_amount > 0 and t.quote_amount > 0:
                self.coin_trades[t.token].append((ts, t.side, float(t.token_amount), float(t.quote_amount), t.quote_token, t.wallet))
            self.first_trade_ts.setdefault(t.token, ts)
            if "curve" in t.venue:
                self.curve_traded.add(t.token)
            touched.add(t.token)
            if t.token not in self.known_tokens:
                self.known_tokens.add(t.token)
                wb.tokens.append((t.token, None, None, "trade", self.launches.get(t.token, {}).get("curve"), block.number))
            if t.token not in self.labels:
                self.tokens_needing_meta.add(t.token)
            if t.side == "buy":
                new_buys[t.wallet].add(tid)
        # 4. sequences for the new buys
        new_seqs: list[tuple[str, dict[str, Any]]] = []
        lo_ring = ts - self.ring_s
        for w, ids in new_buys.items():
            ring = self.wallet_ring[w]
            while ring and ring[0].ts < lo_ring:
                ring.popleft()
            lo = ts - self.window_s - 5
            trades_w = [x for x in ring if x.ts >= lo]
            if not any(x.side == "sell" for x in trades_w):
                continue
            for s in sequences_for_wallet(trades_w, self.window_s):
                if s["buy_trade"] not in ids:
                    continue
                new_seqs.append((w, s))
                wb.sequences.append((self.window_s, w, s["sell_token"], s["buy_token"], s["sell_trade"], s["buy_trade"], s["sell_ts"], s["buy_ts"], s["gap_s"], s["grade"], s["candidates"]))
                if s["grade"] in MAIN:
                    self.coin_seqs[s["buy_token"]].append((s["buy_ts"], w, s["sell_token"]))
                    touched.add(s["buy_token"])
        t_seq = time.time()
        # 5. radar rows (touched coins, plus everything every 5 s of chain time)
        removed: list[str] = []
        tick = clock - self.last_tick >= 5 and not block.is_fragment
        if tick:
            self.last_tick = clock
            removed = self._expire(clock)
            touched |= set(self.rows)
        changed: list[dict[str, Any]] = []
        for tok in touched:
            dq = self.coin_seqs.get(tok)
            if not dq:
                continue
            row = self._row(tok, clock, block.number)
            old = self.rows.get(tok)
            self.rows[tok] = row
            if old is None or not tick or (old["score"], old["inflow_10m"], old["price_quote"], old["progress"]) != (row["score"], row["inflow_10m"], row["price_quote"], row["progress"]):
                changed.append(row)
        self.ranked = sorted((r for r in self.rows.values() if r["wallets_range"] >= 2), key=lambda r: -r["score"])
        rank_of = {r["address"]: i for i, r in enumerate(self.ranked, 1)}
        for r in self.rows.values():
            r["rank"] = rank_of.get(r["address"])
        t_radar = time.time()
        # 6. alerts
        fired, outcomes = self._alerts(clock, block.number, wb)
        # 7. events
        lat = {
            "head_to_logs": round((block.t_logs_complete - block.t_head_received) * 1000, 1) if block.t_head_received else None,
            "logs_to_trades": round((t_trades - block.t_logs_complete) * 1000, 1),
            "trades_to_radar": round((t_radar - t_trades) * 1000, 1),
            "apply": round((time.time() - t_start) * 1000, 1),
        }
        events.append(("block", {
            "number": block.number, "ts": ts, "hash": block.hash, "source": block.source, "flags": sorted(block.flags), "logs": len(block.logs), "swap_txs": n_swap_tx,
            "trades": len(new_trades), "sequences": len(new_seqs), "launches": n_launch, "graduations": n_grad, "deferred_txs": block.deferred_txs,
            "t_head_received": block.t_head_received, "t_logs_complete": block.t_logs_complete, "latency_ms": lat, "ts_estimated": ts_estimated,
        }))
        for tid, t in new_trades:
            events.append(("trade", self._trade_event(tid, t, ts)))
        for w, s in new_seqs:
            events.append(("sequence", {"wallet": w, "window_s": self.window_s, "block": block.number, "sell_symbol": self.label(s["sell_token"])["symbol"], "buy_symbol": self.label(s["buy_token"])["symbol"], **s}))
        if changed or removed:
            events.append(("radar_delta", {"full": False, "reason": "tick" if tick else "block", "clock": clock, "window_s": self.window_s, "span_s": self.span_s, "rows": sorted(changed, key=lambda r: -r["score"]), "removed": removed, "top": [r["address"] for r in self.ranked[:50]]}))
        events.extend(("alert", a) for a in fired + outcomes)
        if not block.is_fragment:
            wb.meta["engine_cursor"] = block.number
        self.stats["blocks"] += 1
        self.stats["trades"] += len(new_trades)
        self.stats["sequences"] += len(new_seqs)
        if time.time() - self.last_sweep > 30:
            self._sweep(time.time())
        timing = {"t_start": t_start, "t_trades": t_trades, "t_seq": t_seq, "t_radar": t_radar, "t_end": time.time()}
        return events, wb, timing

    # ---- lifecycle handlers ----
    def _on_launch(self, l: dict[str, Any], block: int, ts: int | None, tx: str, wb: WriteBatch, live: bool = True) -> int:
        """`live=False` for the catch-up of launches that happened before the engine started: registry only (the curve may
        already have traded, so its reserve starts from a chain snapshot when it first trades here)."""
        if not (l.get("topic1") and l.get("topic2")):
            return 0
        token, curve, deployer = chain.addr_from_topic(l["topic1"]), chain.addr_from_topic(l["topic2"]), chain.addr_from_topic(l["topic3"]) if l.get("topic3") else None
        data = l["data"]
        pair = chain.addr_from_word(data, 0)
        cfg, thr = chain.u256(data, 1), chain.u256(data, 2)
        wb.curves.append(self.add_curve(curve, token, pair, "token_launched"))
        wb.infra.append((curve, "pons_curve", "engine"))
        if token not in self.known_tokens:
            self.known_tokens.add(token)
            wb.tokens.append((token, None, None, "launch", curve, block))
        if live:
            self.tokens_needing_meta.add(token)
        if token not in self.launches:
            self.stats["launches"] += 1
        self.launches[token] = {"curve": curve, "deployer": deployer, "pair_token": pair, "threshold": thr or None, "block": block, "ts": ts}
        wb.launches.append((token, curve, deployer, pair, str(thr), cfg, block, ts, tx, "engine"))
        if live and curve not in self.reserves:  # a backfilled fragment may replay the launch: never reset a live reserve
            phantom = chain.CURVE_PHANTOM_QUOTE_WEI if (pair == chain.NATIVE and cfg == 0) else None
            self.reserves[curve] = CurveReserve(token, pair, phantom=phantom, threshold=thr or chain.CURVE_GRADUATION_THRESHOLD_WEI, complete=True)
        if pair not in self.qdec and pair != chain.NATIVE:
            self.qdec[pair] = 18
        return 1

    def _on_pool(self, l: dict[str, Any], block: int, ts: int, tx: str, wb: WriteBatch) -> int:
        data = l["data"]
        if not l.get("topic1") or len(data) < 2 + 64 * 3:
            return 0
        pid = l["topic1"]
        token, quote, creator = chain.addr_from_word(data, 0), chain.addr_from_word(data, 1), chain.addr_from_word(data, 2)
        c0, c1 = sorted([token, quote])
        self.ctx.universe.add(token)
        self.ctx.pool_token[pid] = {"token": token, "quote": quote, "currency0": c0, "currency1": c1}
        wb.pools.append((pid, c0, c1, 0, 200, chain.PONS_V2_HOOK, block, tx))
        self.graduations[token] = {"stage": "pool", "block": block, "ts": ts, "tx_hash": tx}
        wb.graduations_pool.append((token, "pool", block, ts, tx, quote, creator))
        la = self.launches.get(token)
        if la and la.get("curve") in self.reserves:
            self.reserves[la["curve"]].graduated = True
        if token not in self.known_tokens:
            self.known_tokens.add(token)
            wb.tokens.append((token, None, None, "v4_pool", (la or {}).get("curve"), block))
        self.stats["graduations"] += 1
        return 1

    def _on_completed(self, l: dict[str, Any], block: int, ts: int, tx: str, wb: WriteBatch) -> None:
        c = self.ctx.curves.get(l["address"])
        if not c:
            return
        token = c["token"]
        if token not in self.graduations:
            self.graduations[token] = {"stage": "curve_completed", "block": block, "ts": ts, "tx_hash": tx}
        wb.graduations_ignore.append((token, "curve_completed", block, ts, tx, None, None))
        rs = self.reserves.get(l["address"])
        if rs:
            rs.graduated = True

    def _on_curve_event(self, l: dict[str, Any], block: int, ts: int) -> None:
        curve = l["address"]
        if curve not in self.ctx.curves:
            self.unresolved_curves.add(curve)
            return
        key = (l["tx_hash"], l["log_index"])
        if key in self.applied_curve_events:  # a backfilled fragment can replay a block: apply every event once
            return
        self.applied_curve_events[key] = None
        while len(self.applied_curve_events) > 100000:
            self.applied_curve_events.popitem(last=False)
        rs = self.reserve_for(curve, block, complete=False)
        if rs is None:
            return
        d = l["data"]
        if l["kind"] == "curve_buy":
            rs.apply_buy(chain.u256(d, 0), chain.u256(d, 1), chain.u256(d, 2), chain.u256(d, 3), block, ts)
        else:
            rs.apply_sell(chain.u256(d, 0), chain.u256(d, 1), chain.u256(d, 2), chain.u256(d, 3), block, ts)

    # ---- radar ----
    def _stats(self, tok: str, clock: int) -> dict[str, Any]:
        dq = self.coin_trades.get(tok)
        if not dq:
            return radar_mod.stats_from_rows([], clock, self.qdec)
        lo = clock - 3600
        while dq and dq[0][0] <= lo:
            dq.popleft()
        key = (len(dq), dq[-1][0] if dq else 0, clock // 60)
        hit = self.cs_cache.get(tok)
        if hit and hit[0] == key[0] and hit[1] == key[1] and hit[2].get("_minute") == key[2]:
            return hit[2]
        rows = [r for r in dq if r[0] <= clock]
        cs = radar_mod.stats_from_rows(rows, clock, self.qdec)
        cs["_minute"] = key[2]
        self.cs_cache[tok] = (key[0], key[1], cs)
        return cs

    def price_now(self, tok: str) -> float | None:
        """Median price of the last 5 trades (the same estimator as context.market.curve_stats)."""
        dq = self.coin_trades.get(tok)
        if not dq:
            return None
        last = [dq[i] for i in range(max(0, len(dq) - 5), len(dq))]
        dec = self.qdec.get(last[-1][4] or "", 18)
        scale = 10 ** (18 - dec)
        ps = sorted((r[3] / r[2]) * scale for r in last)
        return ps[len(ps) // 2]

    def _row(self, tok: str, clock: int, block: int) -> dict[str, Any]:
        lo, t10 = clock - self.span_s, clock - 600
        dq = self.coin_seqs[tok]
        w10: set[str] = set()
        wprev: set[str] = set()
        wall: set[str] = set()
        src10: dict[str, set[str]] = {}
        seq10 = seq = 0
        first = last = None
        minute: dict[int, int] = {}
        for bts, w, src in dq:
            if bts <= lo or bts > clock or w in self.bots:
                continue
            wall.add(w)
            seq += 1
            first = bts if first is None else min(first, bts)
            last = bts if last is None else max(last, bts)
            m = (bts - lo) // 60
            minute[m] = minute.get(m, 0) + 1
            if bts > t10:
                w10.add(w)
                seq10 += 1
                src10.setdefault(src, set()).add(w)
            else:
                wprev.add(w)
        inflow10 = len(w10)
        prev_rate = len(wprev) / max(1.0, (t10 - lo) / 600)
        accel = inflow10 / prev_rate if prev_rate > 0 else (float(inflow10) if inflow10 else 0.0)
        breadth = len(src10)
        qs = [self.wscores[w] for w in w10 if w in self.wscores]
        quality = (sum(qs) / len(qs)) if qs else None
        la = self.launches.get(tok)
        age_s = (clock - la["ts"]) if la and la.get("ts") else None
        grad = self.graduations.get(tok)
        graduated = bool(grad and (grad.get("ts") is None or grad["ts"] <= clock))
        rs = self.reserves.get(la["curve"]) if la and la.get("curve") else None
        progress = 1.0 if graduated else (rs.progress() if rs else None)
        stage = "graduated" if graduated else ("curve" if (rs or tok in self.curve_traded or la) else "unknown")
        mentions = self.mentions.get(tok)
        m1h = mentions.get("mentions_1h") if mentions else None
        cs = self._stats(tok, clock)
        sc = radar_mod.score(inflow10, accel, breadth, quality, m1h, age_s, stage, cs.get("chg_10m"))
        row = {
            **self.label(tok),
            "inflow_10m": inflow10,
            "inflow_prev_per_10m": round(prev_rate, 2),
            "accel": round(accel, 2),
            "breadth": breadth,
            "wallets_range": len(wall),
            "sequences_range": seq,
            "sequences_10m": seq10,
            "sources": sorted(({"address": s, "symbol": self.label(s)["symbol"], "wallets": len(ws)} for s, ws in src10.items()), key=lambda x: -x["wallets"])[:3],
            "quality": round(quality, 3) if quality is not None else None,
            "age_s": age_s,
            "stage": stage,
            "progress": round(progress, 4) if progress is not None else None,
            "graduated_ts": grad.get("ts") if graduated and grad else None,
            "first_inflow_ts": first,
            "last_inflow_ts": last,
            "spark": [minute.get(i, 0) for i in range(self.span_s // 60)],
            "mentions_1h": m1h,
            "mentions_24h": mentions.get("mentions_24h") if mentions else None,
            **sc,
            "price_quote": cs["price_quote"],
            "chg_10m": cs.get("chg_10m"),
            "quote_symbol": self.qsym.get(cs["quote"] or "", None),
            "chg_5m": cs["chg_5m"],
            "chg_1h": cs["chg_1h"],
            "vol_1h_quote": cs["vol_1h_quote"],
            "buyers_1h": cs["buyers_1h"],
            "price_spot": rs.price() if rs else None,
            "reserve_quote": str(rs.quote) if rs and rs.complete else None,
            "block": block,
            "updated_ts": clock,
        }
        mk = self.context["market"].get(tok)
        if mk and mk.get("found"):
            row["market_now"] = {k: mk.get(k) for k in ("price_usd", "fdv_usd", "reserve_usd", "vol_h1", "chg_h1", "url", "fetched_at")}
        hd = self.context["holders"].get(tok)
        if hd and "holders" in hd:
            row["holders"] = {k: hd.get(k) for k in ("holders", "top10_share", "dev_share", "dev_sold_share", "launch_block_buyers", "as_of_block")}
        return row

    def _expire(self, clock: int) -> list[str]:
        lo = clock - self.span_s
        removed = []
        for tok in list(self.coin_seqs):
            dq = self.coin_seqs[tok]
            while dq and dq[0][0] <= lo:
                dq.popleft()
            if not dq:
                del self.coin_seqs[tok]
                if tok in self.rows:
                    del self.rows[tok]
                    removed.append(tok)
        return removed

    def _sweep(self, now: float) -> None:
        """Bound the memory of idle wallets and coins (the hot paths evict lazily)."""
        self.last_sweep = now
        lo_ring = self.clock - self.ring_s
        for w in [w for w, dq in self.wallet_ring.items() if not dq or dq[-1].ts < lo_ring]:
            del self.wallet_ring[w]
        lo_tr = self.clock - 3600
        for tok in [t for t, dq in self.coin_trades.items() if not dq or dq[-1][0] <= lo_tr]:
            del self.coin_trades[tok]
            self.cs_cache.pop(tok, None)
        self.stats["wallets_tracked"] = len(self.wallet_ring)
        self.stats["coins_tracked"] = len(self.coin_trades)

    # ---- alerts ----
    def _alerts(self, clock: int, block: int, wb: WriteBatch) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        rule = self.rules["under_radar_top5"]
        fired: list[dict[str, Any]] = []
        for rank, r in enumerate(self.ranked[: rule["rank_max"]], 1):
            tok = r["address"]
            m1h = r.get("mentions_1h")
            if r["score"] < rule["score_min"] or r["inflow_10m"] < rule["inflow_min"] or r["inflow_10m"] > rule["inflow_max"]:
                continue
            if r.get("age_s") is None or r["age_s"] > rule["age_max_s"]:
                continue
            if r.get("chg_10m") is not None and r["chg_10m"] >= rule["chg_10m_max"]:
                continue
            if m1h is not None and m1h > rule["mentions_max"]:
                continue
            last = self.alert_recent.get(tok)
            if last is not None and last > clock - 1800:
                continue
            self.alert_recent[tok] = clock
            detail = {"rank": rank, "sources": r["sources"], "accel": r["accel"], "breadth": r["breadth"], "age_s": r["age_s"], "stage": r["stage"], "mentions_known": m1h is not None, "engine": "wss", "block": block, "progress": r.get("progress")}
            created = int(time.time())
            wb.alerts.append((created, clock, "live", tok, r["symbol"], "under_radar_top5", r["score"], r["inflow_10m"], m1h, r.get("price_quote"), json.dumps(detail)))
            self.alert_pending.append(PendingAlert(tok, clock, r.get("price_quote"), r["symbol"]))
            fired.append({"kind": "fired", "key": f"{tok}:{clock}", "created_ts": created, "clock_ts": clock, "mode": "live", "token": tok, "symbol": r["symbol"], "rule": "under_radar_top5", "score": r["score"], "inflow": r["inflow_10m"], "mentions_1h": m1h, "price": r.get("price_quote"), "detail": detail})
            self.stats["alerts_fired"] += 1
        outcomes: list[dict[str, Any]] = []
        keep: list[PendingAlert] = []
        for p in self.alert_pending:
            upd: dict[str, Any] = {}
            if not p.done_30 and clock >= p.clock_ts + 1800:
                price = self.price_now(p.token)
                p.outcome_30m = ((price / p.price - 1) * 100) if price and p.price else None
                p.done_30 = True
                upd["outcome_30m"] = p.outcome_30m
            if not p.done_60 and clock >= p.clock_ts + 3600:
                price = self.price_now(p.token)
                p.outcome_60m = ((price / p.price - 1) * 100) if price and p.price else None
                p.done_60 = True
                g = self.graduations.get(p.token)
                upd["outcome_60m"] = p.outcome_60m
                upd["graduated_after"] = 1 if (g and g.get("ts") and p.clock_ts <= g["ts"] <= p.clock_ts + 3600) else 0
            if upd:
                wb.alert_updates.append((upd.get("outcome_30m"), upd.get("outcome_60m"), upd.get("graduated_after"), int(time.time()), p.token, p.clock_ts))
                outcomes.append({"kind": "outcome", "key": f"{p.token}:{p.clock_ts}", "token": p.token, "symbol": p.symbol, "clock_ts": p.clock_ts, "price": p.price, **upd})
                self.stats["alert_outcomes"] += 1
            if not (p.done_30 and p.done_60):
                keep.append(p)
        self.alert_pending = keep
        return fired, outcomes

    # ---- events ----
    def _trade_event(self, tid: int, t: Trade, ts: int) -> dict[str, Any]:
        dec = self.qdec.get(t.quote_token or "", 18)
        price = (t.quote_amount / t.token_amount) * 10 ** (18 - dec) if t.token_amount and t.quote_amount else None
        return {
            "id": tid, "tx": t.tx_hash, "block": t.block, "ts": ts, "token": t.token, "symbol": self.label(t.token)["symbol"], "wallet": t.wallet, "side": t.side,
            "token_amount": str(t.token_amount), "quote_amount": str(t.quote_amount), "quote_token": t.quote_token, "quote_symbol": self.qsym.get(t.quote_token or "", None),
            "venue": t.venue, "fee": str(t.fee), "tax": str(t.tax), "snipe": str(t.snipe), "flags": t.flags, "price_quote": price, "bot": t.wallet in self.bots,
        }

    def summary(self) -> dict[str, Any]:
        return {
            **dict(self.stats),
            "curves": len(self.ctx.curves), "pools": len(self.ctx.pool_token), "launches": len(self.launches), "reserves": len(self.reserves),
            "wallets_tracked": len(self.wallet_ring), "coins_tracked": len(self.coin_trades), "radar_rows": len(self.rows), "ranked": len(self.ranked),
            "alerts_pending": len(self.alert_pending), "unresolved_curves": len(self.unresolved_curves), "tokens_needing_meta": len(self.tokens_needing_meta),
            "notes": dict(self.notes), "note_samples": {k: list(v)[-5:] for k, v in list(self.note_samples.items())}, "clock": self.clock, "next_trade_id": self.next_trade_id,
        }
