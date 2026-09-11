"""Fee, creator tax and snipe tax are carried from the curve events onto the trade rows (raw quote units)."""
from helpers import CURVE_A, TOKEN_A, WALLET_1, curve_buy, curve_sell, make_ctx, topic_addr, transfer, w
from stampede import chain
from stampede.normalize import TRADE_INSERT, trades_from_tx
from stampede.store import Store


def curve_buy_with_fees(curve, buyer, recipient, quote_in, tokens_out, fee, tax, li):
    l = curve_buy(curve, buyer, recipient, quote_in, tokens_out, li)
    l["data"] = "0x" + w(quote_in) + w(tokens_out) + w(fee) + w(tax)
    return l


def snipe_tax(curve, recipient, amount, li):
    return {"log_index": li, "address": curve, "topic0": chain.T_SNIPE_TAX, "topic1": topic_addr(recipient), "topic2": None, "topic3": None, "data": "0x" + w(amount), "kind": "snipe_tax"}


def test_buy_carries_fee_tax_and_snipe():
    # 0.23 ETH buy one second after launch: 1% base fee + 618 bps snipe tax, both inside CurveBuy.fee (measured on-chain 2026-09-11)
    quote_in = 230_000_000_000_000_000
    snipe = quote_in * 618 // 10_000
    fee = quote_in // 100 + snipe
    tax = 0
    logs = [
        transfer(TOKEN_A, CURVE_A, WALLET_1, 5_000_000 * 10**18, 1),
        snipe_tax(CURVE_A, WALLET_1, snipe, 2),
        curve_buy_with_fees(CURVE_A, WALLET_1, WALLET_1, quote_in, 5_000_000 * 10**18, fee, tax, 3),
    ]
    trades, notes = trades_from_tx("0xaa", 100, logs, make_ctx())
    assert len(trades) == 1 and not notes
    t = trades[0]
    assert (t.side, t.quote_amount, t.fee, t.tax, t.snipe) == ("buy", quote_in, fee, 0, snipe)
    assert t.fee - t.snipe == quote_in // 100  # the base fee is what remains after the snipe part


def test_sell_carries_fee_and_creator_tax_and_rows_persist():
    tokens_in = 1_000_000 * 10**18
    gross = 3_000_000_000_000_000
    fee, tax = gross // 100, gross // 400  # 1% fee, 25 bps creator tax
    l = curve_sell(CURVE_A, WALLET_1, WALLET_1, tokens_in, gross - fee - tax, 2)
    l["data"] = "0x" + w(tokens_in) + w(gross - fee - tax) + w(fee) + w(tax)
    logs = [transfer(TOKEN_A, WALLET_1, CURVE_A, tokens_in, 1), l]
    trades, _ = trades_from_tx("0xbb", 101, logs, make_ctx())
    assert len(trades) == 1
    t = trades[0]
    assert (t.side, t.fee, t.tax, t.snipe) == ("sell", fee, tax, 0)
    s = Store(":memory:")
    s.db.execute(TRADE_INSERT, t.row(1000, 1))
    row = s.db.execute("SELECT fee_raw, tax_raw, snipe_raw, quote_amount FROM trades").fetchone()
    assert row == (str(fee), str(tax), "0", str(gross - fee - tax))


def test_store_migration_adds_fee_columns_to_old_databases(tmp_path):
    import sqlite3

    p = tmp_path / "old.sqlite"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY AUTOINCREMENT, tx_hash TEXT, block INTEGER, ts INTEGER, ts_exact INTEGER, token TEXT, wallet TEXT, side TEXT, token_amount TEXT, quote_token TEXT, quote_amount TEXT, venue TEXT, swap_logs TEXT, attribution TEXT, flags TEXT, UNIQUE (tx_hash, token, wallet))")
    c.execute("INSERT INTO trades(tx_hash,block,ts,ts_exact,token,wallet,side,token_amount,quote_token,quote_amount,venue,swap_logs,attribution,flags) VALUES('0x1',1,1,1,'t','w','buy','1','q','1','curve','[]','transfer_net','[]')")
    c.commit()
    c.close()
    s = Store(p)
    cols = {r[1] for r in s.db.execute("PRAGMA table_info(trades)")}
    assert {"fee_raw", "tax_raw", "snipe_raw"} <= cols
    assert s.db.execute("SELECT fee_raw FROM trades").fetchone() == (None,)  # old rows: unknown, not zero
    s.close()
