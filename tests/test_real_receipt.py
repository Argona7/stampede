"""A recorded Robinhood Chain receipt: a sell routed through the PONS router.

The CurveSell topics name the router; the attribution must name the sending wallet and never the router.
"""
import json
from pathlib import Path

from stampede import chain
from stampede.normalize import Context, trades_from_tx

FX = Path(__file__).parent / "fixtures"


def load():
    fx = json.loads((FX / "receipt_router_sell.json").read_text())
    curves = json.loads((FX / "receipt_router_sell.curves.json").read_text())
    logs = []
    for l in fx["receipt"]["logs"]:
        tp = l["topics"]
        k = chain.KIND_BY_TOPIC.get(tp[0]) if tp else None
        if k is None:
            continue
        logs.append({"log_index": int(l["logIndex"], 16), "address": l["address"].lower(), "topic0": tp[0], "topic1": tp[1] if len(tp) > 1 else None, "topic2": tp[2] if len(tp) > 2 else None, "topic3": tp[3] if len(tp) > 3 else None, "data": l["data"], "kind": k})
    ctx = Context(curves=curves, pool_token={}, universe={c["token"] for c in curves.values()}, infra=set(chain.KNOWN_INFRA) | set(curves) | {chain.NATIVE})
    return fx, logs, ctx


def test_router_sell_is_attributed_to_tx_from_not_router():
    fx, logs, ctx = load()
    assert fx["tx"]["to"] == chain.PONS_ROUTER
    sell_topics = [l for l in logs if l["kind"] == "curve_sell"]
    assert sell_topics and chain.addr_from_topic(sell_topics[0]["topic1"]) == chain.PONS_ROUTER, "fixture must be a router-routed sell"
    trades, notes = trades_from_tx(fx["tx"]["hash"], int(fx["receipt"]["blockNumber"], 16), logs, ctx)
    assert [(t.side, t.wallet) for t in trades] == [("sell", fx["tx"]["from"])]
    assert all(t.wallet != chain.PONS_ROUTER for t in trades)
    assert trades[0].quote_token == chain.NATIVE and trades[0].quote_amount > 0
