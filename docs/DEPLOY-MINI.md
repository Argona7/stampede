# 24/7 on the Mac mini

The live engine (`stampede serve --mode live --feed wss`) and the Telegram calls poster (`stampede calls`) run on the
owner's always-on Mac mini as launchd user agents. Files: `deploy/mini/` (two plists + `install.sh`). Laptop restarts no
longer stop the paper run or the channel.

## Layout on the mini

| what | where |
|---|---|
| account | `argona` (uid 501, the auto-login console user; reach it with `ssh argona@100.122.123.37` over Tailscale). The laptop's `mac-mini` ssh alias lands on the `agent` account, which has no GUI session and no sudo, so `launchctl bootstrap gui/…` fails there ("Domain does not support specified action"); the API on `127.0.0.1:8821` is reachable from both accounts. |
| checkout | `~/dev/stampede` (clone of github.com/Argona7/stampede, `main`), `.venv` from `uv sync`, Python 3.13 via `uv` (`~/.local/bin/uv`) |
| secrets | `~/dev/stampede/.env` (mode 600, copied from the laptop with `scp`; `ALCHEMY_KEY`, `HYPERSYNC_TOKEN`, `TWITTERAPI_KEY`, `TELEGRAM_*`) |
| store | `~/dev/stampede/data/live-engine.sqlite` (the mini has ~39 GB free - never copy the research stores there). With raw logs persisted it grew ~12 MB/min (~13 GB/day, `logs` 139 MB after 18 min), so the engine plist sets `STAMPEDE_ENGINE_LOGS=0` (docs/ENGINE.md: raw logs of trade transactions are not written; trades, sequences, blocks, alerts, paper ledger are). Measure with the health commands below. |
| seed | `~/dev/stampede/data/seed-registry.sqlite` (252 MB, registry only) |
| agents | `~/Library/LaunchAgents/com.stampede.engine.plist`, `~/Library/LaunchAgents/com.stampede.calls.plist` |
| logs | `~/dev/stampede/data/launchd-engine.log`, `~/dev/stampede/data/launchd-calls.log` (stdout + stderr, append) |

Both agents: `RunAtLoad`, `KeepAlive` with `SuccessfulExit=false` (relaunch after a crash or `kickstart -k`, stay down
after a clean exit), `ThrottleInterval 15`, `ExitTimeOut 10` (SIGTERM, then SIGKILL - two engines on one store would
both assign trade ids, docs/ENGINE.md), `WorkingDirectory ~/dev/stampede`, `PATH` with `~/.local/bin` and
`/opt/homebrew/bin`, `PYTHONUNBUFFERED=1`, the engine additionally `STAMPEDE_ENGINE_LOGS=0`. The engine runs without `--notify` (no banners on a headless machine) and
with `--host 0.0.0.0`, so the web terminal answers on the Tailscale address (http://100.122.123.37:8821/, tailnet
only - the mini is not exposed beyond it and the home LAN); the poster talks to `127.0.0.1:8821`. `__HOME__` in the
plists is replaced by the remote `$HOME` at install time (launchd does not expand `~`).

## Install (first time)

On the mini, as `argona`:

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh          # if ~/.local/bin/uv is missing
export PATH=$HOME/.local/bin:$PATH
uv python install 3.13
git clone https://github.com/Argona7/stampede ~/dev/stampede
cd ~/dev/stampede && uv sync
```

From the laptop:

```sh
cd ~/dev/stampede
M=argona@100.122.123.37
scp .env $M:~/dev/stampede/.env && ssh $M chmod 600 '~/dev/stampede/.env'
ssh $M 'cd ~/dev/stampede && uv run stampede --help >/dev/null && uv run python -c "from stampede.env import env; print(bool(env(\"TELEGRAM_BOT_TOKEN\")))"'   # True

# registry seed: build a registry-only store here (7 s, 252 MB), ship it, seed the engine store from it
uv run python -m stampede.engine.seed --into /tmp/stampede-seed.sqlite --from data/live-engine.sqlite --from data/stampede.sqlite
scp /tmp/stampede-seed.sqlite $M:~/dev/stampede/data/seed-registry.sqlite
ssh $M 'cd ~/dev/stampede && uv run python -m stampede.engine.seed --into data/live-engine.sqlite --from data/seed-registry.sqlite'

