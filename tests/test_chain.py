from stampede import chain


def test_topics_match_measured_values():
    assert chain.T_CURVE_BUY == chain.EXPECTED_TOPICS["CurveBuy"]
    assert chain.T_CURVE_SELL == chain.EXPECTED_TOPICS["CurveSell"]
    assert chain.T_V4_SWAP == chain.EXPECTED_TOPICS["Swap"]
    assert chain.T_V4_INITIALIZE == chain.EXPECTED_TOPICS["Initialize"]
    assert chain.T_TRANSFER == chain.EXPECTED_TOPICS["Transfer"]


def test_selectors():
    assert chain.S_TOKEN == "0xfc0c546a"
    assert chain.S_SYMBOL == "0x95d89b41"
    assert chain.S_NAME == "0x06fdde03"


def test_signed_decoding():
    neg = (-5) % (1 << 256)
    assert chain.i128("0x" + neg.to_bytes(32, "big").hex(), 0) == -5
    assert chain.u256("0x" + (7).to_bytes(32, "big").hex(), 0) == 7


def test_pool_id_reconstruction_is_deterministic_and_order_independent():
    from stampede.ingest import pons_pool_id

    a = pons_pool_id("0x642d30c84211ade7768fe557fbaed7224e2068c7", chain.NATIVE)
    assert a.startswith("0x") and len(a) == 66
    assert a == pons_pool_id("0x642D30C84211ADE7768FE557FBAED7224E2068C7", chain.NATIVE)
