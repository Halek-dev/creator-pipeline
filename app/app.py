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
import describe as D  # noqa: E402
import jobs as J  # noqa: E402

RUNNER = J.Runner()
RUNNER.start()
RESUMED = J.resume_pending(RUNNER.submit)
if RESUMED:
    print(f"resumed {RESUMED} unfinished job(s) from disk", flush=True)

HELP = """
### How to get a good clip
**Reference image**: full body head to toe, facing the camera, face clearly visible, sharp, 1024–2048 px on the long side, plain background. The face comes from this image.

**Driving video**: one person, full body, stable camera, even light, no motion blur. Each pass uses 81 frames (about 3.4 s at 24 fps); a longer video allows 2 or 3 passes for a longer clip. The motion comes from this video.

**Engine**: *Wan Animate 2* generates a new background from the scene text. *SCAIL-2* swaps the character into the original driving video and keeps its background, lighting and camera; describe the whole scene in the text (the Describe button with "driving video" does this), and expect the frame to follow the video's orientation (512×896 or 896×512). *Seedance 2.5* is ByteDance's paid model run through ComfyUI: no GPU needed, renders the whole clip (4–30 s) with sound at 480p/720p/1080p by tier, billed in Comfy credits (needs COMFY_API_KEY on the machine running the app).

**Preset**: with Wan Animate 2 the background is generated from the scene text; it does not come from either input. Edit the scene text per job if you want something specific, or press **Describe** to fill it from the reference image's or the driving video's surroundings (needs an Anthropic API key on the pod).

**Seeds**: 2–4 seeds give you choices; the Jobs tab shows a contact sheet and a face strip per seed so you can pick in seconds.

**Tiers**: Fast is a preview. Standard is what we scored 23/25. High uses the full model at 720p (slower, more detail).

**Finish**: a second pass that upscales ×2 (about 1080p), doubles the frame rate, adds a light camera grain, and keeps the driving video's sound. Adds a few minutes per seed.

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


def touch_keepalive():
    """Someone is preparing a job: tell the idle watchdog not to stop the pod meanwhile."""
    try:
        J.LOGS.mkdir(exist_ok=True)
        (J.LOGS / "keepalive").touch()
    except OSError:
        pass


def on_character(name):
    return presets()["characters"].get(name, {}).get("appearance", "")


def on_describe(source, image, video, current_background):
    """Fill the Background box from the reference image or the driving video's first frame."""
    touch_keepalive()
    path, frame = (image, False) if source.startswith("reference") else (video, True)
    if not path:
        return current_background, f"Add the {'reference image' if not frame else 'driving video'} first."
    if not D.credentials_available():
        return current_background, "Scene description needs an Anthropic API key: set ANTHROPIC_API_KEY before starting the app."
    try:
        text = D.describe_background(path, frame=frame)
    except D.DescribeError as e:
        return current_background, str(e)
    return text, "Background filled from the " + ("reference image" if not frame else "driving video") + ". Edit it if you like, then Run."


def on_scene(name):
    s = presets()["scenes"].get(name, {})
    return s.get("background", ""), s.get("motion", ""), float(s.get("identity_hold", 1.0)), float(s.get("motion_looseness", 1.0))


def estimate_text(tier_name, seeds, chunks, finishing, engine="wan", seconds=5.0):
    p = presets()
    tier = p["tiers"][tier_name]
    if engine == "seedance":
        minutes, usd = J.seedance_estimate(p, tier_name, seconds, int(seeds))
        return (f"Seedance 2.5 at {J.SEEDANCE_RESOLUTION.get(tier_name, '720p')}, {J.SD.clamp_duration(seconds)} s, with sound · about {minutes:.0f} min and "
                f"~${usd:.2f} in Comfy credits for {int(seeds)} seed(s). Finish and the two sliders are not applied (Seedance delivers its own resolution and audio).")
    minutes = J.estimate_minutes(tier, p["finishing"], int(chunks or 1), bool(finishing), int(seeds))
    note = tier.get("note", "")
    if engine == "scail":
        note = f"SCAIL-2 at 512×896 or 896×512 by video orientation, 6 steps (tier timing used as a guess; Identity hold does not apply)"
    return f"{note} · about {minutes:.0f} min and ${minutes / 60 * J.GPU_HOURLY_USD:.2f} for {int(seeds)} seed(s)"


