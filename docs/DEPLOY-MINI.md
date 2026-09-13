# 24/7 on the Mac mini

The live engine (`stampede serve --mode live --feed wss`) and the Telegram calls poster (`stampede calls`) run on the
owner's always-on Mac mini as launchd user agents, plus a 10-minute timer that refreshes the launch facts the poster
prints. Files: `deploy/mini/` (three plists, `install.sh`, `launch_intel_job.py`). Laptop restarts no longer stop the
paper run or the channel.

## Layout on the mini

| what | where |
|---|---|
| account | `argona` (uid 501, the auto-login console user; reach it with `ssh argona@100.122.123.37` over Tailscale). The laptop's `mac-mini` ssh alias lands on the `agent` account, which has no GUI session and no sudo, so `launchctl bootstrap gui/…` fails there ("Domain does not support specified action"); the API on `127.0.0.1:8821` is reachable from both accounts. |
| checkout | `~/dev/stampede` (clone of github.com/Argona7/stampede, `main`), `.venv` from `uv sync`, Python 3.13 via `uv` (`~/.local/bin/uv`) |
| secrets | `~/dev/stampede/.env` (mode 600, copied from the laptop with `scp`; `ALCHEMY_KEY`, `HYPERSYNC_TOKEN`, `TWITTERAPI_KEY`, `TELEGRAM_*`) |
| store | `~/dev/stampede/data/live-engine.sqlite` (the mini has ~39 GB free - never copy the research stores there). With raw logs persisted it grew ~12 MB/min (~13 GB/day, `logs` 139 MB after 18 min), so the engine plist sets `STAMPEDE_ENGINE_LOGS=0` (docs/ENGINE.md: raw logs of trade transactions are not written; trades, sequences, blocks, alerts, paper ledger are). Measure with the health commands below. |
| seed | `~/dev/stampede/data/seed-registry.sqlite` (252 MB, registry only) |
| scratch | `~/dev/stampede/data/live-engine-launch-intel.sqlite` (~360 MB, the launch-intel job's mirror; safe to delete, rebuilt on the next run) |
| agents | `~/Library/LaunchAgents/com.stampede.engine.plist`, `com.stampede.calls.plist`, `com.stampede.launch-intel.plist` |
| logs | `~/dev/stampede/data/launchd-engine.log`, `launchd-calls.log`, `launchd-launch-intel.log` (stdout + stderr, append) |

Engine and poster: `RunAtLoad`, `KeepAlive` with `SuccessfulExit=false` (relaunch after a crash or `kickstart -k`, stay down
after a clean exit), `ThrottleInterval 15`, `ExitTimeOut 10` (SIGTERM, then SIGKILL - two engines on one store would
both assign trade ids, docs/ENGINE.md), `WorkingDirectory ~/dev/stampede`, `PATH` with `~/.local/bin` and
`/opt/homebrew/bin`, `PYTHONUNBUFFERED=1`, the engine additionally `STAMPEDE_ENGINE_LOGS=0`. The engine runs without `--notify` (no banners on a headless machine) and
with `--host 0.0.0.0`, so the web terminal answers on the Tailscale address (http://100.122.123.37:8821/, tailnet
only - the mini is not exposed beyond it and the home LAN); the poster talks to `127.0.0.1:8821`. `__HOME__` in the
plists is replaced by the remote `$HOME` at install time (launchd does not expand `~`).

`com.stampede.launch-intel`: `RunAtLoad` + `StartInterval 600`, no `KeepAlive` (a failed run is retried at the next
tick), runs `deploy/mini/launch_intel_job.py --db data/live-engine.sqlite --horizon 3600 --hours 24` - see "Launch
facts" below for why not the plain `stampede launch-intel`.

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

deploy/mini/install.sh                 # engine + launch-intel timer
deploy/mini/install.sh --with-calls    # ... + poster
deploy/mini/install.sh --only calls    # one agent (engine | calls | launch-intel), repeatable; the others are not touched
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
ssh $M 'launchctl kickstart -k gui/$(id -u)/com.stampede.calls'      # only when calls/ changed: since a903906 the poster resyncs itself after an engine restart
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
ssh $M 'tail -3 ~/dev/stampede/data/launchd-launch-intel.log'      # one line per run: scratch / compute / merge seconds, launches observed
ssh $M 'sqlite3 ~/dev/stampede/data/live-engine.sqlite "select count(*), sum(observed) from launch_intel"'
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

Same for `com.stampede.calls` and `com.stampede.launch-intel` (`launchctl kickstart gui/$(id -u)/com.stampede.launch-intel`
runs the timer job now). To remove everything: bootout all three, delete the plists from `~/Library/LaunchAgents/`,
delete `~/dev/stampede` (the store is the paper track record - copy `data/live-engine.sqlite` first if it matters).

## The poster (`com.stampede.calls`)

`stampede calls --api http://127.0.0.1:8821` reads the mini engine's `/api/stream` and posts to the STAMPEDE Calls
channel with the bot from `.env`. It is installed only with `--with-calls` or `--calls-only`. The poster keeps its
`calls` table in its own store, so a poster on the laptop and a poster on the mini would post every new call twice, and
both would post the 09:00 UTC daily summary: run exactly one, on the mini (docs/CALLS.md: the mini is the
authoritative poster, the laptop runs `--dry-run` only).

Hand-over 2026-09-13: the five calls the laptop poster had posted (message ids 6, 7, 8, 11, 12) were copied into the
mini store's `calls` table (rows dumped with `sqlite3 -json` from the laptop store, inserted over ssh with
`INSERT OR IGNORE` through `stampede.calls.poster.SCHEMA` / `COLS`), so the mini never re-posts those alert keys and
replies to their +30 / +60 outcomes and exits go under the original messages. The mini's `catch-up` reported
`duplicate 0` because its own journal had no `edge_enter` alert yet (its engine started 08:04 UTC, after those calls) -
the dedupe is by alert key against the journal, the rows only matter if the same key fires here.

