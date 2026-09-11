"""Robinhood Chain constants used by STAMPEDE.

Addresses were verified on-chain in the hoodsnipe project (2026-09-03/04) and are re-verified by
`stampede probe` (eth_chainId, live event counts per emitter). Topic hashes are recomputed from the
signatures in tests/test_chain.py so a typo cannot survive.
"""
from __future__ import annotations

from eth_hash.auto import keccak

CHAIN_ID = 4663
PUBLIC_RPC = "https://rpc.mainnet.chain.robinhood.com"
ALCHEMY_HTTP = "https://robinhood-mainnet.g.alchemy.com/v2/{key}"
ALCHEMY_WS = "wss://robinhood-mainnet.g.alchemy.com/v2/{key}"
HYPERSYNC_URL = "https://robinhood.hypersync.xyz"
EXPLORER = "https://robinhoodchain.blockscout.com"
GECKO_NETWORK = "robinhood"

# --- PONS v2 launchpad ---
PONS_V2_FACTORY = "0x7ed598bcef8bd9edd8c97a195c6d13f40801ec7e"
PONS_V2_LAUNCH_AND_BUY = "0xe33e9e479df8802cb0866d5d05258bec4cf62948"
PONS_ROUTER = "0x65050a9b7e5075a2ba5ced7b1b64ee66262c40dc"
PONS_V2_HOOK = "0xe5e702641ea86f4ae6cc3cdaed2b886f976be044"  # V2MemeHook on graduated pools
GASLITE_DROP = "0xe68d0bbc023de3febda04f413db23ce9c5ea1934"

# --- Uniswap v4 / periphery ---
V4_POOL_MANAGER = "0x8366a39cc670b4001a1121b8f6a443a643e40951"
V4_STATE_VIEW = "0xf3334192d15450cdd385c8b70e03f9a6bd9e673b"
UNIVERSAL_ROUTER = "0x8876789976decbfcbbbe364623c63652db8c0904"
PERMIT2 = "0x000000000022d473030f116ddee9f6b43ac78ba3"
WETH = "0x0bd7d308f8e1639fab988df18a8011f41eacad73"
NATIVE = "0x0000000000000000000000000000000000000000"

TOKEN_DECIMALS = 18
TOKEN_SUPPLY = 1_000_000_000

# Addresses that are never a "wallet": they hold tokens only transiently inside a transaction.
KNOWN_INFRA: dict[str, str] = {
    NATIVE: "zero",
    PONS_V2_FACTORY: "pons_factory",
    PONS_V2_LAUNCH_AND_BUY: "pons_launch_and_buy",
    PONS_ROUTER: "pons_router",
    PONS_V2_HOOK: "pons_v2_hook",
    V4_POOL_MANAGER: "v4_pool_manager",
    UNIVERSAL_ROUTER: "universal_router",
    PERMIT2: "permit2",
    WETH: "weth",
    GASLITE_DROP: "gaslite_drop",
}

# --- event signatures ---
SIG_CURVE_BUY = "CurveBuy(address,address,uint256,uint256,uint256,uint256)"
SIG_CURVE_SELL = "CurveSell(address,address,uint256,uint256,uint256,uint256)"
SIG_CURVE_COMPLETED = "CurveCompleted(address,uint256,uint256)"
SIG_TOKEN_LAUNCHED = "TokenLaunched(address,address,address,address,uint256,uint256)"
SIG_V4_SWAP = "Swap(bytes32,address,int128,int128,uint160,uint128,int24,uint24)"
SIG_V4_INITIALIZE = "Initialize(bytes32,address,address,uint24,int24,address,uint160,int24)"
SIG_TRANSFER = "Transfer(address,address,uint256)"
# deployed PonsV2BondingCurve / factory / hook events (verified against the Blockscout-verified sources and live logs, 2026-09-11)
SIG_SNIPE_TAX = "SnipeTaxCharged(address,uint256)"  # recipient indexed; amount is already included in CurveBuy.fee
SIG_CURVE_REFUND = "CurveBuyRefunded(address,uint256)"  # buyer indexed; the clamped buy at graduation refunds the remainder
SIG_FEES_SWEPT = "FeesSwept(uint256,uint256,uint256)"  # protocol, buyback, creator amounts leave trackedQuote
SIG_BUYBACK_LOCKED = "BuybackLocked(uint256,uint256)"  # quoteSpent stays in the reserve, tokensLocked leave trackedTokens
SIG_LAUNCH_SWEPT = "LaunchSwept(address,uint256,uint256)"  # factory: token indexed, quoteOut, tokenOut (graduation phase 1)
SIG_POOL_GRADUATED = "PoolGraduated(address,uint256,uint256,uint256)"  # factory: token indexed, positionId, tokenAmount, pairTokenAmount (phase 2)
SIG_HOOK_FEE = "HookFeeCollected(bytes32,address,uint256,uint256)"  # hook: poolId indexed, currency, feeAmount, taxAmount (post-graduation swaps)


