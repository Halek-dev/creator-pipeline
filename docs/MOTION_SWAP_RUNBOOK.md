# Motion swap runbook (Wan Animate 2 on a rented GPU)

Wan Animate 2 (Alibaba, open source, August 2026) takes a **reference image** (who) and a **driving video** (what motion) and produces your character performing that motion. No pose extraction, no extra node packs.

Verified 13 Oct 2026 on a RunPod A40: three clips rendered, photoreal test scored 23/25. About 7.5 minutes per 81-frame clip on an A40, roughly half that on an L40S. A session costs a few dollars.

## 1. What you need

- A RunPod account with credit.
- A **reference image** of the character: full body, facing camera, face clearly visible, plain background is best. Our Z-Image/SD stills work.
- A **driving video**: one person, similar framing to the reference, at least **81 frames** (about 3.5 s at 24 fps).
- For the very first run, the official sample image and video are downloaded automatically.

## 2. One-time: create a network volume (keeps the models between pods)

RunPod → **Storage** → **Network Volumes** → **New Network Volume**: 100 GB, in a datacenter that lists **L40S** as available. About $7/month. Everything persistent (models, Python environment, inputs, outputs) lives on it under `/workspace/creator-data`, so a new pod is ready in a couple of minutes and you can **Terminate** pods freely.

## 3. Start a pod

RunPod → **Pods** → **Deploy**:

| Setting | Pick |
|---|---|
| Network volume | the one from step 2 (mounts at `/workspace`) |
| GPU | **L40S 48 GB** (default). A40 48 GB works at half the speed. |
| Template | **RunPod PyTorch 2.8** |
| Container disk | 20 GB (models are on the volume) |
| Expose HTTP ports | 8888 (Jupyter, for uploads/downloads) |

Deploy, wait for **Running**, open the **web terminal** from the Connect tab.

## 4. Install and start (first pod ~15 minutes; later pods ~2 minutes)

```bash
git clone https://github.com/Halek-dev/creator-pipeline.git && cd creator-pipeline && bash scripts/setup.sh wan-animate-2 && bash scripts/start.sh
```

`setup.sh` installs a pinned ComfyUI, links our node pack, and downloads the four model files plus the sample inputs into the volume (skipped when already there). For the **High** tier and the **Finish** option add their models once: `bash scripts/setup.sh wan-animate-2-full finishing` (about 21 GB, kept on the volume). `start.sh` then:

- starts ComfyUI and the **team app** in the background (both survive closing the terminal),
- opens a **public link for the team app** (`https://....trycloudflare.com`) and prints it: share this with the team. A second link opens ComfyUI itself for advanced use,
- starts the **idle watchdog**: the pod stops itself after 20 minutes with no jobs (`IDLE_MINUTES=45 bash scripts/start.sh` to change, `IDLE_MINUTES=0` to disable),
- runs the **smoke test** and prints `RESULT: READY` when nodes, models and a tiny render all pass.

Useful: `bash scripts/start.sh stop` stops everything; `tail -f logs/comfy.log` watches the server; `cat logs/public_url` shows the link again. Set the pod's price so the app's cost column is accurate: `GPU_HOURLY_USD=1.79 bash scripts/start.sh` (default 1.00).

## 5. First run (sample assets)

```bash
cd /creator-pipeline && .venv/bin/python tools/validate_workflow.py workflows/wan_animate2.json && .venv/bin/python tools/batch_run.py workflows/wan_animate2.json --count 1
```

Outputs land in `ComfyUI/output/motion_swap/` (on the volume):

- `result_*.mp4`: the swapped clip (480×848 portrait, 81 frames, driving video's frame rate)
- `compare_*.mp4`: result on the left, driving video on the right, for scoring

## 6. Your own clips

Easiest: open the team app link, upload the image and video there, pick a preset, press Run, and download from the Jobs tab. The terminal route below does the same thing without the app.

Upload the reference image and driving video to `ComfyUI/input/` (Jupyter on port 8888 → file browser → `creator-data/input` → upload). Then:

```bash
.venv/bin/python tools/batch_run.py workflows/wan_animate2.json --count 1 \
  --set "Reference Image.image=our_character.png" \
  --set "Driving Video.file=team_dance_clip.mp4" \
  --set "Character Prompt.text=Character appearance description: <looks only, no motion words>. Background description: <where>." \
  --set "Motion Prompt.text=<what the person is doing>"
```

Prompt rules from the model authors:

- **Character Prompt** describes looks and background only. No motion words. The background is generated from this text; it does not come from either input.
- **Motion Prompt** describes the movement in the driving video, e.g. "a woman dancing with fast arm movements".

Several seeds in one go: add `--count 4 --random "Sampler.noise_seed"`. Many clips: put one JSON object per line in a file and use `--jobs file.jsonl` (long pastes get cut off by the web terminal; a jobs file avoids that).

## 7. Score it

Open the compare video and score with the scorecard in the Motion Swap brief (faces, hands and feet, edges, flicker, camera). Do the same clip on the team's current tool and compare totals.

## 8. Knobs worth trying

All are inputs on the `Animate` node, settable with `--set "Animate.<name>=<value>"`:

| Input | Default | Effect |
|---|---|---|
| `reference_image_strength` | 1.0 | Above 1 tightens identity (less face drift); below 1 lets the prompt restyle |
| `pose_strength` | 1.0 | Below 1 loosens motion adherence; above amplifies it |
| `pose_end_percent` | 1.0 | 0.7 keeps the choreography but loosens fine detail |
| `length` | 81 | Frames per clip. Longer needs chaining (Phase 2) |

Sampler: `Scheduler.steps` (10) and `Sampler.noise_seed`.

## 9. Troubleshooting

| Problem | Fix |
|---|---|
| Pod stopped by itself | The idle watchdog did its job. Start a new pod; the volume has everything. |
| RunPod's port-8188 link shows 403 | Use the public link `start.sh` printed (`cat logs/public_url`). |
| `Can't reach ComfyUI` | `bash scripts/start.sh` (the terminal that ran it was probably closed before Phase 0; now it runs in the background). |
| Out of memory | `--set "Model Cache.device=cpu"` (slower, saves VRAM). |
| "not divisible by 16" | Keep width/height multiples of 16. Defaults (480×848) already are. |
| Driving video shorter than 81 frames | Use a longer clip, or lower `Animate.length` and `Driving Trimmed.length` together (49, 65, 81). |
| Wrong person animated | Driving video must contain one person. Crop it first. |
| Face drifts | Raise `reference_image_strength` to 1.2–1.5; use a sharper reference with the face clearly visible. |
| Need a longer video | Chaining 81-frame chunks is Phase 2 of the roadmap. |

## 10. Stop

The watchdog stops the pod when idle. To stop now: RunPod → pod → **Terminate** (the volume keeps everything). Download clips first if you want them locally; they also stay on the volume.

## Cost sense

L40S about $1/hr, A40 about $0.60/hr; a clip is about $0.08 either way. Volume about $7/month. A forgotten pod costs $14–24/day, which is what the watchdog prevents.
