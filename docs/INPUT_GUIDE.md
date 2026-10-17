# Input guide: what makes a motion swap look real

The model copies two things: **who** from the reference image and **how they move** from the driving video. Everything else (background, lighting, camera) comes from the scene text. Good inputs are the single biggest quality lever we have; they cost nothing.

## Reference image (who)

| Do | Why |
|---|---|
| Full body, head to toe, standing | The model needs to see proportions, legs and shoes; cropped references invent the rest |
| Facing the camera, neutral or relaxed pose | Easier to map onto any motion |
| Face sharp and fully visible: no sunglasses, hair across the face, hard shadows | Identity comes from the face region |
| 1024–2048 px on the long side, portrait orientation | Smaller gets soft faces; larger adds nothing |
| Plain or simple background | Less for the model to confuse with the character |
| Clothing that shows the body shape; avoid huge flowing garments if the motion is fast | Loose fabric is where texture flicker shows |
| A photo (or a photoreal render) for photoreal output | A stylised reference gives a stylised clip; that's a feature, not a bug |

Avoid: group photos, selfies from below, motion blur, heavy filters, watermarks, text overlays.

## Driving video (how they move)

| Do | Why |
|---|---|
| One person in frame | Two people confuse the motion source |
| Full body visible for the whole clip, similar framing to the reference | Framing follows the driving video |
| Stable camera (tripod or steady hand), no zooms | Camera shake becomes character jitter |
| Even lighting, no strobe, no heavy backlight | The model reads body edges from the frames |
| 24–30 fps, no motion blur, no slow motion | Blurry source frames become smeared hands |
| At least 81 frames (3.4 s at 24 fps). 161 frames allow 2 passes, 241 allow 3 | Each pass covers 81 frames |
| Portrait or square is ideal; landscape gets cropped to portrait | Output is 480×848 (Standard) or 720×1280 (High) |
| Moves the model handles well: dance, walking, gestures, turns | Very fast hand work and props crossing the face are the hardest cases |

Avoid: multiple people, cuts or scene changes inside the clip, handheld whip pans, strong backlight, fisheye lenses.

## Scene text (where)

Describe looks only, never motion, in the character line. Put the movement in the motion line. The background line is generated from scratch: be concrete ("wooden floor, large windows, soft daylight") rather than moody ("vibes").

## Picking a result

Render 2–4 seeds. In the Jobs tab, the contact sheet shows six frames per seed and the face strip shows the face over time. Pick the seed where the face is stable and the hands are clean in the fastest frames. If none is good, change one thing: a sharper reference, a steadier driving clip, or Identity hold 1.2.

## Quick checklist before pressing Run

- [ ] Reference: full body, sharp face, portrait, plain background
- [ ] Video: one person, full body, stable camera, ≥ 81 frames
- [ ] Character text: looks only
- [ ] Motion text: what the person does in the video
- [ ] Seeds: 2–4
- [ ] Rights: our own character, or written consent

## SCAIL-2 specifics (character swapped into the video)

- The reference image decides everything about the character: face, hair as photographed, skin, top, necklace, body. Anything the photo does not show (legs, back, shoes) is invented, and invented cloth looks plastic. Use a **full-body** reference for full-body videos; a waist-up reference is fine for a waist-up video.
- The reference is padded (never cropped) to the video's aspect ratio and cut out onto black before the model sees it, as the SCAIL-2 authors require. Match the framing of the video's first frame: if the video shows the full body, the photo must too.
- Only the largest person in the reference and in the video is swapped. Dolls, posters and figurines in the background used to be picked up as extra people and pasted into the clip; they are ignored now, but a plain wall behind the character is still the safe choice.
- Hair comes out as it is in the photo, including lighter tips if the photo has them.
- Tiers on SCAIL-2: Fast is the official turbo path (6 steps), Standard adds steps (8), High renders at 704p with a softer speed LoRA and 10 steps (about 3.5x Standard time), the resolution the model card says replacement works best at. The authors' own 40-step setting is available to developers in the builder and takes about 13x.

