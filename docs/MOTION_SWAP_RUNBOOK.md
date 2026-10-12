# Motion swap runbook (Wan Animate 2 on a rented GPU)

Wan Animate 2 (Alibaba, open source, August 2026) takes a **reference image** (who) and a **driving video** (what motion) and produces your character performing that motion. No pose extraction, no extra node packs. This is the same model INSTARAW is promising for their "V3 motion control."

Everything below has been validated structurally against ComfyUI on the laptop. The actual render needs a GPU: budget about **$5–10** for a first session.

## 1. What you need

- A RunPod account (or Vast.ai; the steps are the same after the pod starts).
- A **reference image** of the character: full body, facing camera, plain background is fine. Our Z-Image/SD stills work.
- A **driving video**: one person, similar framing to the reference (full body to full body), at least **81 frames** (about 3 s at 30 fps). Phone footage is fine.
- For the very first run, the official sample image and video are downloaded automatically, so you can test before preparing your own.

## 2. Pick the GPU

| Card | VRAM | Verdict |
|---|---|---|
| RTX 4090 | 24 GB | Works for 480p / 81 frames with the int8 model (one published test). Cheapest option. |
| L40S / RTX 6000 Ada / A6000 | 48 GB | Safe pick for the first session, room for longer clips. |
| A100 / H100 | 80 GB | Faster, not needed for testing. |

Pod settings: **at least 32 GB system RAM**, **80 GB disk**, expose **HTTP port 8188**. The official "RunPod PyTorch" template is fine; we install ComfyUI ourselves.

## 3. Install (about 15 minutes, mostly the 24 GB download)

In the pod's terminal:

```bash
git clone <our repo url> creator-pipeline && cd creator-pipeline
bash scripts/setup.sh wan-animate-2
bash scripts/start.sh
```

`setup.sh wan-animate-2` installs ComfyUI, links our node pack, and downloads the four model files plus the sample reference image and driving video into `ComfyUI/input`.

Open the pod's port-8188 link in a browser to see ComfyUI.

## 4. First run (sample assets)

In a second pod terminal:

```bash
cd creator-pipeline
.venv/bin/python tools/validate_workflow.py workflows/wan_animate2.json
.venv/bin/python tools/batch_run.py workflows/wan_animate2.json --count 1
```

Outputs land in `ComfyUI/output/motion_swap/`:

- `result_*.mp4`: the swapped clip (480×848 portrait, 81 frames, driving video's frame rate)
- `compare_*.mp4`: result on the left, driving video on the right, for scoring

Expect a few minutes per clip on a 4090 on the first run (model load), faster after.

## 5. Your own clips

Upload the reference image and driving video to `ComfyUI/input/` (the ComfyUI web page has an upload button, or use `scp`). Then:

```bash
.venv/bin/python tools/batch_run.py workflows/wan_animate2.json --count 1 \
  --set "Reference Image.image=our_character.png" \
  --set "Driving Video.file=team_dance_clip.mp4" \
  --set "Character Prompt.text=Character appearance description: <looks only, no motion words>.\nBackground description: <where>." \
  --set "Motion Prompt.text=<what the person is doing>"
```

Prompt rules from the model authors:

- **Character Prompt** describes looks and background only. No motion words. The background is generated from this text; it does not come from either input.
- **Motion Prompt** describes the movement in the driving video, e.g. "a woman dancing with fast arm movements".

Try several seeds in one go: add `--count 4 --random "Sampler.noise_seed"`.

## 6. Score it

Open the compare video and score with the scorecard in the Motion Swap brief (faces, hands and feet, edges, flicker, camera). Do the same clip on the team's current tool and compare totals.

## 7. Knobs worth trying

All are inputs on the `Animate` node, settable with `--set "Animate.<name>=<value>"`:

| Input | Default | Effect |
|---|---|---|
| `reference_image_strength` | 1.0 | Above 1 tightens identity (less face drift); below 1 lets the prompt restyle |
| `pose_strength` | 1.0 | Below 1 loosens motion adherence; above amplifies it |
| `pose_end_percent` | 1.0 | 0.7 keeps the choreography but loosens fine detail |
| `length` | 81 | Frames per clip. Longer needs chaining (not in this version) |

Sampler: `Scheduler.steps` (10) and `Sampler.noise_seed`.

## 8. Troubleshooting

| Problem | Fix |
|---|---|
| Out of memory | `--set "Model Cache.device=cpu"` (slower, saves VRAM). Needs the 32 GB system RAM. |
| "not divisible by 16" | Keep width/height multiples of 16. Defaults (480×848) already are. |
| Driving video shorter than 81 frames | Use a longer clip, or lower `Animate.length` and `Driving Trimmed.length` together (multiples of 4 plus 1: 49, 65, 81). |
| Wrong person animated | Driving video must contain one person. Crop it first. |
| Face drifts | Raise `reference_image_strength` to 1.2–1.5; use a sharper reference with the face clearly visible. |
| Need a longer video | Wan Animate 2 chains 81-frame chunks via `continue_motion`. Not wired in this workflow yet; ask and we'll add it once the single-chunk quality is right. |

## 9. Higher quality variant

`bash scripts/setup.sh wan-animate-2-full` adds the non-distilled model plus the lightx2v LoRA (16.5 GB more). It needs a different workflow (LoRA loader, more steps); we'll build it if the distilled output isn't good enough.

## Cost sense

A 4090 is about $0.50–0.70/hr on RunPod; an L40S about $1/hr. Setup plus 10 test clips fits in two hours. Stop the pod when done; keep the volume if you want to skip the download next time.
