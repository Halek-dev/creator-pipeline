"""Team app: upload a reference image and a driving video, pick a preset, render motion swap clips.

  .venv/bin/python app/app.py            # http://0.0.0.0:7860 (start.sh runs this and opens a public link)

Environment: COMFY_SERVER (default http://127.0.0.1:8188), APP_PORT (7860), GPU_HOURLY_USD (1.0).
"""
import csv
import json
import os
import sys
from pathlib import Path

import gradio as gr

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jobs as J  # noqa: E402

RUNNER = J.Runner()
RUNNER.start()

HELP = """
### How to get a good clip
**Reference image**: full body head to toe, facing the camera, face clearly visible, sharp, 1024–2048 px on the long side, plain background. The face comes from this image.

**Driving video**: one person, full body, stable camera, even light, no motion blur, at least 3.5 seconds (the first 81 frames are used). The motion comes from this video.

**Preset**: the background is generated from the scene text; it does not come from either input. Edit the scene text per job if you want something specific.

**Seeds**: 2–4 seeds give you choices; pick the best one. Each seed is one full render.

**Tiers**: Fast is a preview. Standard is what we scored 23/25. High is 720p and experimental until Phase 2.

**Platform rules**: only characters we invented or people who signed consent; label AI content; vary content types (all-motion-control feeds get shadowbanned).
"""


def status_line():
    parts = []
    try:
        wd = json.loads((J.LOGS / "watchdog.status").read_text())
        parts.append(f"pod stops itself in {wd['stops_in_minutes']:.0f} min if idle")
    except Exception:
        pass
    if RUNNER.current:
        parts.append(f"rendering job {RUNNER.current}")
    elif not RUNNER.pending.empty():
        parts.append(f"{RUNNER.pending.qsize()} job(s) waiting")
    else:
        parts.append("idle")
    return " · ".join(parts)


def presets():
    return J.load_presets()


def on_character(name):
    return presets()["characters"].get(name, {}).get("appearance", "")


def on_scene(name):
    s = presets()["scenes"].get(name, {})
    return s.get("background", ""), s.get("motion", ""), float(s.get("identity_hold", 1.0)), float(s.get("motion_looseness", 1.0))


def on_tier(name, seeds):
    t = presets()["tiers"][name]
    est = t["minutes_per_clip"] * seeds
    return f"{t.get('note', '')} · about {est:.0f} min and ${est / 60 * J.GPU_HOURLY_USD:.2f} for {int(seeds)} seed(s)"


def on_preflight(image, video):
    ok, msgs = J.preflight(image, video)
    return ("**Ready.**\n\n" if ok else "**Fix before running.**\n\n") + "\n\n".join(f"- {m}" for m in msgs)


def on_run(image, video, character, appearance, scene_name, background, motion, identity_hold, motion_looseness, tier_name, seeds, note):
    ok, msgs = J.preflight(image, video)
    if not ok:
        return "Not queued: " + " ".join(m for m in msgs if not m.startswith(("Image OK", "Video OK", "Reminder")))
    if not appearance.strip():
        return "Not queued: describe the character's looks (or pick a character preset)."
    tier = presets()["tiers"][tier_name]
    scene = {"background": background, "motion": motion, "identity_hold": identity_hold, "motion_looseness": motion_looseness}
    job = J.new_job(image, video, character, appearance, scene_name, scene, tier_name, tier, int(seeds), note)
    RUNNER.submit(job["id"])
    return f"Queued job **{job['id']}** with {int(seeds)} seed(s). Estimated ${job['cost']['estimate_usd']:.2f}. Watch it on the Jobs tab."


def jobs_table():
    rows, ids = [], []
    for j in J.list_jobs():
        done = sum(1 for r in j["runs"] if r["status"] == "done")
        cost = j["cost"]["actual_usd"] if j["cost"]["actual_usd"] is not None else j["cost"]["estimate_usd"]
        rows.append([j["id"], j["created"][5:16].replace("T", " "), j["status"], j["character"], j["scene"], j["tier"], f"{done}/{len(j['runs'])}", f"${cost:.2f}"])
        ids.append(j["id"])
    return rows, ids


def refresh_jobs():
    rows, ids = jobs_table()
    return rows, ids, status_line()


def on_select_job(ids, evt: gr.SelectData):
    if not ids or evt.index is None:
        return "", None, []
    job = J.load_job(ids[evt.index[0]])
    lines = [f"**{job['id']}** · {job['status']} · {job['character']} · {job['scene']} · {job['tier']}",
             f"Character prompt: {job['prompts']['character']}",
             f"Motion prompt: {job['prompts']['motion']}",
             f"Identity hold {job['settings']['identity_hold']}, motion looseness {job['settings']['motion_looseness']}, {job['tier_settings']['width']}×{job['tier_settings']['height']}, {job['tier_settings']['steps']} steps",
             f"Workflow version: `{job['workflow_version']}`",
             f"Cost: estimated ${job['cost']['estimate_usd']:.2f}" + (f", actual ${job['cost']['actual_usd']:.2f}" if job['cost']['actual_usd'] is not None else "")]
    if job.get("note"):
        lines.append(f"Note: {job['note']}")
    files, preview = [], None
    for r in job["runs"]:
        line = f"- seed {r['seed']}: {r['status']}" + (f" ({r['seconds']} s)" if r.get("seconds") else "")
        if r.get("error"):
            line += f" — {r['error']}"
        lines.append(line)
        for rel in r["outputs"]:
            p = J.output_path(rel)
            if p.exists():
                files.append(str(p))
                if preview is None and "compare" in p.name:
                    preview = str(p)
    if preview is None and files:
        preview = files[0]
    return "\n\n".join(lines), preview, files


