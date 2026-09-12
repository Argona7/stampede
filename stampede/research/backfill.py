"""`stampede backfill`: days of PONS v2 history from Envio HyperSync into a research store, without raw swap logs.

Two passes over [from_block, to_block]:

1. lifecycle: TokenLaunched / PoolRegistered / CurveCompleted / LaunchSwept / PoolGraduated / SnipeTaxExempted (a few
   hundred thousand rows). Stored in `logs` (so `sync_lifecycle` fills `launches` / `graduations` exactly as in the live store) and
   parsed into `curves`, `tokens`, `pools` — the normalizer's universe.
2. trades: CurveBuy / CurveSell / SnipeTaxCharged / CurveBuyRefunded (unique topics, every PONS curve), v4 `Swap` of
   the PONS pools (PoolManager address + poolId in topic1) and HookFeeCollected, with `join_mode=JoinAll` so the
   response carries the transactions (from/to), the blocks (exact timestamps) and the sibling logs of every matched
   transaction — the ERC-20 Transfers the attribution needs. Logs are normalized in memory with the same
   `trades_from_tx` as the live path and only `trades` / `txs` / `blocks` are written. Resumable: the cursor is
   `meta.backfill_cursor`.

Needs HYPERSYNC_TOKEN (free) in .env; the token is never printed. Throughput and row counts land in `runs`.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from .. import chain
from ..env import env
from ..normalize import TRADE_INSERT, Context, build_context, trades_from_tx
from ..store import Store

BLOCKS_PER_SECOND = 10.0  # measured ~100 ms blocks; only used to turn --days into a block span before the first query
POOL_ID_BATCH = 400  # poolIds per topic1 filter list (several LogSelections are OR-ed inside one query)
# Curves and graduated pools keep trading long after their launch: the lifecycle pass starts this many days before the
# trades range so those venues are known (smoke test without it: 2,752 curve events unresolved, 0 v4 trades in 10 min).
LIFECYCLE_LOOKBACK_DAYS = 30.0


def _hex_int(v: Any) -> int:
    if v is None:
        return 0
    if isinstance(v, int):
        return v
    s = str(v)
    return int(s, 16) if s.startswith("0x") else int(s)


def _topics(l: Any) -> list[str | None]:
    tp = getattr(l, "topics", None)
    if tp is None:
        tp = [getattr(l, f"topic{i}", None) for i in range(4)]
    out = [(t.lower() if isinstance(t, str) else None) for t in list(tp)]
    while len(out) < 4:
        out.append(None)
    return out[:4]


def log_dict(l: Any) -> dict[str, Any]:
    """HyperSync Log -> the dict shape `trades_from_tx` expects (plus block / tx for grouping)."""
    t0, t1, t2, t3 = _topics(l)
    return {
        "log_index": int(l.log_index or 0),
        "address": (l.address or "").lower(),
        "topic0": t0,
        "topic1": t1,
        "topic2": t2,
        "topic3": t3,
        "data": l.data or "0x",
        "kind": chain.KIND_BY_TOPIC.get(t0 or "", "other"),
        "block": int(l.block_number or 0),
        "tx_hash": (l.transaction_hash or "").lower(),
    }


def group_by_tx(logs: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    by_tx: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for l in logs:
        by_tx[l["tx_hash"]].append(l)
    for lst in by_tx.values():
        lst.sort(key=lambda x: x["log_index"])
    return by_tx


def pool_row(pr: dict[str, Any]) -> tuple:
    """PoolRegistered(poolId indexed, memecoin, quoteToken, creator) -> `pools` row (currencies sorted like Uniswap does)."""
    token, quote = chain.addr_from_word(pr["data"], 0), chain.addr_from_word(pr["data"], 1)
    c0, c1 = sorted([token, quote])
    return (pr["topic1"], c0, c1, 0, 200, chain.PONS_V2_HOOK, pr["block"], pr["tx_hash"])


def seed_infra(store: Store) -> int:
    """`chain.KNOWN_INFRA` into `infra`, as the live ingest does. `build_context` reads it; without these rows the v4
    PoolManager and the hook are attributed as wallets on every v4 trade (39% of the rows of the first 14-day HyperSync
    store, all v4 rows with quote_amount 0 and flagged two_sided_tx). Returns the number of rows added."""
    before = store.db.execute("SELECT COUNT(*) FROM infra").fetchone()[0]
    store.upsert_infra((a, k, "chain.KNOWN_INFRA") for a, k in chain.KNOWN_INFRA.items())
    store.commit()
    return store.db.execute("SELECT COUNT(*) FROM infra").fetchone()[0] - before


def apply_lifecycle(store: Store, logs: list[dict[str, Any]], source: str = "hypersync") -> Counter:
    """Write lifecycle logs and derive curves / tokens / pools from them."""
    c: Counter = Counter()
    store.insert_logs((l["tx_hash"], l["log_index"], l["block"], l["address"], l["topic0"], l["topic1"], l["topic2"], l["topic3"], l["data"], l["kind"], source) for l in logs)
    curves, tokens, pools = [], [], []
    for l in logs:
        if l["kind"] == "token_launched" and l["address"] == chain.PONS_V2_FACTORY and l["topic1"] and l["topic2"]:
            token, curve = chain.addr_from_topic(l["topic1"]), chain.addr_from_topic(l["topic2"])
            pair = chain.addr_from_word(l["data"], 0)
            curves.append((curve, token, pair, "token_launched"))
            tokens.append((token, "", "", "launch", curve, l["block"]))
            c["launches"] += 1
        elif l["kind"] == "pool_registered" and l["address"] == chain.PONS_V2_HOOK and l["topic1"] and len(l["data"]) >= 2 + 64 * 3:
            pools.append(pool_row(l))
            c["pools"] += 1
    store.upsert_curves(curves)
    store.upsert_tokens(tokens)
    store.upsert_pools(pools)
    store.commit()
    return c


RECV_TIMEOUT_S = 120.0  # a stalled HyperSync stream (DNS blip, dropped connection) never raises: recv() would wait forever
MAX_STREAM_RETRIES = 50


async def resilient_stream(client_factory, make_query, fr: int, to: int, hs_module: Any, concurrency: int, progress=print):
    """Yield QueryResponses for [fr, to]; on a stalled or failed stream, re-open it from the last `next_block`."""
    nb = fr
    retries = 0
    client = client_factory()
    while nb <= to:
        try:
            rx = await client.stream(make_query(nb, to), hs_module.StreamConfig(concurrency=concurrency))
            while True:
                res = await asyncio.wait_for(rx.recv(), timeout=RECV_TIMEOUT_S)
                if res is None:
                    return
                nb = int(res.next_block)
                retries = 0
                yield res
            # unreachable
        except (asyncio.TimeoutError, Exception) as e:  # noqa: BLE001 - the Rust client raises plain Exceptions
            retries += 1
            if retries > MAX_STREAM_RETRIES:
                raise
            wait = min(60.0, 2.0 * retries)
            progress(f"stream stalled/failed at block {nb:,} ({type(e).__name__}: {str(e)[:120]}); reopening in {wait:.0f}s (retry {retries})")
            await asyncio.sleep(wait)
            client = client_factory()


class Backfill:
    def __init__(self, store: Store, client: Any, hs_module: Any, progress=print, client_factory=None):
        self.store = store
        self.client = client
        self.client_factory = client_factory or (lambda: client)
        self.hs = hs_module
        self.progress = progress
        self.stats: Counter = Counter()

    # ---- queries -------------------------------------------------------------------------------------------------
    def _fields(self, tx: bool) -> Any:
        hs = self.hs
        sel = hs.FieldSelection(
            log=[hs.LogField.BLOCK_NUMBER, hs.LogField.TRANSACTION_HASH, hs.LogField.LOG_INDEX, hs.LogField.ADDRESS, hs.LogField.DATA, hs.LogField.TOPIC0, hs.LogField.TOPIC1, hs.LogField.TOPIC2, hs.LogField.TOPIC3],
            block=[hs.BlockField.NUMBER, hs.BlockField.TIMESTAMP],
        )
        if tx:
            sel.transaction = [hs.TransactionField.HASH, hs.TransactionField.BLOCK_NUMBER, hs.TransactionField.FROM, hs.TransactionField.TO]
        return sel

    def lifecycle_query(self, fr: int, to: int) -> Any:
        hs = self.hs
        return hs.Query(
            from_block=fr,
            to_block=to + 1,
            logs=[
                hs.LogSelection(address=[chain.PONS_V2_FACTORY, chain.PONS_V2_HOOK], topics=[[chain.T_TOKEN_LAUNCHED, chain.T_POOL_REGISTERED, chain.T_LAUNCH_SWEPT, chain.T_POOL_GRADUATED]]),
                # emitted by the curves themselves: CurveCompleted at graduation, SnipeTaxExempted in the launch tx
                hs.LogSelection(topics=[[chain.T_CURVE_COMPLETED, chain.T_SNIPE_TAX_EXEMPTED]]),
            ],
            field_selection=self._fields(tx=False),
            join_mode=hs.JoinMode.JOIN_NOTHING,
        )

    def trades_query(self, fr: int, to: int, pool_ids: list[str]) -> Any:
        hs = self.hs
        sels = [
            hs.LogSelection(topics=[[chain.T_CURVE_BUY, chain.T_CURVE_SELL, chain.T_SNIPE_TAX, chain.T_CURVE_REFUND]]),
            hs.LogSelection(address=[chain.PONS_V2_HOOK], topics=[[chain.T_HOOK_FEE]]),
        ]
        for i in range(0, len(pool_ids), POOL_ID_BATCH):
            sels.append(hs.LogSelection(address=[chain.V4_POOL_MANAGER], topics=[[chain.T_V4_SWAP], pool_ids[i : i + POOL_ID_BATCH]]))
        return hs.Query(from_block=fr, to_block=to + 1, logs=sels, field_selection=self._fields(tx=True), join_mode=hs.JoinMode.JOIN_ALL)

    # ---- passes ----------------------------------------------------------------------------------------------------
    async def lifecycle(self, fr: int, to: int) -> Counter:
        hs = self.hs
        t0 = time.time()
        total: Counter = Counter()
        async for res in resilient_stream(self.client_factory, lambda a, b: self.lifecycle_query(a, b), fr, to, hs, 4, self.progress):
            logs = [log_dict(l) for l in res.data.logs]
            blocks = [(int(b.number), _hex_int(b.timestamp), 1) for b in res.data.blocks if b.number is not None]
            self.store.upsert_blocks(blocks)
            total += apply_lifecycle(self.store, logs)
            total["logs"] += len(logs)
            self.store.set_meta("backfill_lifecycle_cursor", int(res.next_block))
        self.store.set_meta("backfill_lifecycle_cursor", to + 1)
        total["seconds"] = round(time.time() - t0, 1)
        self.progress(f"lifecycle: {dict(total)}")
        return total

    async def trades(self, fr: int, to: int, ctx: Context | None = None, checkpoint_every: int = 1) -> Counter:
        hs = self.hs
        ctx = ctx or build_context(self.store)
        pool_ids = sorted(ctx.pool_token)
        t0 = time.time()
        total: Counter = Counter()
        n_resp = 0
        wallets: Counter = Counter()
        async for res in resilient_stream(self.client_factory, lambda a, b: self.trades_query(a, b, pool_ids), fr, to, hs, 4, self.progress):
            n_resp += 1
            ts_by_block = {int(b.number): _hex_int(b.timestamp) for b in res.data.blocks if b.number is not None}
            logs = [log_dict(l) for l in res.data.logs]
            total["logs"] += len(logs)
            by_tx = group_by_tx(logs)
            rows: list[tuple] = []
            for tx_hash, lst in by_tx.items():
                if not any(l["kind"] in ("curve_buy", "curve_sell", "v4_swap") for l in lst):
                    total["txs_without_swap"] += 1
                    continue
                block = lst[0]["block"]
                trades, notes = trades_from_tx(tx_hash, block, lst, ctx)
                for _, n, _ in notes:
                    total[f"note_{n}"] += 1
                ts = ts_by_block.get(block)
                if ts is None:
                    total["trades_unknown_time"] += len(trades)
                for t in trades:
                    rows.append(t.row(ts, 1 if ts is not None else 0))
                    wallets[t.wallet] += 1
                    total[f"trades_{t.side}"] += 1
                total["txs_with_trades"] += 1 if trades else 0
            if rows:
                self.store.db.executemany(TRADE_INSERT, rows)
            txs = [((t.hash or "").lower(), int(t.block_number or 0), (t.from_ or "").lower(), (t.to or "").lower(), "hypersync") for t in res.data.transactions if t.hash]
            if txs:
                self.store.insert_txs(txs)
            self.store.upsert_blocks((n, ts, 1) for n, ts in ts_by_block.items())
            total["txs"] += len(by_tx)
            total["trades"] += len(rows)
            if n_resp % checkpoint_every == 0:
                self.store.set_meta("backfill_cursor", int(res.next_block))
                self.store.commit()
            if n_resp % 20 == 0:
                el = time.time() - t0
                done = int(res.next_block) - fr
                rate = done / el if el else 0
                self.progress(f"trades: block {int(res.next_block):,} ({done / max(1, to - fr) * 100:.1f}%), {total['trades']:,} trades, {rate:,.0f} blocks/s, eta {((to - int(res.next_block)) / rate / 60) if rate else 0:.0f} min")
        if wallets:
            self.store.db.executemany("INSERT INTO wallets(address,is_contract,trades) VALUES(?,NULL,?) ON CONFLICT(address) DO UPDATE SET trades=wallets.trades+excluded.trades", list(wallets.items()))
        self.store.set_meta("backfill_cursor", to + 1)
        self.store.commit()
        total["seconds"] = round(time.time() - t0, 1)
        total["responses"] = n_resp
        self.progress(f"trades pass done: {dict(total)}")
        return total


def make_client(hs_module: Any, token: str | None = None) -> Any:
    tok = token if token is not None else env("HYPERSYNC_TOKEN")
    if not tok:
        raise SystemExit("backfill needs HYPERSYNC_TOKEN in .env (free token: https://envio.dev/app/api-tokens)")
    return hs_module.HypersyncClient(hs_module.ClientConfig(url=chain.HYPERSYNC_URL, api_token=tok, http_req_timeout_millis=120_000, max_num_retries=8))


async def run(store: Store, fr: int, to: int, hs_module: Any, resume: bool = True, progress=print) -> dict[str, Any]:
    client = make_client(hs_module)
    bf = Backfill(store, client, hs_module, progress=progress, client_factory=lambda: make_client(hs_module))
    out: dict[str, Any] = {"from_block": fr, "to_block": to, "infra_seeded": seed_infra(store)}
    lc = store.get_meta("backfill_lifecycle_cursor")
    lifecycle_from = max(0, fr - int(LIFECYCLE_LOOKBACK_DAYS * 86400 * BLOCKS_PER_SECOND))
    if not (resume and lc and int(lc) > to):
        out["lifecycle_from_block"] = lifecycle_from
        out["lifecycle"] = dict(await bf.lifecycle(lifecycle_from, to))
        from ..context.pons import sync_lifecycle

        out["sync_lifecycle"] = sync_lifecycle(store)
    else:
        progress("lifecycle pass already complete (resume)")
    cur = store.get_meta("backfill_cursor")
    start = max(fr, int(cur)) if (resume and cur) else fr
    if start <= to:
        out["trades"] = dict(await bf.trades(start, to))
    else:
        progress("trades pass already complete (resume)")
    store.set_meta("sample_from_block", fr)
    store.set_meta("sample_to_block", to)
    if not store.get_meta("sample_label"):
        store.set_meta("sample_label", f"PONS v2 backfill, blocks {fr}-{to}")
    return out


def main_backfill(args) -> int:
    import hypersync as hs

    store = Store(Path(args.db) if args.db else None)
    store.db.execute("PRAGMA synchronous=OFF")  # research store: resumable via the cursor, so fsync per commit buys nothing
    store.db.execute("PRAGMA cache_size=-262144")  # 256 MB page cache
    client = make_client(hs)
    head = asyncio.run(client.get_height())
    to = int(args.to_block) if args.to_block else head - 100
    if args.from_block:
        fr = int(args.from_block)
    else:
        fr = to - int(float(args.days) * 86400 * BLOCKS_PER_SECOND)
    label = args.label or f"PONS v2 backfill, {args.days} days"
    store.set_meta("sample_label", label)
    run_id = store.start_run("backfill", {"from_block": fr, "to_block": to, "days": args.days, "resume": not args.no_resume})
    print(f"backfill {fr:,} → {to:,} ({to - fr:,} blocks, head {head:,}) into {store.path}", flush=True)
    res = asyncio.run(run(store, fr, to, hs, resume=not args.no_resume, progress=lambda m: print(m, flush=True)))
    if args.rotate:
        from ..rotation import rotate

        for w in (300, 1800):
            res[f"rotate_{w}"] = rotate(store, w)
            print(f"rotate {w}: {res[f'rotate_{w}']}", flush=True)
    store.finish_run(run_id, res)
    print(json.dumps(res, indent=1, default=str))
    return 0
