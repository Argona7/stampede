"""SQLite storage. Raw logs are kept verbatim; every derived row points back to them."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable

from .env import db_path

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, started REAL, finished REAL, params TEXT, stats TEXT);
CREATE TABLE IF NOT EXISTS blocks (number INTEGER PRIMARY KEY, timestamp INTEGER, exact INTEGER);
CREATE TABLE IF NOT EXISTS logs (
  tx_hash TEXT, log_index INTEGER, block INTEGER, address TEXT, topic0 TEXT, topic1 TEXT, topic2 TEXT, topic3 TEXT,
  data TEXT, kind TEXT, source TEXT, PRIMARY KEY (tx_hash, log_index));
CREATE INDEX IF NOT EXISTS logs_block ON logs(block);
CREATE INDEX IF NOT EXISTS logs_kind ON logs(kind);
CREATE TABLE IF NOT EXISTS txs (hash TEXT PRIMARY KEY, block INTEGER, "from" TEXT, "to" TEXT, source TEXT);
CREATE TABLE IF NOT EXISTS curves (curve TEXT PRIMARY KEY, token TEXT, pair_token TEXT, resolved_via TEXT);
CREATE TABLE IF NOT EXISTS pools (
  pool_id TEXT PRIMARY KEY, currency0 TEXT, currency1 TEXT, fee INTEGER, tick_spacing INTEGER, hooks TEXT,
  init_block INTEGER, init_tx TEXT);
CREATE TABLE IF NOT EXISTS tokens (
  address TEXT PRIMARY KEY, symbol TEXT, name TEXT, source TEXT, curve TEXT, first_seen_block INTEGER);
CREATE TABLE IF NOT EXISTS infra (address TEXT PRIMARY KEY, kind TEXT, source TEXT);
CREATE TABLE IF NOT EXISTS trades (
  id INTEGER PRIMARY KEY AUTOINCREMENT, tx_hash TEXT, block INTEGER, ts INTEGER, ts_exact INTEGER,
  token TEXT, wallet TEXT, side TEXT, token_amount TEXT, quote_token TEXT, quote_amount TEXT, venue TEXT,
  swap_logs TEXT, attribution TEXT, flags TEXT, UNIQUE (tx_hash, token, wallet));
CREATE INDEX IF NOT EXISTS trades_wallet ON trades(wallet, ts);
CREATE INDEX IF NOT EXISTS trades_token ON trades(token, ts);
CREATE TABLE IF NOT EXISTS tx_notes (tx_hash TEXT, token TEXT, note TEXT, detail TEXT, PRIMARY KEY (tx_hash, token, note));
CREATE TABLE IF NOT EXISTS sequences (
  id INTEGER PRIMARY KEY AUTOINCREMENT, window_s INTEGER, wallet TEXT, sell_token TEXT, buy_token TEXT,
  sell_trade INTEGER, buy_trade INTEGER, sell_ts INTEGER, buy_ts INTEGER, gap_s INTEGER, grade TEXT, candidates TEXT,
  UNIQUE (window_s, wallet, sell_trade, buy_trade));
CREATE INDEX IF NOT EXISTS seq_edge ON sequences(window_s, sell_token, buy_token);
CREATE INDEX IF NOT EXISTS seq_buy ON sequences(window_s, buy_trade);
CREATE INDEX IF NOT EXISTS seq_wallet ON sequences(window_s, wallet);
CREATE TABLE IF NOT EXISTS edges (
  window_s INTEGER, from_token TEXT, to_token TEXT, wallets_main INTEGER, wallets_direct INTEGER, wallets_clean INTEGER,
  wallets_ambiguous INTEGER, sequences INTEGER, first_ts INTEGER, last_ts INTEGER,
  PRIMARY KEY (window_s, from_token, to_token));
CREATE TABLE IF NOT EXISTS wallets (address TEXT PRIMARY KEY, is_contract INTEGER, trades INTEGER);
CREATE TABLE IF NOT EXISTS quotes (address TEXT PRIMARY KEY, symbol TEXT, decimals INTEGER);
CREATE TABLE IF NOT EXISTS rejected_tokens (address TEXT PRIMARY KEY, checked_at REAL);
CREATE TABLE IF NOT EXISTS reports (name TEXT PRIMARY KEY, created REAL, body TEXT);
"""


