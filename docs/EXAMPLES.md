# Verified examples

Sample: **PONS v2 sample, 60 min**, 2026-09-10 16:29:57 UTC → 2026-09-10 17:29:57 UTC, window 30 min. Each example is one wallet's observed `sell A → buy B`; timestamps below are exact block times. Open the links: the Blockscout *Token transfers* tab of the sell transaction shows the wallet sending A to a curve/router, the buy transaction shows the wallet receiving B.

What this shows: an address sold one PONS v2 token and later bought another inside the window. What it does not show: that the sale funded the purchase, who controls the address, coordination with other addresses, or where the price goes next.

## 1. MARIO (0x7a49…420c) → FLY (0xd07b…e12f) — direct, gap 0 s

- Wallet: [`0x700b3f0098a18f70d22cfccc29298a9a7ca327fe`](https://robinhoodchain.blockscout.com/address/0x700b3f0098a18f70d22cfccc29298a9a7ca327fe) — 7 trades in the sample; this edge has 24 wallets with direct/clean sequences.
- Sell: 1,000,000 MARIO for 0.729523 NVDA on `v4`, block 59554388, 2026-09-10 16:42:30 UTC — [tx 0x2a0e…c68b](https://robinhoodchain.blockscout.com/tx/0x2a0e6b2a65e67f0c910bdafdc1c5b841fd5049efb82b965dc45842b4cf11c68b)
- Buy: 1,368,555 FLY for 9.561 GOOGL on `v4`, block 59554388, 2026-09-10 16:42:30 UTC — [tx 0x2a0e…c68b](https://robinhoodchain.blockscout.com/tx/0x2a0e6b2a65e67f0c910bdafdc1c5b841fd5049efb82b965dc45842b4cf11c68b)
- Flags: minor_buy_recipients:2
- Same transaction: the wallet swapped A for B in one call. This is the strongest observation the map makes; it still says nothing about intent.

## 2. NATIPONS (0x563c…ec48) → PONSLAB (0xd8bb…bdb0) — direct, gap 0 s

- Wallet: [`0x7bb5fadc8a2a6d75fd0d2f2092e249dbea229268`](https://robinhoodchain.blockscout.com/address/0x7bb5fadc8a2a6d75fd0d2f2092e249dbea229268) — 8 trades in the sample; this edge has 15 wallets with direct/clean sequences.
- Sell: 464,814 NATIPONS for 0.005863 ETH on `curve`, block 59557348, 2026-09-10 16:47:31 UTC — [tx 0x0097…ad8c](https://robinhoodchain.blockscout.com/tx/0x00979215135988ffe4a586d2447889dd494a4c49a312bcfab1a89613ba82ad8c)
- Buy: 897,195 PONSLAB for 0.065528 NVDA on `curve`, block 59557348, 2026-09-10 16:47:31 UTC — [tx 0x0097…ad8c](https://robinhoodchain.blockscout.com/tx/0x00979215135988ffe4a586d2447889dd494a4c49a312bcfab1a89613ba82ad8c)
- Same transaction: the wallet swapped A for B in one call. This is the strongest observation the map makes; it still says nothing about intent.

## 3. TruffleHog (0x1a4e…e085) → LUNAR (0x6cdb…55b0) — clean, gap 849 s

- Wallet: [`0xcd754809f7dd445f45fe25381b20bd7d05ecc69e`](https://robinhoodchain.blockscout.com/address/0xcd754809f7dd445f45fe25381b20bd7d05ecc69e) — 3 trades in the sample; this edge has 124 wallets with direct/clean sequences.
- Sell: 15,667,729 TruffleHog for 0.124664 ETH on `curve`, block 59574012, 2026-09-10 17:15:31 UTC — [tx 0x7971…0285](https://robinhoodchain.blockscout.com/tx/0x7971141f10735b9c8c10360c6e2f5138955820aebbbce396aecb46dd7dc00285)
- Buy: 1,229,337 LUNAR for 0.02255 ETH on `curve`, block 59582426, 2026-09-10 17:29:40 UTC — [tx 0xb5a9…1c66](https://robinhoodchain.blockscout.com/tx/0xb5a9037e0c09ac4aba148ed9fecc7c87c73390352345ec9ba68a7ba02fe81c66)

## 4. EDEN (0x9420…c9b6) → AUTON (0x5e1e…5eab) — clean, gap 1274 s

- Wallet: [`0xc2504b09037e048e397cdde41b75d06766bbe597`](https://robinhoodchain.blockscout.com/address/0xc2504b09037e048e397cdde41b75d06766bbe597) — 14 trades in the sample; this edge has 66 wallets with direct/clean sequences.
- Sell: 229,231 EDEN for 0.003524 ETH on `v4`, block 59547833, 2026-09-10 16:31:27 UTC — [tx 0x5495…ee53](https://robinhoodchain.blockscout.com/tx/0x54957d58fe2e52a93a39fd9481f2350add5b9ac2eb5ee3a81ac1c49fe687ee53)
- Buy: 230,901 AUTON for 0.00297 ETH on `v4`, block 59560426, 2026-09-10 16:52:41 UTC — [tx 0x15cb…4ad2](https://robinhoodchain.blockscout.com/tx/0x15cba7a5060bce9f3d17264c3095357bf2e35710bd45931565a1eba5f5ce4ad2)

## 5. Piecoin (0x6c36…1a01) → TruffleHog (0x4ad5…7b08) — clean, gap 867 s

- Wallet: [`0xd60c7abcc6b15c26c525c0dd8828ffde32b07ab6`](https://robinhoodchain.blockscout.com/address/0xd60c7abcc6b15c26c525c0dd8828ffde32b07ab6) — 17 trades in the sample; this edge has 54 wallets with direct/clean sequences.
- Sell: 3,640,335 Piecoin for 22.848 USDG on `curve`, block 59570827, 2026-09-10 17:10:09 UTC — [tx 0x17da…a56d](https://robinhoodchain.blockscout.com/tx/0x17dacbaa69834e67fc52c26303dd9c85e9966e30b9d047388f2f60cd20a8a56d)
- Buy: 9,788,825 TruffleHog for 49.159 USDG on `curve`, block 59579393, 2026-09-10 17:24:35 UTC — [tx 0x20ed…4729](https://robinhoodchain.blockscout.com/tx/0x20ed0eef6f879b6b1004a8fdf44a6d99642dc380c0aa3604063e95ea9f554729)

## 6. EVERYTHING (0xabda…b22f) → MARIO (0xb89c…6819) — clean, gap 1580 s

- Wallet: [`0x513d07c35995abdbe2b8a6752ae754b03c70a1bf`](https://robinhoodchain.blockscout.com/address/0x513d07c35995abdbe2b8a6752ae754b03c70a1bf) — 4 trades in the sample; this edge has 43 wallets with direct/clean sequences.
- Sell: 996,957 EVERYTHING for 0.041266 ETH on `v4`, block 59548528, 2026-09-10 16:32:39 UTC — [tx 0xc350…b6e5](https://robinhoodchain.blockscout.com/tx/0xc350d597e2e184d07f7c2b1785fcb08bc04610304674ea447d04092a88cdb6e5)
- Buy: 190,961 MARIO for 0.026814 ETH on `v4`, block 59564180, 2026-09-10 16:59:00 UTC — [tx 0xb336…a896](https://robinhoodchain.blockscout.com/tx/0xb336aa4cbae7a45b573f98d03fc44a4edc4d5e3e8f73f0cb2bfad28e4b53a896)

## Negative and ambiguous cases (kept out of the main weight)

- Ambiguous: wallet [`0xb797…181a`](https://robinhoodchain.blockscout.com/address/0xb79716d9a447ffc81c581550ccf52dcaae75181a) sold everything (0x856f…5aff) ([tx](https://robinhoodchain.blockscout.com/tx/0x03591c37466b8cbfab062b184161be10e5f55d8c55f37d40e656a13d64fbcdc5)) and bought Neuralconvo (0x026a…61fa) ([tx](https://robinhoodchain.blockscout.com/tx/0xcefbd391254acaa569f8cb2f765f94cce873a96eea83c54d9f7a4ad73c35c3a0)) 1 s later, but in the same window it also sold everything (0x856f…5aff), USELESS (0xdc34…c0b8), Everything (0xdfaf…3fcf), INUVESTOR (0xe3cc…bbf3). The map lists it under *ambiguous* and does not draw it as a single origin.
- No matching event: 42079 buys in the sample have no prior sell by the same wallet inside the window. They are fresh entries and produce no edge.
- Not a trade: 265 swaps delivered tokens to several wallets with no ≥90% recipient and 2814 netted to zero inside the transaction; neither produced a trade. Plain transfers without a swap event never enter the trade table.
