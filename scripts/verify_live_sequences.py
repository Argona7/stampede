#!/usr/bin/env python3
"""Usefulness check against the real chain: take recent observed sequences from a LIVE server and verify
each one independently through raw RPC (Alchemy): both transactions exist, the wallet really sent the
sold token in the sell tx and received the bought token in the buy tx, the sell precedes the buy, and
the recorded times match the block headers.

    .venv/bin/python scripts/verify_live_sequences.py --api-url http://127.0.0.1:8793 --n 6
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stampede.chain import T_TRANSFER, addr_from_topic  # noqa: E402
from stampede.env import load_dotenv  # noqa: E402
from stampede.rpc import Rpc  # noqa: E402


def utc(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%H:%M:%S")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api-url", default="http://127.0.0.1:8793")
    ap.add_argument("--n", type=int, default=6)
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    load_dotenv()
    rpc = Rpc()
    if not rpc.alchemy_url:
        print("needs ALCHEMY_KEY in .env")
        return 2
    st = requests.get(f"{a.api_url}/api/status", timeout=10).json()
    sess = requests.get(f"{a.api_url}/api/session", timeout=10).json()
    if sess["mode"] != "live":
        print(f"server is in {sess['mode']} mode; this check is for live")
        return 2
    live = st["live"]
    ev = requests.get(f"{a.api_url}/api/events", params={"window": f"{sess['window_s']}s", "backfill_s": sess["span_s"], "limit": 2000}, timeout=20).json()
    rows = ev["events"][-a.n :]
    print(f"live: last block {live['last_block']} head {live['head_block']} lag {live['head_lag_s']} s, ticks {live['ticks']}, paused {live['paused']}")
    print(f"observed sequences in the last {sess['span_s'] // 60} min (direct/clean): {ev['count']}; verifying the newest {len(rows)} through raw RPC\n")
    results = []
    for r in rows:
        w = r["wallet"]
        sell_tx = rpc.call("eth_getTransactionByHash", [r["sell_tx"]], prefer="alchemy")
        buy_tx = rpc.call("eth_getTransactionByHash", [r["buy_tx"]], prefer="alchemy")
        sell_rc = rpc.call("eth_getTransactionReceipt", [r["sell_tx"]], prefer="alchemy")
        buy_rc = rpc.call("eth_getTransactionReceipt", [r["buy_tx"]], prefer="alchemy")
        sb = int(sell_rc["blockNumber"], 16)
        bb = int(buy_rc["blockNumber"], 16)
        sh = rpc.call("eth_getBlockByNumber", [hex(sb), False], prefer="alchemy")
        bh = rpc.call("eth_getBlockByNumber", [hex(bb), False], prefer="alchemy")
        sts, bts = int(sh["timestamp"], 16), int(bh["timestamp"], 16)

        def moved(rc, token, wallet, direction):
            for lg in rc["logs"]:
                if lg["address"].lower() == token and lg["topics"] and lg["topics"][0].lower() == T_TRANSFER and len(lg["topics"]) >= 3:
                    frm, to = addr_from_topic(lg["topics"][1]), addr_from_topic(lg["topics"][2])
                    if direction == "out" and frm == wallet:
                        return True
                    if direction == "in" and to == wallet:
                        return True
            return False

        sold_out = moved(sell_rc, r["from"], w, "out")
        bought_in = moved(buy_rc, r["to"], w, "in")
        ok = sold_out and bought_in and (sb < bb or (sb == bb and r["grade"] == "direct")) and abs(bts - r["buy_ts"]) <= (0 if r["buy_ts_exact"] else 6)
        results.append(
            {
                "wallet": w,
                "pair": f"{r['from_symbol']} -> {r['to_symbol']}",
                "grade": r["grade"],
                "sell_tx": r["sell_tx"],
                "buy_tx": r["buy_tx"],
                "sell_block": sb,
                "buy_block": bb,
                "sell_block_time": utc(sts),
                "buy_block_time": utc(bts),
                "recorded_buy_time": utc(r["buy_ts"]) + ("" if r["buy_ts_exact"] else " (interpolated)"),
                "tx_from_is_wallet": {"sell": sell_tx["from"].lower() == w, "buy": buy_tx["from"].lower() == w},
                "wallet_sent_sold_token": sold_out,
                "wallet_received_bought_token": bought_in,
                "sell_before_buy": sb < bb or (sb == bb and r["grade"] == "direct"),
                "verified": ok,
            }
        )
        print(f"{'OK ' if ok else 'FAIL'} {w[:10]}… {r['from_symbol']} -> {r['to_symbol']} [{r['grade']}] sell blk {sb} {utc(sts)} -> buy blk {bb} {utc(bts)} | recorded {utc(r['buy_ts'])}{'' if r['buy_ts_exact'] else '≈'} | sent sold: {sold_out} received bought: {bought_in} | tx.from==wallet sell/buy: {sell_tx['from'].lower() == w}/{buy_tx['from'].lower() == w}")
        time.sleep(0.2)
    n_ok = sum(1 for x in results if x["verified"])
    print(f"\n{n_ok}/{len(results)} sequences verified independently through raw RPC")
    if a.out:
        Path(a.out).write_text(json.dumps({"checked_at_utc": datetime.now(timezone.utc).isoformat(), "live": live, "session": sess, "in_range": ev["count"], "results": results}, indent=1))
        print(f"written {a.out}")
    return 0 if n_ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
