"""Message text of STAMPEDE Calls (Telegram HTML). Pure functions, exact strings covered by tests/test_calls.py.

Voice: terse, mono-friendly, numbers with units, no hype. Every message is one call, one outcome reply or the daily
summary; the pinned channel message carries the disclaimer, so it is not repeated here.
"""
from __future__ import annotations

import html
import re
import time
from datetime import datetime, timezone
from typing import Any

from .. import chain

GECKO = "https://www.geckoterminal.com/robinhood/tokens/{token}"
PONS = "https://pons.fun/token/{token}"  # the pattern `api/radar.py:coin()` links to
MINUS = "\u2212"  # U+2212, the typographic minus used across the product
DOT = " \u00b7 "  # " · "


def esc(s: Any) -> str:
    return html.escape(str(s), quote=False)


def short_addr(addr: str) -> str:
    return (addr or "")[-4:].lower()


def signed_pct(x: float | None, digits: int = 0) -> str:
    """`+37 %`, `−30 %`, `n/a`."""
    if x is None:
        return "n/a"
    s = f"{abs(x):.{digits}f}"
    return ("+" if x >= 0 else MINUS) + s + " %"


def signed_eth(x: float | None, digits: int = 4) -> str:
    if x is None:
        return "n/a"
    s = f"{abs(x):.{digits}f}"
    return ("+" if x >= 0 else MINUS) + s + " ETH"


def eth(x: float | None, digits: int = 4) -> str:
    if x is None:
        return "n/a"
    s = f"{x:.{digits}f}".rstrip("0").rstrip(".")
    return (s if s and s != MINUS else "0") + " ETH"


def pct(x: float | None, digits: int = 0) -> str:
    return "n/a" if x is None else f"{x:.{digits}f} %"


def minutes(s: float | None) -> str:
    if s is None:
        return "n/a"
    return f"{int(round(s / 60))} min"


def clock_utc(ts: float | None) -> str:
    if not ts:
        return "n/a"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%H:%M:%S UTC")


def title(symbol: str | None, token: str) -> str:
    """`SYMBOL·1a01`; symbols the API already disambiguated (`MARIO·9b72`) keep their suffix."""
    sym = (symbol or "?").strip() or "?"
    if "\u00b7" in sym:
        return sym
    return f"{sym}\u00b7{short_addr(token)}"


def links_line(token: str) -> str:
    """pons · explorer · GeckoTerminal (Axiom is skipped: no verified URL pattern in the code base)."""
    return DOT.join(
        [
            f'<a href="{PONS.format(token=token)}">pons</a>',
            f'<a href="{chain.explorer_address(token)}">explorer</a>',
            f'<a href="{GECKO.format(token=token)}">GeckoTerminal</a>',
        ]
    )


_TP = re.compile(r"sell (\d+)% at \+(\d+)%")
_TRAIL = re.compile(r"trail (\d+)% below")
_STOP = re.compile(r"stop at [\u2212\-](\d+)%")
_TIME = re.compile(r"out after (\d+) min")


def exit_line(plan: Any) -> str:
    """`exit: TP +100 % × 50 % · trail 25 % · stop −30 % · 45 min` from the verdict's `exit_plan.text` list (or dict)."""
    lines: list[str] = []
    if isinstance(plan, dict):
        lines = list(plan.get("text") or [])
        if not lines:
            tps = plan.get("tp") or []
            for tp in tps:
                lines.append(f"sell {float(tp.get('frac', 0.5)) * 100:.0f}% at +{float(tp.get('gain', 1.0)) * 100:.0f}%")
            if plan.get("trail") is not None:
                lines.append(f"then trail {float(plan['trail']) * 100:.0f}% below the high")
            if plan.get("sl") is not None:
                lines.append(f"stop at {MINUS}{float(plan['sl']) * 100:.0f}%")
            t = plan.get("time_exit_s") or plan.get("time_s")
            if t:
                lines.append(f"out after {int(t) // 60} min")
    elif isinstance(plan, list):
        lines = [str(x) for x in plan]
    elif isinstance(plan, str):
        lines = [plan]
    parts: list[str] = []
    text = "\n".join(lines)
    for m in _TP.finditer(text):
        parts.append(f"TP +{m.group(2)} % \u00d7 {m.group(1)} %")
    m = _TRAIL.search(text)
    if m:
        parts.append(f"trail {m.group(1)} %")
    m = _STOP.search(text)
    if m:
        parts.append(f"stop {MINUS}{m.group(1)} %")
    m = _TIME.search(text)
    if m:
        parts.append(f"{m.group(1)} min")
    if not parts:
        parts = [f"TP +100 % \u00d7 50 %", "trail 25 %", f"stop {MINUS}30 %", "45 min"]  # the plan of docs/RESEARCH-EDGE.md
    return "exit: " + DOT.join(parts)


