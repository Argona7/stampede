"""`stampede ingest`: fetch swap-class logs and the ERC-20 transfers of their transactions for a block window.

Path used (recorded in the run stats): HyperSync when a token is configured, otherwise the public RPC in
adaptive chunks with Alchemy 10-block fallback. Afterwards the PONS v2 registry is resolved for every
curve and v4 pool seen in the window, and exact block timestamps are fetched for anchor blocks.
"""
from __future__ import annotations

import sys
import time
from typing import Any

from eth_hash.auto import keccak

from . import chain
from .chain import selector
from .env import redact
from .hypersync import HyperSync
from .rpc import Rpc, RpcError
from .store import Store

ANCHOR_EVERY = 50


def short_addr(a: str) -> str:
    return f"{a[:6]}…{a[-4:]}"


def log_row(l: dict, source: str) -> tuple:
    tp = l["topics"]
    return (
        l["transactionHash"],
        int(l["logIndex"], 16),
        int(l["blockNumber"], 16),
        l["address"].lower(),
        tp[0],
        tp[1] if len(tp) > 1 else None,
        tp[2] if len(tp) > 2 else None,
        tp[3] if len(tp) > 3 else None,
        l["data"],
        chain.KIND_BY_TOPIC.get(tp[0], "other"),
        source,
    )


def hs_log_row(l: dict, source: str = "hypersync") -> tuple:
    return (
        l["transaction_hash"],
        int(l["log_index"]),
        int(l["block_number"]),
        l["address"].lower(),
        l.get("topic0"),
        l.get("topic1"),
        l.get("topic2"),
        l.get("topic3"),
        l.get("data"),
        chain.KIND_BY_TOPIC.get(l.get("topic0"), "other"),
        source,
    )


def find_block_by_time(rpc: Rpc, target_ts: int, lo: int, hi: int) -> int:
    """Smallest block with timestamp >= target_ts (binary search on headers, Alchemy preferred)."""
    while lo < hi:
        mid = (lo + hi) // 2
        b = rpc.get_block(mid)
        ts = int(b["timestamp"], 16)
        if ts < target_ts:
            lo = mid + 1
        else:
            hi = mid
    return lo


def pool_id(currency0: str, currency1: str, fee: int, tick_spacing: int, hooks: str) -> str:
    """keccak(abi.encode(PoolKey)) - same as Uniswap v4 PoolId.toId()."""
    def w_addr(a: str) -> bytes:
        return bytes.fromhex(a[2:].rjust(64, "0"))

    def w_uint(v: int) -> bytes:
        return (v % (1 << 256)).to_bytes(32, "big")

    return "0x" + keccak(w_addr(currency0) + w_addr(currency1) + w_uint(fee) + w_uint(tick_spacing) + w_addr(hooks)).hex()


def pons_pool_id(token: str, quote: str) -> str:
    a, b = quote.lower(), token.lower()
    c0, c1 = (a, b) if int(a, 16) < int(b, 16) else (b, a)
    return pool_id(c0, c1, 0, 200, chain.PONS_V2_HOOK)