def on_sheet(file, tier_name):
    """CSV columns: image,video,character,scene,seeds,note. image/video are file names already in ComfyUI/input."""
    if file is None:
        return "Upload a CSV first."
    p = presets()
    tier = p["tiers"][tier_name]
    made, problems = 0, []
    with open(file, newline="", encoding="utf-8") as f:
        for i, row in enumerate(csv.DictReader(f), 1):
            try:
                image = J.COMFY_INPUT / row["image"]
                video = J.COMFY_INPUT / row["video"]
                ch = p["characters"][row["character"]]
                sc = p["scenes"][row["scene"]]
                job = J.new_job(str(image), str(video), row["character"], ch["appearance"], row["scene"], sc, tier_name, tier, int(row.get("seeds") or 1), row.get("note", ""))
                RUNNER.submit(job["id"])
                made += 1
            except Exception as e:
                problems.append(f"row {i}: {e}")
    return f"Queued {made} job(s)." + ("\n\nProblems:\n" + "\n".join(problems) if problems else "")


def build():
    p = presets()
    char_names, scene_names, tier_names = list(p["characters"]), list(p["scenes"]), list(p["tiers"])
    first_scene = p["scenes"][scene_names[0]]
    with gr.Blocks(title="Creator Pipeline") as demo:
        gr.Markdown("# Motion swap")
        status = gr.Markdown(status_line())
        with gr.Tabs():
            with gr.Tab("New clip"):
                with gr.Row():
                    with gr.Column():
                        image = gr.Image(type="filepath", label="Reference image (who)", height=300)
                        video = gr.Video(label="Driving video (what motion)", height=300)
                        preflight_md = gr.Markdown("Add an image and a video.")
                    with gr.Column():
                        character = gr.Dropdown(char_names, value=char_names[0], label="Character")
                        appearance = gr.Textbox(p["characters"][char_names[0]]["appearance"], label="Looks (no motion words)", lines=2)
                        scene = gr.Dropdown(scene_names, value=scene_names[0], label="Scene preset")
                        background = gr.Textbox(first_scene["background"], label="Background", lines=2)
                        motion = gr.Textbox(first_scene["motion"], label="What the person is doing", lines=1)
                        with gr.Row():
                            identity_hold = gr.Slider(0.8, 1.5, value=float(first_scene.get("identity_hold", 1.0)), step=0.05, label="Identity hold")
                            motion_looseness = gr.Slider(0.5, 1.0, value=float(first_scene.get("motion_looseness", 1.0)), step=0.05, label="Motion looseness")
                        with gr.Row():
                            tier = gr.Radio(tier_names, value="Standard", label="Quality tier")
                            seeds = gr.Slider(1, 4, value=2, step=1, label="Seeds (variations)")
                        tier_md = gr.Markdown(on_tier("Standard", 2))
                        note = gr.Textbox(label="Note (optional)", lines=1)
                        run = gr.Button("Run", variant="primary")
                        result_md = gr.Markdown("")
                image.change(on_preflight, [image, video], preflight_md)
                video.change(on_preflight, [image, video], preflight_md)
                character.change(on_character, character, appearance)
                scene.change(on_scene, scene, [background, motion, identity_hold, motion_looseness])
                tier.change(on_tier, [tier, seeds], tier_md)
                seeds.change(on_tier, [tier, seeds], tier_md)
                run.click(on_run, [image, video, character, appearance, scene, background, motion, identity_hold, motion_looseness, tier, seeds, note], result_md)

            with gr.Tab("Jobs"):
                rows, ids = jobs_table()
                ids_state = gr.State(ids)
                table = gr.Dataframe(value=rows, headers=["Job", "Created", "Status", "Character", "Scene", "Tier", "Done", "Cost"], interactive=False, wrap=True)
                detail = gr.Markdown("Select a job to see details and downloads.")
                preview = gr.Video(label="Preview (compare: result left, driving video right)", height=420)
                downloads = gr.Files(label="Downloads")
                timer = gr.Timer(5)
                timer.tick(refresh_jobs, None, [table, ids_state, status])
                table.select(on_select_job, ids_state, [detail, preview, downloads])

            with gr.Tab("Job sheet"):
                gr.Markdown("Bulk mode. Upload a CSV with columns `image,video,character,scene,seeds,note`. `image` and `video` are file names already in `ComfyUI/input` (upload them via Jupyter). Character and scene are preset names.")
                sheet = gr.File(label="CSV", file_types=[".csv"])
                sheet_tier = gr.Radio(tier_names, value="Standard", label="Quality tier for all rows")
                sheet_btn = gr.Button("Queue all rows")
                sheet_md = gr.Markdown("")
                sheet_btn.click(on_sheet, [sheet, sheet_tier], sheet_md)

            with gr.Tab("Help"):
                gr.Markdown(HELP)
    return demo


if __name__ == "__main__":
    build().queue().launch(server_name="0.0.0.0", server_port=int(os.environ.get("APP_PORT", "7860")), show_api=False)
