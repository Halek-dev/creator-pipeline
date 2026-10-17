# RunPod Serverless worker for the creator-pipeline renders.
# Pinned ComfyUI (versions.env) + our node pack + a handler that queues one API-format workflow per request.
# Models, inputs and outputs live on the network volume mounted at /runpod-volume (CREATOR_DATA), never in the image.
FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04

ARG COMFYUI_COMMIT=e8665ca8e4089ab917567cdb491308e1397663d4
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    CREATOR_DATA=/runpod-volume/creator-data COMFY_PORT=8188

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-venv python3-pip git ffmpeg libgl1 libglib2.0-0 ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt
RUN python3 -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH

# ComfyUI at the exact commit the pod uses (same recipe as scripts/setup.sh)
RUN git init -q ComfyUI && git -C ComfyUI remote add origin https://github.com/comfyanonymous/ComfyUI.git \
    && git -C ComfyUI fetch -q --depth 1 origin "$COMFYUI_COMMIT" && git -C ComfyUI checkout -q FETCH_HEAD \
    && rm -rf ComfyUI/.git

RUN pip install --upgrade pip \
    && pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128 \
    && pip install -r ComfyUI/requirements.txt runpod pyyaml

# our node pack and the ComfyUI client the handler reuses
COPY creator_nodes /opt/ComfyUI/custom_nodes/creator_nodes
COPY tools/comfy_client.py /opt/worker/comfy_client.py
COPY worker/handler.py worker/start.sh /opt/worker/
RUN chmod +x /opt/worker/start.sh

CMD ["/opt/worker/start.sh"]
