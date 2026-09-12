"""`stampede normalize`: raw logs -> trades, one row per (transaction, token, wallet).

Attribution rule (see docs/ALGORITHM.md): the wallet of a trade is the address whose net balance of the
token changed inside the transaction, computed from the ERC-20 Transfer logs, after removing
infrastructure (curves, pool manager, routers, hooks, the token contract, the zero address) and any
address whose net change is zero (pass-through routers, learned per transaction). A transaction with no
swap event for that token is a transfer, not a trade. Several net recipients that cannot be reduced to
one dominant wallet are recorded as `ambiguous_recipients` and produce no trade.
"""
from __future__ import annotations

import bisect
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable

from . import chain
from .store import Store

SWAP_KINDS = ("curve_buy", "curve_sell", "v4_swap")
DOMINANT_SHARE = 0.9  # a wallet receiving >= 90% of the token delta is the buyer; smaller shares are noted


@dataclass
class Context:
    curves: dict[str, dict]  # curve -> {token, pair_token}
    pool_token: dict[str, dict]  # pool_id -> {token, quote, currency0, currency1}
    universe: set[str]
    infra: set[str]
    stats: Counter = field(default_factory=Counter)
    v4_sign: Counter = field(default_factory=Counter)
    passthrough: Counter = field(default_factory=Counter)


def build_context(store: Store) -> Context:
    curves = store.curves()
    tokens = store.tokens()
    universe = set(tokens)
    pools = store.pools()
    pool_token: dict[str, dict] = {}
    for pid, p in pools.items():
        c0, c1 = p["currency0"], p["currency1"]
        if c1 in universe and c0 not in universe:
            pool_token[pid] = {"token": c1, "quote": c0, "currency0": c0, "currency1": c1}
        elif c0 in universe and c1 not in universe:
            pool_token[pid] = {"token": c0, "quote": c1, "currency0": c0, "currency1": c1}
        elif c0 in universe and c1 in universe:
            pool_token[pid] = {"token": None, "quote": None, "currency0": c0, "currency1": c1, "both_universe": True}
    infra = set(store.infra()) | set(curves) | {chain.NATIVE}
    return Context(curves=curves, pool_token=pool_token, universe=universe, infra=infra)


class Interp:
    """Block -> (timestamp, exact) from anchor blocks; linear in between.

    Outside the anchor range the estimate extrapolates at ~10 blocks/s from the nearest anchor, but only
    up to MAX_EXTRAPOLATE_BLOCKS; beyond that (or with no anchors at all) the time is UNKNOWN and the
    caller must not store a fake value. This is what produced `ts = 0` rows before.
    """

    MAX_EXTRAPOLATE_BLOCKS = 600
    BLOCK_SECONDS = 0.1

    def __init__(self, anchors: dict[int, int]):
        self.nums = sorted(anchors)
        self.ts = [anchors[n] for n in self.nums]

    def __call__(self, block: int) -> tuple[int | None, int]:
        if not self.nums:
            return None, 0
        i = bisect.bisect_left(self.nums, block)
        if i < len(self.nums) and self.nums[i] == block:
            return self.ts[i], 1
        if i == 0:
            d = self.nums[0] - block
            return (round(self.ts[0] - d * self.BLOCK_SECONDS), 0) if d <= self.MAX_EXTRAPOLATE_BLOCKS else (None, 0)
        if i >= len(self.nums):
            d = block - self.nums[-1]
            return (round(self.ts[-1] + d * self.BLOCK_SECONDS), 0) if d <= self.MAX_EXTRAPOLATE_BLOCKS else (None, 0)
        a, b = self.nums[i - 1], self.nums[i]
        ta, tb = self.ts[i - 1], self.ts[i]
        return round(ta + (tb - ta) * (block - a) / (b - a)), 0


