"""X (Twitter) attention via twitterapi.io: mentions of the ticker/contract in the last hour and day, and
the account the creator declared at launch. Paid per request, so: cache 5 minutes, only for coins the radar
asks about, and a daily call budget. Counts are *observed mentions found by search*, not reach."""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from ..store import Store
from .market import bump

BASE = "https://api.twitterapi.io"
MAX_PAGES = 3  # 20 tweets per page -> at most 60 tweets counted per coin per refresh


class XMentions:
    def __init__(self, store: Store, api_key: str | None, daily_budget: int = 3000, timeout: float = 10.0):
        self.store = store
        self.key = api_key
        self.daily_budget = daily_budget
        self.timeout = timeout
        self.s = requests.Session()
        if api_key:
            self.s.headers.update({"x-api-key": api_key})

    @property
    def enabled(self) -> bool:
        return bool(self.key)

    def _cached(self, key: str, max_age_s: float) -> dict[str, Any] | None:
        r = self.store.db.execute("SELECT fetched_at, body FROM x_cache WHERE key=?", (key,)).fetchone()
        if r and time.time() - r[0] <= max_age_s:
            d = json.loads(r[1])
            d["cache_age_s"] = round(time.time() - r[0])
            return d
        return None

    def _put(self, key: str, body: dict[str, Any]) -> dict[str, Any]:
        body["fetched_at"] = time.time()
        self.store.db.execute("INSERT OR REPLACE INTO x_cache(key, fetched_at, body) VALUES(?,?,?)", (key, body["fetched_at"], json.dumps(body)))
        self.store.commit()
        body["cache_age_s"] = 0
        return body

    def _budget_ok(self) -> bool:
        r = self.store.db.execute("SELECT calls, day FROM api_budget WHERE name='twitterapi'").fetchone()
        return not (r and r[1] == time.strftime("%Y-%m-%d") and r[0] >= self.daily_budget)

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any] | None:
        if not self.enabled or not self._budget_ok():
            return None
        try:
            r = self.s.get(BASE + path, params=params, timeout=self.timeout)
            bump(self.store, "twitterapi")
            # commit right away: the budget row must not hold the SQLite write lock across the next page's HTTP call (the
            # engine's writer waited up to 46 s behind it in docs/ENGINE-PERF.md)
            self.store.commit()
            if r.status_code != 200:
                return None
            return r.json()
        except (requests.RequestException, ValueError):
            return None

    def mentions(self, symbol_raw: str, address: str, max_age_s: float = 300.0, allow_fetch: bool = True) -> dict[str, Any] | None:
        key = f"m:{address}"
        c = self._cached(key, max_age_s)
        if c is not None or not allow_fetch or not self.enabled:
            return c
        since = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%d_%H:%M:%S_UTC")
        sym = re.sub(r"[^A-Za-z0-9_]", "", symbol_raw or "")
        # cashtag, contract address, or the bare word only next to chain context: bare tickers collide with
        # ordinary words (a coin called TruffleHog otherwise "gets" every tweet about the OSINT tool)
        parts = [f'"${sym}"' if sym else None, address, f"({sym} (robinhood OR pons OR memecoin))" if sym and len(sym) >= 3 else None]
        query = "(" + " OR ".join(p for p in parts if p) + f") since:{since}"
        cursor = ""
        tweets: list[dict[str, Any]] = []
        truncated = False
        for _ in range(MAX_PAGES):
            d = self._get("/twitter/tweet/advanced_search", {"query": query, "queryType": "Latest", "cursor": cursor})
            if not d:
                break
            tweets.extend(d.get("tweets") or [])
            if d.get("has_next_page") and d.get("next_cursor"):
                cursor = d["next_cursor"]
                if len(tweets) >= MAX_PAGES * 20:
                    truncated = True
                    break
            else:
                break
        now = datetime.now(timezone.utc)
        n1h = n24h = 0
        top: list[dict[str, Any]] = []
        authors: set[str] = set()
        for t in tweets:
            ts = _parse_created(t.get("createdAt"))
            if ts is None:
                continue
            age = (now - ts).total_seconds()
            if age <= 86400:
                n24h += 1
            if age <= 3600:
                n1h += 1
            a = t.get("author") or {}
            authors.add(a.get("userName") or "")
            top.append({"url": t.get("url") or (f"https://x.com/i/status/{t.get('id')}" if t.get("id") else None), "author": a.get("userName"), "followers": a.get("followers"), "likes": t.get("likeCount"), "created_at": ts.isoformat(), "text": (t.get("text") or "")[:140]})
        top.sort(key=lambda x: -(x.get("likes") or 0))
        return self._put(key, {"query": query, "mentions_1h": n1h, "mentions_24h": n24h, "distinct_authors": len([a for a in authors if a]), "truncated": truncated, "top": top[:3], "counted_tweets": len(tweets)})

    def account(self, handle: str, max_age_s: float = 3600.0) -> dict[str, Any] | None:
        key = f"u:{handle.lower()}"
        c = self._cached(key, max_age_s)
        if c is not None or not self.enabled:
            return c
        d = self._get("/twitter/user/info", {"userName": handle})
        if not d:
            return None
        u = d.get("data") or d
        return self._put(key, {"handle": u.get("userName") or handle, "name": u.get("name"), "followers": u.get("followers"), "created_at": u.get("createdAt"), "verified": u.get("isBlueVerified"), "url": f"https://x.com/{u.get('userName') or handle}"})


def _parse_created(s: str | None) -> datetime | None:
    if not s:
        return None
    for fmt in ("%a %b %d %H:%M:%S %z %Y", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z"):
        try:
            return datetime.strptime(s.replace("Z", "+0000"), fmt)
        except ValueError:
            continue
    return None
