# INSTARAW x Fanvue livestream: what they actually showed

Source: the Google Drive link in INSTARAW's Discord pitch (one 1h48m stream in three file sizes, dated 2 Jun). Full auto-transcript in [instaraw-livestream-transcript.txt](instaraw-livestream-transcript.txt); timestamps below point into it.

Structure: 0:00–51:30 Louis (Fanvue account manager) on Instagram/TikTok growth and bans. 51:37–1:25 "V" (VLRevolution) screen-shares the INSTARAW tutorial. 1:25–1:48 community Q&A and the sales close.

## 1. The demo is stills only. No motion swap, no Wan, no LoRA training shown

| Time | What was shown |
|---|---|
| 52:25 | "A complete technical workflow using **Nano Banana Pro** as the main image generator." Nano Banana Pro is Google's closed image model, used via API ($0.14 per image through the WaveSpeed desktop app, 1:02:47). |
| 53:42–57:00 | Persona and prompts come from **Google AI Studio (Gemini 3.1 Pro, temperature 0.7)**: a "character seed" prompt, then 3–6 dataset prompts. |
| 58:48 | Base image: text-to-image only, because moderation is stricter with a reference image. |
| 1:03:17 | Then image-to-image with the base as reference to make the other 17 images. |
| 59:51–1:00:08 | Quote: "You can use the WaveSpeed AI desktop app to do the same thing by hand. **You don't have to buy a VIP workflow.**" Their "BIG" workflow is batch API calls that auto-retry on moderation errors (1:01:54). |
| 1:09:47–1:13:30 | Body fixes: their "boob and glute workflow" (SDXL inpainting with the Lustify SDXL model), or Seedream 4.5 edit via WaveSpeed. |
| 1:14:00, 1:34:02 | **Flux Klein post-processing** for dark/amateur lighting and realistic skin; works best at 4 MP (2 MP changed the face). |
| 1:30:14 | Quote on that workflow: "This is open source, guys. This is the same workflow available in the workflows channel shared by Hawk. I just tidied it up and put some insta loaders." |
| 1:23:46 | LoRA training itself: "we will cover everything in the INSTARAW 2.5 masterclass." Not shown. |
| 1:25:18 | All prompts and the dataset guide promised **free** in the Discord "free resources" channel after the stream. |

Total time per character dataset: 60–180 minutes (1:24:14).

## 2. Their answer on video is "pay for Seedance"

| Time | Quote / point |
|---|---|
| 1:41:22 | Asked about video: "Many people use Wan 2.2"; LTX-2 "works in simple scenes, in others looks unnatural." |
| 1:42:13 | "The latest intel is to just use **Seedance 2.0 with reference video** and ask it to make it different." |
| 1:43:22 | "I think we need a LoRA there also to do certain kinds of motion better." |
| 1:47:54 | On video help: "In the future we can give more tips." |
| 1:48:07 | Seedance: "you do need to invest a bit because it's quite expensive." |

No Wan Animate, no Kling demo, no motion-swap workflow exists in what they showed. Our Wan Animate 2 route is something they do not have.

## 3. Platform warnings from the Fanvue account manager

| Time | Warning |
|---|---|
| 10:09 | "If you are just using **Kling motion control or any sort of character swap** and your whole page is character swap of Sophie Rain... you're going to get banned" (impersonation). |
| 19:18 | "TikTok is more anti-AI. If every single one of your videos is clean motion control, you're just going to get **shadowbanned**." |
| 20:29 | "Is face swap allowed? No. The only safe way is referencing." |
| 38:40 | Fanvue rule: Pinterest photo + face swap not allowed; result must look different side by side. |
| 37:26 | Tag content as AI. Costs 1–2% conversion, protects against bans; EU law requires it. |
| 16:53 | No ManyChat / DM automation: Instagram removed DM encryption; off-platform solicitation in DMs gets banned. |
| 11:03 | One account per phone, new iCloud, 4G. |

Context the team should know: this community is the OnlyFans/Fanvue AI-influencer niche, and INSTARAW's workflows are built for NSFW character content. The techniques transfer; the content focus does not have to.

## 4. Business signals

- 1:36:03: "It's not finished, guys. There are leakers leaking our workflows." Plan: "lock it down more" and move parts to a **web app with APIs** "that we can always protect" (1:36:24). The "lifetime ComfyUI suite" is moving toward a hosted platform.
- 1:40:14: a community member says the workflows are widely leaked ("50% of the niche use your workflow, all of them leaked"). Do not run leaked packs: tampered copies are a real risk.
- 1:46:24: on 2 Jun, "662 of 777 sold, next 22 spots, $777." The October Discord says 701/777 and "next 22 spots" again, with wrong arithmetic. Same scarcity line four months apart.
- 1:25:18: the actual prompts, dataset guide and Flux Klein workflow were released free. Check the free resources channel before paying.

## 5. Worth adopting into our pipeline

1. **Dataset recipe** (1:07:30, 1:27:46): 18 images is the sweet spot. 4–6 full body, 7–8 medium / three-quarter, 2–3 close-up, 4–5 side/angled, 3–4 from behind. Diverse angles (anything untrained gets hallucinated). Keep training images at or below 2K; the base models were trained at 1K and 4K sources add artifacts. Cool, dark, amateur lighting in the dataset carries into every LoRA output (1:08:31).
2. **Character seed → dataset prompts via an LLM** (53:42): an interactive prompt that defines persona, look, niche, then emits 3–6 varied scene prompts. Direct fit for our Prompt Library; we can do it with Claude.
3. **Flux Klein post-processing at 4 MP** for skin and lighting (open weights). Candidate upgrade to our Reality Finish step.
4. **Face-only edit round-trip** (1:34:38): send only the face crop to the edit model, paste back with no pixel shift. Node idea.
5. **Two LoRAs mixed** (1:29:39): one trained on sharp images, one on dark amateur images, blended at different strengths.
6. **Seedance 2.x for video** with a reference clip (already in our playbook) as the paid comparison against Wan Animate 2.
