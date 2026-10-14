# Test plan: Phase 3 engines and Phase 4 handoff

Three sessions. A runs on your laptop and costs only API credits. B is one GPU pod session of about an hour. C is five minutes from your laptop. Each step says what you need, what to do, what you should see, and what to send back so the results can go into TEST_LOG.md.

Test pair for everything: `reference_streetwear_character.png` and `street_dancer_long.mp4` (241 frames, 24 fps). Both are already in `ComfyUI/input` on the volume; the image is also on the laptop, the long clip is in `creator-data/input` on the volume (the laptop has the shorter `street_dance_drive.mp4`, which is fine for A).

Never paste a key into chat or into a file. Keys are typed into the terminal with `export`, and they live only in that terminal window.

---

## A. Laptop: Seedance 2.5 and Describe (no pod)

Why on the laptop: Seedance runs on ByteDance's servers and Describe runs on Anthropic's. ComfyUI only passes the requests along, so a CPU is enough.

**You need**

1. A Comfy account with credits: https://platform.comfy.org, sign in, buy about $5 of credits (a 5 s 720p clip is about $1.75, so that covers two tries), then API Keys, create one, copy it.
2. An Anthropic API key: https://console.anthropic.com, API Keys, create one. Describe costs a fraction of a cent per press.
3. The repo at the latest commit: in Git Bash, `cd /c/Users/user/Desktop/creator-pipeline && git pull`.

**Steps**

1. Open Git Bash, export the keys and start ComfyUI on the CPU:
   ```bash
   cd /c/Users/user/Desktop/creator-pipeline && export COMFY_API_KEY=<paste> ANTHROPIC_API_KEY=<paste> && .venv/Scripts/python.exe ComfyUI/main.py --listen 127.0.0.1 --port 8188 --cpu
   ```
   Expect: a few seconds of log lines ending with `To see the GUI go to: http://127.0.0.1:8188`. Leave this window open.
2. Open a second Git Bash and start the app with the same keys (they must be exported here too, each terminal is separate):
   ```bash
   cd /c/Users/user/Desktop/creator-pipeline && export COMFY_API_KEY=<paste> ANTHROPIC_API_KEY=<paste> && .venv/Scripts/python.exe app/web/server.py
   ```
   Expect: `WARNING: APP_PASSWORD is not set` (fine on the laptop) and nothing else. Open http://127.0.0.1:7860 in Chrome. The status chip top right should say Idle.
3. Create page: drop `ComfyUI/input/reference_streetwear_character.png` on Reference image and `ComfyUI/input/street_dance_drive.mp4` on Driving video. The check runs by itself; expect green lines for both and a length offer.
4. Engine: click **Seedance 2.5**. The hint under it must not say "COMFY_API_KEY is not set"; if it does, the key was not exported in the app terminal (step 2).
5. Describe test: with the engine still on Seedance, press **Describe**. Expect the Background box to change to a sentence about a plain studio backdrop within about ten seconds, and the hint "Background filled from the reference image". If it says the key is missing, the Anthropic key was not exported in the app terminal. Send: the sentence it wrote.
6. Quality: Standard (720p). Seeds: 1. Note: `seedance test`. The button should read `Render ~$1.xx credits`. Press it.
7. Expect: a card appears at the top of Recent within a second, badge Queued, then Rendering with a stage line; the ComfyUI window prints the ByteDance node working. Seedance takes 2 to 5 minutes. When the card flips to Done, click it: the compare player should play with sound.
8. If the card flips to Failed, click it; the Settings block shows the error text. The two common ones: "insufficient credits" (buy credits) and "api_key" rejected (the key was copied wrong). Send me the error text.
9. Send: the result file (Downloads in the drawer, `*_result_*.mp4`) and your score out of 25 on the scorecard. Also open **Costs** and check the row shows Seedance $ about 1.75.
10. Stop both terminals with Ctrl+C.

---

## B. Pod: Wan Animate 2, SCAIL-2, password, costs, comparison

**You need**

1. RunPod credit of about $5.
2. A password for the team app. Pick a long one and keep it in your password manager.
3. The three-way comparison needs the same pair rendered on all three engines, so keep the Seedance result from A.

**Steps**