class Ingest:
    def __init__(self, store: Store, rpc: Rpc, hs: HyperSync | None = None, chunk: int = 300, workers: int = 4):
        self.store = store
        self.rpc = rpc
        self.hs = hs if (hs and hs.enabled) else None
        self.chunk = chunk
        self.workers = workers
        self.stats: dict[str, Any] = {
            "path": "hypersync" if self.hs else ("alchemy_10_block_parallel" if rpc.alchemy_url else "public_rpc"),
            "chunks": 0,
            "chunk_splits": 0,
            "chunk_failures": 0,
            "logs_swap_kept": 0,
            "logs_swap_other_v4_forks_dropped": 0,
            "logs_transfer_kept": 0,
            "logs_lifecycle_kept": 0,
            "logs_side_kept": 0,
            "logs_transfer_seen": 0,
            "swap_txs": 0,
            "blocks_missing": [],
        }

    # ---- log fetching ----
    def fetch_range(self, fr: int, to: int) -> None:
        """Fetch [fr, to] inclusive, splitting on provider limits; records unrecoverable gaps."""
        if fr > to:
            return
        try:
            if self.hs:
                self._fetch_hypersync(fr, to)
            else:
                self._fetch_rpc(fr, to)
            self.stats["chunks"] += 1
        except RpcError as e:
            if to - fr >= 20:
                self.stats["chunk_splits"] += 1
                mid = (fr + to) // 2
                self.fetch_range(fr, mid)
                self.fetch_range(mid + 1, to)
            else:
                self.stats["chunk_failures"] += 1
                self.stats["blocks_missing"].append([fr, to, redact(str(e))[:120]])
        except Exception as e:  # noqa: BLE001 - transport exhausted; try smaller, then give up honestly
            if to - fr >= 20:
                self.stats["chunk_splits"] += 1
                time.sleep(3)
                mid = (fr + to) // 2
                self.fetch_range(fr, mid)
                self.fetch_range(mid + 1, to)
            else:
                self.stats["chunk_failures"] += 1
                self.stats["blocks_missing"].append([fr, to, redact(str(e))[:120]])

    def _keep_swaps(self, logs: list[dict], is_hs: bool = False) -> list[dict]:
        kept = []
        for l in logs:
            t0 = l.get("topic0") if is_hs else l["topics"][0]
            addr = l["address"].lower()
            if t0 == chain.T_V4_SWAP and addr != chain.V4_POOL_MANAGER:
                self.stats["logs_swap_other_v4_forks_dropped"] += 1
                continue
            kept.append(l)
        return kept

    def _fetch_rpc(self, fr: int, to: int) -> None:
        if self.rpc.alchemy_url:
            # Alchemy: swaps + transfers in ONE topic filter, 10-block sub-ranges, 4 workers (~4 calls/s = free-tier CU cap)
            topics = [chain.SWAP_TOPICS + [chain.T_TRANSFER] + chain.LIFECYCLE_TOPICS + chain.CURVE_SIDE_TOPICS]
            logs, failed = self.rpc.get_logs_parallel(fr, to, topics=topics, workers=self.workers)
            for f in failed:
                # retry each failed sub-range once, sequentially
                try:
                    logs.extend(self.rpc.get_logs(f[0], f[1], topics=topics))
                except Exception as e:  # noqa: BLE001
                    self.stats["chunk_failures"] += 1
                    self.stats["blocks_missing"].append([f[0], f[1], redact(str(e))[:120]])
            # CurveCompleted and SnipeTaxExempted are emitted by the curves themselves (address unknown in advance)
            lifecycle = [l for l in logs if (l["topics"][0] in (chain.T_TOKEN_LAUNCHED, chain.T_POOL_REGISTERED, chain.T_LAUNCH_SWEPT, chain.T_POOL_GRADUATED) and l["address"].lower() in (chain.PONS_V2_FACTORY, chain.PONS_V2_HOOK)) or l["topics"][0] in (chain.T_CURVE_COMPLETED, chain.T_SNIPE_TAX_EXEMPTED)]
            swaps = self._keep_swaps([l for l in logs if l["topics"][0] in chain.SWAP_TOPICS])
            transfers = [l for l in logs if l["topics"][0] == chain.T_TRANSFER]
            side = [l for l in logs if l["topics"][0] in chain.CURVE_SIDE_TOPICS]
            src = "alchemy"
        else:
            swaps = self._keep_swaps(self.rpc.get_logs(fr, to, topics=[chain.SWAP_TOPICS]))
            transfers = self.rpc.get_logs(fr, to, topics=[[chain.T_TRANSFER]])
            lifecycle = self.rpc.get_logs(fr, to, topics=[chain.LIFECYCLE_TOPICS])
            side = self.rpc.get_logs(fr, to, topics=[chain.CURVE_SIDE_TOPICS])
            src = "public_rpc"
        # launch / graduation lifecycle events: kept regardless of swaps (a launch tx has no swap unless launchAndBuy)
        self.store.insert_logs(log_row(l, src) for l in lifecycle)
        self.stats["logs_lifecycle_kept"] += len(lifecycle)
        txs = {l["transactionHash"] for l in swaps}
        self.stats["logs_transfer_seen"] += len(transfers)
        keep_tr = [l for l in transfers if l["transactionHash"] in txs]
        keep_side = [l for l in side if l["transactionHash"] in txs]  # snipe tax / hook fee rows of the same trades
        self.store.insert_logs(log_row(l, src) for l in swaps)
        self.store.insert_logs(log_row(l, src) for l in keep_tr)
        self.store.insert_logs(log_row(l, src) for l in keep_side)
        self.stats["logs_side_kept"] += len(keep_side)
        self.store.commit()
        self.stats["logs_swap_kept"] += len(swaps)
        self.stats["logs_transfer_kept"] += len(keep_tr)
        self.stats["swap_txs"] += len(txs)

    def _fetch_hypersync(self, fr: int, to: int) -> None:
        assert self.hs
        logs, txs, blocks = self.hs.logs(fr, to, chain.SWAP_TOPICS)
        swaps = self._keep_swaps(logs, is_hs=True)
        tx_set = {l["transaction_hash"] for l in swaps}
        tlogs, _, tblocks = self.hs.logs(fr, to, [chain.T_TRANSFER] + chain.CURVE_SIDE_TOPICS + [chain.T_SNIPE_TAX_EXEMPTED], join_tx=False)
        # declared snipe-tax exemptions live in the launch tx, which has no swap unless launchAndBuy: keep them all
        exempt = [l for l in tlogs if l.get("topic0") == chain.T_SNIPE_TAX_EXEMPTED]
        tlogs = [l for l in tlogs if l.get("topic0") != chain.T_SNIPE_TAX_EXEMPTED]
        self.stats["logs_transfer_seen"] += len(tlogs)
        keep_tr = [l for l in tlogs if l["transaction_hash"] in tx_set]
        self.store.insert_logs(hs_log_row(l) for l in swaps)
        self.store.insert_logs(hs_log_row(l) for l in keep_tr)
        self.store.insert_logs(hs_log_row(l) for l in exempt)
        self.stats["logs_lifecycle_kept"] += len(exempt)
        self.store.insert_txs((t["hash"], int(t["block_number"]), (t.get("from") or "").lower(), (t.get("to") or "").lower(), "hypersync") for t in txs if t.get("hash") in tx_set)
        self.store.upsert_blocks((int(b["number"]), int(b["timestamp"], 16) if isinstance(b["timestamp"], str) else int(b["timestamp"]), 1) for b in blocks + tblocks)
        self.store.commit()
        self.stats["logs_swap_kept"] += len(swaps)
        self.stats["logs_transfer_kept"] += len(keep_tr)
        self.stats["swap_txs"] += len(tx_set)

    def run_window(self, fr: int, to: int) -> None:
        t0 = time.time()
        total = to - fr + 1
        b = fr
        n = 0
        while b <= to:
            e = min(b + self.chunk - 1, to)
            self.fetch_range(b, e)
            b = e + 1
            n += 1
            if n % 10 == 0 or b > to:
                done = min(b, to + 1) - fr
                el = time.time() - t0
                eta = el / done * (total - done) if done else 0
                print(f"  blocks {done}/{total}  swap logs {self.stats['logs_swap_kept']}  transfers {self.stats['logs_transfer_kept']}  elapsed {el:.0f}s  eta {eta:.0f}s", file=sys.stderr, flush=True)

    # ---- registry ----
    def resolve_curves(self, fr: int | None = None, to: int | None = None) -> dict[str, Any]:
        known = self.store.curves()
        rng = " AND block BETWEEN ? AND ?" if fr is not None else ""
        args = (fr, to) if fr is not None else ()
        seen = [r[0] for r in self.store.db.execute(f"SELECT DISTINCT address FROM logs WHERE kind IN ('curve_buy','curve_sell'){rng}", args)]
        todo = [c for c in seen if c not in known]
        out = {"curves_seen": len(seen), "curves_resolved_now": 0, "curves_failed": 0}
        if todo:
            both = self.rpc.eth_call_batch([(c, chain.S_TOKEN) for c in todo] + [(c, chain.S_PAIR_TOKEN) for c in todo], size=20)
            toks, pairs = both[: len(todo)], both[len(todo) :]
            rows, trows, infra = [], [], []
            for c, t, p in zip(todo, toks, pairs):
                if not t or len(t) < 66:
                    out["curves_failed"] += 1
                    continue
                token = chain.addr_from_word(t, 0)
                pair = chain.addr_from_word(p, 0) if p and len(p) >= 66 else chain.NATIVE
                rows.append((c, token, pair, "eth_call token()/pairToken()"))
                trows.append((token, None, None, "curve", c, None))
                infra.append((c, "pons_curve", "logs"))
            self.store.upsert_curves(rows)
            self.store.upsert_tokens(trows)
            self.store.upsert_infra(infra)
            self.store.commit()
            out["curves_resolved_now"] = len(rows)
        return out

    def resolve_pools(self, fr: int | None = None, to: int | None = None) -> dict[str, Any]:
        """Resolve v4 poolIds seen in Swap logs to PONS v2 tokens.

        Proof by reconstruction: the Transfer logs of the pool's swap transactions name candidate ERC-20s; a
        candidate that the PONS v2 factory knows (getLaunchedToken) and whose computed PoolKey
        (quote, token, fee 0, tickSpacing 200, V2MemeHook) hashes to the observed poolId is accepted.
        Pools that fail this test stay unresolved and are reported as not covered.
        """
        pools = self.store.pools()
        rng = " AND block BETWEEN ? AND ?" if fr is not None else ""
        args = (fr, to) if fr is not None else ()
        ids = [r[0] for r in self.store.db.execute(f"SELECT DISTINCT topic1 FROM logs WHERE kind='v4_swap'{rng}", args)]
        todo = [i for i in ids if i not in pools]
        out = {"pools_seen": len(ids), "pools_resolved_now": 0, "pools_unresolved": 0, "candidate_tokens_checked": 0}
        if not todo:
            return out
        # candidate tokens per pool from transfers touching the PoolManager in the same txs
        cand: dict[str, set[str]] = {i: set() for i in todo}
        q = f"""
            SELECT s.topic1, t.address FROM logs s JOIN logs t ON t.tx_hash = s.tx_hash AND t.kind='transfer'
            WHERE s.kind='v4_swap'{rng.replace('block', 's.block')} AND (t.topic1 LIKE ? OR t.topic2 LIKE ?)
        """
        pm_topic = "%" + chain.V4_POOL_MANAGER[2:]
        for pid, tok in self.store.db.execute(q, tuple(args) + (pm_topic, pm_topic)):
            if pid in cand:
                cand[pid].add(tok)
        tokens = sorted({t for s in cand.values() for t in s})
        out["candidate_tokens_checked"] = len(tokens)
        known_tokens = self.store.tokens()
        rejected = {r[0] for r in self.store.db.execute("SELECT address FROM rejected_tokens")}
        launched: dict[str, dict] = {}
        need = [t for t in tokens if (t not in known_tokens or not known_tokens[t].get("curve")) and t not in rejected]
        res = self.rpc.eth_call_batch([(chain.PONS_V2_FACTORY, chain.S_GET_LAUNCHED_TOKEN + t[2:].rjust(64, "0")) for t in need])
        newly_rejected = []
        for t, r in zip(need, res):
            if r and len(r) >= 2 + 64 * 5:
                curve = chain.addr_from_word(r, 1)
                if int(curve, 16) != 0:
                    launched[t] = {"curve": curve, "pair_token": chain.addr_from_word(r, 4)}
                else:
                    newly_rejected.append((t, time.time()))
        # negative cache: tokens the factory does not know are never re-checked (PONIE, PONS, stocks, other launchpads)
        self.store.db.executemany("INSERT OR IGNORE INTO rejected_tokens(address, checked_at) VALUES(?,?)", newly_rejected)
        out["tokens_rejected_now"] = len(newly_rejected)
        for t, k in known_tokens.items():
            if k.get("curve"):
                cv = self.store.db.execute("SELECT pair_token FROM curves WHERE curve=?", (k["curve"],)).fetchone()
                launched[t] = {"curve": k["curve"], "pair_token": cv[0] if cv else chain.NATIVE}
        rows, trows, crows = [], [], []
        for pid, toks in cand.items():
            hit = None
            for t in toks:
                if t not in launched:
                    continue
                quotes = {chain.NATIVE, launched[t]["pair_token"], chain.WETH}
                for qt in quotes:
                    if pons_pool_id(t, qt) == pid:
                        hit = (t, qt)
                        break
                if hit:
                    break
            if hit:
                t, qt = hit
                c0, c1 = (qt, t) if int(qt, 16) < int(t, 16) else (t, qt)
                rows.append((pid, c0, c1, 0, 200, chain.PONS_V2_HOOK, None, None))
                trows.append((t, None, None, "v4_pool", launched[t]["curve"], None))
                crows.append((launched[t]["curve"], t, launched[t]["pair_token"], "factory getLaunchedToken()"))
            else:
                out["pools_unresolved"] += 1
        self.store.upsert_pools(rows)
        self.store.upsert_tokens(trows)
        if crows:
            self.store.db.executemany("INSERT OR IGNORE INTO curves(curve,token,pair_token,resolved_via) VALUES(?,?,?,?)", crows)
            self.store.upsert_infra((c, "pons_curve", "factory") for c, _, _, _ in crows)
        self.store.commit()
        out["pools_resolved_now"] = len(rows)
        return out

    def resolve_token_meta(self) -> int:
        todo = [r[0] for r in self.store.db.execute("SELECT address FROM tokens WHERE symbol IS NULL")]
        if not todo:
            return 0
        syms = self.rpc.eth_call_batch([(t, chain.S_SYMBOL) for t in todo])
        names = self.rpc.eth_call_batch([(t, chain.S_NAME) for t in todo])
        n_ok = 0
        for t, s, n in zip(todo, syms, names):
            if s is None:
                continue  # left NULL; the next run retries
            self.store.update_token_meta(t, chain.dec_string(s)[:32] or "?", chain.dec_string(n or "")[:64] or "")
            n_ok += 1
        self.store.commit()
        return n_ok

    def resolve_quotes(self) -> int:
        """Symbol + decimals for every quote currency (curve pair tokens and v4 pool quote sides)."""
        have = set(self.store.quotes())
        cands = {r[0] for r in self.store.db.execute("SELECT DISTINCT pair_token FROM curves")}
        universe = set(self.store.tokens())
        for c0, c1 in self.store.db.execute("SELECT currency0, currency1 FROM pools"):
            for c in (c0, c1):
                if c not in universe:
                    cands.add(c)
        todo = sorted(c for c in cands if c not in have and c != chain.NATIVE)
        if not todo:
            return 0
        syms = self.rpc.eth_call_batch([(t, chain.S_SYMBOL) for t in todo])
        decs = self.rpc.eth_call_batch([(t, selector("decimals()")) for t in todo])
        rows = []
        for t, s, d in zip(todo, syms, decs):
            if s is None or d is None:
                continue
            rows.append((t, chain.dec_string(s)[:32] or short_addr(t), chain.u256(d, 0) if len(d) >= 66 else 18))
        self.store.db.executemany("INSERT OR REPLACE INTO quotes(address,symbol,decimals) VALUES(?,?,?)", rows)
        self.store.db.execute("INSERT OR REPLACE INTO quotes(address,symbol,decimals) VALUES(?,?,?)", (chain.NATIVE, "ETH", 18))
        self.store.commit()
        return len(rows)

    def anchor_blocks(self, fr: int, to: int) -> dict[str, Any]:
        have = self.store.blocks()
        wanted = sorted(set(range(fr, to + 1, ANCHOR_EVERY)) | {fr, to})
        todo = [n for n in wanted if have.get(n, (None, 0))[1] != 1]
        got = self.rpc.get_blocks(todo) if todo else {}
        self.store.upsert_blocks((n, int(b["timestamp"], 16), 1) for n, b in got.items())
        self.store.commit()
        return {"anchors_wanted": len(wanted), "anchors_fetched_now": len(got), "anchor_every_blocks": ANCHOR_EVERY}

    def seed_infra(self) -> None:
        self.store.upsert_infra((a, k, "chain.KNOWN_INFRA") for a, k in chain.KNOWN_INFRA.items())
        self.store.commit()


