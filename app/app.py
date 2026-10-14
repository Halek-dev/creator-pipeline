"""Team app: upload a reference image and a driving video, pick a preset, render motion swap clips.

  .venv/bin/python app/app.py            # http://0.0.0.0:7860 (start.sh runs this and opens a public link)

Environment: COMFY_SERVER (default http://127.0.0.1:8188), APP_PORT (7860), GPU_HOURLY_USD (1.0).
"""
import csv
import json
import os
from datetime import datetime
import sys
from pathlib import Path

import gradio as gr

sys.path.insert(0, str(Path(__file__).resolve().parent))
import describe as D  # noqa: E402
import jobs as J  # noqa: E402
from theme import CSS, FORCE_DARK_JS, THEME  # noqa: E402

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


def status_chip():
    """The status line as a small pill with a green (idle) or amber (busy) dot."""
    text = status_line()
    busy = "rendering" in text or "waiting" in text
    return f'<div class="status-chip{" busy" if busy else ""}">{text}</div>'


def status_line():
    parts = []
    try:
        wd = json.loads((J.LOGS / "watchdog.status").read_text())
        age = (datetime.now() - datetime.fromisoformat(wd["checked"])).total_seconds()
        if age < 180:  # a stale file means the watchdog is not running; say nothing rather than something wrong
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


def refresh_jobs():  # status goes back as the chip html
    rows, ids = jobs_table()
    return rows, ids, status_chip()


def on_select_job(ids, evt: gr.SelectData):
    try:
        return job_details(ids, evt)
    except Exception as e:  # never show Gradio's red "Error" bubbles for a bad row
        return f"Couldn't load this job: {e.__class__.__name__}: {e}", None, [], []


class _FirstRow:
    index = (0, 0)


