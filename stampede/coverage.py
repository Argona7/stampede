"""`stampede report`: docs/COVERAGE.md (what was fetched, what was not, what failed) and docs/EXAMPLES.md
(independently checkable sequences with transaction links and exact timestamps)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from . import chain
from .env import ROOT
from .rpc import Rpc
from .store import Store

WINDOW_NAMES = {300: "5 min", 1800: "30 min", 7200: "2 h"}


def utc(ts: int | None) -> str:
    if not ts:
        return "?"
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def short(a: str) -> str:
    return f"{a[:6]}…{a[-4:]}" if a and len(a) > 12 else a


def units(raw: str | int | None, decimals: int = 18) -> str:
    if raw in (None, "", "0", 0):
        return "0"
    v = int(raw) / 10**decimals
    if v >= 1000:
        return f"{v:,.0f}"
    if v >= 1:
        return f"{v:,.3f}"
    return f"{v:.6f}".rstrip("0").rstrip(".")


def sample_bounds(store: Store) -> dict[str, Any]:
    fr, to = store.get_meta("sample_from_block"), store.get_meta("sample_to_block")
    b = store.blocks()
    t_fr = b.get(fr, (None, 0))[0]
    t_to = b.get(to, (None, 0))[0]
    return {"from_block": fr, "to_block": to, "from_ts": t_fr, "to_ts": t_to, "label": store.get_meta("sample_label", "sample")}


def label_of(tokens: dict[str, dict], addr: str) -> str:
    t = tokens.get(addr, {})
    sym = t.get("symbol") or "?"
    return f"{sym} ({short(addr)})"


def render_coverage(store: Store) -> str:
    q = store.db.execute
    sb = sample_bounds(store)
    ing = store.last_run("ingest") or {"stats": {}, "params": {}}
    nrm = store.last_run("normalize") or {"stats": {}}
    ver = store.last_run("verify") or {"stats": {}}
    rot = {}
    for r in q("SELECT params, stats FROM runs WHERE kind='rotate' AND finished IS NOT NULL ORDER BY id"):
        p, s = json.loads(r[0]), json.loads(r[1])
        rot[p["window_s"]] = s
    probe = {}
    pj = ROOT / "data" / "probe.json"
    if pj.exists():
        probe = json.loads(pj.read_text())
    tokens = store.tokens()
    s = ing["stats"]
    n = nrm["stats"]
    v = ver["stats"]
    L: list[str] = []
    L.append("# Coverage report\n")
    L.append(f"Generated {utc(int(datetime.now(timezone.utc).timestamp()))}. Everything below describes **one explicitly bounded sample**, not the whole chain.\n")
    L.append("## Sample\n")
    L.append(f"- Label: **{sb['label']}**")
    L.append(f"- Chain: Robinhood Chain (id {chain.CHAIN_ID}). Blocks {sb['from_block']}–{sb['to_block']} ({(sb['to_block'] or 0) - (sb['from_block'] or 0) + 1} blocks).")
    L.append(f"- Time: {utc(sb['from_ts'])} → {utc(sb['to_ts'])} ({((sb['to_ts'] or 0) - (sb['from_ts'] or 0)) / 60:.0f} min), from exact block headers.")
    L.append(f"- Ingest path: `{s.get('path')}`; chunks {s.get('chunks')}, splits {s.get('chunk_splits')}, failed chunks {s.get('chunk_failures')}; block ranges lost: {len(s.get('blocks_missing', []))}.")
    rpc = s.get("rpc", {})
    L.append(f"- RPC accounting: {rpc.get('calls')} calls, {rpc.get('rate_limited')} rate-limited responses (all retried), {rpc.get('mb_in')} MB received; per host {rpc.get('per_host')}.")
    n_exact = q("SELECT COUNT(*) FROM blocks WHERE exact=1").fetchone()[0]
    nums = [r[0] for r in q("SELECT number FROM blocks WHERE exact=1 ORDER BY number")]
    gap = max((b - a for a, b in zip(nums, nums[1:])), default=0)
    L.append(f"- Block timestamps: {n_exact} exact anchor headers, largest gap between anchors {gap} blocks (≈{gap / 10:.0f} s at ~10 blocks/s); other blocks are linearly interpolated and marked `ts_exact=0`. Trades cited in `EXAMPLES.md` carry exact timestamps.")
    L.append("\n## Universe: what counts as a coin here\n")
    n_curve = q("SELECT COUNT(*) FROM tokens WHERE source='curve'").fetchone()[0]
    n_pool = q("SELECT COUNT(*) FROM tokens WHERE source='v4_pool'").fetchone()[0]
    pools = s.get("pools", {})
    unresolved_swaps = q("SELECT COUNT(*) FROM logs WHERE kind='v4_swap' AND topic1 NOT IN (SELECT pool_id FROM pools)").fetchone()[0]
    L.append(f"- Nodes are **PONS v2 launchpad tokens only**: {n_curve} seen on bonding curves in the window (curve → `token()` via eth_call) and {n_pool} more seen only in graduated Uniswap v4 pools, accepted only when the pool id recomputes from `(quote, token, fee 0, tickSpacing 200, V2MemeHook)` and the PONS v2 factory knows the token.")
    L.append(f"- v4 pools seen: {pools.get('pools_seen')}; resolved as PONS v2: {pools.get('pools_resolved_now')}; **not resolved: {pools.get('pools_unresolved')} pools with {unresolved_swaps} Swap logs** — other tokens/hooks (PONS token itself, PONIE, stock pairs, other launchpads). They are not in the map.")
    gt = probe.get("geckoterminal", {})
    if gt.get("dexes"):
        others = [d for d in gt["dexes"] if "pons" not in d]
        L.append(f"- Venues not covered at all (GeckoTerminal lists {gt.get('dex_count')} DEX ids on this chain): " + ", ".join(f"`{d}`" for d in others) + ".")
    L.append("\n## Raw data\n")
    L.append(f"- Swap-class logs kept: {s.get('logs_swap_kept')} (CurveBuy/CurveSell from any curve, v4 `Swap` only from PoolManager `{short(chain.V4_POOL_MANAGER)}`; {s.get('logs_swap_other_v4_forks_dropped')} Swap logs from other addresses dropped).")
    L.append(f"- Transactions with swap-class logs: {s.get('swap_txs')}. ERC-20 Transfer logs: {s.get('logs_transfer_seen')} seen in the window, {s.get('logs_transfer_kept')} kept (those inside swap transactions).")
    L.append("\n## Normalization (logs → trades)\n")
    L.append(f"- {n.get('txs_seen')} transactions examined → {n.get('txs_with_trades')} produced trades → **{n.get('trades')} trades** ({n.get('trades_buy')} buys, {n.get('trades_sell')} sells) by {n.get('wallets')} wallets in {n.get('tokens_with_trades')} tokens; by venue {n.get('trades_by_venue')}.")
    L.append(f"- Attribution: `transfer_net` for every trade (net ERC-20 balance change inside the transaction). Pass-through addresses learned (net zero in ≥3 txs): {n.get('passthrough_addresses_learned')}. {n.get('txs_with_direct_sell_and_buy_same_wallet')} transactions contain a sell and a buy by the same wallet (direct swaps).")
    notes = n.get("notes", {})
    expl = {
        "non_universe_pool": "v4 Swap in a pool that is not a resolved PONS v2 pool (excluded venue)",
        "no_net_wallet_change": "all token movement netted to zero or touched only infrastructure",
        "ambiguous_recipients": "several wallets received the token and none held ≥90% — no buyer asserted",
        "ambiguous_senders": "several wallets sent the token and none ≥90% — no seller asserted",
        "swap_without_transfer": "swap event present but no ERC-20 Transfer of that token in the receipt",
        "unresolved_curve": "curve contract could not be resolved to a token",
        "pool_with_two_universe_tokens": "pool between two PONS tokens (skipped, would be two sides at once)",
    }
    L.append("- Swap events that did **not** become trades, by reason:")
    for k, c in sorted(notes.items(), key=lambda kv: -kv[1]):
        L.append(f"  - `{k}`: {c} — {expl.get(k, '')}")
    vs = n.get("v4_sign_convention", {})
    L.append(f"- Uniswap v4 `Swap` sign convention observed: on buys the token amount was positive in {vs.get('buy_event_sign_pos', 0)} and negative in {vs.get('buy_event_sign_neg', 0)} cases; on sells negative in {vs.get('sell_event_sign_neg', 0)}, positive in {vs.get('sell_event_sign_pos', 0)}. Side is always taken from Transfer direction, never from the sign.")
    L.append(f"- Contract wallets: of the {n.get('contract_wallets_checked')} most active wallets, {n.get('contract_wallets')} have code (bots); they account for {n.get('trades_by_contract_wallets')} trades and are kept but marked.")
    L.append(f"- Exact timestamps on trades: {round((n.get('ts_exact_share') or 0) * 100, 1)}% (anchor blocks); the rest interpolated (see above).")
    L.append("\n## Verification against receipts\n")
    if v:
        L.append(f"- Random sample of {v.get('sample_size')} trade transactions (seed {v.get('seed')}) fetched as receipts from Alchemy: {v.get('completeness')}; {v.get('reproducibility')}.")
        L.append(f"- Attributed wallet vs `tx.from`: {v.get('tx_from_agreement')}; {v.get('wallet_equals_tx_to_contract', 0)} wallets are the contract the transaction called (bot contracts holding the tokens), {v.get('wallet_other_address', 0)} are third addresses (tokens delivered to an address other than the sender).")
        L.append("- Transaction targets in the sample: " + ", ".join(f"{lbl} {c}" for lbl, c in v.get("tx_to_distribution", [])) + ". Routers appear as `tx.to` and in event topics, never as wallets.")
        if v.get("mismatches"):
            L.append(f"- Mismatches: {len(v['mismatches'])} (see run stats).")
    else:
        L.append("- Not run.")
    L.append("\n## Rotation sequences and edges\n")
    L.append("A sequence is `sell A → buy B` by one wallet inside the window. Grades: `direct` (same transaction), `clean` (A the only token sold in the window before the buy and nothing else bought in between), `ambiguous` (everything else). Edge main weight = distinct wallets with a direct or clean sequence.\n")
    for w, st in sorted(rot.items()):
        g = st.get("sequences_by_grade", {})
        L.append(f"- **Window {WINDOW_NAMES.get(w, str(w) + ' s')}**: {st.get('sequences')} sequences (direct {g.get('direct', 0)}, clean {g.get('clean', 0)}, ambiguous {g.get('ambiguous', 0)}); {st.get('edges')} edges, {st.get('edges_with_main_weight')} with main weight ≥1, {st.get('edges_main_weight_ge_3')} with ≥3 wallets; {st.get('buys_without_prior_sell_in_window')} buys had no prior sell by the same wallet in the window (fresh entries, no edge).")
    if 1800 in rot:
        L.append("\nTop edges, 30 min window (main weight = distinct wallets):\n")
        for e in rot[1800].get("top_edges", [])[:10]:
            L.append(f"- {label_of(tokens, e['from'])} → {label_of(tokens, e['to'])}: **{e['wallets_main']} wallets** (direct {e['direct']}, clean {e['clean']}; ambiguous {e['ambiguous']}; {e['sequences']} sequences)")
    L.append("\n## Limits that stay true regardless of the numbers\n")
    L.append("- An edge is an observed order of trades by one address. It does not prove that the proceeds of the sale funded the purchase, that several addresses belong to one person, coordination, insider knowledge, or any future price move.")
    L.append("- Symbols are not unique on this chain (copycat launches share names); every label carries the address.")
    L.append("- Bots and contract wallets trade in the same pools; their trades are marked, not removed.")
    L.append("- One hour of one launchpad. Extending the window or adding venues changes the picture; the sample label travels with every artifact.")
    return "\n".join(L) + "\n"


def pick_examples(store: Store, window_s: int = 1800) -> list[dict]:
    """6 sequences: 2 direct + 4 clean, distinct wallets and token pairs, low-frequency wallets, visible edges."""
    q = store.db.execute
    rows = q(
        """
        SELECT q.id, q.wallet, q.grade, q.gap_s, q.sell_token, q.buy_token, q.sell_trade, q.buy_trade, e.wallets_main, w.trades, w.is_contract
        FROM sequences q JOIN edges e ON e.window_s=q.window_s AND e.from_token=q.sell_token AND e.to_token=q.buy_token
        JOIN wallets w ON w.address=q.wallet
        JOIN trades ts ON ts.id=q.sell_trade JOIN trades tb ON tb.id=q.buy_trade
        WHERE q.window_s=? AND q.grade IN ('direct','clean') AND w.trades<=20 AND COALESCE(w.is_contract,0)=0
          AND ts.flags NOT LIKE '%two_sided%' AND tb.flags NOT LIKE '%two_sided%'
        ORDER BY e.wallets_main DESC, q.gap_s DESC
        """,
        (window_s,),
    ).fetchall()
    out: list[dict] = []
    seen_w: set[str] = set()
    seen_pair: set[tuple[str, str]] = set()
    for grade, want in (("direct", 2), ("clean", 4)):
        n = 0
        for r in rows:
            if r[2] != grade or n >= want:
                continue
            if grade == "clean" and r[3] < 60:
                continue
            if r[1] in seen_w or (r[4], r[5]) in seen_pair:
                continue
            seen_w.add(r[1])
            seen_pair.add((r[4], r[5]))
            out.append({"seq_id": r[0], "wallet": r[1], "grade": grade, "gap_s": r[3], "sell_token": r[4], "buy_token": r[5], "sell_trade": r[6], "buy_trade": r[7], "edge_wallets": r[8], "wallet_trades": r[9]})
            n += 1
    return out


def exactify(store: Store, rpc: Rpc | None, trade_ids: list[int]) -> None:
    """Fetch exact headers for the blocks of the given trades and rewrite their timestamps."""
    if not rpc or not trade_ids:
        return
    blocks = sorted({r[0] for r in store.db.execute(f"SELECT block FROM trades WHERE id IN ({','.join('?' * len(trade_ids))})", trade_ids)})
    got = rpc.get_blocks(blocks)
    store.upsert_blocks((n, int(b["timestamp"], 16), 1) for n, b in got.items())
    for n, b in got.items():
        store.db.execute("UPDATE trades SET ts=?, ts_exact=1 WHERE block=?", (int(b["timestamp"], 16), n))
    store.commit()


def render_examples(store: Store, rpc: Rpc | None, window_s: int = 1800) -> str:
    q = store.db.execute
    tokens = store.tokens()
    sb = sample_bounds(store)
    ex = pick_examples(store, window_s)
    exactify(store, rpc, [e["sell_trade"] for e in ex] + [e["buy_trade"] for e in ex])
    quotes = store.quotes()

    def qfmt(amount: str | None, qt: str | None, flags: list[str]) -> str:
        if "two_sided_tx" in flags:
            return "n/a (two wallets traded this token in the same transaction; quote not attributable)"
        qi = quotes.get(qt or "", {"symbol": short(qt or ""), "decimals": 18})
        return f"{units(amount, qi['decimals'])} {qi['symbol']}"

    L: list[str] = []
    L.append("# Verified examples\n")
    L.append(f"Sample: **{sb['label']}**, {utc(sb['from_ts'])} → {utc(sb['to_ts'])}, window {WINDOW_NAMES.get(window_s)}. Each example is one wallet's observed `sell A → buy B`; timestamps below are exact block times. Open the links: the Blockscout *Token transfers* tab of the sell transaction shows the wallet sending A to a curve/router, the buy transaction shows the wallet receiving B.\n")
    L.append("What this shows: an address sold one PONS v2 token and later bought another inside the window. What it does not show: that the sale funded the purchase, who controls the address, coordination with other addresses, or where the price goes next.\n")
    for i, e in enumerate(ex, 1):
        st = q("SELECT tx_hash, block, ts, ts_exact, token_amount, quote_token, quote_amount, venue, flags FROM trades WHERE id=?", (e["sell_trade"],)).fetchone()
        bt = q("SELECT tx_hash, block, ts, ts_exact, token_amount, quote_token, quote_amount, venue, flags FROM trades WHERE id=?", (e["buy_trade"],)).fetchone()
        a, b = e["sell_token"], e["buy_token"]
        sfl, bfl = json.loads(st[8] or "[]"), json.loads(bt[8] or "[]")
        L.append(f"## {i}. {label_of(tokens, a)} → {label_of(tokens, b)} — {e['grade']}, gap {e['gap_s']} s\n")
        L.append(f"- Wallet: [`{e['wallet']}`]({chain.explorer_address(e['wallet'])}) — {e['wallet_trades']} trades in the sample; this edge has {e['edge_wallets']} wallets with direct/clean sequences.")
        L.append(f"- Sell: {units(st[4])} {tokens.get(a, {}).get('symbol')} for {qfmt(st[6], st[5], sfl)} on `{st[7]}`, block {st[1]}, {utc(st[2])}{'' if st[3] else ' (interpolated)'} — [tx {short(st[0])}]({chain.explorer_tx(st[0])})")
        L.append(f"- Buy: {units(bt[4])} {tokens.get(b, {}).get('symbol')} for {qfmt(bt[6], bt[5], bfl)} on `{bt[7]}`, block {bt[1]}, {utc(bt[2])}{'' if bt[3] else ' (interpolated)'} — [tx {short(bt[0])}]({chain.explorer_tx(bt[0])})")
        fl = sorted(set(sfl + bfl))
        if fl:
            L.append(f"- Flags: {', '.join(fl)}")
        if e["grade"] == "direct":
            L.append("- Same transaction: the wallet swapped A for B in one call. This is the strongest observation the map makes; it still says nothing about intent.")
        L.append("")
    # negative examples
    L.append("## Negative and ambiguous cases (kept out of the main weight)\n")
    amb = q(
        """SELECT q.wallet, q.sell_token, q.buy_token, q.gap_s, q.candidates, ts.tx_hash, tb.tx_hash FROM sequences q
           JOIN trades ts ON ts.id=q.sell_trade JOIN trades tb ON tb.id=q.buy_trade JOIN wallets w ON w.address=q.wallet
           WHERE q.window_s=? AND q.grade='ambiguous' AND w.trades<=15 ORDER BY q.gap_s LIMIT 1""",
        (window_s,),
    ).fetchone()
    if amb:
        c = json.loads(amb[4] or "{}")
        sold = [label_of(tokens, t) for t in c.get("sold_in_window", [])]
        between = [label_of(tokens, t) for t in c.get("bought_between", [])]
        L.append(f"- Ambiguous: wallet [`{short(amb[0])}`]({chain.explorer_address(amb[0])}) sold {label_of(tokens, amb[1])} ([tx]({chain.explorer_tx(amb[5])})) and bought {label_of(tokens, amb[2])} ([tx]({chain.explorer_tx(amb[6])})) {amb[3]} s later, but in the same window it also sold {', '.join(sold) if len(sold) > 1 else 'nothing else'}" + (f" and bought {', '.join(between)} in between" if between else "") + ". The map lists it under *ambiguous* and does not draw it as a single origin.")
    nb = q("SELECT COUNT(*) FROM trades b WHERE b.side='buy' AND NOT EXISTS (SELECT 1 FROM sequences s WHERE s.window_s=? AND s.buy_trade=b.id)", (window_s,)).fetchone()[0]
    L.append(f"- No matching event: {nb} buys in the sample have no prior sell by the same wallet inside the window. They are fresh entries and produce no edge.")
    nt = store.last_run("normalize") or {"stats": {}}
    notes = nt["stats"].get("notes", {})
    L.append(f"- Not a trade: {notes.get('ambiguous_recipients', 0)} swaps delivered tokens to several wallets with no ≥90% recipient and {notes.get('no_net_wallet_change', 0)} netted to zero inside the transaction; neither produced a trade. Plain transfers without a swap event never enter the trade table.")
    return "\n".join(L) + "\n"


def main_report(args) -> int:
    store = Store()
    rpc = None
    try:
        rpc = Rpc()
        if not rpc.alchemy_url:
            rpc = None
    except Exception:  # noqa: BLE001
        rpc = None
    cov = render_coverage(store)
    ex = render_examples(store, rpc)
    (ROOT / "docs").mkdir(exist_ok=True)
    (ROOT / "docs" / "COVERAGE.md").write_text(cov)
    (ROOT / "docs" / "EXAMPLES.md").write_text(ex)
    store.save_report("COVERAGE.md", cov)
    store.save_report("EXAMPLES.md", ex)
    print(cov)
    print(ex)
    return 0
