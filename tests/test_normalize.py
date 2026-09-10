from helpers import CURVE_A, POOL_B, ROUTER, TOKEN_A, TOKEN_B, WALLET_1, WALLET_2, PM, curve_buy, curve_sell, make_ctx, transfer, v4_swap
from stampede import chain
from stampede.normalize import Interp, trades_from_tx
from stampede.store import Store

TX = "0x" + "11" * 32


def test_transfer_only_is_not_a_trade():
    ctx = make_ctx()
    logs = [transfer(TOKEN_A, WALLET_2, WALLET_1, 10**18, 0)]
    trades, notes = trades_from_tx(TX, 100, logs, ctx)
    assert trades == [] and notes == []


def test_direct_curve_buy_attributes_to_recipient():
    ctx = make_ctx()
    logs = [transfer(TOKEN_A, CURVE_A, WALLET_1, 5 * 10**18, 0), curve_buy(CURVE_A, WALLET_1, WALLET_1, 10**16, 5 * 10**18, 1)]
    trades, notes = trades_from_tx(TX, 100, logs, ctx)
    assert len(trades) == 1
    t = trades[0]
    assert (t.wallet, t.side, t.token, t.token_amount, t.quote_amount, t.venue) == (WALLET_1, "buy", TOKEN_A, 5 * 10**18, 10**16, "curve")
    assert notes == []


def test_router_is_never_the_wallet():
    """Sell through the PONS router: event topics name the router, transfers name the wallet."""
    ctx = make_ctx()
    logs = [
        transfer(TOKEN_A, WALLET_1, ROUTER, 3 * 10**18, 0),
        transfer(TOKEN_A, ROUTER, CURVE_A, 3 * 10**18, 1),
        curve_sell(CURVE_A, ROUTER, ROUTER, 3 * 10**18, 2 * 10**15, 2),
    ]
    trades, notes = trades_from_tx(TX, 100, logs, ctx)
    assert [(t.wallet, t.side) for t in trades] == [(WALLET_1, "sell")]
    assert all(t.wallet != ROUTER for t in trades)


def test_unknown_passthrough_contract_is_excluded_by_net_zero():
    """An unlisted aggregator contract that forwards everything nets to zero and is not a buyer."""
    ctx = make_ctx()
    agg = "0x00000000000000000000000000000000000a66e6"
    logs = [
        transfer(TOKEN_A, CURVE_A, agg, 7 * 10**18, 0),
        transfer(TOKEN_A, agg, WALLET_1, 7 * 10**18, 1),
        curve_buy(CURVE_A, agg, agg, 10**16, 7 * 10**18, 2),
    ]
    trades, _ = trades_from_tx(TX, 100, logs, ctx)
    assert [(t.wallet, t.side) for t in trades] == [(WALLET_1, "buy")]
    assert ctx.passthrough[agg] == 1


def test_multi_hop_collapses_to_one_sell_and_one_buy():
    """sell A on the curve and buy B in the v4 pool inside one transaction -> two trades, same wallet."""
    ctx = make_ctx()
    logs = [
        transfer(TOKEN_A, WALLET_1, ROUTER, 10**18, 0),
        transfer(TOKEN_A, ROUTER, CURVE_A, 10**18, 1),
        curve_sell(CURVE_A, ROUTER, ROUTER, 10**18, 4 * 10**15, 2),
        v4_swap(POOL_B, ROUTER, -(4 * 10**15), 2 * 10**18, 3),
        transfer(TOKEN_B, PM, WALLET_1, 2 * 10**18, 4),
    ]
    trades, notes = trades_from_tx(TX, 100, logs, ctx)
    got = sorted((t.token, t.side, t.wallet, t.venue) for t in trades)
    assert got == [(TOKEN_A, "sell", WALLET_1, "curve"), (TOKEN_B, "buy", WALLET_1, "v4")]
    assert notes == []


def test_ambiguous_split_recipients_produce_no_trade():
    ctx = make_ctx()
    logs = [
        transfer(TOKEN_A, CURVE_A, WALLET_1, 5 * 10**18, 0),
        transfer(TOKEN_A, CURVE_A, WALLET_2, 5 * 10**18, 1),
        curve_buy(CURVE_A, WALLET_1, WALLET_1, 10**16, 10 * 10**18, 2),
    ]
    trades, notes = trades_from_tx(TX, 100, logs, ctx)
    assert trades == []
    assert [n[1] for n in notes] == ["ambiguous_recipients"]


def test_dominant_recipient_is_kept_and_flagged():
    ctx = make_ctx()
    logs = [
        transfer(TOKEN_A, CURVE_A, WALLET_1, 95 * 10**18, 0),
        transfer(TOKEN_A, CURVE_A, WALLET_2, 5 * 10**18, 1),
        curve_buy(CURVE_A, WALLET_1, WALLET_1, 10**16, 100 * 10**18, 2),
    ]
    trades, notes = trades_from_tx(TX, 100, logs, ctx)
    assert [(t.wallet, t.side) for t in trades] == [(WALLET_1, "buy")]
    assert any(f.startswith("minor_buy_recipients") for f in trades[0].flags)


def test_swap_without_transfer_is_noted_not_traded():
    ctx = make_ctx()
    logs = [curve_buy(CURVE_A, WALLET_1, WALLET_1, 10**16, 5 * 10**18, 0)]
    trades, notes = trades_from_tx(TX, 100, logs, ctx)
    assert trades == [] and notes == [(TOKEN_A, "swap_without_transfer", "")]


def test_non_universe_pool_is_skipped_with_note():
    ctx = make_ctx()
    logs = [v4_swap("0x" + "ee" * 32, ROUTER, -1, 1, 0)]
    trades, notes = trades_from_tx(TX, 100, logs, ctx)
    assert trades == [] and notes[0][1] == "non_universe_pool"


def test_store_deduplicates_logs_by_tx_and_index(tmp_path):
    s = Store(tmp_path / "t.sqlite")
    row = (TX, 3, 100, TOKEN_A, chain.T_TRANSFER, None, None, None, "0x", "transfer", "test")
    s.insert_logs([row, row])
    s.insert_logs([row])
    s.commit()
    assert s.db.execute("SELECT COUNT(*) FROM logs").fetchone()[0] == 1


def test_interpolation_marks_exact_and_estimated():
    it = Interp({100: 1000, 200: 1010})
    assert it(100) == (1000, 1)
    assert it(150) == (1005, 0)
    assert it(200) == (1010, 1)
    assert it(300) == (1010, 0)  # clamped, marked as not exact
