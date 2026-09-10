"""`stampede verify`: cross-check a random subsample of trade transactions against Alchemy receipts.

Three independent questions per transaction:
1. completeness: does the receipt contain exactly the swap/transfer logs we collected via eth_getLogs
   (missing or extra logs would mean the scan lost data)?
2. reproducibility: re-running the attribution on the receipt's own logs yields the same
   (token, wallet, side) trades?
3. tx.from agreement: how often the attributed wallet equals the transaction sender, and what the
   transaction targets (`to`) were - routers, curves, bots - so the "router is not a buyer" rule is visible.
"""
from __future__ import annotations

import json
import random
import sys
from collections import Counter
from typing import Any

from . import chain
from .normalize import build_context, trades_from_tx
from .rpc import Rpc
from .store import Store

LABELS = {
    chain.PONS_ROUTER: "PONS router",
    chain.UNIVERSAL_ROUTER: "Uniswap UniversalRouter",
    chain.PONS_V2_LAUNCH_AND_BUY: "PONS LaunchAndBuy",
    chain.V4_POOL_MANAGER: "v4 PoolManager",
}


def verify(store: Store, rpc: Rpc, n: int = 200, seed: int = 7) -> dict[str, Any]:
    rnd = random.Random(seed)
    txs = [r[0] for r in store.db.execute("SELECT DISTINCT tx_hash FROM trades")]
    sample = rnd.sample(txs, min(n, len(txs)))
    ctx = build_context(store)
    curves = set(store.curves())
    res: Counter = Counter()
    to_dist: Counter = Counter()
    mismatches: list[dict] = []
    for tx in sample:
        rc = rpc.get_receipt(tx)
        t = rpc.get_tx(tx)
        if not rc or not t:
            res["receipt_missing"] += 1
            continue
        res["checked"] += 1
        our = store.db.execute("SELECT log_index, kind FROM logs WHERE tx_hash=?", (tx,)).fetchall()
        our_idx = {li for li, _ in our}
        rc_logs = []
        rc_idx = set()
        for l in rc["logs"]:
            k = chain.KIND_BY_TOPIC.get(l["topics"][0]) if l["topics"] else None
            if k in ("curve_buy", "curve_sell", "v4_swap") or k == "transfer":
                if k == "v4_swap" and l["address"].lower() != chain.V4_POOL_MANAGER:
                    continue
                li = int(l["logIndex"], 16)
                rc_idx.add(li)
                tp = l["topics"]
                rc_logs.append({"log_index": li, "address": l["address"].lower(), "topic0": tp[0], "topic1": tp[1] if len(tp) > 1 else None, "topic2": tp[2] if len(tp) > 2 else None, "topic3": tp[3] if len(tp) > 3 else None, "data": l["data"], "kind": k})
        missing, extra = rc_idx - our_idx, our_idx - rc_idx
        if missing:
            res["txs_with_missing_logs"] += 1
            res["missing_logs"] += len(missing)
        if extra:
            res["txs_with_extra_logs"] += 1
        # reproducibility
        ours = {(r[0], r[1], r[2]) for r in store.db.execute("SELECT token, wallet, side FROM trades WHERE tx_hash=?", (tx,))}
        redo, _ = trades_from_tx(tx, int(rc["blockNumber"], 16), rc_logs, ctx)
        theirs = {(x.token, x.wallet, x.side) for x in redo}
        if ours == theirs:
            res["reproduced"] += 1
        else:
            res["not_reproduced"] += 1
            mismatches.append({"tx": tx, "stored": sorted(ours), "from_receipt": sorted(theirs)})
        # tx.from agreement and tx.to distribution
        frm = t["from"].lower()
        to = (t.get("to") or "").lower()
        for _, wallet, _ in ours:
            res["trades"] += 1
            if wallet == frm:
                res["wallet_equals_tx_from"] += 1
            elif wallet == to:
                res["wallet_equals_tx_to_contract"] += 1
            else:
                res["wallet_other_address"] += 1
        label = LABELS.get(to) or ("PONS curve (direct call)" if to in curves else ("token contract" if to in ctx.universe else "other contract " + to[:10]))
        to_dist[label] += 1
    top_to = to_dist.most_common(12)
    out = {
        "sample_size": len(sample),
        "seed": seed,
        **{k: v for k, v in res.items()},
        "completeness": f"{res['checked'] - res['txs_with_missing_logs']}/{res['checked']} txs have every receipt log we index",
        "reproducibility": f"{res['reproduced']}/{res['checked']} txs reproduce the same trades from the receipt",
        "tx_from_agreement": f"{res['wallet_equals_tx_from']}/{res['trades']} attributed wallets equal tx.from",
        "tx_to_distribution": top_to,
        "mismatches": mismatches[:10],
    }
    return out


def main_verify(args) -> int:
    store = Store()
    rpc = Rpc()
    if not rpc.alchemy_url:
        print("verify needs ALCHEMY_KEY (receipts)", file=sys.stderr)
        return 2
    run_id = store.start_run("verify", {"n": args.n, "seed": args.seed})
    out = verify(store, rpc, args.n, args.seed)
    store.finish_run(run_id, out)
    print(json.dumps(out, indent=1))
    return 0