def engine_key(engine_label):
    return J.ENGINES.get(engine_label, "wan")


def on_preflight(image, video, engine_label):
    touch_keepalive()
    ok, msgs, info = J.preflight(image, video, engine_key(engine_label))
    text = ("**Ready.**\n\n" if ok else "**Fix before running.**\n\n") + "\n\n".join(f"- {m}" for m in msgs)
    choices = J.length_choices(info.get("frames", 0), engine_key(engine_label), info.get("fps", 24.0)) if ok else []
    radio = gr.update(choices=[c[0] for c in choices], value=choices[0][0] if choices else None, interactive=bool(choices))
    return text, radio, info


def on_run(image, video, engine_label, character, appearance, scene_name, background, motion, identity_hold, motion_looseness,
           tier_name, seeds, length_label, finishing, note, info):
    engine = engine_key(engine_label)
    ok, msgs, info = J.preflight(image, video, engine)
    if not ok:
        return "Not queued: " + " ".join(m for m in msgs if not m.startswith(("Image OK", "Video OK", "Reminder")))
    if not appearance.strip():
        return "Not queued: describe the character's looks (or pick a character preset)."
    chunks = dict(J.length_choices(info.get("frames", 0), engine, info.get("fps", 24.0))).get(length_label, 1)
    tier = presets()["tiers"][tier_name]
    scene = {"background": background, "motion": motion, "identity_hold": identity_hold, "motion_looseness": motion_looseness}
    job = J.new_job(image, video, character, appearance, scene_name, scene, tier_name, tier, int(seeds), note,
                    chunks=chunks, finishing=bool(finishing), info=info, engine=engine)
    RUNNER.submit(job["id"])
    return (f"Queued job **{job['id']}** on {engine_label}: {int(seeds)} seed(s), {chunks} pass(es){', with Finish' if finishing else ''}. "
            f"Estimated {job['cost']['estimate_minutes']:.0f} min, ${job['cost']['estimate_usd']:.2f}. Watch it on the Jobs tab.")


def jobs_table():
    rows, ids = [], []
    for j in J.list_jobs():
        done = sum(1 for r in j["runs"] if r["status"] == "done")
        cost = j["cost"]["actual_usd"] if j["cost"]["actual_usd"] is not None else j["cost"]["estimate_usd"]
        opts = j.get("options", {})
        extras = f"{opts.get('chunks', 1)}p" + ("+F" if opts.get("finishing") else "")
        tier = ("SCAIL " if j.get("engine") == "scail" else "") + f"{j['tier']} {extras}"
        rows.append([j["id"], j["created"][5:16].replace("T", " "), j["status"], j["character"], j["scene"], tier, f"{done}/{len(j['runs'])}", f"${cost:.2f}"])
        ids.append(j["id"])
    return rows, ids


def refresh_jobs():
    rows, ids = jobs_table()
    return rows, ids, status_line()


def on_select_job(ids, evt: gr.SelectData):
    try:
        return job_details(ids, evt)
    except Exception as e:  # never show Gradio's red "Error" bubbles for a bad row
        return f"Couldn't load this job: {e.__class__.__name__}: {e}", None, [], []


def job_details(ids, evt):
    if not ids or evt.index is None:
        return "", None, [], []
    job = J.load_job(ids[evt.index[0]])
    opts = job.get("options", {})
    engine_name = J.ENGINE_LABELS.get(job.get("engine", "wan"), "Wan Animate 2")
    lines = [f"**{job['id']}** · {job['status']} · {engine_name} · {job['character']} · {job['scene']} · {job['tier']} · {opts.get('chunks', 1)} pass(es)" + (" · Finish" if opts.get("finishing") else ""),
             f"Character prompt: {job['prompts']['character']}",
             f"Motion prompt: {job['prompts']['motion']}",
             J.render_settings(job),
             f"Workflow version: `{job['workflow_version']}`",
             f"Cost: estimated ${job['cost']['estimate_usd']:.2f}" + (f", actual ${job['cost']['actual_usd']:.2f}" if job['cost']['actual_usd'] is not None else "")]
    if job.get("note"):
        lines.append(f"Note: {job['note']}")
    files, preview, sheets = [], None, []
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
        for s in r.get("sheets", []):
            if Path(s).exists():
                sheets.append((s, f"seed {r['seed']}: " + ("faces" if "faces" in s else "frames")))
    if preview is None and files:
        preview = files[0]
    return "\n\n".join(lines), preview, files, sheets


