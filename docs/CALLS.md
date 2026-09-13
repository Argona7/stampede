# STAMPEDE Calls (Telegram poster)

`stampede calls` posts the live engine's `edge_enter` alerts to the channel [t.me/stampede_calls](https://t.me/stampede_calls),
replies under each call with the measured +30 / +60 min outcome and the paper exit, and posts one summary a day. It is a
client of the engine (docs/ENGINE.md): one process that reads `GET /api/stream`, applies the rule of
`stampede/signals/calls-config.json` and talks to the Bot API. Code: `stampede/calls/` (`poster.py`, `format.py`);
tests: `tests/test_calls.py`.

## What a call is

A call is one `edge_enter` alert of the engine that passed the poster's rule. The engine fires `edge_enter` when the
stage-4 verdict of a ranked radar row is ENTER (docs/RESEARCH-EDGE.md: model p(≥ 2× in 30 min) ≥ 0.455, EV > 0, the
risk engine gives a size), at most 5 per clock hour, one per coin per 30 min. The poster adds its own filter on top and
posts within a few hundred milliseconds of the engine's event. Every call gets its outcomes under it; nothing is edited
or deleted afterwards. The channel's pinned message carries the disclaimer; the calls do not repeat it.

## The rule (`stampede/signals/calls-config.json`)

| key | default | meaning |
|---|---|---|
| `rule` | `edge_enter` | the alert rule that becomes a call; alerts of other rules are ignored (no row, no log line) |
| `min_p` | `0.30` | minimum `p_2x_30m` of the alert. 0.30 is the 8-alerts/hour row of docs/RESEARCH-EDGE.md (42.5 % ≥ 2× on the test folds, lift 7.9×). The engine already fires at p ≥ 0.455, so today this only matters if the engine threshold is lowered |
| `max_per_hour` | `5` | posted calls per rolling clock hour (the engine's own cap is also 5, so the poster's rarely binds) |
| `dedupe_s` | `1800` | one call per coin per 30 min |
| `skip_if.launch_farm` | `true` | skip coins whose deployer belongs to a launch farm (`launch_intel.launch_farm`) |
| `skip_if.bundle_n_gt` | `4` | skip when more than 4 wallets bought in the launch bundle |
| `skip_if.dev_buy_share_gt` | `0.10` | skip when the deployer bought more than 10 % of the supply |
| `require_size_gt_0` | `true` | the risk engine must have given a size (an ENTER refused by risk stays an alert, not a call) |
| `catchup_s` | `1800` | on start, alerts of the journal younger than this are posted if they were missed |
| `summary_utc_hour` | `9` | the hour (UTC) of the daily summary |
| `ev_basis_quote` | `0.02` | the per-trade cap the EV line is quoted against (`risk.cap_per_trade_quote`) |

Launch facts come from the `launch_intel` table of the live store (the same row `GET /api/coin/{addr}` shows as
`launch.intel`), read directly (sub-millisecond) because the coin endpoint recomputes the whole card (5-10 s on the
live store). Without a store path the poster falls back to `GET /api/coin/{addr}` with a 3 s timeout. **Unknown facts
never skip**: a coin without a `launch_intel` row shows `launch: n/a` and passes the `skip_if` checks. Today the live
store has no `launch_intel` rows (the `stampede launch-intel` pass has not been run on it), so every call shows
`launch: n/a`; run `stampede launch-intel --db data/live-engine.sqlite --no-report` periodically to fill them.

Changing thresholds: edit the JSON, restart the poster (`kill <pid>`; start it again as below). Restarts are safe: the
`calls` table remembers every decision, the catch-up posts only what was fired while the poster was down and not yet
decided. Lowering `min_p` below the engine's threshold has no effect until the engine's `edge_enter` rule is lowered
too (`stampede/signals/edge-config.json` / `engine/state.py` rules).

## Message anatomy