deploy/mini/install.sh                 # engine only
deploy/mini/install.sh --with-calls    # engine + poster (once `stampede calls` is in the checkout)
deploy/mini/install.sh --calls-only    # poster only; the running engine is not touched
ssh $M 'cd ~/dev/stampede/web && npm ci --no-audit --no-fund && npm run build'   # web terminal (node 26 is on the mini; 9 s)
```

`install.sh` fills `__HOME__`, lints the plists, copies them to `~/Library/LaunchAgents/`, boots the agents out if they
are already loaded (and waits until launchd has dropped them - bootstrapping while the old instance is still registered
fails with `Bootstrap failed: 5: Input/output error`), then `launchctl enable` + `launchctl bootstrap gui/<uid>` and
prints `state` / `pid`. The static files of the web terminal are mounted at engine start, so build `web/dist` before
the (re)install or `kickstart -k` afterwards. The seed is optional (docs/ENGINE.md: an unseeded store resolves curves
on first sight with two `eth_call`s each); the seed tool has no network mode, it copies `curves`, `pools`, `launches`,
`graduations`, `quotes`, `infra`, `wallet_scores`, `tokens` from another store, hence the intermediate registry-only
file instead of shipping a multi-GB store.

## Update (new commits on `main`)

```sh
M=argona@100.122.123.37
ssh $M 'cd ~/dev/stampede && git pull --ff-only && ~/.local/bin/uv sync && launchctl kickstart -k gui/$(id -u)/com.stampede.engine'
ssh $M 'launchctl kickstart -k gui/$(id -u)/com.stampede.calls'      # ALWAYS after an engine restart (see the resume issue below)
ssh $M 'cd ~/dev/stampede/web && npm ci --no-audit --no-fund && npm run build'   # only when web/ changed, then kickstart the engine
```

The mini pulls from GitHub, so commits reach it after the owner pushes. On 2026-09-13 the three poster commits
(`3511bba`, `878af5e`, `fe35b77`) were shipped ahead of the push with a bundle: `git bundle create /tmp/m.bundle
e155f6c..main` on the laptop, `scp`, `git pull --ff-only /tmp/m.bundle main` on the mini. Once those commits are on
`origin/main` unchanged, `git pull --ff-only` is a no-op for them; if they were rewritten before the push, realign the
mini with `git fetch origin && git reset --hard origin/main` (the mini checkout holds no local work).

`kickstart -k` sends SIGTERM (SIGKILL after 10 s) and relaunches immediately; the engine backfills the blocks it missed
from Alchemy on start (`engine_cursor` up to 600 blocks behind the head, docs/ENGINE.md). If a plist changed, run
`deploy/mini/install.sh` again instead (it boots out and re-bootstraps).

## Health

```sh
M=argona@100.122.123.37
ssh $M 'curl -s http://127.0.0.1:8821/api/perf' | python3 -c '
import json,sys; p=json.load(sys.stdin); b=p["blocks"]; f=p["feed"]
print("uptime", p["uptime_s"], "blocks", b["processed"], "head", b["head"], "unfilled", b["unfilled"], "gaps", b["gaps_found"])
print("block_to_emit p50/p95", p["latency_ms"]["block_to_emit"]["p50"], p["latency_ms"]["block_to_emit"]["p95"])
print("feed", {c: (v["endpoint"], v["connected"]) for c, v in f["connections"].items()}, "reconnects", f["reconnects"])
print("rss_mb", p["process"]["rss_mb"], "writer errors", p["writer"]["errors"])'
ssh $M 'launchctl print gui/$(id -u)/com.stampede.engine | grep -E "^.(state|pid|last exit code) ="'
ssh $M 'tail -20 ~/dev/stampede/data/launchd-engine.log'
ssh $M 'tail -20 ~/dev/stampede/data/launchd-calls.log'
ssh $M 'curl -s "http://127.0.0.1:8821/api/alerts?limit=5"'
ssh $M 'du -sh ~/dev/stampede/data; df -h ~ | tail -1'
```

Healthy: `block_to_emit` p50 < 300 ms, `unfilled` 0, both connections on `wss://robinhood-rpc.publicnode.com` (or the
pocket failover), head age under a second. Over Tailscale: http://100.122.123.37:8821/?view=signals (the web
terminal), `curl http://100.122.123.37:8821/api/perf`, `stampede terminal --api-url http://100.122.123.37:8821` from
the laptop.

## Stop / start / remove

```sh
M=argona@100.122.123.37
ssh $M 'launchctl kill SIGTERM gui/$(id -u)/com.stampede.engine'    # stops the process; KeepAlive relaunches it after 15 s
ssh $M 'launchctl bootout gui/$(id -u)/com.stampede.engine'         # stop and unload until the next install / login
ssh $M 'launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.stampede.engine.plist'   # load again
ssh $M 'launchctl disable gui/$(id -u)/com.stampede.calls'          # keep it from loading at login (enable to undo)
```

