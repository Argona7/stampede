"""JSON-RPC client for Robinhood Chain with two endpoints and honest accounting.

- public RPC (rpc.mainnet.chain.robinhood.com): no getLogs range cap, but 429s and a pruned state.
- Alchemy (robinhood-mainnet): fast and reliable, but the free tier caps eth_getLogs at 10 blocks.

Wide log scans go to the public RPC and fall back to 10-block Alchemy chunks; everything else prefers
Alchemy when a key is configured. Every call is counted so the coverage report can say what was fetched,
retried and lost. API keys never reach logs or exceptions (see env.redact).
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

import requests

from . import chain
from .env import env, redact


class RpcError(Exception):
    def __init__(self, code: Any, message: str, data: Any = None):
        super().__init__(f"rpc error {code}: {redact(str(message))}")
        self.code = code
        self.message = message
        self.data = data


@dataclass
class Stats:
    calls: int = 0
    batches: int = 0
    rate_limited: int = 0
    errors: int = 0
    retries: int = 0
    bytes_in: int = 0
    per_host: dict[str, dict[str, int]] = field(default_factory=dict)
    latencies_ms: dict[str, list[float]] = field(default_factory=dict)

    def host(self, url: str) -> dict[str, int]:
        h = "alchemy" if "alchemy" in url else ("public" if "robinhood.com" in url else redact(url))
        return self.per_host.setdefault(h, {"calls": 0, "rate_limited": 0, "errors": 0})

    def lat(self, url: str, ms: float) -> None:
        h = "alchemy" if "alchemy" in url else "public"
        self.latencies_ms.setdefault(h, []).append(ms)

    def summary(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "calls": self.calls,
            "batches": self.batches,
            "rate_limited": self.rate_limited,
            "errors": self.errors,
            "retries": self.retries,
            "mb_in": round(self.bytes_in / 1e6, 1),
            "per_host": self.per_host,
        }
        for h, ls in self.latencies_ms.items():
            if ls:
                s = sorted(ls)
                out[f"{h}_latency_ms"] = {"median": round(s[len(s) // 2]), "p90": round(s[int(len(s) * 0.9) - 1] if len(s) > 1 else s[0]), "n": len(s)}
        return out


class Rpc:
    ALCHEMY_LOGS_RANGE = 10  # free tier cap (error -32600 "block range" above it)

    def __init__(self, public_url: str | None = None, alchemy_key: str | None = None, timeout: float = 30.0):
        self.public_url = public_url or env("PUBLIC_RPC", chain.PUBLIC_RPC)
        key = alchemy_key if alchemy_key is not None else env("ALCHEMY_KEY")
        self.alchemy_url = chain.ALCHEMY_HTTP.format(key=key) if key else None
        self.timeout = timeout
        self.stats = Stats()
        self._id = 0
        self._local = threading.local()
        self._lock = threading.Lock()
        self._bad_until: dict[str, float] = {}
        # the shared public node 429s after a handful of quick calls (measured); pace it and back off hard
        self._min_interval = {self.public_url: 3.0}
        self._last_call: dict[str, float] = {}

    @property
    def _sess(self) -> requests.Session:
        s = getattr(self._local, "sess", None)
        if s is None:
            s = requests.Session()
            s.headers.update({"content-type": "application/json", "user-agent": "stampede/0.1"})
            self._local.sess = s
        return s

    # ---- transport ----
    def _urls(self, prefer: str) -> list[str]:
        urls = [u for u in ([self.alchemy_url, self.public_url] if prefer == "alchemy" else [self.public_url, self.alchemy_url]) if u]
        now = time.time()
        good = [u for u in urls if self._bad_until.get(u, 0) <= now]
        return good or urls

    def _throttle(self, url: str) -> None:
        mi = self._min_interval.get(url, 0)
        if mi:
            with self._lock:
                dt = time.time() - self._last_call.get(url, 0)
                wait = mi - dt if dt < mi else 0
                self._last_call[url] = time.time() + wait
            if wait > 0:
                time.sleep(wait)

    def _post(self, url: str, payload: Any) -> Any:
        self._throttle(url)
        t0 = time.time()
        r = self._sess.post(url, json=payload, timeout=self.timeout)
        with self._lock:
            self.stats.lat(url, (time.time() - t0) * 1000)
            self.stats.bytes_in += len(r.content)
            h = self.stats.host(url)
            h["calls"] += 1
        if r.status_code == 429:
            with self._lock:
                h["rate_limited"] += 1
                self.stats.rate_limited += 1
            self._bad_until[url] = time.time() + (8.0 if url == self.public_url else 1.0)
            raise RuntimeError("429")
        if r.status_code >= 400:
            # providers (Alchemy) return JSON-RPC errors with HTTP 4xx; surface the message, not the status
            try:
                j = r.json()
                if isinstance(j, dict) and "error" in j or isinstance(j, list):
                    return j
            except ValueError:
                pass
        r.raise_for_status()
        return r.json()

    def call(self, method: str, params: list | None = None, prefer: str = "alchemy", retries: int = 4, urls: list[str] | None = None) -> Any:
        self._id += 1
        payload = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or []}
        self.stats.calls += 1
        last: Exception | None = None
        for attempt in range(retries):
            for url in urls or self._urls(prefer):
                try:
                    j = self._post(url, payload)
                    if "error" in j:
                        err = j["error"]
                        msg = str(err.get("message", err))
                        low = msg.lower()
                        if "rate" in low or "too many" in low or "exceeded" in low or "429" in low:
                            self.stats.rate_limited += 1
                            self.stats.host(url)["rate_limited"] += 1
                            self._bad_until[url] = time.time() + 2.0
                            last = RuntimeError(msg)
                            continue
                        raise RpcError(err.get("code"), msg, err.get("data"))
                    return j["result"]
                except RpcError:
                    self.stats.errors += 1
                    raise
                except Exception as e:  # noqa: BLE001 - transport layer, try the next endpoint
                    last = e
                    self.stats.errors += 1
                    self._bad_until[url] = time.time() + 1.0
                    continue
            self.stats.retries += 1
            time.sleep(0.5 * (attempt + 1))
        raise RuntimeError(f"rpc {method} failed: {redact(str(last))}")

    def batch(self, calls: list[tuple[str, list]], prefer: str = "alchemy", retries: int = 3) -> list[Any]:
        """One HTTP round-trip for many calls. Items with an error come back as RpcError instances."""
        if not calls:
            return []
        body = [{"jsonrpc": "2.0", "id": i, "method": m, "params": p} for i, (m, p) in enumerate(calls)]
        self.stats.batches += 1
        self.stats.calls += len(calls)
        last: Exception | None = None
        for attempt in range(retries):
            for url in self._urls(prefer):
                try:
                    res = self._post(url, body)
                    if isinstance(res, dict):
                        raise RuntimeError(redact(str(res))[:200])
                    byid = {x["id"]: x for x in res}
                    out: list[Any] = []
                    for i in range(len(calls)):
                        x = byid.get(i)
                        if x is None:
                            out.append(RpcError(None, "missing in batch response"))
                        elif "error" in x:
                            out.append(RpcError(x["error"].get("code"), x["error"].get("message")))
                        else:
                            out.append(x["result"])
                    return out
                except Exception as e:  # noqa: BLE001
                    last = e
                    self.stats.errors += 1
                    self._bad_until[url] = time.time() + 1.0
                    continue
            self.stats.retries += 1
            time.sleep(0.5 * (attempt + 1))
        raise RuntimeError(f"rpc batch failed: {redact(str(last))}")

    # ---- helpers ----
    def chain_id(self) -> int:
        return int(self.call("eth_chainId", prefer="public"), 16)

    def block_number(self, prefer: str = "public") -> int:
        return int(self.call("eth_blockNumber", prefer=prefer), 16)

    def get_block(self, number: int | str, full: bool = False, prefer: str = "alchemy") -> dict | None:
        tag = number if isinstance(number, str) else hex(number)
        return self.call("eth_getBlockByNumber", [tag, full], prefer=prefer)

    def get_blocks(self, numbers: list[int], prefer: str = "alchemy") -> dict[int, dict]:
        out: dict[int, dict] = {}
        for i in range(0, len(numbers), 50):
            chunk = numbers[i : i + 50]
            res = self.batch([("eth_getBlockByNumber", [hex(n), False]) for n in chunk], prefer=prefer)
            for n, r in zip(chunk, res):
                if isinstance(r, dict):
                    out[n] = r
        return out

    def get_receipt(self, tx_hash: str) -> dict | None:
        return self.call("eth_getTransactionReceipt", [tx_hash])

    def get_tx(self, tx_hash: str) -> dict | None:
        return self.call("eth_getTransactionByHash", [tx_hash])

    def eth_call(self, to: str, data: str, block: str = "latest") -> str:
        return self.call("eth_call", [{"to": to, "data": data}, block])

    def eth_call_batch(self, items: list[tuple[str, str]], block: str = "latest", size: int = 12) -> list[str | None]:
        """items = [(to, data)]. Returns hex results (None on per-item error after one retry).

        Batches stay small: Alchemy bills ~26 CU per eth_call and throttles the free tier at ~330 CU/s.
        """
        out: list[str | None] = [None] * len(items)
        pending = list(range(len(items)))
        for attempt in range(2):
            still: list[int] = []
            for i in range(0, len(pending), size):
                idx = pending[i : i + size]
                res = self.batch([("eth_call", [{"to": items[j][0], "data": items[j][1]}, block]) for j in idx])
                for j, r in zip(idx, res):
                    if isinstance(r, str):
                        out[j] = r
                    elif isinstance(r, RpcError) and r.code == 3 or (isinstance(r, RpcError) and "revert" in str(r.message).lower()):
                        out[j] = None  # contract reverted: a real answer, do not retry
                    else:
                        still.append(j)
                time.sleep(0.05)
            pending = still
            if not pending:
                break
            time.sleep(1.5)
        return out

    def get_code_batch(self, addrs: list[str], size: int = 12) -> dict[str, bool]:
        """address -> is_contract."""
        out: dict[str, bool] = {}
        for i in range(0, len(addrs), size):
            chunk = addrs[i : i + size]
            res = self.batch([("eth_getCode", [a, "latest"]) for a in chunk])
            for a, r in zip(chunk, res):
                if isinstance(r, str):
                    out[a] = r not in ("0x", "0x0", "")
            time.sleep(0.05)
        return out

    def get_logs(self, from_block: int, to_block: int, address: str | list[str] | None = None, topics: list | None = None) -> list[dict]:
        """One eth_getLogs. Ranges wider than Alchemy's cap go to the public RPC; narrow ones prefer Alchemy."""
        flt: dict[str, Any] = {"fromBlock": hex(from_block), "toBlock": hex(to_block)}
        if address:
            flt["address"] = address
        if topics:
            flt["topics"] = topics
        wide = (to_block - from_block + 1) > self.ALCHEMY_LOGS_RANGE
        if wide or not self.alchemy_url:
            return self.call("eth_getLogs", [flt], prefer="public", retries=2, urls=[self.public_url])
        return self.call("eth_getLogs", [flt], prefer="alchemy", urls=[self.alchemy_url])

    def get_logs_parallel(self, from_block: int, to_block: int, topics: list | None = None, workers: int = 4) -> tuple[list[dict], list[tuple[int, int, str]]]:
        """Alchemy path: 10-block sub-ranges fetched concurrently. Returns (logs, failed_ranges)."""
        from concurrent.futures import ThreadPoolExecutor

        ranges = []
        b = from_block
        while b <= to_block:
            e = min(b + self.ALCHEMY_LOGS_RANGE - 1, to_block)
            ranges.append((b, e))
            b = e + 1

        def one(r: tuple[int, int]):
            try:
                return r, self.get_logs(r[0], r[1], topics=topics), None
            except Exception as ex:  # noqa: BLE001
                return r, [], redact(str(ex))[:120]

        logs: list[dict] = []
        failed: list[tuple[int, int, str]] = []
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for r, ls, err in ex.map(one, ranges):
                if err:
                    failed.append((r[0], r[1], err))
                else:
                    logs.extend(ls)
        return logs, failed