1. RunPod, Pods, Deploy. Attach the `creator-data` volume, GPU **L40S** (A40 or RTX PRO 4500 are fine if L40S is sold out), template RunPod PyTorch 2.8, container disk 20 GB, port 8888 exposed. Deploy, wait for Running, open the web terminal.
2. Paste one line. It pulls the latest code, installs, downloads the SCAIL-2 models once (about 20 GB, 5 to 10 minutes, kept on the volume), sets the password and the pod's hourly price, and starts everything:
   ```bash
   cd /workspace && (test -d creator-pipeline || git clone https://github.com/Halek-dev/creator-pipeline.git) && cd creator-pipeline && git pull && bash scripts/setup.sh wan-animate-2 scail2 && export APP_PASSWORD=<your password> && GPU_HOURLY_USD=<price from the pod card> bash scripts/start.sh
   ```
   Expect at the end: a READY block with `Team app: https://....trycloudflare.com` and `RESULT: READY` from the smoke test. If the smoke test prints a missing node or model, stop here and send me the block.
3. Open the team app link in Chrome. The browser asks for a user name and password: `team` and your password. Expect the Create page with an Idle chip that says "Pod ready, stops in 45 min if idle".
4. Phase 4 check, password: open the same link in a private window and press Cancel on the prompt. Expect "Login required", nothing else. Close it.
5. Wan Animate 2 run (the baseline): inputs are already on the volume, so use Batch or upload again. Simplest: Create page, drop the two files from your laptop (the image and `street_dancer_long.mp4` from your Downloads or wherever you keep it; if you only have it on the volume, use the Library's "Use these settings" on the earlier Run A clip). Engine Wan Animate 2, character Streetwear woman, scene Studio, Standard, length 2 passes (6.7 s), seeds 2, note `compare wan`. Render. About 12 minutes.
6. SCAIL-2 run: press **Use these settings** on the Wan card to reload everything, switch Engine to **SCAIL-2**, press **Describe** so the background text describes the dancer's actual street (the hint should say "from the driving video"), note `compare scail`. Render. Expect the check line "SCAIL-2 renders 512×896" since the clip is portrait, and the estimate near the Wan one. About 12 to 15 minutes; the first SCAIL job also loads the models, add 2 minutes.
7. While they render: the Library shows both cards with a stage line ("Seed 1 of 2, about 9 min left") and a bar moving. The chip says Rendering. Watch for one minute; if a card stays on Queued for more than two minutes with the chip Idle, send me `tail -n 30 logs/app.log` from the terminal.
8. When both are Done: click each card, play the compare, pick the better seed from the contact sheets, download `*_result_*.mp4` for the best seed of each. Score both. SCAIL-2 should keep the original street background and camera; Wan should show the studio.
9. Phase 4 check, costs: open **Costs**. Expect one row for 2026-10 with Jobs 2, Render min about 25, Pod hours about 1, Pod $ matching the price you set times the hours. Send a screenshot.
10. Optional High tier check if you have 20 minutes left: same pair, Wan Animate 2, High, 1 pass, 1 seed, Finish on. Confirms the full model and Finish still work after the review changes.
11. Leave the pod alone. The watchdog terminates it 20 minutes after the last job. Confirm on RunPod that the pod is gone after about 25 minutes and note the session cost from Billing.

**Send back:** the three result clips (Wan, SCAIL, Seedance), scores, the Costs screenshot, the RunPod session cost. I will write the comparison into TEST_LOG.md and the roadmap.

---

## C. Laptop: pod helper (5 minutes, no pod running)

**You need:** a RunPod API key: RunPod, Settings, API Keys, create one with read and write.

1. In Git Bash:
   ```bash
   cd /c/Users/user/Desktop/creator-pipeline && export RUNPOD_API_KEY=<paste> && .venv/Scripts/python.exe tools/pod.py status
   ```
   Expect: a list of your pods (probably none after B). If it prints `RunPod API error`, send me the message; the script has never run against a live account.
2. Dry run of a deploy, nothing is created:
   ```bash
   .venv/Scripts/python.exe tools/pod.py start --dry-run --volume <volume id from RunPod Storage>
   ```
   Expect: the API call printed. Only run it without `--dry-run` when you want a pod started from the laptop; it bills from that moment.

---

## What "pass" means

| Item | Pass when |
|---|---|
| Describe | Background text filled from the image or video in under 15 s |
| Seedance 2.5 | Clip with sound, Done card, Costs shows credits |
| Wan Animate 2 | Done, 2 seeds, same quality as the Phase 2 runs (23 or more) |
| SCAIL-2 | Done, original background kept, character replaced, 20 or more |
| Password | Private window without the password sees "Login required" |
| Costs | Row matches the session within a dollar |
| Watchdog | Pod gone 20 to 25 minutes after the last job |
| pod.py | `status` lists pods without an API error |
