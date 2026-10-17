#!/usr/bin/env bash
# Worker entry point: ComfyUI in the background, pointed at the network volume, then the RunPod handler.
#   CREATOR_DATA   where models/input/output/user live (default /runpod-volume/creator-data, the pod's /workspace/creator-data)
#   SERVE_API_LOCALLY=true   run the handler as a local HTTP API on port 8000 (testing the image on a pod)
set -uo pipefail
DATA="${CREATOR_DATA:-/runpod-volume/creator-data}"
PORT="${COMFY_PORT:-8188}"
echo "[worker] data directory: $DATA"
if [ ! -d /runpod-volume ]; then
    echo "[worker] /runpod-volume is not mounted: attach the creator-data network volume to this endpoint (Manage > Network volume)." >&2
fi
if [ ! -d "$DATA/models" ]; then
    echo "[worker] no models at $DATA/models. The volume is either not attached or has a different layout. Listing /runpod-volume:" >&2
    ls -la /runpod-volume 2>&1 | head -20 >&2
    echo "[worker] ComfyUI cannot start without it; the handler will fail every job with this message." >&2
    # keep the handler alive so RunPod shows the error per job instead of restarting the worker forever
    export WORKER_FATAL="models folder missing at $DATA/models (network volume not attached?)"
    cd /opt/worker && exec python handler.py
fi
mkdir -p "$DATA/input" "$DATA/output" "$DATA/user"

cd /opt/ComfyUI
python main.py --listen 127.0.0.1 --port "$PORT" \
    --models-directory "$DATA/models" --input-directory "$DATA/input" --output-directory "$DATA/output" --user-directory "$DATA/user" \
    --disable-auto-launch 2>&1 | tee /tmp/comfy.log &
echo "[worker] ComfyUI starting on port $PORT"

cd /opt/worker
if [ "${SERVE_API_LOCALLY:-false}" = "true" ]; then
    exec python handler.py --rp_serve_api --rp_api_host 0.0.0.0 --rp_api_port 8000
fi
exec python handler.py