def parse_minutes(s: str) -> int:
    s = s.strip().lower()
    mult = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    return int(float(s[:-1]) * mult[s[-1]]) if s[-1] in mult else int(s)


def main_ingest(args) -> int:
    rpc = Rpc()
    store = Store()
    hs = HyperSync()
    head = rpc.block_number(prefer="alchemy" if rpc.alchemy_url else "public")
    if args.minutes:
        to_block = head - 100
        to_ts = int(rpc.get_block(to_block)["timestamp"], 16)
        from_block = find_block_by_time(rpc, to_ts - int(args.minutes * 60), max(1, to_block - int(args.minutes * 60 * 20)), to_block)
    else:
        if args.from_block is None or args.to_block is None:
            print("need --from-block/--to-block or --minutes", file=sys.stderr)
            return 2
        from_block, to_block = args.from_block, args.to_block
    ing = Ingest(store, rpc, hs, chunk=args.chunk)
    params = {"from_block": from_block, "to_block": to_block, "chunk": args.chunk, "label": args.label, "head_at_start": head}
    run_id = store.start_run("ingest", params)
    print(f"ingest blocks {from_block}..{to_block} ({to_block - from_block + 1} blocks) via {ing.stats['path']}", file=sys.stderr)
    ing.seed_infra()
    ing.run_window(from_block, to_block)
    print("resolving registry…", file=sys.stderr)
    ing.stats["anchors"] = ing.anchor_blocks(from_block, to_block)
    ing.stats["curves"] = ing.resolve_curves()
    ing.stats["pools"] = ing.resolve_pools()
    ing.stats["token_meta_resolved"] = ing.resolve_token_meta()
    ing.stats["quotes_resolved"] = ing.resolve_quotes()
    ing.stats["rpc"] = rpc.stats.summary()
    ing.stats["hypersync_calls"] = hs.calls
    # keep sample bounds for reports
    lo, hi = store.get_meta("sample_from_block", from_block), store.get_meta("sample_to_block", to_block)
    store.set_meta("sample_from_block", min(lo, from_block))
    store.set_meta("sample_to_block", max(hi, to_block))
    if args.label:
        store.set_meta("sample_label", args.label)
    store.finish_run(run_id, ing.stats)
    s = ing.stats
    print(f"done: {s['logs_swap_kept']} swap logs, {s['logs_transfer_kept']} transfers in {s['swap_txs']} txs; curves {s['curves']}; pools {s['pools']}; chunks {s['chunks']} (splits {s['chunk_splits']}, failures {s['chunk_failures']})", file=sys.stderr)
    return 0