```
ENTER STACK·39ac                                              ← bold; symbol + last 4 hex of the address
0x3bd73a113b6543402e30a6fb48b79cb006d039ac                    ← <code>, tap to copy
p(2× 30m) 41 %  ·  EV +0.0024 ETH / 0.02 ETH  ·  size 0.015 ETH
inflow 22 wallets/10 min · age 5 min · curve 12 %             ← rotation inflow, coin age, curve progress (n/a when unknown)
launch: dev 2.1 % · bundle 1 · tax 25 bps                     ← launch_intel facts, or `launch: n/a`
exit: TP +100 % × 50 % · trail 25 % · stop −30 % · 45 min      ← the verdict's exit plan
clock 07:08:19 UTC · block 61,344,094                         ← chain clock and block of the alert
pons · explorer · GeckoTerminal                               ← links (HTML, previews off)
```

- `p(2× 30m)` is the verdict's `p_2x_30m`; `EV` the verdict's `ev_per_trade_quote` against the 0.02 ETH cap; `size` the
  risk engine's size for the simulated position.
- Links: `https://pons.fun/token/<addr>` (the pattern `api/radar.py:coin()` uses), the Blockscout address page
  (`chain.explorer_address`), `https://www.geckoterminal.com/robinhood/tokens/<addr>`. Axiom is not linked: the code
  base has no verified URL pattern for it.
- Symbols are HTML-escaped; symbols the API already disambiguated (`MARIO·9b72`) keep their suffix, others get
  `·<last 4 hex>`.

## Outcomes

Replies to the call message (`reply_to_message_id`):

- `+30 min: +37 %` and `+60 min: −12 %` (`· graduated` when the coin graduated inside the hour) from the `alert`
  event of kind `outcome` (the engine measures the price change on indexed trades at +30 / +60 min of chain time).
- `exit stop · −30 % · pnl −0.0060 ETH · 12 min` / `paper, simulated` from the `position` event of kind `closed`
  (matched by token + `alert_ts`): the exit reason of the simulated position, its return, pnl in ETH and hold time.
  The second line is there because the number is a paper fill, not a trade anyone made.

A call gets each reply once; the message ids are kept in the `calls` row (`outcome_30_msg`, `outcome_60_msg`,
`exit_msg`). On start, outcomes already settled in the journal for posted calls without a reply are posted from the
journal.

## Daily summary

At `summary_utc_hour` (09:00 UTC; checked between stream frames, so within ~15 s) and with `stampede calls --summary`
on demand, one message from `GET /api/track-record?since=<now − 24 h>` plus the poster's counters:

```
STAMPEDE · 24 h 2026-09-13 09:00 UTC
calls posted 7 · skipped by rule 3 · post latency p50 612 ms / p95 1480 ms
edge_enter alerts 30 · ≥ 2× at +30 min 2/27 (7 %) · median +30 min −5.8 %
paper (simulated) 20 trades · hit 25 % · expectancy −0.0015 ETH · 95 % CI [−0.0033 ETH, +0.0004 ETH]
engine uptime 12.3 h · blocks 4,315 · gaps 0 · block→emit p95 176.0 ms
```

`calls_meta.last_summary_day` in the store makes it once per day across restarts.

## How it works

- Stream: `GET /api/stream?types=alert,position,session`, `Last-Event-ID` (`?last_event_id=`) on every reconnect,
  backoff 1 → 15 s. A hello with `replay_gap: true` (the ring no longer reaches the last id) re-reads the journal.
- Catch-up: on start `GET /api/alerts?limit=400`, filtered client-side to the rule and to `created_ts ≥ now − catchup_s`,
  oldest first, through the same path as live events (`source = catchup`, no latency sample).