def topic(sig: str) -> str:
    return "0x" + keccak(sig.encode()).hex()


def selector(sig: str) -> str:
    return "0x" + keccak(sig.encode())[:4].hex()


T_CURVE_BUY = topic(SIG_CURVE_BUY)
T_CURVE_SELL = topic(SIG_CURVE_SELL)
T_CURVE_COMPLETED = topic(SIG_CURVE_COMPLETED)
T_TOKEN_LAUNCHED = topic(SIG_TOKEN_LAUNCHED)
T_V4_SWAP = topic(SIG_V4_SWAP)
T_V4_INITIALIZE = topic(SIG_V4_INITIALIZE)
T_TRANSFER = topic(SIG_TRANSFER)
T_SNIPE_TAX = topic(SIG_SNIPE_TAX)
T_CURVE_REFUND = topic(SIG_CURVE_REFUND)
T_FEES_SWEPT = topic(SIG_FEES_SWEPT)
T_BUYBACK_LOCKED = topic(SIG_BUYBACK_LOCKED)
T_LAUNCH_SWEPT = topic(SIG_LAUNCH_SWEPT)
T_POOL_GRADUATED = topic(SIG_POOL_GRADUATED)
T_HOOK_FEE = topic(SIG_HOOK_FEE)

# Values measured on-chain (2026-09-10 / 2026-09-11); tests assert topic() reproduces them.
EXPECTED_TOPICS = {
    "CurveBuy": "0xec36bf571f136799e8dc0b0b8bea4b04d8bd3d43de838aab0d5fc21d4cbfc455",
    "CurveSell": "0x8113d738abdcb6b38357e9d53a54a7157861a09031b453651f0fe7fe151f59df",
    "Swap": "0x40e9cecb9f5f1f1c5b9c97dec2917b7ee92e57ba5563708daca94dd84ad7112f",
    "Initialize": "0xdd466e674ea557f56295e2d0218a125ea4b4f0f6f3307b95f85e6110838d6438",
    "Transfer": "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef",
    "TokenLaunched": "0x8d4aad4953d0ca700d468f3753aa14432d1b35b43ec6409f051fb6aa43a89607",
    "SnipeTaxCharged": "0x3bc39a5562b28f5fe8f36cecabfbaa12bb969acf05717994709225fc412a9934",
    "CurveBuyRefunded": "0xa69e8258ccc7b9bbb70ab953fc2d1062b4ee28b8ca827534097e1732e87b0262",
    "CurveCompleted": "0xf8d37a90738ae063b8b8058b66f5880cf3cf7ab0c5d4fa78219696591dfbfb67",
    "LaunchSwept": "0xcdb72f157fd3666758a6ce201387ffb52038c7562e4fff352828da1096c4b6b4",
    "PoolGraduated": "0x0a44ef75df69c534f43cd6c1aa3ef8983065fe5fe79ef9e79f6494e6f258c259",
    "HookFeeCollected": "0xc532c43b3423e14ef72748f1c8291238829ca0af8ba9b67975ad1483485a4b4d",
}