## Launch facts (`com.stampede.launch-intel`)

The poster prints `launch: dev … · bundle … · tax …` from `launch_intel`; a fresh live store has no such rows
(`launch: n/a`). `stampede launch-intel --db data/live-engine.sqlite` cannot fill them while the engine runs:
`context/launch_intel.py` iterates a SELECT over `launches` and INSERTs into `launch_intel` on the same connection while
the cursor is open; with the engine committing every 250 ms the snapshot upgrade fails at once with
`sqlite3.OperationalError: database is locked` (SQLITE_BUSY_SNAPSHOT, the busy timeout does not apply; 3 of 3 runs,
0.5 s each, 2026-09-13). `deploy/mini/launch_intel_job.py` works around it: it mirrors the tables launch_intel reads
into `data/live-engine-launch-intel.sqlite` (registry tables in full, `trades` and `blocks` of the last 24 h, no
`logs`), runs `launch_intel.compute` there, and merges the rows back in one short `BEGIN IMMEDIATE` transaction
(launches inside the copied trade window replace the live rows, older launches are inserted only when missing).
Measured on the mini: first run 6.6 s (426k rows inserted, the engine's writer waited 1.5 s once), steady state 5-10 s
per run of which the write lock is held < 0.5 s (`replaced ~750, inserted 0`); with `ProcessType Background` the same
run took 54 s and held the lock 4 s, so the plist does not set it. Rows: 427k launches, ~750 `observed` (launched since
the engine started, the ones with dev / bundle / tax facts); `launch_line` renders e.g.
`launch: dev 1.7 % · bundle 0 · tax 0 bps`. Fix for the CLI itself (owner of `context/launch_intel.py`): materialize the
`launches` cursor (`.fetchall()`) before the first `flush()`, or write through a second connection; then the plist can
run `stampede launch-intel --db data/live-engine.sqlite --horizon 3600 --no-report` directly and the job file goes.

Caveat of the 10-minute cadence: a coin alerted less than ~10 min after its launch has no row yet, and the poster caches
the miss per token (`LaunchFacts.cache`), so that call keeps `launch: n/a`. A shorter `StartInterval` (120 s costs ~6 s
of CPU per run, the scratch store is topped up, not rebuilt) or a cache expiry in the poster would close it.

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
  no longer lists `logs`); without them ~2 GB/day (5 MB per 3 min), 665 MB at 08:46 UTC.
- Update to `3cc602c` (08:38 UTC): `git pull --ff-only && uv sync`, `kickstart -k` engine then poster; 8 min later
  5,222 blocks, p50 106.0 / p95 204.2 / p99 464 ms, 2 gaps (70 blocks, the restart window) all backfilled, 0 unfilled,
  writer 0 errors, RSS 425 MB, CPU 15 %.

## Resolved: the poster's resume across an engine restart (`a903906`)

Observed 2026-09-13 08:20 UTC on the mini: after an engine restart the poster resumed with a stale `Last-Event-ID`
(per-process counters), the server dropped every event with a smaller id and sent no keepalive, so the poster was blind
until the new counter passed the old id (seconds after a short run, hours after a long one). Fixed in `a903906` on both
sides: the hello carries `started_at` and reports `replay_gap: true` for a stale id; the poster drops its cursor,
re-reads the journal and reconnects without the id. Verified on the mini 08:47 UTC (`kickstart -k` of the engine only,
poster untouched): `stream connected (resume from 28272)` -> `resync (replay gap): dropping Last-Event-ID 28272,
re-reading the journal` -> `catch-up: {...}` -> `stream connected`, `/api/perf` `subscribers 1` with the new counter at
1,723.

## Not done on the mini (yet)

- Log rotation of `data/launchd-*.log` (a few MB per day of uvicorn access lines; truncate with `: > file` when needed,
  or point `newsyslog` at them - that needs sudo).
- `stampede track-record` for the mini store (`--db data/live-engine.sqlite --api http://127.0.0.1:8821`) is run by hand.
