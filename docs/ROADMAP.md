# Creator Pipeline roadmap

Agreed 13 Oct 2026. Source of truth for what we build, in what order, and what "done" means for each phase. Update this file when scope changes.

## Goal

A motion swap pipeline the whole team can use from a browser link, on a GPU we rent by the hour, that produces clips we would post without explaining them. Built on open models we control, with paid models (Kling, Seedance) available as options, not dependencies.

## Baseline (done)

- Node pack (Character Prompt, Prompt Library, Folder Loader, Reality Finish), batch runner, workflow validator, setup scripts, model downloader.
- Wan Animate 2 workflow rendered three clips on a RunPod A40: stylised sample 22/25, photoreal streetwear woman + street dance **23/25**, gallery clip pending score. About 7.5 min per 81-frame clip at 480×848. Session cost about $1.
- INSTARAW livestream reviewed: their product is stills tooling; their video answer is "pay Seedance". We are ahead on motion swap.

## Operating model

**Pod sessions.** Rent a GPU by the hour ($0.60–1.00), render in batches, pod stops itself when idle. About **$0.08 per clip**. Serverless (pay per clip, $0 idle) stays a later option for on-demand use; the same workflows run on both.

Default GPU: **L40S 48 GB** (about $1/hr, ~4 min per clip). A40 works at half the speed for the same price per clip.

---

## Phase 0: Session foundations

Goal: a fresh pod is usable in under 5 minutes and cannot waste money.

| # | Deliverable | Detail |
|---|---|---|
| 0.1 | Network volume | 100 GB RunPod volume holding all models (~60 GB by Phase 3). Pods mount it; no re-downloads. ~$7/month. |
| 0.2 | Idle watchdog | `start.sh` stops the pod after 20 min with no ComfyUI jobs (configurable). |
| 0.3 | Reliable web access | Tunnel (cloudflared) started with ComfyUI, giving a stable HTTPS link; removes the RunPod proxy 403. |
| 0.4 | Boot smoke test | On start: ComfyUI up, node pack loaded, models present, one tiny render passes. Prints a green/red summary. |
| 0.5 | Linux fixes | Error messages reference `start.sh` on Linux; `batch_run` handles terminal paste limits (jobs file first-class). |

Done when: terminate pod → new pod → working link in under 5 minutes; leave it alone → it stops itself; cost visible in RunPod matches the session.

Effort: 2–3 days. Test cost: ~$3.

---

## Phase 1: Team app on the pod

Goal: a non-technical teammate renders a clip from a link, with nothing to paste.

| # | Deliverable | Detail |
|---|---|---|
| 1.1 | App (Gradio, one file in the repo) | Upload reference image + driving video, pick a **preset**, choose seeds (1–4) and **quality tier** (Fast / Standard / High), click Run. |
| 1.2 | Queue and history | Job list with status and progress; each job stores its full settings, seed and workflow version; download result and compare clip. |
| 1.3 | Presets file | `presets.yaml`: name, character prompt, background prompt, motion prompt, knobs. Edited by you; no code. Ships with 6 starter presets. |
| 1.4 | Preflight checks | Before queueing: image resolution and face visibility, video has ≥81 frames and one person, aspect warnings. Clear messages, no jargon. |
| 1.5 | Job sheet mode | CSV/JSONL of many jobs → one click → overnight batch. |
| 1.6 | Cost line | Estimated and actual GPU cost per job and per session, shown in the app. |

Done when: a teammate with no instructions beyond "open this link" renders and downloads a clip; every clip's settings can be reproduced from history.

Effort: 3–4 days. Test cost: ~$3.

---

## Phase 2: Quality (the "better than 23/25" list)

Goal: a clip you would post without explaining it; 1080p, smooth, 5–10 s.

| # | Deliverable | Detail |
|---|---|---|
| 2.1 | Input guide | One-page guide + in-app hints: reference photo rules (sharp, 2K, full body, plain background, neutral pose) and driving video rules (stable camera, even light, full body, 24 fps, no motion blur). |
| 2.2 | Multi-seed with contact sheet | 3–4 seeds per job; the app builds a contact sheet and face strip so picking takes seconds. |
| 2.3 | High tier | Full Wan Animate 2 model + lightx2v LoRA, 720p render (A40/L40S have the memory). ~2× render time. |
| 2.4 | Finishing chain | SeedVR2 3B int8 video upscale ×2 (→ ~960×1696) → FILM interpolation 24→48 fps → Reality Finish (grain 0.01, slight warmth). Runs as a second pass; toggle per job. |
| 2.5 | Knob presets | `reference_image_strength` 1.2–1.3 for identity; `pose_end_percent` 0.7 to loosen rigid motion. Exposed as "Identity hold" and "Motion looseness" sliders. |
| 2.6 | Chaining | 5–10 s clips via `continue_motion` + `video_frame_offset` across 81-frame chunks. |

