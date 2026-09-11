"""PONS on-chain lifecycle: launch (age, deployer, quote asset, graduation threshold), curve progress,
graduation, and the socials the creator declared in the launch transaction.

Everything here is read from the chain (logs already in the store, or one on-demand fetch). Nothing is
inferred from names or off-chain lists.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

from .. import chain
from ..chain import addr_from_topic, addr_from_word, u256
from ..store import Store

ZERO = "0x0000000000000000000000000000000000000000"


def _ts_for(store: Store, block: int) -> tuple[int | None, int]:
    from ..normalize import Interp

    interp = Interp({n: ts for n, (ts, ex) in store.blocks().items() if ex == 1})
    return interp(block)


def sync_lifecycle(store: Store) -> dict[str, int]:
    """Parse TokenLaunched / CurveCompleted / PoolRegistered logs already in the store into launches/graduations."""
    q = store.db.execute
    n_l = n_g = 0
    from ..normalize import Interp

    interp = Interp({n: ts for n, (ts, ex) in store.blocks().items() if ex == 1})
    for tx, block, t1, t2, t3, data in q("SELECT tx_hash, block, topic1, topic2, topic3, data FROM logs WHERE kind='token_launched' AND address=?", (chain.PONS_V2_FACTORY,)):
        token, curve, deployer = addr_from_topic(t1), addr_from_topic(t2), addr_from_topic(t3)
        ts, _ = interp(block)
        store.db.execute(
            "INSERT INTO launches(token,curve,deployer,pair_token,threshold,launch_config,block,ts,tx_hash,source) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(token) DO UPDATE SET ts=COALESCE(excluded.ts, launches.ts)",
            (token, curve, deployer, addr_from_word(data, 0), str(u256(data, 2)), u256(data, 1), block, ts, tx, "ingest"),
        )
        n_l += 1
    for tx, block, data in q("SELECT tx_hash, block, data FROM logs WHERE kind='pool_registered' AND address=?", (chain.PONS_V2_HOOK,)):
        if len(data) < 2 + 64 * 3:
            continue
        token, quote, creator = addr_from_word(data, 0), addr_from_word(data, 1), addr_from_word(data, 2)
        ts, _ = interp(block)
        store.db.execute(
            "INSERT INTO graduations(token,stage,block,ts,tx_hash,quote_token,creator) VALUES(?,?,?,?,?,?,?) ON CONFLICT(token) DO UPDATE SET stage='pool', block=excluded.block, ts=excluded.ts, tx_hash=excluded.tx_hash",
            (token, "pool", block, ts, tx, quote, creator),
        )
        n_g += 1
    # CurveCompleted(address indexed?, ...) is emitted by the curve itself: map curve -> token via launches or curves
    curve_token = {r[0]: r[1] for r in q("SELECT curve, token FROM launches WHERE curve IS NOT NULL")}
    curve_token.update({r[0]: r[1] for r in q("SELECT curve, token FROM curves")})
    for tx, block, addr in q("SELECT tx_hash, block, address FROM logs WHERE kind='curve_completed'"):
        token = curve_token.get(addr)
        if not token:
            continue
        ts, _ = interp(block)
        store.db.execute("INSERT OR IGNORE INTO graduations(token,stage,block,ts,tx_hash,quote_token,creator) VALUES(?,?,?,?,?,?,?)", (token, "curve_completed", block, ts, tx, None, None))
    store.commit()
    return {"launches": n_l, "graduations": n_g}


def fetch_launch(store: Store, rpc, token: str) -> dict[str, Any] | None:
    """On-demand: one TokenLaunched log for a token launched before the indexed range (public RPC, wide range)."""
    row = store.db.execute("SELECT token FROM launches WHERE token=?", (token,)).fetchone()
    if row:
        return launch_of(store, token)
    hi = first_seen_block(store, token)
    if hi is None:
        return None
    lo = max(1, hi - 60000)  # launches are followed by trades within minutes; 60k blocks = ~100 min of slack
    try:
        logs = rpc.get_logs(lo, hi, topics=[[chain.T_TOKEN_LAUNCHED], ["0x" + token[2:].rjust(64, "0")]], address=chain.PONS_V2_FACTORY)
    except Exception:  # noqa: BLE001
        return None
    if not logs:
        return None
    l = logs[0]
    block = int(l["blockNumber"], 16)
    data = l["data"]
    hdr = rpc.get_block(block)
    ts = int(hdr["timestamp"], 16) if hdr else None
    if ts:
        store.upsert_blocks([(block, ts, 1)])
    store.db.execute(
        "INSERT OR REPLACE INTO launches(token,curve,deployer,pair_token,threshold,launch_config,block,ts,tx_hash,source) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (token, addr_from_topic(l["topics"][2]), addr_from_topic(l["topics"][3]), addr_from_word(data, 0), str(u256(data, 2)), u256(data, 1), block, ts, l["transactionHash"], "on_demand"),
    )
    store.commit()
    return launch_of(store, token)


def first_seen_block(store: Store, token: str) -> int | None:
    """First block we saw the token trade (tokens.first_seen_block is only set for pool tokens)."""
    r = store.db.execute("SELECT first_seen_block FROM tokens WHERE address=?", (token,)).fetchone()
    if r and r[0]:
        return r[0]
    r = store.db.execute("SELECT MIN(block) FROM trades WHERE token=?", (token,)).fetchone()
    return r[0] if r and r[0] else None


def launch_of(store: Store, token: str) -> dict[str, Any] | None:
    r = store.db.execute("SELECT token,curve,deployer,pair_token,threshold,launch_config,block,ts,tx_hash,source FROM launches WHERE token=?", (token,)).fetchone()
    if not r:
        return None
    return {"token": r[0], "curve": r[1], "deployer": r[2], "pair_token": r[3], "threshold": int(r[4]) if r[4] else None, "launch_config": r[5], "block": r[6], "ts": r[7], "tx_hash": r[8], "source": r[9]}


def graduation_of(store: Store, token: str) -> dict[str, Any] | None:
    r = store.db.execute("SELECT stage, block, ts, tx_hash, quote_token, creator FROM graduations WHERE token=?", (token,)).fetchone()
    if not r:
        return None
    return {"stage": r[0], "block": r[1], "ts": r[2], "tx_hash": r[3], "quote_token": r[4], "creator": r[5]}


def curve_progress(store: Store, token: str, as_of_ts: int | None = None) -> dict[str, Any]:
    """Net quote taken in by the curve (buys minus sells, raw units) against the launch threshold.
    Computed from indexed curve trades, so it is as-of the replay clock when one is given."""
    la = launch_of(store, token)
    cond = "token=? AND venue='curve'"
    args: list[Any] = [token]
    if as_of_ts is not None:
        cond += " AND ts<=?"
        args.append(as_of_ts)
    r = store.db.execute(f"SELECT COALESCE(SUM(CASE WHEN side='buy' THEN CAST(quote_amount AS REAL) ELSE -CAST(quote_amount AS REAL) END),0), COUNT(*), COUNT(DISTINCT wallet), MIN(ts), MAX(ts) FROM trades WHERE {cond}", args).fetchone()
    net = float(r[0] or 0)
    thr = float(la["threshold"]) if la and la.get("threshold") else None
    grad = graduation_of(store, token)
    graduated = bool(grad and (as_of_ts is None or (grad.get("ts") or 0) <= as_of_ts))
    return {
        "net_quote_raw": net,
        "threshold_raw": thr,
        "progress": (min(1.0, net / thr) if thr and thr > 0 else None) if not graduated else 1.0,
        "curve_trades": r[1],
        "curve_traders": r[2],
        "first_trade_ts": r[3],
        "last_trade_ts": r[4],
        "graduated": graduated,
        "graduation": grad if graduated else None,
        "stage": "graduated" if graduated else ("curve" if r[1] else "unknown"),
    }


URL_RE = re.compile(r"https?://[^\s\"'<>\\]+", re.I)
X_RE = re.compile(r"(?:https?://)?(?:www\.)?(?:x\.com|twitter\.com)/([A-Za-z0-9_]{1,15})(?:/status/\d+)?", re.I)
TG_RE = re.compile(r"(?:https?://)?t\.me/([A-Za-z0-9_+]{3,64})", re.I)


def extract_strings(data_hex: str, min_len: int = 3) -> list[str]:
    """Printable UTF-8 runs inside ABI calldata (name, symbol, logo, description, socials live in dynamic strings)."""
    raw = bytes.fromhex(data_hex[2:] if data_hex.startswith("0x") else data_hex)
    out: list[str] = []
    cur = bytearray()
    for b in raw:
        if 32 <= b < 127:
            cur.append(b)
        else:
            if len(cur) >= min_len:
                out.append(cur.decode("ascii", "ignore"))
            cur = bytearray()
    if len(cur) >= min_len:
        out.append(cur.decode("ascii", "ignore"))
    return out


def launch_socials(store: Store, rpc, token: str, max_age_s: float = 7 * 86400) -> dict[str, Any] | None:
    """Socials/description the creator put into the launch calldata (declared, not verified)."""
    row = store.db.execute("SELECT twitter, telegram, website, description, strings, fetched_at FROM launch_meta WHERE token=?", (token,)).fetchone()
    if row and time.time() - row[5] < max_age_s:
        return {"twitter": row[0], "telegram": row[1], "website": row[2], "description": row[3], "declared_in": "launch calldata"}
    la = launch_of(store, token) or (fetch_launch(store, rpc, token) if rpc else None)
    if not la or not la.get("tx_hash"):
        return None
    try:
        tx = rpc.call("eth_getTransactionByHash", [la["tx_hash"]], prefer="alchemy")
    except Exception:  # noqa: BLE001
        return None
    if not tx or not tx.get("input"):
        return None
    strings = extract_strings(tx["input"])
    joined = " ".join(strings)
    xm = X_RE.search(joined)
    tgm = TG_RE.search(joined)
    urls = [u for u in URL_RE.findall(joined) if not re.search(r"(x\.com|twitter\.com|t\.me)", u, re.I)]
    website = next((u for u in urls if not u.lower().startswith("ipfs") and "ipfs" not in u.lower()), None)
    # description: the longest string that is not a URL and not the name/symbol
    sym = store.db.execute("SELECT symbol, name FROM tokens WHERE address=?", (token,)).fetchone() or ("", "")
    cid = re.compile(r"^[;,.]?(bafk|bafy|Qm)[A-Za-z0-9]{20,}")  # ipfs content ids (logo) are not descriptions
    cands = [s.strip(";,. ") for s in strings if not URL_RE.search(s) and s not in (sym[0], sym[1]) and len(s) > 12 and not cid.match(s.strip())]
    description = max(cands, key=len) if cands else None
    twitter = xm.group(1) if xm and xm.group(1).lower() not in ("home", "search", "i", "intent") else None
    store.db.execute(
        "INSERT OR REPLACE INTO launch_meta(token,twitter,telegram,website,description,strings,fetched_at) VALUES(?,?,?,?,?,?,?)",
        (token, twitter, tgm.group(1) if tgm else None, website, description, json.dumps(strings[:40]), time.time()),
    )
    store.commit()
    return {"twitter": twitter, "telegram": tgm.group(1) if tgm else None, "website": website, "description": description, "declared_in": "launch calldata"}
