#!/usr/bin/env bash
# Start ComfyUI on a Linux GPU box, with a public link and an idle watchdog.
#
#   bash scripts/start.sh                       # ComfyUI + public tunnel + watchdog (stops the pod after 20 idle minutes)
#   IDLE_MINUTES=45 bash scripts/start.sh       # longer idle allowance
#   IDLE_MINUTES=0 bash scripts/start.sh        # no watchdog
#   NO_TUNNEL=1 bash scripts/start.sh           # local only
#   GPU_HOURLY_USD=1.79 bash scripts/start.sh   # this pod's price per hour, so the app's cost column is right
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
    for name in comfy app tunnel app_tunnel watchdog; do
        if [ -f "$LOGS/$name.pid" ] && kill -0 "$(cat "$LOGS/$name.pid")" 2>/dev/null; then
            kill "$(cat "$LOGS/$name.pid")" && echo "stopped $name"
        fi
        rm -f "$LOGS/$name.pid"
    done
}
if [ "${1:-}" = "stop" ]; then stop_all; exit 0; fi

# "restart-app": restart only the team app (after a git pull), leaving ComfyUI, its queue and the tunnels alone.
if [ "${1:-}" = "restart-app" ]; then
    if [ -f "$LOGS/app.pid" ] && kill -0 "$(cat "$LOGS/app.pid")" 2>/dev/null; then kill "$(cat "$LOGS/app.pid")"; sleep 2; fi
    rm -f "$LOGS/app.pid"
    COMFY_SERVER="http://127.0.0.1:$PORT" APP_PORT="${APP_PORT:-7860}" GPU_HOURLY_USD="${GPU_HOURLY_USD:-1.0}" nohup "$PY" app/web/server.py > "$LOGS/app.log" 2>&1 &
    echo $! > "$LOGS/app.pid"
    sleep 6; echo "app restarted (pid $(cat "$LOGS/app.pid")); link unchanged: $(cat "$LOGS/app_public_url" 2>/dev/null)"; tail -n 3 "$LOGS/app.log"
    exit 0
fi

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

# Team app (port 7860)
APP_PORT="${APP_PORT:-7860}"
if [ -f "$LOGS/app.pid" ] && kill -0 "$(cat "$LOGS/app.pid")" 2>/dev/null; then
    :
else
    COMFY_SERVER="http://127.0.0.1:$PORT" APP_PORT="$APP_PORT" GPU_HOURLY_USD="${GPU_HOURLY_USD:-1.0}" nohup "$PY" app/web/server.py > "$LOGS/app.log" 2>&1 &
    echo $! > "$LOGS/app.pid"
fi

# Public links: one tunnel for the app (team), one for ComfyUI (advanced use)
tunnel() {  # name port -> prints URL
    local name="$1" port="$2" url=""
    if [ -f "$LOGS/$name.pid" ] && kill -0 "$(cat "$LOGS/$name.pid")" 2>/dev/null; then
        :
    else
        nohup "$CF" tunnel --url "http://127.0.0.1:$port" --no-autoupdate > "$LOGS/$name.log" 2>&1 &
        echo $! > "$LOGS/$name.pid"
    fi
    for _ in $(seq 1 30); do
        url="$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOGS/$name.log" | head -1 || true)"
        [ -n "$url" ] && break
        sleep 1
    done
    echo "$url"
}
PUBLIC_URL=""; APP_URL=""
if [ -z "${NO_TUNNEL:-}" ]; then
    CF="$ROOT/bin/cloudflared"
    if [ ! -x "$CF" ]; then
        echo "Downloading cloudflared..."
        curl -sL -o "$CF" https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 && chmod +x "$CF"
    fi
    APP_URL="$(tunnel app_tunnel "$APP_PORT")"
    PUBLIC_URL="$(tunnel tunnel "$PORT")"
    echo "$PUBLIC_URL" > "$LOGS/public_url"
    echo "$APP_URL" > "$LOGS/app_public_url"
fi

if [ "$IDLE_MINUTES" != "0" ]; then
    if [ -f "$LOGS/watchdog.pid" ] && kill -0 "$(cat "$LOGS/watchdog.pid")" 2>/dev/null; then
        kill "$(cat "$LOGS/watchdog.pid")"
    fi
    # Terminate (delete the pod) only when the data actually lives on a network volume; otherwise just stop it.
    ACTION="stop"
    case "$(readlink -f "$ROOT/ComfyUI/output" 2>/dev/null || true)" in /workspace/*) ACTION="terminate";; esac
    [ -n "${CREATOR_DATA:-}" ] && ACTION="terminate"
    nohup "$PY" tools/idle_watchdog.py --minutes "$IDLE_MINUTES" --port "$PORT" --action "$ACTION" > "$LOGS/watchdog.log" 2>&1 &
    echo $! > "$LOGS/watchdog.pid"
fi

"$PY" tools/smoke_test.py --port "$PORT" || true

echo
echo "================  READY  ================"
echo "Team app:  http://127.0.0.1:$APP_PORT"
[ -n "$APP_URL" ] && echo "           $APP_URL   <- share this link with the team"
echo "ComfyUI:   http://127.0.0.1:$PORT"
[ -n "$PUBLIC_URL" ] && echo "           $PUBLIC_URL   (advanced: the node editor)"
[ "$IDLE_MINUTES" != "0" ] && echo "Watchdog: pod will $ACTION itself after 45 idle minutes before the first job, $IDLE_MINUTES after (logs/watchdog.log)"
echo "Logs:    $LOGS/comfy.log"
echo "Stop:    bash scripts/start.sh stop"
echo "========================================="