def on_sheet(file, tier_name, finishing, engine_label):
    """CSV columns: image,video,character,scene,seeds,passes,note[,engine]. image/video are file names already in
    ComfyUI/input; engine is wan, scail or seedance (defaults to the radio choice)."""
    if file is None:
        return "Upload a CSV first."
    p = presets()
    tier = p["tiers"][tier_name]
    made, problems = 0, []
    with open(file, newline="", encoding="utf-8") as f:
        for i, row in enumerate(csv.DictReader(f), 1):
            try:
                engine = (row.get("engine") or "").strip().lower() or engine_key(engine_label)
                if engine not in J.ENGINE_LABELS:
                    raise ValueError(f"engine must be one of {', '.join(J.ENGINE_LABELS)}")
                image = J.COMFY_INPUT / row["image"]
                video = J.COMFY_INPUT / row["video"]
                ok, msgs, info = J.preflight(str(image), str(video), engine)
                if not ok:
                    raise ValueError(" ".join(msgs))
                ch = p["characters"][row["character"]]
                sc = p["scenes"][row["scene"]]
                allowed = [c[1] for c in J.length_choices(info["frames"], engine, info["fps"])]
                chunks = min(int(row.get("passes") or 1), max(allowed))
                job = J.new_job(str(image), str(video), row["character"], ch["appearance"], row["scene"], sc, tier_name, tier,
                                int(row.get("seeds") or 1), row.get("note", ""), chunks=chunks, finishing=bool(finishing), info=info, engine=engine)
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
                info_state = gr.State({})
                with gr.Row():
                    with gr.Column():
                        image = gr.Image(type="filepath", label="Reference image (who)", height=300)
                        video = gr.Video(label="Driving video (what motion)", height=300)
                        preflight_md = gr.Markdown("Add an image and a video.")
                    with gr.Column():
                        engine = gr.Radio(list(J.ENGINES), value=list(J.ENGINES)[0], label="Engine")
                        character = gr.Dropdown(char_names, value=char_names[0], label="Character")
                        appearance = gr.Textbox(p["characters"][char_names[0]]["appearance"], label="Looks (no motion words)", lines=2)
                        scene = gr.Dropdown(scene_names, value=scene_names[0], label="Scene preset")
                        background = gr.Textbox(first_scene["background"], label="Background", lines=2)
                        with gr.Row():
                            describe_source = gr.Radio(["reference image", "driving video"], value="reference image", label="Describe the scene from", scale=3)
                            describe_btn = gr.Button("Describe", scale=1)
                        describe_md = gr.Markdown("")
                        motion = gr.Textbox(first_scene["motion"], label="What the person is doing", lines=1)
                        with gr.Row():
                            identity_hold = gr.Slider(0.8, 1.5, value=float(first_scene.get("identity_hold", 1.0)), step=0.05, label="Identity hold")
                            motion_looseness = gr.Slider(0.5, 1.0, value=float(first_scene.get("motion_looseness", 1.0)), step=0.05, label="Motion looseness")
                        with gr.Row():
                            tier = gr.Radio(tier_names, value="Standard", label="Quality tier")
                            seeds = gr.Slider(1, 4, value=2, step=1, label="Seeds (variations)")
                        with gr.Row():
                            length = gr.Radio([], label="Clip length (set after the video is checked)", interactive=False)
                            finishing = gr.Checkbox(False, label="Finish: upscale ×2, double frame rate, sound")
                        tier_md = gr.Markdown(estimate_text("Standard", 2, 1, False))
                        note = gr.Textbox(label="Note (optional)", lines=1)
                        run = gr.Button("Run", variant="primary")
                        result_md = gr.Markdown("")
                image.change(on_preflight, [image, video, engine], [preflight_md, length, info_state])
                video.change(on_preflight, [image, video, engine], [preflight_md, length, info_state])
                engine.change(on_preflight, [image, video, engine], [preflight_md, length, info_state])
                character.change(on_character, character, appearance)
                scene.change(on_scene, scene, [background, motion, identity_hold, motion_looseness])
                describe_btn.click(on_describe, [describe_source, image, video, background], [background, describe_md])

                def est(tier_name, seeds, length_label, finishing, info, engine_label):
                    info = info or {}
                    eng = engine_key(engine_label)
                    chunks = dict(J.length_choices(info.get("frames", 0), eng, info.get("fps", 24.0))).get(length_label, 1)
                    seconds = (info.get("frames") or 120) / float(info.get("fps") or 24)
                    return estimate_text(tier_name, seeds, chunks, finishing, eng, seconds)

                for comp in (tier, seeds, length, finishing, engine):
                    comp.change(est, [tier, seeds, length, finishing, info_state, engine], tier_md)
                run.click(on_run, [image, video, engine, character, appearance, scene, background, motion, identity_hold, motion_looseness,
                                   tier, seeds, length, finishing, note, info_state], result_md)

            with gr.Tab("Jobs"):
                rows, ids = jobs_table()
                ids_state = gr.State(ids)
                table = gr.Dataframe(value=rows, headers=["Job", "Created", "Status", "Character", "Scene", "Tier", "Done", "Cost"], interactive=False, wrap=True)
                detail = gr.Markdown("Select a job to see details and downloads.")
                sheets = gr.Gallery(label="Contact sheets (pick a seed)", columns=2, height=360)
                preview = gr.Video(label="Preview (compare: result left, driving video right)", height=420)
                downloads = gr.Files(label="Downloads")
                timer = gr.Timer(5)
                timer.tick(refresh_jobs, None, [table, ids_state, status])
                table.select(on_select_job, ids_state, [detail, preview, downloads, sheets])

            with gr.Tab("Job sheet"):
                gr.Markdown("Bulk mode. Upload a CSV with columns `image,video,character,scene,seeds,passes,note` and optionally `engine` (wan, scail or seedance). `image` and `video` are file names already in `ComfyUI/input` (upload them via Jupyter). Character and scene are preset names; `passes` is 1–3 (capped by the video length).")
                sheet = gr.File(label="CSV", file_types=[".csv"])
                with gr.Row():
                    sheet_engine = gr.Radio(list(J.ENGINES), value=list(J.ENGINES)[0], label="Engine for rows without one")
                    sheet_tier = gr.Radio(tier_names, value="Standard", label="Quality tier for all rows")
                    sheet_finish = gr.Checkbox(False, label="Finish for all rows")
                sheet_btn = gr.Button("Queue all rows")
                sheet_md = gr.Markdown("")
                sheet_btn.click(on_sheet, [sheet, sheet_tier, sheet_finish, sheet_engine], sheet_md)

            with gr.Tab("Help"):
                gr.Markdown(HELP)
    return demo


if __name__ == "__main__":
    # ComfyUI/output usually points at the network volume, outside the app folder; Gradio must be told it may serve from there.
    allowed = [str(J.COMFY_OUTPUT), str(J.COMFY_OUTPUT.resolve()), str(J.COMFY_INPUT.resolve())]
    # The public link is reachable by anyone who has it and the app can spend GPU time and API credits:
    # APP_PASSWORD puts a login in front of it (user "team"). Unset = open, for a private LAN only.
    auth = ("team", os.environ["APP_PASSWORD"]) if os.environ.get("APP_PASSWORD") else None
    if auth is None:
        print("WARNING: APP_PASSWORD is not set; anyone with the link can queue jobs.", flush=True)
    build().queue().launch(server_name="0.0.0.0", server_port=int(os.environ.get("APP_PORT", "7860")), show_api=False, allowed_paths=allowed, auth=auth)
