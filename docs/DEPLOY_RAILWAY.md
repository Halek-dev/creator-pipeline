# Hosting the team app on Railway

The app is a small Python service (FastAPI plus static files). Hosted, it has no ComfyUI next to it: every render goes to the RunPod serverless endpoint and every file lives on the network volume, reached over RunPod's S3 API. The host only needs Python, a few hundred MB of RAM and a public URL.

Why Railway and not Vercel: Vercel runs short-lived functions. The app keeps a render thread alive for the whole job (minutes to an hour), polls the endpoint, and keeps job records on disk. That needs an always-on process, which Railway (or Render, Fly.io) gives you. Railway bills by usage; this app idles at roughly $1 to $3 a month.

What is already in the repo for this:

| File | Role |
|---|---|
| `Dockerfile` (repo root) | The app image: Python 3.12, `app/`, `tools/`, `workflows/`. Nothing from ComfyUI |
| `railway.json` | Tells Railway to build that Dockerfile and to check `/healthz` |
| `.dockerignore` | Keeps models, docs and the worker out of the image |
| `APP_DATA` env | One writable folder for uploads, inputs, outputs and logs (`/data` in the image) |

## Step by step (about 15 minutes)

1. **Create the project.** railway.com, New Project, Deploy from GitHub repo, choose `Halek-dev/creator-pipeline`, branch `master`. Railway finds `railway.json` and builds the root `Dockerfile`. The first build takes 2 to 4 minutes.

2. **Attach a volume** so job records survive redeploys. In the project canvas right-click the service, Attach Volume, mount path `/data`. 1 GB is plenty: clips are fetched from the RunPod volume on demand and can be dropped at any time.

3. **Variables.** Service, Variables tab, add these (RAW editor accepts `KEY=value` lines):

   | Variable | Value |
   |---|---|
   | `APP_PASSWORD` | the team password. Without it anyone with the link can queue renders |
   | `RENDER_BACKEND` | `endpoint` |
   | `RUNPOD_ENDPOINT_ID` | the endpoint id, for example `mb0p16x9aea9n8` |
   | `RUNPOD_API_KEY` | RunPod API key (read and write) |
   | `RUNPOD_VOLUME_ID` | the network volume id |
   | `RUNPOD_S3_ENDPOINT` | `https://s3api-eu-ro-1.runpod.io` (the volume's data centre) |
   | `RUNPOD_S3_KEY` | S3 access key, `user_...` |
   | `RUNPOD_S3_SECRET` | S3 secret, `rps_...` |
   | `ENDPOINT_VRAM_GIB` | `48` if the endpoint has 48 GB cards ticked, `32` if only 32 GB. The VRAM guard uses it |
   | `GPU_HOURLY_USD` | `1.22`, the fallback price when a worker's card is not in `app/presets.yaml` `serverless_rates` |
   | `ANTHROPIC_API_KEY` | optional, enables Describe |
   | `COMFY_API_KEY` | optional, enables Seedance 2.5 (billed in Comfy credits; runs through the endpoint's ComfyUI) |

   `PORT` and `RAILWAY_GIT_COMMIT_SHA` are set by Railway; the app reads both.

4. **Public URL.** Service, Settings, Networking, Generate Domain. Railway gives `something.up.railway.app` with HTTPS. Share that link and the password with the team only.

5. **Check.** Open the URL: the login page appears, then the app with the status chip reading `Serverless: ...`. The Library fills from the volume at start-up (the deploy log prints `synced N job record(s) from the volume`). Queue a Standard render to confirm the full path.

## Day to day

- **Updates**: push to `master`; Railway rebuilds and restarts in a few minutes. A restart logs everyone out (session cookies are per process) and resumes unfinished jobs from the volume records.
- **Keys**: change them in Variables and redeploy. Nothing is stored in the repo or in the image.
- **Costs**: the Costs page prices each serverless render at the card the worker reported (`serverless_rates` in `app/presets.yaml`, RunPod list prices as of 17 Oct 2026). Confirm against RunPod Billing.
- **Logs**: service, Deployments, View Logs. Render errors are also on each clip's card and in its details drawer.

## Limits to know

- One render at a time, as on the pod: the endpoint has max workers 1. Raise both when the team grows.
- Uploads go through the app (600 MB limit), then to the volume. A 1 GB Railway volume holds the local copies; old copies can be deleted from `/data/uploads` and `/data/input` without losing anything, the volume keeps the originals.
- Seedance 2.5 on the hosted app runs on an endpoint worker (ComfyUI makes the API call from there), so it costs a minute or two of worker time on top of the Comfy credits.