@dataclass
class Trade:
    tx_hash: str
    block: int
    token: str
    wallet: str
    side: str
    token_amount: int
    quote_token: str | None
    quote_amount: int
    venue: str
    swap_logs: list[int]
    flags: list[str]
    fee: int = 0  # curve fee (incl. snipe tax) or hook fee, quote units; raw integer
    tax: int = 0  # creator tax, quote units for curve trades
    snipe: int = 0  # the snipe-tax part of `fee` (SnipeTaxCharged in the same transaction)

    def row(self, ts: int | None, exact: int) -> tuple:
        """The `trades` INSERT row (see TRADE_COLUMNS); one place for every writer."""
        return (self.tx_hash, self.block, ts, exact, self.token, self.wallet, self.side, str(self.token_amount), self.quote_token, str(self.quote_amount), self.venue, json.dumps(self.swap_logs), "transfer_net", json.dumps(self.flags), str(self.fee), str(self.tax), str(self.snipe))


TRADE_COLUMNS = "tx_hash,block,ts,ts_exact,token,wallet,side,token_amount,quote_token,quote_amount,venue,swap_logs,attribution,flags,fee_raw,tax_raw,snipe_raw"
TRADE_INSERT = f"INSERT OR IGNORE INTO trades({TRADE_COLUMNS}) VALUES({','.join('?' * len(TRADE_COLUMNS.split(',')))})"


