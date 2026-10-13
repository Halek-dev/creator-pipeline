#!/usr/bin/env bash
# Set up ComfyUI + the creator node pack on a Linux GPU machine (RunPod, Vast.ai, Colab).
#
#   bash scripts/setup.sh                    # install
#   bash scripts/setup.sh wan-animate-2      # install and download a model profile (see models.json)
#   bash scripts/start.sh                    # then start ComfyUI
#
# Persistent data: if a RunPod network volume is mounted at /workspace, models, inputs, outputs and the
# Python environment live there (/workspace/creator-data), so a fresh pod is ready in a couple of minutes
# instead of re-downloading. Override with CREATOR_DATA=/some/path.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
source "$ROOT/versions.env"

if [ -n "${CREATOR_DATA:-}" ]; then DATA="$CREATOR_DATA"
elif [ -d /workspace ] && [ -w /workspace ]; then DATA=/workspace/creator-data
else DATA="$ROOT/data"; fi
mkdir -p "$DATA/models" "$DATA/input/batch" "$DATA/output" "$DATA/user"
echo "Data directory: $DATA"

# ComfyUI, pinned to the commit we tested
if [ ! -d ComfyUI/.git ]; then
    git init -q ComfyUI
    git -C ComfyUI remote add origin https://github.com/comfyanonymous/ComfyUI.git
fi
if [ "$(git -C ComfyUI rev-parse HEAD 2>/dev/null || true)" != "$COMFYUI_COMMIT" ]; then
    echo "Fetching ComfyUI $COMFYUI_COMMIT"
    git -C ComfyUI fetch -q --depth 1 origin "$COMFYUI_COMMIT"
    git -C ComfyUI checkout -q FETCH_HEAD
fi

# Persistent folders inside ComfyUI
for d in models input output user; do
    if [ -d "ComfyUI/$d" ] && [ ! -L "ComfyUI/$d" ]; then
        cp -rn "ComfyUI/$d/." "$DATA/$d/" 2>/dev/null || true
        rm -rf "ComfyUI/$d"
    fi
    [ -L "ComfyUI/$d" ] || ln -s "$DATA/$d" "ComfyUI/$d"
done

# Python environment, persistent too. Recreated if the system Python changed.
VENV="$DATA/venv"
if ! "$VENV/bin/python" -c "import torch, sys; assert sys.version_info[:2] == tuple(map(int, '$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')'.split('.')))" 2>/dev/null; then
    echo "Creating Python environment in $VENV"
    rm -rf "$VENV"
    python3 -m venv "$VENV"
    "$VENV/bin/python" -m pip install --upgrade pip
    "$VENV/bin/python" -m pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128
fi
[ -L .venv ] || [ -d .venv ] || ln -s "$VENV" .venv
"$VENV/bin/python" -m pip install -q -r ComfyUI/requirements.txt

[ -e ComfyUI/custom_nodes/creator_nodes ] || ln -s "$ROOT/creator_nodes" ComfyUI/custom_nodes/creator_nodes
chmod +x scripts/*.sh

if [ $# -ge 1 ]; then "$VENV/bin/python" tools/download_models.py "$1"; fi

echo
echo "Done. Start ComfyUI with: bash scripts/start.sh"
