#!/usr/bin/env bash
# Install (or reinstall) the STAMPEDE launchd agents on the Mac mini from this laptop.
#
#   deploy/mini/install.sh                       # engine + launch-intel timer (com.stampede.engine, com.stampede.launch-intel)
#   deploy/mini/install.sh --with-calls          # ... + Telegram poster (com.stampede.calls)
#   deploy/mini/install.sh --only calls          # (re)install one agent; repeatable (engine | calls | launch-intel)
#   deploy/mini/install.sh --calls-only          # = --only calls
#   MINI_HOST=user@host deploy/mini/install.sh
#
# The target must be the mini's *console* account (auto-login user `argona`, uid 501, over Tailscale): a
# LaunchAgent lives in that user's gui/<uid> domain, which only exists for a logged-in user. The `mac-mini`
# ssh alias of this laptop lands on the `agent` account (no GUI session, no sudo), where
# `launchctl bootstrap gui/...` fails with "Domain does not support specified action".
# Prerequisites on the mini (docs/DEPLOY-MINI.md): ~/dev/stampede cloned, `uv sync` done, .env in place,
# data/live-engine.sqlite seeded. The plists use __HOME__ because launchd does not expand ~; the placeholder is
# filled with the remote $HOME here. Reinstalling boots the agent out first (the engine restarts once). For a
# code update use `launchctl kickstart -k` instead (see the doc).
set -euo pipefail

HOST="${MINI_HOST:-argona@100.122.123.37}"
SERVICES=(engine launch-intel)
ONLY=()
while [ $# -gt 0 ]; do
    case "$1" in
        --with-calls) SERVICES=(engine launch-intel calls) ;;
        --calls-only) ONLY+=(calls) ;;
        --only) shift; ONLY+=("$1") ;;
        -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
    shift
done
[ ${#ONLY[@]} -gt 0 ] && SERVICES=("${ONLY[@]}")
for s in "${SERVICES[@]}"; do
    case "$s" in engine|calls|launch-intel) ;; *) echo "unknown agent: $s (engine | calls | launch-intel)" >&2; exit 2 ;; esac
done

HERE="$(cd "$(dirname "$0")" && pwd)"
RHOME="$(ssh "$HOST" 'printf %s "$HOME"')"
RUID="$(ssh "$HOST" 'id -u')"
echo "target: $HOST (uid $RUID, home $RHOME)"

ssh "$HOST" 'test -x ~/dev/stampede/.venv/bin/python' \
    || { echo "~/dev/stampede/.venv/bin/python missing on $HOST: clone + uv sync first (docs/DEPLOY-MINI.md)" >&2; exit 1; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
for s in "${SERVICES[@]}"; do
    sed "s#__HOME__#$RHOME#g" "$HERE/com.stampede.$s.plist" > "$TMP/com.stampede.$s.plist"
    plutil -lint "$TMP/com.stampede.$s.plist"
done

ssh "$HOST" 'mkdir -p ~/Library/LaunchAgents ~/dev/stampede/data'
scp -q "$TMP"/com.stampede.*.plist "$HOST:$RHOME/Library/LaunchAgents/"

for s in "${SERVICES[@]}"; do
    # bootout returns before the process is gone (SIGTERM, SIGKILL after ExitTimeOut); bootstrapping while the old
    # instance is still registered fails with "Bootstrap failed: 5: Input/output error", so wait for it to leave
    ssh "$HOST" "if launchctl print gui/$RUID/com.stampede.$s >/dev/null 2>&1; then
                     launchctl bootout gui/$RUID/com.stampede.$s
                     for i in \$(seq 1 30); do launchctl print gui/$RUID/com.stampede.$s >/dev/null 2>&1 || break; sleep 1; done
                 fi
                 launchctl enable gui/$RUID/com.stampede.$s
                 launchctl bootstrap gui/$RUID $RHOME/Library/LaunchAgents/com.stampede.$s.plist"
    echo "com.stampede.$s: bootstrapped"
done

sleep 3
for s in "${SERVICES[@]}"; do
    ssh "$HOST" "launchctl print gui/$RUID/com.stampede.$s | grep -E '^.(state|pid|last exit code) =' | sed 's/^/  /'" || true
done
echo "health: ssh $HOST 'curl -s http://127.0.0.1:8821/api/perf' | python3 -c \"import json,sys; p=json.load(sys.stdin); print(p['blocks'], p['latency_ms']['block_to_emit'])\""