- Store: the `calls` and `calls_meta` tables in the live store (`--db`, default `data/live-engine.sqlite`), created with
  `CREATE TABLE IF NOT EXISTS` over the poster's own connection (WAL, 10 s busy timeout). `calls(alert_key PRIMARY KEY,
  token, symbol, clock_ts, message_id, chat_id, posted_at, latency_ms, source, skipped, outcome_30_msg, outcome_60_msg,
  exit_msg)`; a skipped alert is a row with `message_id NULL` and the reason in `skipped`, so a restart does not
  re-decide it.
- Bot API: `sendMessage` with `parse_mode=HTML`, `disable_web_page_preview=true`, 10 s timeout, retry on 429 (sleeps
  `retry_after`) and 5xx (0.5 → 8 s), 5 tries; 4xx other than 429 raises. `deleteMessage` for `--delete`.
- Latency: `ts_emit` of the SSE frame → Bot API acknowledgement, per live call, kept as p50 / p95 / max of the last 500
  in `data/calls-perf.json` and on every `call` log line. Target p95 ≤ 2 s.
- Secrets: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHANNEL` (the `-100…` chat id), `TELEGRAM_CHANNEL_USERNAME` (for the
  message links in logs) from `.env` through `stampede.env.env`; never logged.

## Running it

```sh
uv run stampede calls --api http://127.0.0.1:8821 --dry-run --once     # print the most recent alert as a call
uv run stampede calls --api http://127.0.0.1:8821 --once               # post it as a format check (not recorded) ...
uv run stampede calls --delete <message_id>                            # ... and delete it
uv run stampede calls --api http://127.0.0.1:8821 --dry-run --summary  # print the daily summary
uv run stampede calls --api http://127.0.0.1:8821 --summary            # post it now
# the poster itself, detached, log data/calls.log:
.venv/bin/python scripts/bg.py calls -- .venv/bin/python -m stampede.cli calls --api http://127.0.0.1:8821
```

Options: `--config` (rule JSON), `--db` (the live store), `--perf-json` (latency file). Restart: `pkill -f "stampede.cli
calls"`, start again; the catch-up and the `calls` table make it idempotent. **Exactly one poster per channel**: two
posters on two stores (laptop and Mac mini) would each post their own engine's alerts. When the Mac mini deployment
takes over, stop the laptop instance first (`pkill -f "stampede.cli calls"` on the laptop), then start the Mac mini one;
the Mac mini engine's store starts with an empty `calls` table, so its catch-up posts the alerts of the last 30 min
that its engine fired - if the laptop poster already posted some of them the channel shows those coins twice, once
per engine. Starting the Mac mini poster with `catchup_s: 0` in its config for the first start avoids that.

Log lines (`data/calls.log`, UTC): `call <symbol> <clock> -> <link> post <ms> latency <ms> p50 <ms> p95 <ms>`,
`skip <symbol> <clock>: <reason>`, `outcome +30/+60 …`, `exit …`, `catch-up: {...}`, `stream connected (resume from N)`,
`stream <error>; reconnect in N s`.

## First live run (laptop engine :8821, 2026-09-13)

Started 07:58:53 UTC against the engine restarted at 07:46 UTC. The start-up catch-up posted the two `edge_enter`
alerts fired in the previous two minutes ([/6](https://t.me/stampede_calls/6), [/7](https://t.me/stampede_calls/7));
the first call taken from the stream was [/8](https://t.me/stampede_calls/8) at 08:00:51 UTC, PayOff·b382,
**167 ms** from the engine's `ts_emit` to the Bot API acknowledgement (the `sendMessage` round trip itself 166 ms). The
format check before the run (`--once`) went to message /5 and was deleted with `--delete 5`.

RUN_NUMBERS_PLACEHOLDER

## Known limits

- `launch: n/a` on every call until `launch_intel` rows exist in the live store; the `skip_if` filter cannot fire
  without them (see the rule section).
- `age n/a` when the engine's radar row has no launch timestamp for the coin (`age_s: null` in the alert).
- The poster posts what the engine fires; it does not re-evaluate the verdict. If the engine is restarted, the alerts of
  its first minutes are the same alerts the previous process would have fired (the 30-min dedupe is stored in the
  engine's `alerts` table), so the poster's own dedupe rarely binds.
- One `sendMessage` per call, one per outcome reply: ~3-4 messages per call; Telegram's channel limit (~20/min) is far
  away at 5 calls per hour.
