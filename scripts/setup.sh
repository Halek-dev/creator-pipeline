#!/usr/bin/env bash
# Set up ComfyUI + the creator node pack on a Linux GPU machine (RunPod, Vast.ai, Colab, Kaggle).
#   bash scripts/setup.sh                    # install
#   bash scripts/setup.sh z-image-turbo      # install and download a model profile (see models.json)
#   bash scripts/start.sh                    # start ComfyUI on port 8188
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

[ -d ComfyUI ] || git clone --depth 1 https://github.com/comfyanonymous/ComfyUI.git ComfyUI
[ -d .venv ] || python3 -m venv .venv
PY="$ROOT/.venv/bin/python"

"$PY" -m pip install --upgrade pip
"$PY" -m pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128
"$PY" -m pip install -r ComfyUI/requirements.txt

[ -e ComfyUI/custom_nodes/creator_nodes ] || ln -s "$ROOT/creator_nodes" ComfyUI/custom_nodes/creator_nodes
mkdir -p ComfyUI/input/batch

if [ $# -ge 1 ]; then "$PY" tools/download_models.py "$1"; fi

cat > scripts/start.sh <<'EOF'
#!/usr/bin/env bash
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec "$ROOT/.venv/bin/python" "$ROOT/ComfyUI/main.py" --listen 0.0.0.0 --port 8188 "$@"
EOF
chmod +x scripts/start.sh
echo "Done. Start ComfyUI with: bash scripts/start.sh"
