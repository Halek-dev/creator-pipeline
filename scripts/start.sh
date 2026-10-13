#!/usr/bin/env bash
# Start ComfyUI on a Linux GPU box, with a public link and an idle watchdog.
#
#   bash scripts/start.sh                       # ComfyUI + public tunnel + watchdog (stops the pod after 20 idle minutes)
#   IDLE_MINUTES=45 bash scripts/start.sh       # longer idle allowance
#   IDLE_MINUTES=0 bash scripts/start.sh        # no watchdog
#   NO_TUNNEL=1 bash scripts/start.sh           # local only
#   bash scripts/start.sh stop                  # stop everything this script started
#
# Everything runs in the background and survives closing the terminal. Logs are in logs/.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
PORT="${PORT:-8188}"
IDLE_MINUTES="${IDLE_MINUTES:-20}"
LOGS="$ROOT/logs"
mkdir -p "$LOGS" "$ROOT/bin"
cd "$ROOT"

stop_all() {
    for name in comfy tunnel watchdog; do
        if [ -f "$LOGS/$name.pid" ] && kill -0 "$(cat "$LOGS/$name.pid")" 2>/dev/null; then
            kill "$(cat "$LOGS/$name.pid")" && echo "stopped $name"
        fi
        rm -f "$LOGS/$name.pid"
    done
}
if [ "${1:-}" = "stop" ]; then stop_all; exit 0; fi

if [ -f "$LOGS/comfy.pid" ] && kill -0 "$(cat "$LOGS/comfy.pid")" 2>/dev/null; then
    echo "ComfyUI already running (pid $(cat "$LOGS/comfy.pid")). Use 'bash scripts/start.sh stop' first to restart."
else
    nohup "$PY" ComfyUI/main.py --listen 0.0.0.0 --port "$PORT" > "$LOGS/comfy.log" 2>&1 &
    echo $! > "$LOGS/comfy.pid"
    echo "ComfyUI starting (pid $!)..."
fi

for _ in $(seq 1 90); do
    curl -sf -o /dev/null "http://127.0.0.1:$PORT/system_stats" && break
    sleep 2
done
if ! curl -sf -o /dev/null "http://127.0.0.1:$PORT/system_stats"; then
    echo "ComfyUI did not come up. Last log lines:"; tail -n 30 "$LOGS/comfy.log"; exit 1
fi

PUBLIC_URL=""
if [ -z "${NO_TUNNEL:-}" ]; then
    CF="$ROOT/bin/cloudflared"
    if [ ! -x "$CF" ]; then
        echo "Downloading cloudflared..."
        curl -sL -o "$CF" https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 && chmod +x "$CF"
    fi
    if [ -f "$LOGS/tunnel.pid" ] && kill -0 "$(cat "$LOGS/tunnel.pid")" 2>/dev/null; then
        :
    else
        nohup "$CF" tunnel --url "http://127.0.0.1:$PORT" --no-autoupdate > "$LOGS/tunnel.log" 2>&1 &
        echo $! > "$LOGS/tunnel.pid"
    fi
    for _ in $(seq 1 30); do
        PUBLIC_URL="$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOGS/tunnel.log" | head -1 || true)"
        [ -n "$PUBLIC_URL" ] && break
        sleep 1
    done
    echo "$PUBLIC_URL" > "$LOGS/public_url"
fi

if [ "$IDLE_MINUTES" != "0" ]; then
    if [ -f "$LOGS/watchdog.pid" ] && kill -0 "$(cat "$LOGS/watchdog.pid")" 2>/dev/null; then
        kill "$(cat "$LOGS/watchdog.pid")"
    fi
    ACTION="stop"; [ -n "${RUNPOD_VOLUME_ID:-}" ] && ACTION="terminate"
    nohup "$PY" tools/idle_watchdog.py --minutes "$IDLE_MINUTES" --port "$PORT" --action "$ACTION" > "$LOGS/watchdog.log" 2>&1 &
    echo $! > "$LOGS/watchdog.pid"
fi

"$PY" tools/smoke_test.py --port "$PORT" || true

echo
echo "================  READY  ================"
echo "Local:   http://127.0.0.1:$PORT"
[ -n "$PUBLIC_URL" ] && echo "Public:  $PUBLIC_URL   (share this link with the team)"
[ "$IDLE_MINUTES" != "0" ] && echo "Watchdog: pod will $ACTION itself after $IDLE_MINUTES idle minutes (logs/watchdog.log)"
echo "Logs:    $LOGS/comfy.log"
echo "Stop:    bash scripts/start.sh stop"
echo "========================================="