SWAP_TOPICS = [T_CURVE_BUY, T_CURVE_SELL, T_V4_SWAP]
# PoolRegistered on the PONS V2MemeHook fires when a graduated pool is created; payload = [memecoin, quoteToken, creator].
# topic0 as documented by Bitquery's Pons API guide (2026-09); the event signature itself is not published.
T_POOL_REGISTERED = "0x01bf263a1db1652580721573296e1a1fa70b3d4c87f61d02a69c4e1109d2d573"
LIFECYCLE_TOPICS = [T_TOKEN_LAUNCHED, T_CURVE_COMPLETED, T_POOL_REGISTERED, T_LAUNCH_SWEPT, T_POOL_GRADUATED]
# curve-side events that carry money but are not trades: fee breakdown and reserve changes
CURVE_SIDE_TOPICS = [T_SNIPE_TAX, T_CURVE_REFUND, T_FEES_SWEPT, T_BUYBACK_LOCKED, T_HOOK_FEE]
PONS_V2_LOCKER = "0x267444d099b10fb5ed7c3cc7b7c767adca574952"  # holds the permanently locked supply of graduated tokens
PONS_V2_LAUNCH_DEPLOYER = "0x3711cea4feade896c913c68f01eda97cb06d1a42"
PONS_V2_FEE_ESCROW = "0xd3afeb2a57f70ef218aa82451c51b2fb0416ac9e"
KIND_BY_TOPIC = {
    T_CURVE_BUY: "curve_buy",
    T_CURVE_SELL: "curve_sell",
    T_V4_SWAP: "v4_swap",
    T_TRANSFER: "transfer",
    T_TOKEN_LAUNCHED: "token_launched",
    T_CURVE_COMPLETED: "curve_completed",
    T_POOL_REGISTERED: "pool_registered",
    T_SNIPE_TAX: "snipe_tax",
    T_CURVE_REFUND: "curve_refund",
    T_FEES_SWEPT: "fees_swept",
    T_BUYBACK_LOCKED: "buyback_locked",
    T_LAUNCH_SWEPT: "launch_swept",
    T_POOL_GRADUATED: "pool_graduated",
    T_HOOK_FEE: "hook_fee",
}

# --- PONS v2 curve economics (live launch config 0 read from the factory on 2026-09-11; every curve snapshots its own
# values at launch, so a simulator reads them per curve and uses these only as defaults / for tests) ---
CURVE_FEE_BPS = 100  # 1% of quote-in on buys, of gross quote-out on sells
CURVE_PHANTOM_QUOTE_WEI = 1_680_000_000_000_000_000  # 1.68 ETH virtual quote reserve
CURVE_GRADUATION_THRESHOLD_WEI = 4_200_000_000_000_000_000  # 4.2 ETH real quote reserve at graduation
CURVE_SUPPLY_WEI = TOKEN_SUPPLY * 10**TOKEN_DECIMALS
SNIPE_TAX_START_BPS = 9900
SNIPE_TAX_SECONDS = 3  # 9900 >> (elapsed*14 // 3): 99% at t=0 s, 6.18% at 1 s, 0.19% at 2 s, 0 from 3 s
HOOK_FEE_BPS = 100  # post-graduation swaps: 1% of the unspecified currency plus the creator tax

# --- selectors for eth_call ---
S_TOKEN = selector("token()")
S_PAIR_TOKEN = selector("pairToken()")
S_SYMBOL = selector("symbol()")
S_NAME = selector("name()")
S_GET_LAUNCHED_TOKEN = selector("getLaunchedToken(address)")


# --- small ABI helpers (all words are 32 bytes) ---
def word(data_hex: str, i: int) -> str:
    d = data_hex[2:] if data_hex.startswith("0x") else data_hex
    return d[64 * i : 64 * (i + 1)]


def u256(data_hex: str, i: int) -> int:
    w = word(data_hex, i)
    return int(w, 16) if w else 0


def i128(data_hex: str, i: int) -> int:
    v = u256(data_hex, i)
    return v - (1 << 256) if v >= (1 << 255) else v


def i24(data_hex: str, i: int) -> int:
    v = u256(data_hex, i)
    return v - (1 << 256) if v >= (1 << 255) else v


def addr_from_topic(t: str) -> str:
    return ("0x" + t[-40:]).lower()


def addr_from_word(data_hex: str, i: int) -> str:
    return ("0x" + word(data_hex, i)[-40:]).lower()


def dec_string(ret: str) -> str:
    """ABI string or bytes32 return value -> str."""
    if not ret or ret == "0x":
        return ""
    raw = bytes.fromhex(ret[2:])
    if len(raw) >= 64:
        try:
            off = int.from_bytes(raw[:32], "big")
            ln = int.from_bytes(raw[off : off + 32], "big")
            return raw[off + 32 : off + 32 + ln].decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            pass
    return raw[:32].rstrip(b"\x00").decode("utf-8", "replace")


def explorer_tx(tx_hash: str) -> str:
    return f"{EXPLORER}/tx/{tx_hash}"


def explorer_address(addr: str) -> str:
    return f"{EXPLORER}/address/{addr}"
