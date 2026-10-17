# Serverless setup: renders on a RunPod endpoint, app anywhere, nothing billed while idle

What this gives you: press Render on the app, a GPU worker starts, renders, writes the clip to the network volume and stops. The meter runs only during the render (plus the cold start and a short idle window). The app itself runs on the laptop (or any machine) and talks to the volume through RunPod's S3 API, so clips can be fetched and evaluated with no pod running.

Pieces, all in this repo:

| Piece | File | Job |
|---|---|---|
| Worker image | `worker/Dockerfile`, `worker/start.sh`, `worker/handler.py` | Pinned ComfyUI + our nodes; one request = one workflow rendered to the volume |
| Endpoint backend | `app/backends.py` | The app's Runner sends workflows to the endpoint and polls `/status` instead of a local ComfyUI |
| Volume storage | `app/storage.py`, `tools/volume.py` | Inputs up, outputs down over S3; browse and fetch clips from anywhere |

## One-time setup (about 30 minutes, RunPod console)

1. **Keys.** RunPod Settings:
   - API Keys: create one with read and write. This is `RUNPOD_API_KEY`.
   - S3 API Keys: create one. You get an access key (`user_...`) and a secret (`rps_...`). These are `RUNPOD_S3_KEY` and `RUNPOD_S3_SECRET`. The S3 endpoint for our datacentre is `https://s3api-eu-ro-1.runpod.io`.
   - Storage: note the network volume id of `creator-data`. This is `RUNPOD_VOLUME_ID`.
2. **Connect GitHub.** Settings, Connections, GitHub, Connect, choose the `creator-pipeline` repository.
3. **Create the endpoint.** Serverless, New Endpoint, Import Git Repository, pick `creator-pipeline`:
   - Branch `master`, Dockerfile path `worker/Dockerfile`, Next.
   - Endpoint name `creator-standard`, type Queue.
   - GPUs, in priority order: RTX PRO 4500 (32 GB), RTX 5090 (32 GB), 48 GB (A40 / A6000). Up to three.
   - Active workers 0, max workers 1 (the app renders one seed at a time; raise later).
   - Idle timeout 60 s (seeds of one job reuse the warm worker), execution timeout 3600 s, FlashBoot on.
   - Network volume: `creator-data` (this pins the endpoint to EU-RO-1).
   - Environment: `CREATOR_DATA=/runpod-volume/creator-data`, `RUNPOD_INIT_TIMEOUT=800`.
   - Deploy. The first build takes 10 to 20 minutes (torch download); watch the Builds tab. Note the endpoint id, this is `RUNPOD_ENDPOINT_ID`.
   - A second endpoint `creator-large` with RTX PRO 6000, A100, B200 is the home for High and Finish renders; same image, same volume. Optional at first.
4. **Rebuilds.** Pushing to GitHub does not update the endpoint. Create a GitHub release (tag) when the worker or ComfyUI pin changes; RunPod builds that release.

## Running the app on the laptop (or any machine)

To host the app on Railway instead, so the team opens one link and nobody runs anything locally, follow [DEPLOY_RAILWAY.md](DEPLOY_RAILWAY.md); the variables below are the same ones you paste into Railway.

The app needs no GPU. Export the keys in the terminal, then start it. Seedance jobs still need a ComfyUI next to the app (the CPU one), the GPU engines go to the endpoint.

```bash
export RENDER_BACKEND=endpoint RUNPOD_ENDPOINT_ID=<id> RUNPOD_API_KEY=<key> \
       RUNPOD_VOLUME_ID=<volume id> RUNPOD_S3_ENDPOINT=https://s3api-eu-ro-1.runpod.io RUNPOD_S3_KEY=<user_...> RUNPOD_S3_SECRET=<rps_...> \
       GPU_HOURLY_USD=1.15 APP_PASSWORD=<team password>
python app/web/server.py
```

`GPU_HOURLY_USD` is the serverless flex rate of the first GPU in the list (RTX PRO 4500 $1.15, 48 GB $1.22, L40S $1.75). Costs are computed from the billed `executionTime` the endpoint reports, so the Costs page shows what RunPod charges, with the cold start listed apart on each seed.

What happens per job: inputs are copied to the volume over S3 (once per file), the workflow is sent to `/run` with an execution timeout of twice the estimate (12 minute floor), the app polls `/status`, and when the job completes the result and compare files are fetched from the volume so the Library, sheets and downloads work exactly as on a pod.

## Looking at clips without a pod

```bash
python tools/volume.py jobs                      # every job on the volume with status and note
python tools/volume.py ls                        # job folders
python tools/volume.py ls output/motion_swap/<job id>
python tools/volume.py get output/motion_swap/<job id>/s123_compare_00001_.mp4
```

## Testing the image before trusting the endpoint

Deploy the same image as a pod template (Pods, Deploy, custom image from the endpoint's build, volume attached, `SERVE_API_LOCALLY=true`), then from the pod's shell:

```bash
curl -s -X POST http://127.0.0.1:8000/run -H 'Content-Type: application/json' -d @/opt/worker/test_input.json
```

A tiny grey PNG appears under `/workspace/creator-data/output/smoke/`. Then queue a real Wan Standard job from the app with `RENDER_BACKEND=endpoint`.

## Known limits

- Cold start: 1 to 4 minutes billed per fresh worker (ComfyUI import plus 16 to 24 GB of weights read from the volume). The 60 s idle window keeps a worker warm between seeds of one job.
- The endpoint is pinned to EU-RO-1 by the volume; when its GPUs are out of stock jobs wait in the queue. The status chip shows worker and queue counts from `/health`.
- One app instance writes job records; do not run two apps against the same volume.
- Seedance never goes to the endpoint (it would bill GPU time for an API wait); it runs through the CPU ComfyUI beside the app.