def trades_from_tx(tx_hash: str, block: int, logs: list[dict], ctx: Context) -> tuple[list[Trade], list[tuple[str, str, str]]]:
    """logs: dicts with keys log_index, address, topic0..3, data, kind. Returns (trades, notes)."""
    notes: list[tuple[str, str, str]] = []
    swaps = [l for l in logs if l["kind"] in SWAP_KINDS]
    if not swaps:
        return [], notes
    # money that is not a trade but belongs to one: snipe tax per curve, hook fees per pool (same transaction)
    snipe_by_curve: dict[str, int] = defaultdict(int)
    hook_fee_by_pool: dict[str, tuple[int, int]] = {}
    for l in logs:
        if l["kind"] == "snipe_tax":
            snipe_by_curve[l["address"]] += chain.u256(l["data"], 0)
        elif l["kind"] == "hook_fee" and l.get("topic1"):
            f, t = hook_fee_by_pool.get(l["topic1"], (0, 0))
            hook_fee_by_pool[l["topic1"]] = (f + chain.u256(l["data"], 1), t + chain.u256(l["data"], 2))
    # token -> swap events
    events: dict[str, list[dict]] = defaultdict(list)
    for l in swaps:
        if l["kind"] in ("curve_buy", "curve_sell"):
            c = ctx.curves.get(l["address"])
            if not c:
                notes.append(("", "unresolved_curve", l["address"]))
                continue
            events[c["token"]].append(l | {"_quote": c["pair_token"], "_venue": "curve"})
        else:
            p = ctx.pool_token.get(l["topic1"] or "")
            if not p:
                notes.append(("", "non_universe_pool", l["topic1"] or ""))
                continue
            if p.get("both_universe"):
                notes.append(("", "pool_with_two_universe_tokens", l["topic1"] or ""))
                continue
            events[p["token"]].append(l | {"_quote": p["quote"], "_venue": "v4", "_c0": p["currency0"]})
    if not events:
        return [], notes
    # membership checks against the (possibly very large) infra set; never copy it per transaction
    infra, event_tokens = ctx.infra, set(events)
    out: list[Trade] = []
    for token, evs in events.items():
        delta: dict[str, int] = defaultdict(int)
        gross: dict[str, int] = defaultdict(int)
        n_tr = 0
        for l in logs:
            if l["kind"] != "transfer" or l["address"] != token or not l["topic2"]:
                continue
            v = chain.u256(l["data"], 0)
            a, b = chain.addr_from_topic(l["topic1"]), chain.addr_from_topic(l["topic2"])
            delta[a] -= v
            delta[b] += v
            gross[a] += v
            gross[b] += v
            n_tr += 1
        if n_tr == 0:
            notes.append((token, "swap_without_transfer", ""))
            continue
        passthrough = {a for a, d in delta.items() if d == 0 and gross[a] > 0 and a not in infra and a not in event_tokens}
        for a in passthrough:
            ctx.passthrough[a] += 1
        cand = {a: d for a, d in delta.items() if d != 0 and a not in infra and a not in event_tokens and a not in passthrough}
        pos = {a: d for a, d in cand.items() if d > 0}
        neg = {a: -d for a, d in cand.items() if d < 0}
        # quote amounts and venue from the events
        quote_amt = 0
        quote_tok = evs[0]["_quote"]
        venues = sorted({e["_venue"] for e in evs})
        ev_token_sign = 0
        fee_amt = tax_amt = snipe_amt = 0
        hook_pools_seen: set[str] = set()
        for e in evs:
            if e["kind"] == "curve_buy":
                # CurveBuy(buyer, recipient, quoteIn, tokensOut, fee, tax): fee already includes the snipe tax
                quote_amt += chain.u256(e["data"], 0)
                fee_amt += chain.u256(e["data"], 2)
                tax_amt += chain.u256(e["data"], 3)
                snipe_amt += snipe_by_curve.pop(e["address"], 0)
                ev_token_sign += 1
            elif e["kind"] == "curve_sell":
                # CurveSell(seller, recipient, tokensIn, quoteOut, fee, tax)
                quote_amt += chain.u256(e["data"], 1)
                fee_amt += chain.u256(e["data"], 2)
                tax_amt += chain.u256(e["data"], 3)
                ev_token_sign -= 1
            else:
                a0, a1 = chain.i128(e["data"], 0), chain.i128(e["data"], 1)
                tok_amt, q_amt = (a0, a1) if token == e["_c0"] else (a1, a0)
                quote_amt += abs(q_amt)
                ev_token_sign += 1 if tok_amt > 0 else -1
                pid = e.get("topic1") or ""
                if pid in hook_fee_by_pool and pid not in hook_pools_seen:
                    hf, ht = hook_fee_by_pool[pid]
                    fee_amt += hf
                    tax_amt += ht
                    hook_pools_seen.add(pid)
        flags: list[str] = []
        if hook_pools_seen:
            flags.append("hook_fee_in_unspecified_currency")  # the hook takes its cut in the output currency, not always the quote
        if len(venues) > 1:
            flags.append("multi_venue")
        if len(evs) > 1:
            flags.append("multi_event")

        def pick(side_map: dict[str, int], side: str) -> str | None:
            if not side_map:
                return None
            if len(side_map) == 1:
                return next(iter(side_map))
            total = sum(side_map.values())
            best, amt = max(side_map.items(), key=lambda kv: kv[1])
            if amt / total >= DOMINANT_SHARE:
                flags.append(f"minor_{side}_recipients:{len(side_map) - 1}")
                return best
            notes.append((token, f"ambiguous_{'recipients' if side == 'buy' else 'senders'}", json.dumps({a: str(d) for a, d in sorted(side_map.items(), key=lambda kv: -kv[1])[:6]})))
            return None

        buyer = pick(pos, "buy")
        seller = pick(neg, "sell")
        if buyer is None and seller is None:
            if not pos and not neg:
                notes.append((token, "no_net_wallet_change", "all transfers netted out or only infrastructure moved"))
            continue
        venue = "+".join(venues)
        if buyer and seller:
            flags.append("two_sided_tx")
        for wallet, side, amt in ((buyer, "buy", pos.get(buyer, 0)), (seller, "sell", neg.get(seller, 0))):
            if not wallet:
                continue
            if any(e["kind"] == "v4_swap" for e in evs):
                ctx.v4_sign[f"{side}_event_sign_{'pos' if ev_token_sign > 0 else 'neg' if ev_token_sign < 0 else 'zero'}"] += 1
            q = quote_amt if not (buyer and seller) else 0
            # fees belong to the side the event describes; on a two-sided tx they are attributed to both rows unsplit (flagged)
            out.append(Trade(tx_hash, block, token, wallet, side, amt, quote_tok, q, venue, [l["log_index"] for l in evs], list(flags), fee_amt, tax_amt, snipe_amt))
    return out, notes


