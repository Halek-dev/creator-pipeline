# Test log

Scores use the five-criterion scorecard from the Motion Swap brief (face, hands/feet, edges/background, stability, camera/timing; 1–5 each). "From stills" means scored on extracted frames; flicker needs eyes on the moving clip.

| Date | Run | Inputs | Engine / tier | GPU | Time per clip | Cost | Score | Notes |
|---|---|---|---|---|---|---|---|---|
| 13 Oct 2026 | Sample 1 | Pink-hair stylised render + street dance (official samples) | Wan Animate 2 distilled, 480×848, 10 steps | A40 | 469 s (incl. model load) | ~$0.08 | 22/25 from stills | Identity 5, hands 3 (gold mitts), background 5, stability 4, timing 5 |
| 13 Oct 2026 | Run A | Photoreal streetwear woman + male street dance | same | A40 | 469 s | ~$0.08 | **23/25** from stills | Identity 5, hands 4 (two smeared frames in fast moves), background 5, stability 4 (hair bun flicker), timing 5. Cross-gender transfer kept her body |
| 13 Oct 2026 | Run B | Same woman + gallery dance clip | same | A40 | 464 s | ~$0.08 | not scored | Clip rendered; compare file not reviewed before the pod was terminated |
| 14 Oct 2026 | App job fbb6 | Run A pair via the team app, Studio preset, 2 seeds | same | RTX PRO 4500 (32 GB) | 400 s, 318 s | $0.20 actual (est. $0.15) | same level as Run A | Rendered through the app; both seeds usable; found and fixed the Gradio allowed_paths bug |

## Observations

- Reference photo quality is the biggest lever: sharp, full-body, plain background gave the clean swap.
- Hands smear only during the fastest moves; feet and boots stay solid.
- Background and camera come from the prompt, not from either input (Wan Animate 2 behaviour).
- Model load adds ~80 s to the first clip of a session; later clips are ~5.3 min on the RTX PRO 4500.
- Watchdog terminated an idle pod exactly on schedule; the 45-minute first-job grace was added as a result.

## Next measurements (Phase 2)

High tier (full model, 720p), SeedVR2 upscale, FILM interpolation, identity-hold 1.2, multi-seed picking. Target: ≥24/25 on the Run A pair.
