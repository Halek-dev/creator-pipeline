# The team app (FastAPI + static front end) for a host such as Railway. No ComfyUI inside: renders go to the
# RunPod serverless endpoint and files live on the network volume, so the container only needs Python.
# Build context is the repository root; worker/Dockerfile is the GPU worker and is unrelated to this image.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 APP_DATA=/data RENDER_BACKEND=endpoint
WORKDIR /srv

COPY app/requirements.txt app/requirements.txt
RUN pip install -r app/requirements.txt av pillow

COPY app app
COPY tools tools
COPY workflows workflows

RUN mkdir -p /data
EXPOSE 8080
CMD ["python", "app/web/server.py"]