def iter_tx_groups(store: Store) -> Iterable[tuple[str, int, list[dict]]]:
    cur = store.db.execute("SELECT tx_hash, log_index, block, address, topic0, topic1, topic2, topic3, data, kind FROM logs ORDER BY block, tx_hash, log_index")
    cur_tx: str | None = None
    cur_block = 0
    buf: list[dict] = []
    for tx_hash, li, block, addr, t0, t1, t2, t3, data, kind in cur:
        if tx_hash != cur_tx and buf:
            yield cur_tx, cur_block, buf
            buf = []
        cur_tx, cur_block = tx_hash, block
        buf.append({"log_index": li, "address": addr, "topic0": t0, "topic1": t1, "topic2": t2, "topic3": t3, "data": data, "kind": kind})
    if buf:
        yield cur_tx, cur_block, buf


def normalize(store: Store, reset: bool = False, rpc=None) -> dict[str, Any]:
    if reset:
        store.db.execute("DELETE FROM trades")
        store.db.execute("DELETE FROM tx_notes")
        store.db.execute("DELETE FROM wallets")
        store.commit()
    ctx = build_context(store)
    interp = Interp({n: ts for n, (ts, ex) in store.blocks().items() if ex == 1})
    stats: Counter = Counter()
    notes_c: Counter = Counter()
    rows: list[tuple] = []
    note_rows: list[tuple] = []
    direct_pairs = 0
    for tx_hash, block, logs in iter_tx_groups(store):
        stats["txs"] += 1
        trades, notes = trades_from_tx(tx_hash, block, logs, ctx)
        for tk, n, d in notes:
            notes_c[n] += 1
            note_rows.append((tx_hash, tk, n, d))
        if not trades:
            continue
        stats["txs_with_trades"] += 1
        ts, exact = interp(block)
        if ts is None:
            stats["trades_unknown_time"] += len(trades)
        by_wallet: dict[str, set[str]] = defaultdict(set)
        for t in trades:
            stats[f"trades_{t.side}"] += 1
            by_wallet[t.wallet].add(t.side)
            rows.append(t.row(ts, exact))
        direct_pairs += sum(1 for s in by_wallet.values() if s == {"buy", "sell"})
        if len(rows) >= 5000:
            store.db.executemany(TRADE_INSERT, rows)
            store.db.executemany("INSERT OR IGNORE INTO tx_notes(tx_hash,token,note,detail) VALUES(?,?,?,?)", note_rows)
            rows, note_rows = [], []
    store.db.executemany(TRADE_INSERT, rows)
    store.db.executemany("INSERT OR IGNORE INTO tx_notes(tx_hash,token,note,detail) VALUES(?,?,?,?)", note_rows)
    store.commit()
    # learned pass-through addresses: remembered for transparency (they are excluded per transaction anyway)
    learned = [(a, "passthrough_learned", f"net-zero in {n} txs") for a, n in ctx.passthrough.items() if n >= 3]
    store.upsert_infra(learned)
    # wallets: count trades and mark contracts for the most active ones
    store.db.execute("INSERT OR REPLACE INTO wallets(address,is_contract,trades) SELECT wallet, NULL, COUNT(*) FROM trades GROUP BY wallet")
    store.commit()
    contract_checked = 0
    if rpc is not None:
        top = [r[0] for r in store.db.execute("SELECT address FROM wallets WHERE is_contract IS NULL ORDER BY trades DESC LIMIT 400")]
        try:
            codes = rpc.get_code_batch(top)
            store.db.executemany("UPDATE wallets SET is_contract=? WHERE address=?", [(1 if v else 0, a) for a, v in codes.items()])
            store.commit()
            contract_checked = len(codes)
        except Exception as e:  # noqa: BLE001
            stats["contract_check_error"] = 1
            print(f"contract check skipped: {e}", file=sys.stderr)
    q = store.db.execute
    total = q("SELECT COUNT(*) FROM trades").fetchone()[0]
    exact = q("SELECT COUNT(*) FROM trades WHERE ts_exact=1").fetchone()[0]
    result = {
        "txs_seen": stats["txs"],
        "txs_with_trades": stats["txs_with_trades"],
        "trades": total,
        "trades_buy": stats["trades_buy"],
        "trades_sell": stats["trades_sell"],
        "txs_with_direct_sell_and_buy_same_wallet": direct_pairs,
        "wallets": q("SELECT COUNT(*) FROM wallets").fetchone()[0],
        "tokens_with_trades": q("SELECT COUNT(DISTINCT token) FROM trades").fetchone()[0],
        "trades_by_venue": dict(q("SELECT venue, COUNT(*) FROM trades GROUP BY venue").fetchall()),
        "ts_exact_share": round(exact / total, 3) if total else None,
        "notes": dict(notes_c),
        "v4_sign_convention": dict(ctx.v4_sign),
        "passthrough_addresses_learned": len(learned),
        "contract_wallets_checked": contract_checked,
        "contract_wallets": q("SELECT COUNT(*) FROM wallets WHERE is_contract=1").fetchone()[0],
        "trades_by_contract_wallets": q("SELECT COALESCE(SUM(t.n),0) FROM (SELECT wallet, COUNT(*) n FROM trades GROUP BY wallet) t JOIN wallets w ON w.address=t.wallet WHERE w.is_contract=1").fetchone()[0],
    }
    return result


