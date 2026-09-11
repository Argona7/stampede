"""USD rates for the quote assets PONS coins trade against (native ETH first, then USDG and tokenized stocks).

`fx_rates(token, ts_hour, usd, source)`: one row per asset per hour. Two feeds, both free and keyless:
- CoinGecko `coins/ethereum/market_chart` for ETH (hourly history, up to 90 days) → token = NATIVE and WETH.
- GeckoTerminal `simple/networks/robinhood/token_price/{addresses}` for every other approved pair token: a spot
  price stamped on every hour of the range with source `geckoterminal_spot` — a flag, not history. PnL in the research
  reports is stated in quote units first; USD is a second column and inherits this limitation for non-ETH pairs.
"""
from __future__ import annotations

import time
from typing import Any, Iterable

import requests

from .. import chain
from ..store import Store

COINGECKO = "https://api.coingecko.com/api/v3"
GECKOTERMINAL = "https://api.geckoterminal.com/api/v2"
HOUR = 3600


def hour(ts: int) -> int:
    return int(ts) // HOUR * HOUR


def eth_hourly(days: float, session: requests.Session | None = None) -> list[tuple[int, float]]:
    """(ts_hour, usd) points from CoinGecko; hourly granularity for 2–90 days."""
    s = session or requests.Session()
    r = s.get(f"{COINGECKO}/coins/ethereum/market_chart", params={"vs_currency": "usd", "days": str(max(2, int(days + 1)))}, timeout=30, headers={"user-agent": "stampede/0.1"})
    r.raise_for_status()
    out: dict[int, float] = {}
    for ms, price in r.json().get("prices", []):
        out[hour(int(ms) // 1000)] = float(price)
    return sorted(out.items())


def spot_prices(addresses: list[str], session: requests.Session | None = None) -> dict[str, float]:
    """GeckoTerminal spot USD per token address (≤30 per call, 30 calls/min public limit)."""
    s = session or requests.Session()
    out: dict[str, float] = {}
    for i in range(0, len(addresses), 30):
        batch = addresses[i : i + 30]
        r = s.get(f"{GECKOTERMINAL}/simple/networks/{chain.GECKO_NETWORK}/token_price/{','.join(batch)}", timeout=30, headers={"accept": "application/json", "user-agent": "stampede/0.1"})
        if r.status_code == 429:
            time.sleep(21)
            r = s.get(f"{GECKOTERMINAL}/simple/networks/{chain.GECKO_NETWORK}/token_price/{','.join(batch)}", timeout=30, headers={"accept": "application/json", "user-agent": "stampede/0.1"})
        r.raise_for_status()
        prices = (r.json().get("data") or {}).get("attributes", {}).get("token_prices", {}) or {}
        for a, p in prices.items():
            if p is not None:
                out[a.lower()] = float(p)
        if i + 30 < len(addresses):
            time.sleep(2.1)
    return out


def quote_tokens(store: Store) -> list[str]:
    q = {r[0] for r in store.db.execute("SELECT DISTINCT quote_token FROM trades WHERE quote_token IS NOT NULL")}
    q |= {r[0] for r in store.db.execute("SELECT DISTINCT pair_token FROM curves WHERE pair_token IS NOT NULL")}
    return sorted(a for a in q if a and a != chain.NATIVE and a != chain.WETH)


def load_fx(store: Store, days: float, session: requests.Session | None = None, now: int | None = None) -> dict[str, Any]:
    now = now or int(time.time())
    first = hour(now - int(days * 86400))
    hours = list(range(first, hour(now) + HOUR, HOUR))
    rows: list[tuple] = []
    eth = eth_hourly(days, session)
    for h, usd in eth:
        if h >= first:
            rows.append((chain.NATIVE, h, usd, "coingecko_hourly"))
            rows.append((chain.WETH, h, usd, "coingecko_hourly"))
    others = quote_tokens(store)
    spot = spot_prices(others, session) if others else {}
    for a, usd in spot.items():
        for h in hours:
            rows.append((a, h, usd, "geckoterminal_spot"))
    store.db.executemany("INSERT OR REPLACE INTO fx_rates(token,ts_hour,usd,source) VALUES(?,?,?,?)", rows)
    store.commit()
    return {"eth_hours": sum(1 for h, _ in eth if h >= first), "spot_tokens": len(spot), "spot_missing": sorted(set(others) - set(spot)), "rows": len(rows)}


class Fx:
    """In-memory lookup: usd(token, ts) = the rate of the latest hour ≤ ts (None when the asset has no rate)."""

    def __init__(self, store: Store):
        self.rates: dict[str, list[tuple[int, float]]] = {}
        for tok, h, usd in store.db.execute("SELECT token, ts_hour, usd FROM fx_rates ORDER BY token, ts_hour"):
            self.rates.setdefault(tok, []).append((h, usd))

    def usd(self, token: str | None, ts: int) -> float | None:
        import bisect

        pts = self.rates.get((token or chain.NATIVE).lower())
        if not pts:
            return None
        i = bisect.bisect_right([p[0] for p in pts], ts) - 1
        if i < 0:
            return None  # before the first known hour: unknown, not the first rate
        return pts[i][1]

    def to_usd(self, token: str | None, raw_amount: int, decimals: int, ts: int) -> float | None:
        rate = self.usd(token, ts)
        return None if rate is None else raw_amount / 10**decimals * rate


def main_fx(args) -> int:
    from pathlib import Path

    store = Store(Path(args.db) if args.db else None)
    res = load_fx(store, args.days)
    import json

    print(json.dumps(res, indent=1))
    return 0