class Store:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path))
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(SCHEMA)

    # ---- meta / runs ----
    def set_meta(self, key: str, value: Any) -> None:
        self.db.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (key, json.dumps(value)))
        self.db.commit()

    def get_meta(self, key: str, default: Any = None) -> Any:
        r = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(r[0]) if r else default

    def start_run(self, kind: str, params: dict) -> int:
        cur = self.db.execute("INSERT INTO runs(kind,started,params) VALUES(?,?,?)", (kind, time.time(), json.dumps(params)))
        self.db.commit()
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, stats: dict) -> None:
        self.db.execute("UPDATE runs SET finished=?, stats=? WHERE id=?", (time.time(), json.dumps(stats, default=str), run_id))
        self.db.commit()

    def last_run(self, kind: str) -> dict | None:
        r = self.db.execute("SELECT id, started, finished, params, stats FROM runs WHERE kind=? AND finished IS NOT NULL ORDER BY id DESC LIMIT 1", (kind,)).fetchone()
        if not r:
            return None
        return {"id": r[0], "started": r[1], "finished": r[2], "params": json.loads(r[3] or "{}"), "stats": json.loads(r[4] or "{}")}

    # ---- raw ----
    def insert_logs(self, rows: Iterable[tuple]) -> int:
        cur = self.db.executemany("INSERT OR IGNORE INTO logs(tx_hash,log_index,block,address,topic0,topic1,topic2,topic3,data,kind,source) VALUES(?,?,?,?,?,?,?,?,?,?,?)", rows)
        return cur.rowcount

    def insert_txs(self, rows: Iterable[tuple]) -> None:
        self.db.executemany('INSERT OR IGNORE INTO txs(hash,block,"from","to",source) VALUES(?,?,?,?,?)', rows)

    def upsert_blocks(self, rows: Iterable[tuple]) -> None:
        # exact timestamps always win over interpolated ones
        self.db.executemany(
            "INSERT INTO blocks(number,timestamp,exact) VALUES(?,?,?) ON CONFLICT(number) DO UPDATE SET timestamp=excluded.timestamp, exact=excluded.exact WHERE excluded.exact>=blocks.exact",
            rows,
        )

    def upsert_curves(self, rows: Iterable[tuple]) -> None:
        self.db.executemany("INSERT OR REPLACE INTO curves(curve,token,pair_token,resolved_via) VALUES(?,?,?,?)", rows)

    def upsert_pools(self, rows: Iterable[tuple]) -> None:
        self.db.executemany("INSERT OR REPLACE INTO pools(pool_id,currency0,currency1,fee,tick_spacing,hooks,init_block,init_tx) VALUES(?,?,?,?,?,?,?,?)", rows)

    def upsert_tokens(self, rows: Iterable[tuple]) -> None:
        self.db.executemany("INSERT OR IGNORE INTO tokens(address,symbol,name,source,curve,first_seen_block) VALUES(?,?,?,?,?,?)", rows)

    def update_token_meta(self, address: str, symbol: str, name: str) -> None:
        self.db.execute("UPDATE tokens SET symbol=?, name=? WHERE address=?", (symbol, name, address))

    def upsert_infra(self, rows: Iterable[tuple]) -> None:
        self.db.executemany("INSERT OR IGNORE INTO infra(address,kind,source) VALUES(?,?,?)", rows)

    def commit(self) -> None:
        self.db.commit()

    # ---- lookups ----
    def curves(self) -> dict[str, dict]:
        return {r[0]: {"token": r[1], "pair_token": r[2]} for r in self.db.execute("SELECT curve,token,pair_token FROM curves")}

    def pools(self) -> dict[str, dict]:
        return {r[0]: {"currency0": r[1], "currency1": r[2], "fee": r[3], "tick_spacing": r[4], "hooks": r[5]} for r in self.db.execute("SELECT pool_id,currency0,currency1,fee,tick_spacing,hooks FROM pools")}

    def tokens(self) -> dict[str, dict]:
        return {r[0]: {"symbol": r[1], "name": r[2], "source": r[3], "curve": r[4]} for r in self.db.execute("SELECT address,symbol,name,source,curve FROM tokens")}

    def infra(self) -> dict[str, str]:
        return {r[0]: r[1] for r in self.db.execute("SELECT address,kind FROM infra")}

    def quotes(self) -> dict[str, dict]:
        out = {r[0]: {"symbol": r[1], "decimals": r[2]} for r in self.db.execute("SELECT address,symbol,decimals FROM quotes")}
        out.setdefault("0x0000000000000000000000000000000000000000", {"symbol": "ETH", "decimals": 18})
        return out

    def block_range(self) -> tuple[int, int] | None:
        r = self.db.execute("SELECT MIN(block), MAX(block) FROM logs").fetchone()
        return (r[0], r[1]) if r and r[0] is not None else None

    def blocks(self) -> dict[int, tuple[int, int]]:
        return {r[0]: (r[1], r[2]) for r in self.db.execute("SELECT number,timestamp,exact FROM blocks")}

    def save_report(self, name: str, body: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO reports(name,created,body) VALUES(?,?,?)", (name, time.time(), body))
        self.db.commit()

    def close(self) -> None:
        self.db.close()