def repair_unknown_ts(store: Store, rpc, limit: int = 20000) -> dict[str, Any]:
    """Trades with ts NULL/0: fetch exact headers for their blocks (every 5th block as anchors + interpolate)."""
    blocks = [r[0] for r in store.db.execute("SELECT DISTINCT block FROM trades WHERE ts IS NULL OR ts=0 ORDER BY block LIMIT ?", (limit,))]
    if not blocks:
        return {"trades_fixed": 0, "blocks": 0}
    lo, hi = blocks[0], blocks[-1]
    wanted = sorted(set(range(lo, hi + 1, 5)) | {lo, hi} | set(blocks[: min(len(blocks), 200)]))
    have = {n for n, (ts, ex) in store.blocks().items() if ex == 1}
    todo = [n for n in wanted if n not in have]
    got = rpc.get_blocks(todo) if todo else {}
    store.upsert_blocks((n, int(b["timestamp"], 16), 1) for n, b in got.items())
    store.commit()
    interp = Interp({n: ts for n, (ts, ex) in store.blocks().items() if ex == 1})
    fixed = 0
    for b in blocks:
        ts, exact = interp(b)
        if ts is None:
            continue
        cur = store.db.execute("UPDATE trades SET ts=?, ts_exact=? WHERE block=? AND (ts IS NULL OR ts=0)", (ts, exact, b))
        fixed += cur.rowcount
    store.commit()
    return {"trades_fixed": fixed, "blocks": len(blocks), "headers_fetched": len(got), "still_unknown": store.db.execute("SELECT COUNT(*) FROM trades WHERE ts IS NULL OR ts=0").fetchone()[0]}


def main_repair_ts(args) -> int:
    from .rpc import Rpc

    store = Store()
    rpc = Rpc()
    if not rpc.alchemy_url:
        print("repair-ts needs ALCHEMY_KEY", file=sys.stderr)
        return 2
    res = repair_unknown_ts(store, rpc, args.limit)
    print(json.dumps(res, indent=1))
    return 0


def main_normalize(args) -> int:
    from .rpc import Rpc

    store = Store()
    run_id = store.start_run("normalize", {"reset": bool(args.reset)})
    rpc = None
    try:
        rpc = Rpc()
        if not rpc.alchemy_url:
            rpc = None
    except Exception:  # noqa: BLE001
        rpc = None
    res = normalize(store, reset=args.reset, rpc=rpc)
    store.finish_run(run_id, res)
    print(json.dumps(res, indent=1))
    return 0
