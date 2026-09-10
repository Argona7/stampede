"""Synthetic log builders shaped exactly like rows coming out of the store (see normalize.iter_tx_groups)."""
from __future__ import annotations

from stampede import chain

TOKEN_A = "0x00000000000000000000000000000000000000a1"
TOKEN_B = "0x00000000000000000000000000000000000000b2"
TOKEN_C = "0x00000000000000000000000000000000000000c3"
CURVE_A = "0x000000000000000000000000000000000000ca01"
CURVE_C = "0x000000000000000000000000000000000000cc03"
POOL_B = "0x" + "b2" * 32
WALLET_1 = "0x0000000000000000000000000000000000001111"
WALLET_2 = "0x0000000000000000000000000000000000002222"
ROUTER = chain.PONS_ROUTER
PM = chain.V4_POOL_MANAGER


def topic_addr(a: str) -> str:
    return "0x" + a[2:].rjust(64, "0")


def w(v: int) -> str:
    return (v % (1 << 256)).to_bytes(32, "big").hex()


def transfer(token: str, frm: str, to: str, value: int, li: int) -> dict:
    return {"log_index": li, "address": token, "topic0": chain.T_TRANSFER, "topic1": topic_addr(frm), "topic2": topic_addr(to), "topic3": None, "data": "0x" + w(value), "kind": "transfer"}


def curve_buy(curve: str, buyer: str, recipient: str, quote_in: int, tokens_out: int, li: int) -> dict:
    return {"log_index": li, "address": curve, "topic0": chain.T_CURVE_BUY, "topic1": topic_addr(buyer), "topic2": topic_addr(recipient), "topic3": None, "data": "0x" + w(quote_in) + w(tokens_out) + w(0) + w(0), "kind": "curve_buy"}


def curve_sell(curve: str, seller: str, recipient: str, tokens_in: int, quote_out: int, li: int) -> dict:
    return {"log_index": li, "address": curve, "topic0": chain.T_CURVE_SELL, "topic1": topic_addr(seller), "topic2": topic_addr(recipient), "topic3": None, "data": "0x" + w(tokens_in) + w(quote_out) + w(0) + w(0), "kind": "curve_sell"}


def v4_swap(pool_id: str, sender: str, amount0: int, amount1: int, li: int) -> dict:
    return {"log_index": li, "address": PM, "topic0": chain.T_V4_SWAP, "topic1": pool_id, "topic2": topic_addr(sender), "topic3": None, "data": "0x" + w(amount0) + w(amount1) + w(0) + w(0) + w(0) + w(0), "kind": "v4_swap"}


def make_ctx():
    from stampede.normalize import Context

    return Context(
        curves={CURVE_A: {"token": TOKEN_A, "pair_token": chain.NATIVE}, CURVE_C: {"token": TOKEN_C, "pair_token": chain.NATIVE}},
        pool_token={POOL_B: {"token": TOKEN_B, "quote": chain.NATIVE, "currency0": chain.NATIVE, "currency1": TOKEN_B}},
        universe={TOKEN_A, TOKEN_B, TOKEN_C},
        infra=set(chain.KNOWN_INFRA) | {CURVE_A, CURVE_C, chain.NATIVE},
    )
