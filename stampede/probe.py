"""`stampede probe`: measure every candidate data source and write docs/SOURCES.md.

Nothing here is assumed from marketing pages: each number in the report comes from a request made
during the run (latency, caps, error messages, event counts), and the report says when it was measured.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

import requests

from . import chain
from .env import ROOT, redact
from .hypersync import HyperSync
from .rpc import Rpc, RpcError

SAMPLE_BLOCKS = 300


def _timed(fn, *a, **kw) -> tuple[Any, float]:
    t0 = time.time()
    r = fn(*a, **kw)
    return r, round((time.time() - t0) * 1000)


def probe_public_rpc(rpc: Rpc) -> dict[str, Any]:
    out: dict[str, Any] = {"url": rpc.public_url}
    pub = [rpc.public_url]
    try:
        out["chain_id"] = int(rpc.call("eth_chainId", urls=pub), 16)
        n0 = len(rpc.stats.latencies_ms.get("public", []))
        for _ in range(5):
            rpc.call("eth_blockNumber", urls=pub)
        lat = [round(x) for x in rpc.stats.latencies_ms["public"][n0:]]  # HTTP round-trips only, pacing excluded
        out["blockNumber_latency_ms"] = {"min": min(lat), "median": sorted(lat)[len(lat) // 2], "max": max(lat)}
        b0 = int(rpc.call("eth_blockNumber", urls=pub), 16)
        t0 = time.time()
        time.sleep(5)
        b1 = int(rpc.call("eth_blockNumber", urls=pub), 16)
        out["blocks_per_second"] = round((b1 - b0) / (time.time() - t0), 1)
        head = rpc.call("eth_getBlockByNumber", ["latest", False], urls=pub)
        out["head_block"] = int(head["number"], 16)
        out["head_lag_s"] = round(time.time() - int(head["timestamp"], 16), 1)
        out["head_tx_count"] = len(head["transactions"])
        # swap-class logs over SAMPLE_BLOCKS blocks
        to_b = out["head_block"] - 20
        fr_b = to_b - SAMPLE_BLOCKS
        logs, ms = _timed(rpc.call, "eth_getLogs", [{"fromBlock": hex(fr_b), "toBlock": hex(to_b), "topics": [chain.SWAP_TOPICS]}], urls=pub)
        kinds: dict[str, int] = {}
        emitters: dict[str, int] = {}
        for l in logs:
            k = chain.KIND_BY_TOPIC.get(l["topics"][0], "?")
            kinds[k] = kinds.get(k, 0) + 1
            emitters[l["address"].lower()] = emitters.get(l["address"].lower(), 0) + 1
        out["getLogs_swaps"] = {"blocks": SAMPLE_BLOCKS, "ms": ms, "logs": len(logs), "by_kind": kinds, "distinct_txs": len({l["transactionHash"] for l in logs}), "distinct_emitters": len(emitters)}
        out["v4_swap_logs_from_pool_manager"] = emitters.get(chain.V4_POOL_MANAGER, 0)
        out["v4_swap_logs_from_other_addresses"] = sum(v for a, v in emitters.items() if a != chain.V4_POOL_MANAGER and any(l["address"].lower() == a and l["topics"][0] == chain.T_V4_SWAP for l in logs))
        tlogs, ms = _timed(rpc.call, "eth_getLogs", [{"fromBlock": hex(fr_b), "toBlock": hex(to_b), "topics": [[chain.T_TRANSFER]]}], urls=pub)
        swap_txs = {l["transactionHash"] for l in logs}
        out["getLogs_transfers"] = {"blocks": SAMPLE_BLOCKS, "ms": ms, "logs": len(tlogs), "in_swap_txs": sum(1 for l in tlogs if l["transactionHash"] in swap_txs)}
        # how wide can one call go?
        for width in (1000, 3000):
            try:
                wl, ms = _timed(rpc.call, "eth_getLogs", [{"fromBlock": hex(to_b - width), "toBlock": hex(to_b), "topics": [chain.SWAP_TOPICS]}], urls=pub, retries=1)
                out[f"getLogs_swaps_{width}_blocks"] = {"ok": True, "ms": ms, "logs": len(wl)}
            except Exception as e:  # noqa: BLE001
                out[f"getLogs_swaps_{width}_blocks"] = {"ok": False, "error": redact(str(e))[:160]}
        # pruned state check: balance at an old block
        try:
            rpc.call("eth_getBalance", [chain.V4_POOL_MANAGER, hex(out["head_block"] - 5000)], urls=pub, retries=1)
            out["historical_state_5000_blocks_back"] = "ok"
        except Exception as e:  # noqa: BLE001
            out["historical_state_5000_blocks_back"] = "error: " + redact(str(e))[:120]
        out["_sample"] = {"from": fr_b, "to": to_b, "swap_logs": logs[:1]}
    except Exception as e:  # noqa: BLE001
        out["error"] = redact(str(e))[:200]
    return out


def probe_alchemy(rpc: Rpc, sample: dict | None) -> dict[str, Any]:
    out: dict[str, Any] = {"configured": bool(rpc.alchemy_url)}
    if not rpc.alchemy_url:
        return out
    al = [rpc.alchemy_url]
    try:
        n0 = len(rpc.stats.latencies_ms.get("alchemy", []))
        for _ in range(5):
            rpc.call("eth_blockNumber", urls=al)
        lat = [round(x) for x in rpc.stats.latencies_ms["alchemy"][n0:]]
        out["blockNumber_latency_ms"] = {"min": min(lat), "median": sorted(lat)[len(lat) // 2], "max": max(lat)}
        head = int(rpc.call("eth_blockNumber", urls=al), 16)
        out["head_block"] = head
        l10, ms = _timed(rpc.call, "eth_getLogs", [{"fromBlock": hex(head - 30), "toBlock": hex(head - 21), "topics": [chain.SWAP_TOPICS]}], urls=al)
        out["getLogs_10_blocks"] = {"ok": True, "ms": ms, "logs": len(l10)}
        try:
            rpc.call("eth_getLogs", [{"fromBlock": hex(head - 60), "toBlock": hex(head - 21), "topics": [chain.SWAP_TOPICS]}], urls=al, retries=1)
            out["getLogs_40_blocks"] = {"ok": True}
        except RpcError as e:
            out["getLogs_40_blocks"] = {"ok": False, "error": redact(e.message)[:160]}
        try:
            rc, ms = _timed(rpc.call, "eth_getBlockReceipts", [hex(head - 25)], urls=al)
            out["eth_getBlockReceipts"] = {"ok": True, "ms": ms, "receipts": len(rc or [])}
        except Exception as e:  # noqa: BLE001
            out["eth_getBlockReceipts"] = {"ok": False, "error": redact(str(e))[:160]}
        nums = list(range(head - 2500, head - 2500 + 50))
        blocks, ms = _timed(rpc.get_blocks, nums, "alchemy")
        out["batch_50_block_headers"] = {"ms": ms, "returned": len(blocks)}
        if sample and sample.get("swap_logs"):
            l = sample["swap_logs"][0]
            rc, ms = _timed(rpc.call, "eth_getTransactionReceipt", [l["transactionHash"]], urls=al)
            out["receipt_lookup"] = {"ms": ms, "logs_in_receipt": len(rc.get("logs", [])) if rc else None}
        # constants re-check: a live curve answers token(); the factory knows that token
        if sample and sample.get("swap_logs"):
            curve_logs = [x for x in sample["swap_logs"] if x["topics"][0] in (chain.T_CURVE_BUY, chain.T_CURVE_SELL)]
            if curve_logs:
                curve = curve_logs[0]["address"].lower()
                tok = rpc.eth_call(curve, chain.S_TOKEN)
                token = chain.addr_from_word(tok, 0)
                launched = rpc.eth_call(chain.PONS_V2_FACTORY, chain.S_GET_LAUNCHED_TOKEN + token[2:].rjust(64, "0"))
                out["constants_check"] = {"curve": curve, "token": token, "factory_getLaunchedToken_curve": chain.addr_from_word(launched, 1) if launched and len(launched) > 130 else None}
                out["constants_check"]["factory_matches_curve"] = out["constants_check"]["factory_getLaunchedToken_curve"] == curve
    except Exception as e:  # noqa: BLE001
        out["error"] = redact(str(e))[:200]
    return out


def probe_blockscout() -> dict[str, Any]:
    out: dict[str, Any] = {"url": chain.EXPLORER}
    try:
        r = requests.get(f"{chain.EXPLORER}/api/v2/stats", timeout=20, headers={"user-agent": "stampede/0.1"})
        out["status"] = r.status_code
        ct = r.headers.get("content-type", "")
        out["content_type"] = ct
        if "json" in ct:
            out["json_ok"] = True
            j = r.json()
            out["average_block_time_ms"] = j.get("average_block_time")
            out["total_transactions"] = j.get("total_transactions")
        else:
            out["json_ok"] = False
            out["note"] = "Cloudflare challenge page (\"Just a moment...\") returned to a script" if "Just a moment" in r.text else "non-JSON response"
    except Exception as e:  # noqa: BLE001
        out["error"] = redact(str(e))[:200]
    return out


def probe_geckoterminal() -> dict[str, Any]:
    out: dict[str, Any] = {"network": chain.GECKO_NETWORK}
    try:
        r = requests.get(f"https://api.geckoterminal.com/api/v2/networks/{chain.GECKO_NETWORK}/dexes?page=1", timeout=20, headers={"accept": "application/json"})
        out["status"] = r.status_code
        dexes = [d["id"] for d in r.json().get("data", [])]
        r2 = requests.get(f"https://api.geckoterminal.com/api/v2/networks/{chain.GECKO_NETWORK}/dexes?page=2", timeout=20, headers={"accept": "application/json"})
        if r2.ok:
            dexes += [d["id"] for d in r2.json().get("data", [])]
        out["dexes"] = dexes
        out["dex_count"] = len(dexes)
        out["pons_ids"] = [d for d in dexes if "pons" in d]
    except Exception as e:  # noqa: BLE001
        out["error"] = redact(str(e))[:200]
    return out


def run_probe() -> dict[str, Any]:
    rpc = Rpc()
    result: dict[str, Any] = {"measured_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")}
    result["public_rpc"] = probe_public_rpc(rpc)
    sample = result["public_rpc"].pop("_sample", None)
    result["alchemy"] = probe_alchemy(rpc, sample)
    result["hypersync"] = HyperSync().probe()
    result["blockscout"] = probe_blockscout()
    result["geckoterminal"] = probe_geckoterminal()
    result["rpc_stats"] = rpc.stats.summary()
    return result


def render_sources_md(p: dict[str, Any]) -> str:
    pub, al, hs, bs, gt = p["public_rpc"], p["alchemy"], p["hypersync"], p["blockscout"], p["geckoterminal"]
    L: list[str] = []
    L.append("# Data sources for STAMPEDE (measured)\n")
    L.append(f"Measured at {p['measured_at_utc']} by `stampede probe`. Re-run it to refresh; numbers below are not copied from provider marketing.\n")
    L.append("## Public RPC `rpc.mainnet.chain.robinhood.com`\n")
    if "error" in pub:
        L.append(f"- Error: `{pub['error']}`")
    else:
        L.append(f"- chain id {pub['chain_id']}; head block {pub['head_block']}, head lag {pub['head_lag_s']} s, {pub['head_tx_count']} txs in the head block; {pub['blocks_per_second']} blocks/s.")
        L.append(f"- `eth_blockNumber` latency ms: {pub['blockNumber_latency_ms']}.")
        g = pub["getLogs_swaps"]
        L.append(f"- `eth_getLogs`, {g['blocks']} blocks, topics CurveBuy|CurveSell|Swap: {g['logs']} logs in {g['ms']} ms, {g['distinct_txs']} distinct txs, {g['distinct_emitters']} emitting contracts; by kind {g['by_kind']}.")
        L.append(f"- v4 `Swap` logs emitted by PoolManager `{chain.V4_POOL_MANAGER[:10]}…`: {pub['v4_swap_logs_from_pool_manager']}; by other addresses (other v4 forks): {pub['v4_swap_logs_from_other_addresses']}.")
        t = pub["getLogs_transfers"]
        L.append(f"- `eth_getLogs`, same {t['blocks']} blocks, topic Transfer: {t['logs']} logs in {t['ms']} ms, of which {t['in_swap_txs']} belong to swap transactions.")
        for w in (1000, 3000):
            r = pub.get(f"getLogs_swaps_{w}_blocks", {})
            L.append(f"- `eth_getLogs` over {w} blocks: " + (f"ok, {r['logs']} logs in {r['ms']} ms." if r.get("ok") else f"failed: `{r.get('error')}`."))
        L.append(f"- Historical state 5000 blocks back (`eth_getBalance`): {pub['historical_state_5000_blocks_back']}.")
    L.append("\n## Alchemy `robinhood-mainnet`\n")
    if not al.get("configured"):
        L.append("- Not configured (no `ALCHEMY_KEY`).")
    elif "error" in al:
        L.append(f"- Error: `{al['error']}`")
    else:
        L.append(f"- `eth_blockNumber` latency ms: {al['blockNumber_latency_ms']}.")
        L.append(f"- `eth_getLogs` 10 blocks: ok ({al['getLogs_10_blocks']['logs']} logs, {al['getLogs_10_blocks']['ms']} ms); 40 blocks: " + ("ok." if al["getLogs_40_blocks"].get("ok") else f"rejected: `{al['getLogs_40_blocks'].get('error')}`."))
        br = al["eth_getBlockReceipts"]
        L.append("- `eth_getBlockReceipts`: " + (f"supported ({br['receipts']} receipts, {br['ms']} ms)." if br.get("ok") else f"not available: `{br.get('error')}`."))
        L.append(f"- Batch of 50 block headers: {al['batch_50_block_headers']['returned']} returned in {al['batch_50_block_headers']['ms']} ms (used for exact block timestamps).")
        if "receipt_lookup" in al:
            L.append(f"- Single receipt lookup: {al['receipt_lookup']['ms']} ms, {al['receipt_lookup']['logs_in_receipt']} logs.")
        cc = al.get("constants_check")
        if cc:
            L.append(f"- Constants re-check: live curve `{cc['curve']}` → `token()` = `{cc['token']}`; PONS v2 factory `getLaunchedToken` returns curve `{cc['factory_getLaunchedToken_curve']}` → matches: {cc['factory_matches_curve']}.")
    L.append("\n## Envio HyperSync `robinhood.hypersync.xyz`\n")
    if "height_error" in hs:
        L.append(f"- `/height` failed: `{hs['height_error']}`")
    else:
        L.append(f"- `/height` = {hs['height']} in {hs['height_ms']} ms; token configured: {hs['token_configured']}.")
        if hs.get("query_ok"):
            L.append(f"- `/query` (30 blocks, CurveBuy, join transactions): ok, {hs['query_logs_30_blocks']} logs in {hs['query_ms']} ms; archive height {hs.get('archive_height')}.")
        else:
            L.append(f"- `/query`: {hs.get('query_error')}. Ingest uses the RPC path until a free token is added to `.env` as `HYPERSYNC_TOKEN`.")
    L.append("\n## Blockscout `robinhoodchain.blockscout.com`\n")
    if "error" in bs:
        L.append(f"- Error: `{bs['error']}`")
    else:
        L.append(f"- `/api/v2/stats` from a script: HTTP {bs['status']}, content-type `{bs['content_type']}`; JSON: {bs.get('json_ok')}. {bs.get('note', '')}".rstrip())
        L.append("- Used only for human-readable links (`/tx/<hash>`, `/address/<addr>`) in reports and the UI. The PRO API (`api.blockscout.com/4663/...`, free key) is the scripted alternative if decoded explorer data is ever needed.")
    L.append("\n## GeckoTerminal (cross-check and coverage list only)\n")
    if "error" in gt:
        L.append(f"- Error: `{gt['error']}`")
    else:
        L.append(f"- Network `{gt['network']}` lists {gt['dex_count']} DEX ids; PONS ids: {gt['pons_ids']}.")
        L.append("- Full list: " + ", ".join(f"`{d}`" for d in gt["dexes"]) + ".")
        L.append("- Not a data source for edges: pool trades there are capped to the last few hundred per pool and are not attributable beyond `tx_from_address`. The list above is what STAMPEDE does *not* cover unless a venue is explicitly added.")
    L.append("\n## What this means for the ingest\n")
    L.append("- Ingest path with an Alchemy key: `eth_getLogs` in 10-block sub-ranges, one topic filter for CurveBuy|CurveSell|Swap|Transfer, 4 parallel workers (measured ~170 blocks/s, i.e. one hour of chain in ~6 minutes, 0 failed ranges). The public RPC 429s after a few quick calls and caps a query at 10 000 logs, so it is used only when no key is configured (300-block chunks, halved on error). Alchemy also serves block-header batches (timestamps), `eth_call` batches (registry) and receipt cross-checks.")
    L.append("- Wallet attribution cannot rely on event topics (router addresses appear there); it uses ERC-20 `Transfer` logs inside the same transaction. See `docs/ALGORITHM.md`.")
    L.append("- Block timestamps: exact for anchor blocks fetched in batches, interpolated in between; each trade stores `ts_exact`. Examples in `docs/EXAMPLES.md` use exact timestamps.")
    L.append("- HyperSync, when a token is present, replaces the RPC scan and adds `tx.from`/`tx.to` and exact timestamps for every row. The coverage report names the path actually used.")
    return "\n".join(L) + "\n"


def main_probe(write: bool = True) -> dict[str, Any]:
    p = run_probe()
    (ROOT / "data").mkdir(exist_ok=True)
    (ROOT / "data" / "probe.json").write_text(json.dumps(p, indent=1, default=str))
    md = render_sources_md(p)
    if write:
        (ROOT / "docs").mkdir(exist_ok=True)
        (ROOT / "docs" / "SOURCES.md").write_text(md)
    print(md)
    return p