Same for `com.stampede.calls`. To remove everything: bootout both, delete the two plists from `~/Library/LaunchAgents/`,
delete `~/dev/stampede` (the store is the paper track record - copy `data/live-engine.sqlite` first if it matters).

## The poster (`com.stampede.calls`)

`stampede calls --api http://127.0.0.1:8821` reads the mini engine's `/api/stream` and posts to the STAMPEDE Calls
channel with the bot from `.env`. It is installed only with `--with-calls` or `--calls-only`. The poster keeps its
`calls` table in its own store, so a poster on the laptop and a poster on the mini would post every new call twice, and
both would post the 09:00 UTC daily summary: run exactly one, on the mini. Stop a laptop instance
(`pkill -f "stampede.cli calls"`, docs/CALLS.md) as soon as the mini one logs `stream connected`.

## Verified 2026-09-13

Engine on the mini (`argona@100.122.123.37`, macOS 26.3, 10 cores / 16 GB, 40 GB free), seeded store, first start
08:04:30 UTC:

- after 10 min (`uptime_s` 621): 6,169 blocks processed (6,090 live, 79 fragments), `block_to_emit` p50 120.5 ms /
  p90 182.9 / p95 210.2 / p99 287.6 / max 1,025; `head_to_logs_complete` p50 110.8; `apply_block` p50 6.0 ms; 0 gaps,
  0 unfilled, 11 deferred txs, 1,178 late logs; both connections on publicnode, 0 reconnects; 98 events/s; writer
  6,229 flushes / 265k rows / 0 errors; CPU 20 % of one core, RSS 435 MB; state 427k curves, 10.7k wallets tracked,
  230 radar rows, 10 alerts fired. Store 397 MB after 10 min (started at 252 MB from the seed).
- `launchctl kickstart -k`: new pid within 1 s, API answering after ~5 s, the 86 blocks of the restart window
  backfilled from Alchemy (`gaps_found` 2, `gaps_backfilled_blocks` 86, 0 unfilled), p50 back at 112 ms a minute later.
- `kill -9` of the engine: launchd relaunched it after ~1 s (the last launch was > 15 s ago, so `ThrottleInterval`
  did not delay it), API after ~5 s, 43 blocks backfilled, 0 unfilled.
- `com.stampede.calls` (commit `fe35b77`): `catch-up: {posted 0, skipped 0, duplicate 0}`, `stream connected`; across
  an engine restart it backed off 1 -> 15 s and reconnected with `resume from <last id>`.
- Web terminal built on the mini (`npm ci` 5 s, `npm run build` 3 s, `dist/` 1.5 MB), `/?view=signals` -> 200 over
  Tailscale.
- Store growth with raw logs on: 252 MB (seed) -> 397 MB at +10 min -> 486 MB at +18 min; the `logs` table and its
  index were 165 of the 234 MB written. Restarted with `STAMPEDE_ENGINE_LOGS=0` at 08:24 UTC (the writer's `by_table`
  no longer lists `logs`).

## Open issue: the poster's resume across an engine restart

Observed 2026-09-13 08:20 UTC on the mini: the engine was restarted while the poster stayed up; the poster reconnected
with `resume from 7614`, then dropped the stream 40 s later (`ConnectionError`, its read timeout) and reconnected. Cause,
in `stampede/api/stream.py`: the event counter is per server process, so after a restart the new engine's ids start at
1 while the client still sends `last_event_id=7614`. `bus.replay` reports no gap (`since_id < items[0].id - 1` is false
for a large `since_id`), and the generator drops every live event with `id <= last_sent` (`last_sent = since`) - and
sends no keepalive while it drops them. Until the new counter passes the old id the poster receives nothing: seconds
after a short run, **hours after a day-long one** (~100 events/s). Neither side notices; no journal catch-up runs.
Fix candidates (owner of `api/stream.py` / `calls/poster.py`): server - when `since > bus.last_id` treat it as a gap
(`replay_gap: true`, `last_sent = 0`); poster - on the hello, if `data.last_event_id < self.last_id` the engine has
restarted: forget `last_id`, run `catch_up()`, reconnect without `last_event_id`. Until then: **kickstart the poster
after every engine restart** (the update recipe above does; a launchd crash-relaunch of the engine does not).

## Not done on the mini (yet)

- Log rotation of `data/launchd-*.log` (a few MB per day of uvicorn access lines; truncate with `: > file` when needed,
  or point `newsyslog` at them - that needs sudo).
- `stampede track-record` for the mini store (`--db data/live-engine.sqlite --api http://127.0.0.1:8821`) is run by hand.
