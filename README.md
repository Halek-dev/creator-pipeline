# Creator Pipeline

Our own ComfyUI setup for character images and video: a custom node pack, a batch runner, setup scripts and model profiles. Built on free, open-source parts (ComfyUI, Z-Image Turbo, Wan 2.2).

## What's here

| Path | What it does |
|---|---|
| `creator_nodes/` | Our ComfyUI node pack (appears under **creator** in the node menu) |
| `creator_nodes/prompt_library/` | JSON prompt libraries. Add a file here to add a library |
| `tools/batch_run.py` | Queues 10, 50, 100+ runs of a workflow and logs the outputs |
| `tools/download_models.py` | Downloads a model profile from `models.json` |
| `workflows/` | Test workflows in ComfyUI API format |
| `scripts/` | Setup and start scripts for Windows and Linux GPU servers |
| `tests/` | Unit tests for the node logic |

### Nodes

| Node | Inputs | Use |
|---|---|---|
| **Character Prompt** | trigger, shot, scene, style | Builds a prompt that always starts with the LoRA trigger word |
| **Prompt Library** | library, mode, seed | Fills a scene template. `random` picks by seed; `sequential` walks every combination without repeats |
| **Folder Image Loader** | folder, index | Loads one image by index from a folder. Index auto-increments and wraps, so queueing N runs walks the folder |
| **Reality Finish** | grain, vignette, warmth, lens fringe, JPEG quality, seed | Makes renders look like they came from a phone camera |

## Run it on a laptop (CPU only)

No GPU needed. Slow, but enough to test nodes and workflows.

```powershell
.\scripts\setup.ps1 -Cpu
.\scripts\start.ps1 -Cpu
```

Open http://127.0.0.1:8188. Then, in a second terminal:

```powershell
# Finish every image in ComfyUI\input\batch
.venv\Scripts\python tools\batch_run.py workflows\finish_folder.json --count 4 --step "Folder Image Loader.index=0"

# Optional: small test model (about 5 GB) for end-to-end image generation
.venv\Scripts\python tools\download_models.py cpu-test
.venv\Scripts\python tools\batch_run.py workflows\sd_turbo_character.json --count 3 --step "Prompt Library.seed=0" --random "Sampler.noise_seed"
```

Outputs land in `ComfyUI\output`. Each batch writes a log to `batch_runs\`.

## Run it on a rented GPU (RunPod, Vast.ai, Colab, Kaggle)

```bash
git clone <this repo> creator-pipeline && cd creator-pipeline
bash scripts/setup.sh z-image-turbo
bash scripts/start.sh
```

Model profiles: `python tools/download_models.py --list`.

## Batch runner

Address any input as `"<node title>.<input>"` (titles are the `_meta.title` values in the API workflow).

| Flag | Meaning |
|---|---|
| `--count N` | Number of runs |
| `--set KEY=VALUE` | Same value every run |
| `--step KEY=START` | START, START+1, ... per run |
| `--random KEY` | New random seed per run |
| `--jobs file.jsonl` | One JSON object of overrides per line, one run per line |
| `--dry-run` | Show the runs without queueing |

To batch a workflow you built in the UI: **Workflow → Export (API)**, save it in `workflows/`, then run it with the flags above.

## Tests

```powershell
.venv\Scripts\python -m unittest discover tests
```

## Rules for the team

- Only train LoRAs on characters we created or on people who gave written consent.
- Label AI-generated posts where the platform requires it (Instagram and TikTok both do).
- Only install third-party custom nodes from known GitHub repos, and read the code first.
