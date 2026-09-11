"""Holder structure from the token's own Transfer history (on demand, cached): top-10 share, dev holding,
dev sold, holder count, launch-block bundle. Uses the public RPC for one wide getLogs (Alchemy's free tier
caps ranges at 10 blocks), splitting when the node's 10k-log cap is hit."""
from __future__ import annotations

import json
import time
from collections import defaultdict
from typing import Any

from .. import chain
from ..chain import addr_from_topic, u256
from ..store import Store
from .pons import first_seen_block, launch_of

EXCLUDE = {chain.PONS_V2_LOCKER, chain.V4_POOL_MANAGER, "0x0000000000000000000000000000000000000000", "0x000000000000000000000000000000000000dead"}
SUPPLY = 10**27  # 1e9 tokens * 1e18


def _transfers(rpc, token: str, fr: int, to: int, depth: int = 0) -> list[dict]:
    try:
        return rpc.get_logs(fr, to, address=token, topics=[[chain.T_TRANSFER]])
    except Exception as e:  # noqa: BLE001
        if depth < 6 and to - fr > 200 and ("limit" in str(e).lower() or "exceed" in str(e).lower() or "10000" in str(e)):
            mid = (fr + to) // 2
            return _transfers(rpc, token, fr, mid, depth + 1) + _transfers(rpc, token, mid + 1, to, depth + 1)
        raise


def holders(store: Store, rpc, token: str, head_block: int, max_age_s: float = 300.0, allow_fetch: bool = True) -> dict[str, Any] | None:
    r = store.db.execute("SELECT fetched_at, as_of_block, body FROM holders_cache WHERE token=?", (token,)).fetchone()
    if r and time.time() - r[0] <= max_age_s:
        d = json.loads(r[2])
        d["cache_age_s"] = round(time.time() - r[0])
        return d
    if not allow_fetch or rpc is None:
        return json.loads(r[2]) if r else None
    la = launch_of(store, token)
    first = first_seen_block(store, token)
    start = (la["block"] if la and la.get("block") else None) or ((first - 50) if first else None)
    if start is None:
        return None
    try:
        logs = _transfers(rpc, token, start, head_block)
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:120], "as_of_block": head_block}
    bal: dict[str, int] = defaultdict(int)
    first_block_buyers: set[str] = set()
    launch_block = la["block"] if la and la.get("block") else start
    curve = (la or {}).get("curve")
    excl = set(EXCLUDE) | ({curve} if curve else set()) | {token}
    peak_dev = 0
    dev = (la or {}).get("deployer")
    for l in logs:
        if len(l["topics"]) < 3:
            continue
        frm, to = addr_from_topic(l["topics"][1]), addr_from_topic(l["topics"][2])
        v = u256(l["data"], 0)
        bal[frm] -= v
        bal[to] += v
        blk = int(l["blockNumber"], 16)
        if blk <= launch_block + 1 and to not in excl:
            first_block_buyers.add(to)
        if dev and to == dev:
            peak_dev = max(peak_dev, bal[dev])
    holders_ = {a: b for a, b in bal.items() if b > 0 and a not in excl}
    total_held = sum(holders_.values()) or 1
    top = sorted(holders_.items(), key=lambda x: -x[1])[:10]
    top10 = sum(b for _, b in top)
    dev_bal = bal.get(dev, 0) if dev else None
    body = {
        "as_of_block": head_block,
        "holders": len(holders_),
        "top10_share": top10 / SUPPLY,
        "top10_share_of_held": top10 / total_held,
        "top": [{"address": a, "share": b / SUPPLY} for a, b in top],
        "dev": dev,
        "dev_share": (dev_bal / SUPPLY) if dev_bal is not None else None,
        "dev_sold_share": ((peak_dev - dev_bal) / SUPPLY) if dev_bal is not None and peak_dev else 0.0,
        "launch_block_buyers": len(first_block_buyers),
        "launch_block_buyers_share": sum(bal.get(a, 0) for a in first_block_buyers if bal.get(a, 0) > 0) / SUPPLY,
        "transfers": len(logs),
        "excluded": sorted(excl),
        "fetched_at": time.time(),
    }
    store.db.execute("INSERT OR REPLACE INTO holders_cache(token, fetched_at, as_of_block, body) VALUES(?,?,?,?)", (token, body["fetched_at"], head_block, json.dumps(body)))
    store.commit()
    body["cache_age_s"] = 0
    return body
