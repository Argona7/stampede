from helpers import TOKEN_A, TOKEN_B, TOKEN_C
from stampede.rotation import T, parse_window, sequences_for_wallet

W = 1800


def t(i, ts, token, side, tx=None, block=None):
    return T(i, tx or f"0x{i:064x}", block or ts, ts, token, side)


def test_parse_window():
    assert parse_window("30m") == 1800 and parse_window("2h") == 7200 and parse_window("90") == 90


def test_no_prior_sell_means_no_sequence():
    assert sequences_for_wallet([t(1, 1000, TOKEN_B, "buy")], W) == []
    assert sequences_for_wallet([t(1, 1000, TOKEN_A, "sell"), t(2, 1000 + W + 1, TOKEN_B, "buy")], W) == []


def test_window_boundary_inclusive():
    seqs = sequences_for_wallet([t(1, 1000, TOKEN_A, "sell"), t(2, 1000 + W, TOKEN_B, "buy")], W)
    assert len(seqs) == 1 and seqs[0]["grade"] == "clean" and seqs[0]["gap_s"] == W


def test_clean_sequence():
    seqs = sequences_for_wallet([t(1, 1000, TOKEN_A, "sell"), t(2, 1300, TOKEN_B, "buy")], W)
    assert [(s["sell_token"], s["buy_token"], s["grade"], s["gap_s"]) for s in seqs] == [(TOKEN_A, TOKEN_B, "clean", 300)]


def test_same_token_reentry_is_not_a_rotation():
    assert sequences_for_wallet([t(1, 1000, TOKEN_A, "sell"), t(2, 1300, TOKEN_A, "buy")], W) == []


def test_two_tokens_sold_before_buy_is_ambiguous():
    seqs = sequences_for_wallet([t(1, 1000, TOKEN_A, "sell"), t(2, 1100, TOKEN_C, "sell"), t(3, 1300, TOKEN_B, "buy")], W)
    assert sorted(s["sell_token"] for s in seqs) == sorted([TOKEN_A, TOKEN_C])
    assert all(s["grade"] == "ambiguous" for s in seqs)
    assert all(s["candidates"] for s in seqs)


def test_other_buy_in_between_makes_it_ambiguous_but_first_destination_clean():
    seqs = sequences_for_wallet([t(1, 1000, TOKEN_A, "sell"), t(2, 1100, TOKEN_B, "buy"), t(3, 1200, TOKEN_C, "buy")], W)
    by = {s["buy_token"]: s["grade"] for s in seqs}
    assert by == {TOKEN_B: "clean", TOKEN_C: "ambiguous"}


def test_direct_same_transaction():
    tx = "0x" + "ab" * 32
    seqs = sequences_for_wallet([t(1, 1000, TOKEN_A, "sell", tx=tx, block=5), t(2, 1000, TOKEN_B, "buy", tx=tx, block=5)], W)
    assert len(seqs) == 1 and seqs[0]["grade"] == "direct" and seqs[0]["gap_s"] == 0


def test_direct_with_two_tokens_sold_in_same_tx_is_ambiguous():
    tx = "0x" + "ab" * 32
    trades = [t(1, 1000, TOKEN_A, "sell", tx=tx, block=5), t(2, 1000, TOKEN_C, "sell", tx=tx, block=5), t(3, 1000, TOKEN_B, "buy", tx=tx, block=5)]
    seqs = sequences_for_wallet(trades, W)
    assert len(seqs) == 2 and all(s["grade"] == "ambiguous" for s in seqs)


def test_duplicate_trade_rows_do_not_double_count_wallets(tmp_path):
    """Edge weight counts distinct wallets: one wallet with two identical sequences -> weight 1."""
    from stampede.rotation import rotate
    from stampede.store import Store

    s = Store(tmp_path / "r.sqlite")
    rows = [
        ("0x1", 1, 1000, 1, TOKEN_A, "0xw1", "sell", "1", None, "0", "curve", "[]", "transfer_net", "[]"),
        ("0x2", 2, 1100, 1, TOKEN_B, "0xw1", "buy", "1", None, "0", "curve", "[]", "transfer_net", "[]"),
        ("0x3", 3, 1200, 1, TOKEN_B, "0xw1", "buy", "1", None, "0", "curve", "[]", "transfer_net", "[]"),
    ]
    s.db.executemany("INSERT INTO trades(tx_hash,block,ts,ts_exact,token,wallet,side,token_amount,quote_token,quote_amount,venue,swap_logs,attribution,flags) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    s.commit()
    res = rotate(s, W)
    e = s.db.execute("SELECT wallets_main, sequences FROM edges WHERE from_token=? AND to_token=?", (TOKEN_A, TOKEN_B)).fetchone()
    assert e == (1, 2) and res["edges"] == 1
