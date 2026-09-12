"""Envio HyperSync HTTP client (JSON). Optional: needs a free API token (HYPERSYNC_TOKEN).

One /query returns logs joined with their transactions (from/to) and blocks (timestamp) for an
arbitrary block range, paginated by `next_block`. Without a token the endpoint answers 401 and the
ingest falls back to the RPC path; the probe records which one was used.
"""
from __future__ import annotations

import time
from typing import Any

import requests

from . import chain
from .env import env, redact

LOG_FIELDS = ["block_number", "transaction_hash", "log_index", "address", "topic0", "topic1", "topic2", "topic3", "data", "transaction_index"]
TX_FIELDS = ["hash", "from", "to", "block_number"]
BLOCK_FIELDS = ["number", "timestamp"]


class HyperSync:
    def __init__(self, token: str | None = None, url: str = chain.HYPERSYNC_URL, timeout: float = 60.0):
        self.url = url.rstrip("/")
        self.token = token if token is not None else env("HYPERSYNC_TOKEN")
        self.timeout = timeout
        self._sess = requests.Session()
        self._sess.headers.update({"content-type": "application/json", "user-agent": "stampede/0.1"})
        if self.token:
            self._sess.headers["authorization"] = f"Bearer {self.token}"
        self.calls = 0
        self.bytes_in = 0

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    def height(self) -> int:
        r = self._sess.get(f"{self.url}/height", timeout=self.timeout)
        r.raise_for_status()
        return int(r.json()["height"])

    def query_once(self, body: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        r = self._sess.post(f"{self.url}/query", json=body, timeout=self.timeout)
        self.bytes_in += len(r.content)
        if r.status_code == 401:
            raise PermissionError("hypersync: 401 (API token missing or invalid)")
        r.raise_for_status()
        return r.json()

    def logs(self, from_block: int, to_block: int, topics0: list[str], address: list[str] | None = None, join_tx: bool = True) -> tuple[list[dict], list[dict], list[dict]]:
        """All logs with topic0 in `topics0` in [from_block, to_block] (inclusive). Returns (logs, txs, blocks)."""
        logs: list[dict] = []
        txs: list[dict] = []
        blocks: list[dict] = []
        nb = from_block
        sel = {"log": LOG_FIELDS, "block": BLOCK_FIELDS}
        if join_tx:
            sel["transaction"] = TX_FIELDS
        while nb <= to_block:
            body: dict[str, Any] = {
                "from_block": nb,
                "to_block": to_block + 1,  # exclusive
                "logs": [{"topics": [topics0]} | ({"address": address} if address else {})],
                "field_selection": sel,
            }
            if join_tx:
                body["join_mode"] = "Default"  # logs → their transactions and blocks
            res = self.query_once(body)
            for batch in res.get("data", []):
                logs.extend(batch.get("logs", []))
                txs.extend(batch.get("transactions", []))
                blocks.extend(batch.get("blocks", []))
            nxt = int(res.get("next_block", to_block + 1))
            if nxt <= nb:
                break
            nb = nxt
            if nb <= to_block:
                time.sleep(0.05)
        return logs, txs, blocks

    def probe(self) -> dict[str, Any]:
        out: dict[str, Any] = {"url": self.url, "token_configured": self.enabled}
        t0 = time.time()
        try:
            out["height"] = self.height()
            out["height_ms"] = round((time.time() - t0) * 1000)
        except Exception as e:  # noqa: BLE001
            out["height_error"] = redact(str(e))[:200]
            return out
        try:
            h = out["height"]
            t0 = time.time()
            res = self.query_once({"from_block": h - 30, "to_block": h, "logs": [{"topics": [[chain.T_CURVE_BUY]]}], "field_selection": {"log": ["block_number", "transaction_hash"], "transaction": ["from"], "block": ["timestamp"]}, "join_mode": "Default"})
            n = sum(len(b.get("logs", [])) for b in res.get("data", []))
            out["query_ok"] = True
            out["query_ms"] = round((time.time() - t0) * 1000)
            out["query_logs_30_blocks"] = n
            out["archive_height"] = res.get("archive_height")
        except PermissionError as e:
            out["query_ok"] = False
            out["query_error"] = str(e)
        except Exception as e:  # noqa: BLE001
            out["query_ok"] = False
            out["query_error"] = redact(str(e))[:200]
        return out
