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
