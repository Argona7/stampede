"""Small HTTP client for the STAMPEDE API. Injected into the TUI so tests can replace it."""
from __future__ import annotations

from typing import Any

import requests


class ApiError(Exception):
    pass


class ApiClient:
    def __init__(self, base_url: str, timeout: float = 6.0):
        self.base = base_url.rstrip("/")
        self.timeout = timeout
        self._s = requests.Session()
        self._s.headers.update({"user-agent": "stampede-tui/0.2"})

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        try:
            r = self._s.get(self.base + path, params={k: v for k, v in (params or {}).items() if v is not None}, timeout=self.timeout)
        except requests.RequestException as e:  # noqa: PERF203
            raise ApiError(f"{type(e).__name__}: {str(e)[:120]}") from e
        if r.status_code >= 400:
            raise ApiError(f"HTTP {r.status_code} {path}")
        return r.json()

    def _post(self, path: str, body: dict[str, Any]) -> Any:
        try:
            r = self._s.post(self.base + path, json=body, timeout=self.timeout)
        except requests.RequestException as e:
            raise ApiError(f"{type(e).__name__}: {str(e)[:120]}") from e
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail")
            except Exception:  # noqa: BLE001
                detail = r.text[:120]
            raise ApiError(f"HTTP {r.status_code}: {detail}")
        return r.json()

    def status(self) -> dict[str, Any]:
        return self._get("/api/status")

    def session(self) -> dict[str, Any]:
        return self._get("/api/session")

    def control(self, action: str, **kw: Any) -> dict[str, Any]:
        return self._post("/api/session", {"action": action, **kw})

    def events(self, window_s: int, until: int | None, after: str | None, limit: int = 500, backfill_s: int = 1800) -> dict[str, Any]:
        return self._get("/api/events", {"window": f"{window_s}s", "until": until, "after": after, "limit": limit, "backfill_s": backfill_s})

    def graph(self, window_s: int, from_ts: int | None, to_ts: int | None, min_wallets: int = 1, limit: int = 12) -> dict[str, Any]:
        return self._get("/api/graph", {"window": f"{window_s}s", "from": from_ts, "to": to_ts, "min_wallets": min_wallets, "limit": limit})

    def edge(self, a: str, b: str, window_s: int, from_ts: int | None, to_ts: int | None, limit: int = 40) -> dict[str, Any]:
        return self._get(f"/api/edge/{a}/{b}", {"window": f"{window_s}s", "from": from_ts, "to": to_ts, "limit": limit, "exact": 1})
