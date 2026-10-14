# Pinned versions and how to update (Phase 4.2)

Everything that decides what a clip looks like is pinned, so a pod started next month renders the same as one started today. Change a pin on purpose, test, commit. Never update on a pod the team is using.

## What is pinned where

| Thing | Pinned in | Current |
|---|---|---|
| ComfyUI | `versions.env` → `COMFYUI_COMMIT` | `e8665ca8` (v0.39.0, 11 Oct 2026) |
| Model files (names, URLs, folders) | `models.json` profiles | wan-animate-2, wan-animate-2-full, finishing, scail2 |
| Python packages for the app | `app/requirements.txt` | gradio 5.50.0, pyyaml, anthropic 1.x |
| Torch | `scripts/setup.sh` (cu128 wheel index) | whatever pip resolves at venv creation; the venv persists on the volume |
| Our node pack and workflow generators | this repo (git) | each job records `workflow_version` = git sha + digest of the generators |
| Presets (prompts, tiers, prices) | `app/presets.yaml` | six scenes, three characters, three tiers |
| Claude model for Describe | `app/describe.py` | `claude-opus-5-5` |

`setup.sh` is idempotent: it only fetches ComfyUI when the checked-out commit differs from the pin, only creates the venv when it is missing or the system Python changed, and only downloads models that are missing or the wrong size.

## Update procedure

Do this on a **fresh pod** (or a second pod) with the volume attached, not on the one the team is using. Budget one GPU hour.

1. **Pick the new ComfyUI commit.** Look at https://github.com/comfyanonymous/ComfyUI/releases. Put the full sha in `versions.env`.
2. **Check the nodes we depend on still exist.** On the pod:
   ```bash
   bash scripts/setup.sh && bash scripts/start.sh
   ```
   `start.sh` runs the smoke test, which asks ComfyUI for every node class our workflows use (Wan Animate 2, SCAIL-2, SeedVR2, FILM, SAM3.1, Seedance, our Creator nodes) and fails loudly if one is missing or renamed.
3. **Validate every generated workflow against the live server** (structure, input names, model names):
   ```bash
   for f in workflows/wan_animate2.json workflows/wan_animate2_high.json workflows/wan_animate2_chain2.json workflows/wan_animate2_finish.json workflows/scail2_replace.json workflows/scail2_replace_seg2.json workflows/seedance25_reference.json; do .venv/bin/python tools/validate_workflow.py "$f"; done
   ```
   If a node changed an input name, fix the generator in `tools/build_*.py`, regenerate (`python tools/build_workflows.py`, `build_scail.py`, `build_seedance.py`) and re-validate.
4. **Render the reference pair** (the streetwear image + dance clip in `docs/TEST_LOG.md`) at Standard tier with the same seed as the last logged run, and compare side by side. Score it with the scorecard. If it is not at least as good, do not update.
5. **Run the unit tests** locally or on the pod:
   ```bash
   python -m unittest discover tests
   ```
6. **Commit** the pin change with the test result in the message, push, and add a line to `docs/TEST_LOG.md`.
7. **Roll out**: next time the team's pod is terminated and recreated, `setup.sh` fetches the new commit. The venv is only rebuilt if you delete `/workspace/creator-data/venv` (do that when ComfyUI's `requirements.txt` moved a major version).

## Rolling back

Put the old sha back in `versions.env`, push, recreate the pod. Models are never deleted by an update, so a rollback needs no downloads.

## Updating models

Add the new file to the right profile in `models.json` (folder + URL). Keep the old entry until the new one has been scored. `setup.sh <profile>` downloads only what is missing. Model file names are referenced from the generators (`tools/build_*.py`); change them there, regenerate, validate.

## Updating the app's Python packages

Edit `app/requirements.txt`, then on the pod:
```bash
.venv/bin/python -m pip install -r app/requirements.txt && bash scripts/start.sh restart-app
```
Gradio is pinned exactly because UI behaviour changes between minor versions.

## What never changes without a decision

GPU type (L40S default), the Seedance price table in `presets.yaml` (check comfy.org when the badge price differs), and the six scene presets' prompts (they are the team's look).
