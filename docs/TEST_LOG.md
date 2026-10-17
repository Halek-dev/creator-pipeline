# Test log

Scores use the five-criterion scorecard from the Motion Swap brief (face, hands/feet, edges/background, stability, camera/timing; 1–5 each). "From stills" means scored on extracted frames; flicker needs eyes on the moving clip. Dollar figures use the pod's real hourly price.

## Phase 1 baseline (13–14 Oct 2026)

| Run | Inputs | Engine / tier | GPU | Time per clip | Cost | Score | Notes |
|---|---|---|---|---|---|---|---|
| Sample 1 | Pink-hair stylised render + street dance (official samples) | Wan Animate 2 distilled, 480×848, 10 steps | A40 | 469 s (incl. model load) | ~$0.08 | 22/25 from stills | Identity 5, hands 3 (gold mitts), background 5, stability 4, timing 5 |
| Run A | Photoreal streetwear woman + male street dance | same | A40 | 469 s | ~$0.08 | **23/25** from stills | Identity 5, hands 4, background 5, stability 4 (hair bun flicker), timing 5 |
| Run B | Same woman + gallery dance clip | same | A40 | 464 s | ~$0.08 | not scored | Pod terminated before review |
| App job fbb6 | Run A pair via the team app, Studio, 2 seeds | same | RTX PRO 4500 | 400 s, 318 s | $0.14 | same level as Run A | First render through the app; found and fixed the Gradio allowed_paths bug |

## Phase 2 verification (14 Oct 2026, A100 80 GB PCIe, $1.79/hr)

Driving clip: `street_dancer_long.mp4` (the street dancer forward then reversed, 241 frames, 24 fps, no audio track). Reference: `reference_streetwear_character.png`. Scene: Studio.

| Job | Tier · passes · Finish | Seeds | Time per seed | Cost per seed | Output | Score | Notes |
|---|---|---|---|---|---|---|---|
| A (fe2e) | Standard · 2 passes · Finish | 2 | 1126 s, ~1030 s | ~$0.54 | compare 161 f @24; final **960×1696, 321 f @48 fps**, 6.7 s | not scored (Standard baseline known) | Chaining clean: no visible jump at the pass join (frames 78–83); upscale and interpolation correct |
| B (7bfa) | High · 1 pass | 2 | ~560 s each | ~$0.28 | 720×1280, 81 f | **23–24/25** from stills | Identity 5, hands 4 (one smear in the fastest frame), background 5, stability 4 (fabric stays fabric; bun consistent), timing 5. Sharper than Standard; same choreography |
| C (fe32) | High · 2 passes · Finish | 2 | 2057 s (seed 1) | ~$1.02 | **1440×2560, 321 f @48 fps**, 6.7 s, 50 MB | consistent with B | The "post it" clip. Seed 2 rendered after the app-restart fix (adopted mid-render) |
| Audio check | LoadVideo → frames + audio → save (no model) | 1 | seconds | — | 720×1280 with AAC audio | — | Proves audio passthrough; A/C were silent only because the test clip has no audio track |

Derived timings on the A100: Standard pass ≈ 6 min, High pass ≈ 9 min, Finish ≈ 6 min per pass at Standard resolution and ≈ 8 min per pass at High. Model load adds 1–2 minutes to the first clip of a session.

## Observations

- Reference photo quality is the biggest lever: sharp, full-body, plain background gave the clean swap.
- High tier buys sharpness and texture (720p, fabric folds stay fabric, fewer hand smears), not different motion. Hands in the fastest frames remain the limit; seed-picking fixes more than any setting.
- Chaining (continue motion + seek + drop overlap + trim) produces exactly the expected frame counts (161 for 2 passes) with no visible seam.
- The Finish pass doubles resolution and frame rate and keeps audio; it costs about as much time as the render itself at High tier.
- Background and camera come from the prompt, not from either input (Wan Animate 2 behaviour).
- Operational: the app process died once mid-session (cause not captured); jobs now resume after a restart and adopt renders still running in ComfyUI. The watchdog terminated an idle pod exactly on schedule earlier; the 45-minute first-job grace was added as a result.

## Phase 2 "done when" check

- Run A pair ≥ 24/25 at High tier: **23–24 from stills**; at the threshold, limited by hands in the fastest frames.
- 1080p, 48 fps, 8-second clip from the app: **1440×2560, 48 fps, 6.7 s** delivered (2 passes). 3 passes (10 s) is the same code path with one more block and was not exercised this session.

## Next measurements

Phase 3: auto background prompt, SCAIL-2 replace-into-video, Seedance 2.5 reference-to-video, three-way comparison on this same pair.

## 14 Oct 2026, code review and Phase 4 (laptop only, no GPU)

Code review of app/tools/scripts: 21 findings, all fixed, suite grown to 57 tests. Notable: the runner no longer waits forever when ComfyUI restarts mid-job (it requeues once), a transient poll error no longer fails a job, the public link takes a password, `validate_workflow.py` no longer interrupts other people's renders, `setup.sh` fails loudly when pip fails, the watchdog retries a failed stop.