def launch_line(li: dict[str, Any] | None) -> str:
    """`launch: dev 2.1 % · bundle 1 · tax 25 bps` from a launch_intel row; `launch: n/a` when the coin has none."""
    if not li:
        return "launch: n/a"
    parts: list[str] = []
    dv = li.get("dev_buy_share")
    if dv is not None:
        parts.append(f"dev {float(dv) * 100:.1f} %")
    bn = li.get("bundle_n")
    if bn is not None:
        parts.append(f"bundle {int(bn)}")
    tax = li.get("creator_tax_bps")
    if tax is not None:
        parts.append(f"tax {int(tax)} bps")
    if li.get("launch_farm"):
        parts.append("farm")
    return "launch: " + (DOT.join(parts) if parts else "n/a")


def call_message(alert: dict[str, Any], launch: dict[str, Any] | None = None, ev_basis_quote: float = 0.02) -> str:
    """The call. `alert` is the `fired` payload of the `alert` SSE event (or a journal row of `/api/alerts`)."""
    d = alert.get("detail") or {}
    token = alert["token"]
    p = d.get("p_2x_30m")
    ev = d.get("ev_per_trade_quote")
    size = d.get("size_quote")
    inflow = alert.get("inflow")
    age = d.get("age_s")
    prog = d.get("progress")
    L = [
        f"<b>ENTER {esc(title(alert.get('symbol'), token))}</b>",
        f"<code>{esc(token)}</code>",
        f"p(2\u00d7 30m) {pct(p * 100 if p is not None else None)}  \u00b7  EV {signed_eth(ev)} / {eth(ev_basis_quote, 2)}  \u00b7  size {eth(size, 4)}",
        f"inflow {inflow if inflow is not None else 'n/a'} wallets/10 min{DOT}age {minutes(age)}{DOT}curve {pct(prog * 100 if prog is not None else None)}",
        launch_line(launch),
        exit_line(d.get("exit_plan")),
        f"clock {clock_utc(alert.get('clock_ts'))}{DOT}block {int(d['block']):,}" if d.get("block") else f"clock {clock_utc(alert.get('clock_ts'))}",
        links_line(token),
    ]
    return "\n".join(L)


def outcome_message(horizon_min: int, pct_change: float | None, graduated: bool | None = None) -> str:
    """Reply under the call: `+30 min: +37 %`."""
    s = f"+{horizon_min} min: {signed_pct(pct_change)}"
    if graduated:
        s += DOT + "graduated"
    return s


EXIT_LABELS = {"stop": "stop", "trail": "trail", "time": "time", "inflow_dies": "inflow dies", "tp": "take profit", "graduation": "graduation"}


def exit_message(pos: dict[str, Any]) -> str:
    """Reply under the call when the paper position closes: `exit stop · −30 % · pnl −0.0060 ETH · 12 min`."""
    reason = str(pos.get("exit_reason") or "?")
    label = EXIT_LABELS.get(reason, reason.replace("trigger: ", ""))
    parts = [f"exit {esc(label)}"]
    ret = pos.get("ret")
    if ret is not None:
        parts.append(signed_pct(float(ret) * 100))
    parts.append(f"pnl {signed_eth(pos.get('pnl_quote'))}")
    if pos.get("hold_s") is not None:
        parts.append(minutes(pos["hold_s"]))
    return DOT.join(parts) + "\npaper, simulated"


def _ms(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.0f} ms"


def summary_message(tr: dict[str, Any], calls: dict[str, Any], now: float | None = None) -> str:
    """The daily summary from `GET /api/track-record?since=<24 h ago>` plus the poster's own counters."""
    now = now or time.time()
    day = datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    edge = ((tr.get("alerts") or {}).get("by_rule") or {}).get("edge_enter") or {}
    st = (tr.get("paper") or {}).get("stats") or {}
    perf = tr.get("perf") or {}
    fired = edge.get("fired") or 0
    with30 = edge.get("with_outcome_30m") or 0
    ge2 = edge.get("ge_2x_30m") or 0
    hit = f"{ge2}/{with30} ({100 * ge2 / with30:.0f} %)" if with30 else f"{ge2}/0"
    ci = st.get("ci95_quote")
    ci_s = f"{DOT}95 % CI [{signed_eth(ci[0])}, {signed_eth(ci[1])}]" if ci else ""
    trades = st.get("trades") or 0
    up = perf.get("uptime_s")
    L = [
        f"<b>STAMPEDE \u00b7 24 h</b> {day}",
        f"calls posted {calls.get('posted', 0)}{DOT}skipped by rule {calls.get('skipped', 0)}{DOT}post latency p50 {_ms(calls.get('p50_ms'))} / p95 {_ms(calls.get('p95_ms'))}",
        f"edge_enter alerts {fired}{DOT}\u2265 2\u00d7 at +30 min {hit}{DOT}median +30 min {signed_pct(edge.get('median_30m'), 1)}",
        f"paper (simulated) {trades} trades{DOT}hit {pct((st.get('hit_rate') or 0) * 100) if trades else 'n/a'}{DOT}expectancy {signed_eth(st.get('expectancy_quote')) if trades else 'n/a'}{ci_s if trades else ''}",
        f"engine uptime {up / 3600:.1f} h{DOT}blocks {int(perf.get('blocks_processed') or 0):,}{DOT}gaps {perf.get('gaps_found', 'n/a')}{DOT}block\u2192emit p95 {perf.get('latency_p95_ms', 'n/a')} ms" if up is not None else "engine: n/a",
    ]
    return "\n".join(L)