Done when: the Run A pair scores ≥24/25 at High tier on the scorecard; a 1080p 48 fps 8-second clip exports from the app.

Effort: 4–5 days. Test cost: ~$8. Downloads: +16.5 GB (full model) +6 GB (SeedVR2) +0.5 GB (FILM).

---

## Phase 3: Backgrounds (A, B, C)

Goal: choose where the clip happens: described, the reference image's scene, or the original video's scene.

| # | Deliverable | Detail |
|---|---|---|
| 3A | Auto background prompt | A vision model describes the reference image's scene (or a driving-video frame) into a background prompt. App option: "Background: preset / from reference image / from video / custom". ~1 day. |
| 3B | Replace inside the original video | **SCAIL-2 int8** workflow (all core ComfyUI nodes): SAM3 tracks the person in the driving video → mask → `WanSCAILToVideo` with the DPO LoRA → colour transfer to match the original. Keeps the real background, lighting and camera. 2–3 days. Models: ~22 GB. |
| 3C | Kling 3 Motion Control | API workflow using the installed `KlingMotionControl` node: reference image in its own scene + driving video. Paid per clip via Comfy credits (your account, ~$20 to start). Shown in the app as "Kling (paid)". ~1 day. |
| 3.4 | Three-way comparison | Same input pair through Wan Animate 2, SCAIL-2 and Kling; scorecard filled; written recommendation per use case. |

Done when: a teammate can pick any of the four background modes from the app and get a clip; the comparison doc exists.

Effort: 5–7 days. Test cost: ~$8 GPU + ~$20 Kling credits.

---

## Phase 4: Hardening and handoff

| # | Deliverable |
|---|---|
| 4.1 | Team guide: start a pod, open the link, pick a preset, download, stop. One page with screenshots. |
| 4.2 | Pinned versions (ComfyUI commit, node pack, models) and an update procedure. |
| 4.3 | Monthly cost summary from job history. |
| 4.4 | Optional: "Start pod" button in the app (RunPod API); serverless deployment path documented. |

Effort: 2–3 days.

---

## Timeline and budget

| Phase | Build | Test GPU | Other |
|---|---|---|---|
| 0 Foundations | 2–3 days | ~$3 | volume ~$7/month |
| 1 Team app | 3–4 days | ~$3 | |
| 2 Quality | 4–5 days | ~$8 | |
| 3 Backgrounds | 5–7 days | ~$8 | Kling credits ~$20 |
| 4 Handoff | 2–3 days | ~$2 | |
| **Total** | **~4 weeks** | **~$25** | |

Phases run in order; 3A and 3C can start during Phase 2 if wanted.

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Hands smear in fast motion (model ceiling) | Some choreography stays imperfect | Multi-seed picking, slower moves, High tier; set expectations |
| SCAIL-2 needs a recent ComfyUI | 3B blocked until updated | Pin a ComfyUI version that has it; verify on Phase 0 smoke test |
| GPU sold out on RunPod | Session delayed | Volume region with several GPU types; A40 fallback |
| Kling/Comfy credit changes | 3C cost drift | Shown per job; optional path |
| Platform policy (impersonation, AI labelling, TikTok motion-control shadowbans) | Account bans | Original characters only; consent for real people; label AI content; vary content types |
| Maintenance | Breakage after updates | Pinned versions, smoke test, update procedure |

## Decisions needed from you

1. Default GPU: **L40S** (recommended) or A40.
2. Approve ~$20 Comfy credits when Phase 3C starts.
3. Starter presets: I draft six; you edit names and looks.
4. Who on the team tests each phase (one person is enough).

## Out of scope (for now)

Public web app with logins, desktop installer, NSFW tooling, training LoRAs for Wan Animate 2 (unproven), lip-sync.