Phase 4 delivered without a GPU session: team guide with screenshots (taken from the app running locally against three demo jobs built from the Phase 2 clips), update procedure, Costs tab and report, pod helper script (untested against a live RunPod account). Still to measure: SCAIL-2 and Seedance on the reference pair (Phase 3.4).

## 14 Oct 2026, Seedance 2.5 on the laptop (no GPU)

First live Seedance job through the new front end: streetwear reference + 14 s street dancer clip, tier High (1080p), task type "reference". Rendered in about 7 minutes with sound. **Cost: 2,800 Comfy credits (about $13.30 at 211 credits per dollar)**, far above the app's estimate; the Comfy account went negative. Fixes: prices now in measured credits (200 per second at 1080p), a 600-credit cap refuses bigger jobs, the compare clip is built by the app instead of inside ComfyUI (the ComfyUI compare step ran out of RAM on the laptop after the paid render had been saved; the clip was recovered).

**Result:** identity, outfit and 1080p finish are good; the choreography only loosely follows the driving video and the timing drifts. Cause: we used task type "reference" with a plain description. The node's "edit" mode replaces the person in the uploaded video itself and keeps its length and framing, and Seedance prompts should name the files as @Image1 / @Video1. The app now defaults Seedance to "Swap into the video" (edit) with an @-referenced prompt, offers "New scene from the text" (reference) as the alternative, and uses the cheaper Seedance 2.5 Draft model for the Fast tier. Not yet re-rendered in edit mode (needs a Comfy top-up; a 5 s clip at 720p is about 450 credits).

## 17 Oct 2026, SCAIL-2 first live render (RTX PRO 4500, $0.72/h)

Pod from clone to READY in about 12 minutes including the 20 GB SCAIL-2 download; smoke test green (SCAIL-2, SeedVR2, FILM, all model profiles). First render through the new front end with the login page: user's own pair (waist-up reference, 8.3 s 720x1280 30 fps dance clip), SCAIL-2 Standard, 3 segments, 233 frames, 1 seed. Job 20261016-234423-36bc, about 20 minutes.

Result: motion copied frame for frame with identical timing; face, hair, skin, top and necklace from the reference; the video's studio, lighting and camera kept; hands clean. Flaws: (1) the reference photo's bookshelf leaked into the left edge of the frame (the model and CLIP vision saw the whole reference image); (2) the jeans, which the reference does not show, were invented and look plastic; (3) hair tips lighter, which matches the reference photo. Fixes shipped the same day: reference cut out onto neutral grey via the SAM track (GrowMask + FeatherMask + ImageCompositeMasked) before encoding; SCAIL High tier = no distill LoRA, 16 steps, cfg 3.5; guide now asks for full-body references for full-body videos. Cost of the run about $0.25 of pod time.

Second run (job 20261017-002357-35be, same pair, reference cut out onto grey): figurines still present and a grey rim around the hair. Real cause found: the reference tracker (prompt "human", up to 4 objects, sorted left to right) detected the two figurines on the shelf as people, and SCAIL composites every identity into the clip. Fix: identities sorted by area and only index 0 (the largest person) used for both the reference and the driving video; cut-out off by default. Lesson for the guide: a reference with dolls, posters or other faces in it needs this guard; a plain wall is still the safe choice.

## 17 Oct 2026, SCAIL-2 deep research and rebuild (no pod)

Research report: docs/SCAIL2_BEST_PRACTICE.md (authors' issues, official template, community, our graph vs template). Changes shipped from it: reference padded to the video's aspect (never cropped) and cut out onto BLACK for CLIP vision (the node already blacks the VAE path); T2V LightX2V distill LoRA instead of the I2V one the template ships (maintainers attribute driver-clothing leaks to I2V); SAM prompt "human" on both tracks; long descriptive SCAIL prompt built from the character, scene and motion text; tiers Fast 512p/6 steps, Standard 512p/8, High 704p/10 at strength 0.6; author-grade 40 steps cfg 4 kept in the builder for final renders. Next pod session test order: R1 same pair at Standard (identity guard + black cut-out + T2V LoRA), R2 High 704p on one segment, R3 full 3 segments on the winner, record wall-clock times.

## 17 Oct 2026, VRAM guard and chunked Finish (no pod)

From docs/GPU_CHOICE.md: the app now reads ComfyUI's GPU (name, VRAM) and refuses a tier the card cannot hold without offloading (Wan Fast/Standard 22 GiB, Wan High 46, SCAIL Fast/Standard 28, SCAIL High 40, Finish 20 once chunked), with an "Allow offloading" override; the GPU is recorded on every job. The Finish pass now splits the SeedVR2 latent into temporal chunks sized to free VRAM (SeedVR2TemporalChunk auto, 4-frame overlap, SeedVR2TemporalMerge), so 161- and 241-frame Finish renders no longer need an 80 GB card. Both graphs accepted by ComfyUI structurally; VRAM numbers are inferred until the next instrumented pod session logs nvidia-smi peaks.