def show_latest_job(ids):
    """Details of the newest job, for when the Jobs tab opens; same shape as a table click."""
    if not ids:
        return "No jobs yet. Queue one on the New clip tab.", None, [], []
    return on_select_job(ids, _FirstRow())


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
    with gr.Blocks(title="Creator Pipeline", theme=THEME, css=CSS, js=FORCE_DARK_JS) as demo:
        with gr.Row(elem_classes=["hero"]):
            gr.HTML('<div class="brand"><div class="logo">▶</div><div><h1>Motion swap studio</h1>'
                    '<p class="tag">Your character, any move. Upload, pick a look, render, download.</p></div></div>')
            status = gr.HTML(status_chip())
        start_tab = os.environ.get("APP_START_TAB", "new")
        with gr.Tabs(selected=start_tab if start_tab in ("new", "jobs", "sheet", "costs", "help") else "new"):
            with gr.Tab("New clip", id="new"):
                info_state = gr.State({})
                with gr.Row(equal_height=False):
                    with gr.Column(scale=5):
                        with gr.Group(elem_classes=["step"]):
                            gr.HTML('<div class="step-title"><span class="n">1</span><h3>Inputs</h3><span class="hint">who + what move</span></div>')
                            with gr.Row():
                                image = gr.Image(type="filepath", label="Reference image (who)", height=320, elem_classes=["upload-tile"])
                                video = gr.Video(label="Driving video (what motion)", height=320, elem_classes=["upload-tile"])
                            preflight_md = gr.Markdown("Add an image and a video. The check runs by itself.", elem_classes=["preflight"])
                        with gr.Group(elem_classes=["step"]):
                            gr.HTML('<div class="step-title"><span class="n">2</span><h3>Look</h3><span class="hint">engine, character, scene</span></div>')
                            engine = gr.Radio(list(J.ENGINES), value=list(J.ENGINES)[0], label="Engine")
                            with gr.Row():
                                character = gr.Dropdown(char_names, value=char_names[0], label="Character")
                                scene = gr.Dropdown(scene_names, value=scene_names[0], label="Scene preset")
                            appearance = gr.Textbox(p["characters"][char_names[0]]["appearance"], label="Looks (no motion words)", lines=2)
                            background = gr.Textbox(first_scene["background"], label="Background", lines=2)
                            with gr.Row():
                                describe_source = gr.Radio(["reference image", "driving video"], value="reference image", label="Describe the scene from", scale=3)
                                describe_btn = gr.Button("✨ Describe", scale=1)
                            describe_md = gr.Markdown("")
                            motion = gr.Textbox(first_scene["motion"], label="What the person is doing", lines=1)
                    with gr.Column(scale=4):
                        with gr.Group(elem_classes=["step"]):
                            gr.HTML('<div class="step-title"><span class="n">3</span><h3>Quality</h3><span class="hint">tier, length, variations</span></div>')
                            tier = gr.Radio(tier_names, value="Standard", label="Quality tier")
                            length = gr.Radio([], label="Clip length (appears after the video is checked)", interactive=False)
                            seeds = gr.Slider(1, 4, value=2, step=1, label="Seeds (variations to pick from)")
                            finishing = gr.Checkbox(False, label="Finish: upscale ×2, double frame rate, sound")
                            with gr.Accordion("Fine tuning", open=False):
                                identity_hold = gr.Slider(0.8, 1.5, value=float(first_scene.get("identity_hold", 1.0)), step=0.05, label="Identity hold")
                                motion_looseness = gr.Slider(0.5, 1.0, value=float(first_scene.get("motion_looseness", 1.0)), step=0.05, label="Motion looseness")
                            tier_md = gr.Markdown(estimate_text("Standard", 2, 1, False), elem_classes=["estimate"])
                        with gr.Group(elem_classes=["step"]):
                            gr.HTML('<div class="step-title"><span class="n">4</span><h3>Render</h3></div>')
                            note = gr.Textbox(label="Note (optional)", lines=1, placeholder="e.g. TikTok teaser, try 2")
                            run = gr.Button("▶  Render clip", variant="primary", elem_classes=["run-btn"])
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

            with gr.Tab("Jobs", id="jobs"):
                rows, ids = jobs_table()
                ids_state = gr.State(ids)
                table = gr.Dataframe(value=rows, headers=["Job", "Created", "Status", "Character", "Scene", "Tier", "Done", "Cost"], interactive=False, wrap=True, elem_classes=["jobs-table"])
                with gr.Row(equal_height=False):
                    with gr.Column(scale=5):
                        with gr.Group(elem_classes=["step"]):
                            gr.HTML('<div class="step-title"><h3>Preview</h3><span class="hint">result left, driving video right</span></div>')
                            preview = gr.Video(label="Compare", height=460, show_label=False)
                        with gr.Group(elem_classes=["step"]):
                            gr.HTML('<div class="step-title"><h3>Pick a seed</h3><span class="hint">six frames + a face strip per seed</span></div>')
                            sheets = gr.Gallery(label="Contact sheets", columns=2, height=320, show_label=False)
                    with gr.Column(scale=4):
                        with gr.Group(elem_classes=["step"]):
                            gr.HTML('<div class="step-title"><h3>Details</h3></div>')
                            detail = gr.Markdown("Select a job to see details and downloads.", elem_classes=["detail"])
                            downloads = gr.Files(label="Downloads")
                timer = gr.Timer(5)
                timer.tick(refresh_jobs, None, [table, ids_state, status])
                table.select(on_select_job, ids_state, [detail, preview, downloads, sheets])
                demo.load(show_latest_job, ids_state, [detail, preview, downloads, sheets])

            with gr.Tab("Job sheet", id="sheet"):
                gr.HTML('<div class="step-title"><h3>Bulk mode</h3><span class="hint">one CSV row = one job</span></div>')
                gr.Markdown("Upload a CSV with columns `image,video,character,scene,seeds,passes,note` and optionally `engine` (wan, scail or seedance). `image` and `video` are file names already in `ComfyUI/input` (upload them via Jupyter). Character and scene are preset names; `passes` is 1–3 (capped by the video length).")
                sheet = gr.File(label="CSV", file_types=[".csv"])
                with gr.Row():
                    sheet_engine = gr.Radio(list(J.ENGINES), value=list(J.ENGINES)[0], label="Engine for rows without one")
                    sheet_tier = gr.Radio(tier_names, value="Standard", label="Quality tier for all rows")
                    sheet_finish = gr.Checkbox(False, label="Finish for all rows")
                sheet_btn = gr.Button("Queue all rows", variant="primary")
                sheet_md = gr.Markdown("")
                sheet_btn.click(on_sheet, [sheet, sheet_tier, sheet_finish, sheet_engine], sheet_md)

            with gr.Tab("Costs", id="costs"):
                gr.Markdown(f"Per month, from the job history. **Render $** is the GPU time the clips took at ${J.GPU_HOURLY_USD:.2f}/h. "
                            f"**Pod $ (est.)** is what the pod most likely billed: the span of each day's jobs plus the watchdog's {J.IDLE_ALLOWANCE_MIN:.0f}-minute idle allowance "
                            "and a few minutes of start-up. **Seedance $** is Comfy credits, not GPU time. Check the real numbers on RunPod → Billing and comfy.org → Credits.")
                cost_table = gr.Dataframe(value=J.cost_rows(J.cost_summary()), headers=J.COST_HEADERS, interactive=False)
                cost_btn = gr.Button("Refresh", variant="secondary")
                cost_btn.click(lambda: J.cost_rows(J.cost_summary()), None, cost_table)

            with gr.Tab("Help", id="help"):
                gr.Markdown(HELP, elem_classes=["help"])
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
