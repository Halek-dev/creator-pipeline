#!/usr/bin/env bash
# Worker entry point: ComfyUI in the background, pointed at the network volume, then the RunPod handler.
#   CREATOR_DATA   where models/input/output/user live (default /runpod-volume/creator-data, the pod's /workspace/creator-data)
#   SERVE_API_LOCALLY=true   run the handler as a local HTTP API on port 8000 (testing the image on a pod)
set -euo pipefail
DATA="${CREATOR_DATA:-/runpod-volume/creator-data}"
PORT="${COMFY_PORT:-8188}"
mkdir -p "$DATA/input" "$DATA/output" "$DATA/user"
if [ ! -d "$DATA/models" ]; then
    echo "No models at $DATA/models: attach the creator-data network volume (setup.sh on a pod fills it)." >&2
fi

cd /opt/ComfyUI
python main.py --listen 127.0.0.1 --port "$PORT" \
    --models-directory "$DATA/models" --input-directory "$DATA/input" --output-directory "$DATA/output" --user-directory "$DATA/user" \
    --disable-auto-launch > /tmp/comfy.log 2>&1 &
echo "ComfyUI starting (pid $!), data at $DATA"

cd /opt/worker
if [ "${SERVE_API_LOCALLY:-false}" = "true" ]; then
    exec python handler.py --rp_serve_api --rp_api_host 0.0.0.0 --rp_api_port 8000
fi
exec python handler.py
