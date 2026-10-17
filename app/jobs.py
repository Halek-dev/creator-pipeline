"""Job engine for the team app: presets, preflight checks, workflow building, a sequential runner, and job history.

Jobs and their settings are stored as JSON under ComfyUI/output/_jobs/<job_id>/ (on the network volume
when one is mounted), next to the rendered clips in ComfyUI/output/motion_swap/<job_id>/.
"""
import hashlib
import json
import os
import queue
import random
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import build_scail as S  # noqa: E402
import build_seedance as SD  # noqa: E402
import build_workflows as B  # noqa: E402
from comfy_client import ComfyError, collect_outputs, error_message, poll, queue_contains, queue_prompt, set_input  # noqa: E402

ENGINES = {
    "Wan Animate 2": "wan",                            # background and camera from the scene text (open model)
    "SCAIL-2 (keep the video's background)": "scail",  # character swapped into the original driving video (open model)
    "Seedance 2.5 (paid, no GPU)": "seedance",         # ByteDance API through ComfyUI; needs COMFY_API_KEY
}
ENGINE_LABELS = {v: k for k, v in ENGINES.items()}
COMFY_API_KEY = os.environ.get("COMFY_API_KEY", "")

COMFY_INPUT = ROOT / "ComfyUI" / "input"
COMFY_OUTPUT = ROOT / "ComfyUI" / "output"
JOBS_DIR = COMFY_OUTPUT / "_jobs"
WORKFLOW = ROOT / "workflows" / "wan_animate2.json"
VERSIONED_FILES = [WORKFLOW, ROOT / "tools" / "build_workflows.py", ROOT / "tools" / "build_scail.py", ROOT / "tools" / "build_seedance.py"]
PRESETS_FILE = ROOT / "app" / "presets.yaml"
LOGS = ROOT / "logs"
SERVER = os.environ.get("COMFY_SERVER", "http://127.0.0.1:8188")
GPU_HOURLY_USD = float(os.environ.get("GPU_HOURLY_USD", "1.0"))
MIN_FRAMES = B.CHUNK_FRAMES
MAX_CHUNKS = 3
MAX_SEEDS = 8
SEEDANCE_RESOLUTION = {"Fast": "480p", "Standard": "720p", "High": "1080p"}


def load_presets():
    return yaml.safe_load(PRESETS_FILE.read_text(encoding="utf-8"))


def workflow_version():
    """git sha + a digest of the base workflow and every generator, so a job records exactly which recipe made it."""
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=5).stdout.strip() or "unknown"
    except Exception:
        sha = "unknown"
    h = hashlib.sha256()
    for p in VERSIONED_FILES:
        h.update(p.read_bytes())
    return f"{sha}/recipes@{h.hexdigest()[:8]}"


# ---------- preflight ----------

def preflight(image_path, video_path, engine="wan"):
    """Return (ok, messages, info). Blocks on things that would fail; warns on things that lower quality.
    info carries what the app needs later: driving frames, fps, sizes."""
    messages, ok, info = [], True, {}
    if not image_path:
        return False, ["Add a reference image."], info
    if not video_path:
        return False, ["Add a driving video."], info
    try:
        from PIL import Image

        with Image.open(image_path) as im:
            w, h = im.size
        info.update(image_w=w, image_h=h)
        if max(w, h) < 768:
            messages.append(f"Warning: reference image is small ({w}×{h}). 1024–2048 px on the long side gives sharper faces.")
        if w > h:
            messages.append("Warning: reference image is landscape. Full-body portrait references work best.")
        messages.append(f"Image OK: {w}×{h}.")
    except Exception as e:
        ok = False
        messages.append(f"Can't read the image: {e}")
    try:
        import av

        with av.open(video_path) as c:
            s = c.streams.video[0]
            fps = float(s.average_rate or 24)
            frames = s.frames or (int(float(s.duration * s.time_base) * fps) if s.duration else 0)
            vw, vh = s.width, s.height
        info.update(frames=frames, fps=fps, video_w=vw, video_h=vh)
        if frames < MIN_FRAMES:
            ok = False
            messages.append(f"Driving video has {frames} frames; at least {MIN_FRAMES} are needed (about {MIN_FRAMES / fps:.1f} s at {fps:.0f} fps).")
        else:
            choices = length_choices(frames, engine, fps)
            if engine == "seedance":
                reach = f"Seedance renders {SD.clamp_duration(frames / fps)} s in one call."
            else:
                passes = max(c[1] for c in choices)
                usable = min(frames, S.frames_for_segments(passes) if engine == "scail" else B.frames_for_chunks(passes))
                reach = f"Up to {passes} pass(es) = {usable / fps:.1f} s of output."
            messages.append(f"Video OK: {vw}×{vh}, {fps:.0f} fps, {frames} frames ({frames / fps:.1f} s). {reach}")
        if vw > vh:
            messages.append({
                "wan": "Warning: driving video is landscape; Wan Animate 2 renders portrait, so the sides will be cropped.",
                "scail": "Note: driving video is landscape; SCAIL-2 renders 896×512 to match it.",
                "seedance": "Note: driving video is landscape; Seedance renders 16:9 to match it.",
            }[engine])
        messages.append("Reminder: one person in the video, full body, similar framing to the image.")
    except Exception as e:
        ok = False
        messages.append(f"Can't read the video: {e}")
    return ok, messages, info


def length_choices(frames, engine="wan", fps=24.0):
    """Radio choices for clip length given the driving video's frame count: [(label, passes), ...].
    Wan Animate 2 passes are 81 frames with 1 shared; SCAIL-2 segments are 81 frames with 5 shared;
    Seedance renders the whole clip (4-30 s) in one call."""
    if engine == "seedance":
        seconds = SD.clamp_duration(frames / float(fps or 24))
        return [(f"{seconds} s (whole clip)", 1)]
    out = []
    for n in range(1, MAX_CHUNKS + 1):
        need = S.frames_for_segments(n) if engine == "scail" else B.frames_for_chunks(n)
        if frames >= need:
            out.append((f"{need} frames · {n} pass{'es' if n > 1 else ''}", n))
    return out


# ---------- building ----------

def build_prompts(appearance, scene):
    character_prompt = f"Character appearance description: {appearance.strip().rstrip('.')}. Background description: {scene['background'].strip().rstrip('.')}."
    return character_prompt, scene["motion"].strip()


def _portrait(opts):
    return (opts.get("video_h") or 1) >= (opts.get("video_w") or 0)


def _set_outputs(wf, job, seed, finishing):
    prefix = f"motion_swap/{job['id']}/s{seed}"
    set_input(wf, "Save.filename_prefix", f"{prefix}_result")
    if "Save Compare" in B.by_title(wf):  # Seedance graphs have no compare node; the app builds that clip itself
        set_input(wf, "Save Compare.filename_prefix", f"{prefix}_compare")
    if finishing:
        set_input(wf, "Final Video.fps", round(float(job["options"].get("fps") or 24) * 2, 3))
        set_input(wf, "Save Final.filename_prefix", f"{prefix}_final")


# SCAIL-2 quality tiers (resolution follows the video's orientation). Fast = the official turbo path. Standard adds
# steps. High renders at 704p, which the model card says replacement performs better at, with a softer distill
# strength and more steps (maintainers: "lower the strength or raise the steps"). None of them is the authors'
# 40-step cfg 4 setting; that is S.build(distill=False) and takes about 13x the turbo time.
SCAIL_TIERS = {
    "Fast": {"size": (512, 896), "steps": 6, "strength": 0.8, "factor": 1.0},
    "Standard": {"size": (512, 896), "steps": 8, "strength": 0.8, "factor": 1.35},
    "High": {"size": (704, 1280), "steps": 10, "strength": 0.6, "factor": 3.5},
}


def scail_prompt(appearance, background, motion):
    """The authors want a long description of the finished video, including the new character's clothing."""
    looks, scene, move = (x.strip().rstrip(".") for x in (appearance, background, motion))
    return (f"A video of {looks}. The scene is {scene}. The person is {move}, and the character keeps exactly the same pose, "
            "movement and timing as the original performer. Natural skin texture, real fabric with folds and seams, consistent lighting "
            "with the scene, steady camera, no extra people, no duplicated limbs, no distortion.")


def build_scail_workflow(job, seed):
    """SCAIL-2 replacement: the driving video's own orientation decides the frame; the tier decides size and steps."""
    opts = job["options"]
    tier = SCAIL_TIERS.get(job.get("tier"), SCAIL_TIERS["Standard"])
    w, h = tier["size"]
    width, height = (w, h) if _portrait(opts) else (h, w)
    wf = S.build(segments=opts["chunks"], width=width, height=height, distill=True, replace=True, finishing=opts["finishing"],
                 steps=tier["steps"], distill_strength=tier["strength"])
    set_input(wf, "Reference Image.image", job["inputs"].get("image_scail") or job["inputs"]["image"])
    set_input(wf, "Driving Video.file", job["inputs"]["video"])
    appearance = job["prompts"]["character"].split("Character appearance description: ", 1)[-1].split(". Background description:", 1)[0]
    background = job["prompts"]["character"].split("Background description: ", 1)[-1]
    set_input(wf, "Character Prompt.text", scail_prompt(appearance, background, job["prompts"]["motion"]))
    for node in wf.values():
        if node["class_type"] == "WanSCAILToVideo":
            node["inputs"]["pose_end"] = job["settings"]["motion_looseness"]
        elif node["class_type"] == "SamplerCustom":
            node["inputs"]["noise_seed"] = seed
    total = min(S.frames_for_segments(opts["chunks"]), opts.get("frames") or S.frames_for_segments(opts["chunks"]))
    set_input(wf, "Trim Tail.length", total)
    set_input(wf, "Driving Trimmed.length", total)
    _set_outputs(wf, job, seed, opts["finishing"])
    return wf


SEEDANCE_MODES = {"edit": "Swap into the video", "reference": "New scene from the text"}


def seedance_prompt(mode, appearance, background, motion):
    """Seedance wants the files named in the prompt: @Image1 is the reference image, @Video1 the driving video."""
    looks = appearance.strip().rstrip(".")
    if mode == "edit":
        return (f"In @Video1, replace the person with the character from @Image1: {looks}. Keep the scene, background, lighting, "
                "camera movement, choreography, body posture and timing exactly as in @Video1. Keep the face, hairstyle and outfit "
                "consistent with @Image1. No background change, no camera change, no body deformation.")
    return (f"The character from @Image1 ({looks}) performs exactly the same dance choreography, body posture, rhythm and timing as the "
            f"person in @Video1, move for move. Do not copy the dancer's appearance, clothing or background. "
            f"Scene: {background.strip().rstrip('.')}. The person is {motion.strip().rstrip('.')}.")


def build_seedance_workflow(job, seed):
    """Seedance in one call. "edit" (default) swaps the character into the driving video and keeps its length and
    framing; "reference" makes a new clip (4-30 s) in the scene from the text. Tier picks the resolution; Fast uses
    the cheaper Draft model."""
    opts = job["options"]
    fps = float(opts.get("fps") or 24)
    seconds = (opts.get("frames") or 5 * fps) / fps
    mode = opts.get("seedance_mode", "edit")
    appearance = (job["prompts"]["character"].split("Character appearance description: ", 1)[-1].split(". Background description:", 1)[0])
    background = job["prompts"]["character"].split("Background description: ", 1)[-1]
    prompt = seedance_prompt(mode, appearance, background, job["prompts"]["motion"])
    wf = SD.build(prompt=prompt, resolution=SEEDANCE_RESOLUTION.get(job["tier"], "720p"), ratio="9:16" if _portrait(opts) else "16:9",
                  duration=seconds, audio=True, seed=seed, task_type=mode, model="Seedance 2.5 Draft" if job["tier"] == "Fast" else "Seedance 2.5")
    set_input(wf, "Reference Image.image", job["inputs"]["image"])
    set_input(wf, "Driving Video.file", job["inputs"]["video"])
    _set_outputs(wf, job, seed, False)
    return wf


def build_workflow(base, job, seed):
    if job.get("engine") == "scail":
        return build_scail_workflow(job, seed)
    if job.get("engine") == "seedance":
        return build_seedance_workflow(job, seed)
    tier, opts = job["tier_settings"], job["options"]
    wf = B.build(model=tier.get("model", "distilled"), chunks=opts["chunks"], finishing=opts["finishing"], base=base)
    set_input(wf, "Reference Image.image", job["inputs"]["image"])
    set_input(wf, "Driving Video.file", job["inputs"]["video"])
    set_input(wf, "Character Prompt.text", job["prompts"]["character"])
    set_input(wf, "Motion Prompt.text", job["prompts"]["motion"])
    set_input(wf, "Negative Prompt.text", job["prompts"]["negative"])
    for key in ("Resize Driving.resize_type.width", "Resize Reference.resize_type.width"):
        set_input(wf, key, tier["width"])
    for key in ("Resize Driving.resize_type.height", "Resize Reference.resize_type.height"):
        set_input(wf, key, tier["height"])
    set_input(wf, "Scheduler.steps", tier["steps"])
    for node in wf.values():  # every pass and every sampler, not only the first
        if node["class_type"] == "WanAnimate2ToVideo":
            node["inputs"].update(width=tier["width"], height=tier["height"],
                                  reference_image_strength=job["settings"]["identity_hold"],
                                  pose_end_percent=job["settings"]["motion_looseness"])
        elif node["class_type"] == "SamplerCustom":
            node["inputs"]["noise_seed"] = seed
    if opts["chunks"] > 1:
        total = min(B.frames_for_chunks(opts["chunks"]), opts.get("frames") or B.frames_for_chunks(opts["chunks"]))
        set_input(wf, "Trim Tail.length", total)
        set_input(wf, "Driving Trimmed.length", total)
    _set_outputs(wf, job, seed, opts["finishing"])
    return wf


def render_settings(job):
    """One line describing what actually rendered, per engine (the Wan tier numbers don't apply to the others)."""
    s, t, opts = job["settings"], job["tier_settings"], job.get("options", {})
    engine = job.get("engine", "wan")
    if engine == "scail":
        tier = SCAIL_TIERS.get(job.get("tier"), SCAIL_TIERS["Standard"])
        w, h = tier["size"]
        size = f"{w}×{h}" if _portrait(opts) else f"{h}×{w}"
        return (f"SCAIL-2 int8 + DPO, T2V distill {tier['strength']}, {tier['steps']} steps, {size}, reference padded and cut out on black, "
                f"one identity (largest person), motion looseness {s['motion_looseness']} (Identity hold does not apply)")
    if engine == "seedance":
        mode = opts.get("seedance_mode", "edit")
        return (f"Seedance 2.5{' Draft' if job['tier'] == 'Fast' else ''} at {SEEDANCE_RESOLUTION.get(job['tier'], '720p')}, "
                f"{SEEDANCE_MODES.get(mode, mode).lower()}, with sound (sliders do not apply)")
    return f"Identity hold {s['identity_hold']}, motion looseness {s['motion_looseness']}, {t['width']}×{t['height']}, {t['steps']} steps, model {t.get('model', 'distilled')}"


def stage_input(src, job_id, kind):
    """Copy an uploaded file into ComfyUI/input with a unique, safe name. Returns the name ComfyUI sees."""
    ext = Path(src).suffix.lower() or (".png" if kind == "ref" else ".mp4")
    name = f"app_{job_id}_{kind}{ext}"
    COMFY_INPUT.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, COMFY_INPUT / name)
    return name


def pad_reference(src, dst, video_w, video_h, max_side=2048):
    """Pad (never crop) the reference onto black so it has the driving video's aspect ratio, as the SCAIL-2 authors ask:
    the node would otherwise centre-crop it to the output size and lose the top of the head or the feet."""
    from PIL import Image

    with Image.open(src) as im:
        im = im.convert("RGB")
        if im.width > max_side or im.height > max_side:
            im.thumbnail((max_side, max_side))
        target = (video_w or 9) / (video_h or 16)
        w, h = im.size
        if w / h > target:  # too wide: add height
            new_w, new_h = w, max(h, round(w / target))
        else:               # too tall or equal: add width
            new_w, new_h = max(w, round(h * target)), h
        canvas = Image.new("RGB", (new_w, new_h), "black")
        canvas.paste(im, ((new_w - w) // 2, (new_h - h) // 2))
        canvas.save(dst)
    return canvas.size


SCAIL_BASE_MINUTES = 6  # measured Standard-resolution turbo pass on an RTX PRO 4500 / A100 class GPU


def estimate_minutes(tier, finishing_cfg, chunks, finishing, seeds, engine="wan", tier_name=None):
    minutes = tier["minutes_per_clip"]
    if engine == "scail":
        minutes = SCAIL_BASE_MINUTES * SCAIL_TIERS.get(tier_name or tier.get("name"), SCAIL_TIERS["Standard"])["factor"]
    per_clip = minutes * chunks + (finishing_cfg["minutes_per_clip"] * chunks if finishing else 0)
    return per_clip * seeds


def seedance_estimate(presets, tier_name, seconds, seeds):
    """(minutes, usd) for Seedance, from Comfy credits per second of video by resolution (presets.yaml, measured
    where marked). Use seedance_credits() for the credit figure itself."""
    minutes, credits, usd, _ = seedance_credits(presets, tier_name, seconds, seeds)
    return minutes, usd


def seedance_credits(presets, tier_name, seconds, seeds, mode="edit"):
    """(minutes, credits, usd, measured) for a Seedance job. Comfy bills BytePlus tokens: (input + output seconds)
    x pixels x fps / 1024, at a per-resolution credit rate. The driving video is the input, so its length is billed on
    top of the output. edit: output = input length. reference: output = clamp(input, 4..30 s)."""
    cfg = presets.get("seedance", {})
    res = SEEDANCE_RESOLUTION.get(tier_name, "720p")
    tokens_s = cfg.get("tokens_per_second", {}).get(res, 21600)
    rate = cfg.get("credits_per_1k_tokens", {}).get(res, 1.931)
    input_s = max(4.0, min(30.0, float(seconds or 5)))
    output_s = input_s if mode == "edit" else float(SD.clamp_duration(seconds))
    credits = int(round((input_s + output_s) * tokens_s / 1000 * rate * seeds))
    usd = round(credits / float(cfg.get("credits_per_usd", 211)), 2)
    return cfg.get("minutes_per_clip", 7) * seeds, credits, usd, True


def seedance_cap(presets):
    return int(presets.get("seedance", {}).get("max_credits", 900))


def new_job(image_path, video_path, character_name, appearance, scene_name, scene, tier_name, tier, seeds,
            note="", chunks=1, finishing=False, info=None, engine="wan", seedance_mode="edit"):
    presets = load_presets()
    info = info or {}
    seeds = max(1, min(MAX_SEEDS, int(seeds)))
    chunks = max(1, min(MAX_CHUNKS, int(chunks)))
    job_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
    rng = random.Random()
    seed_list = [rng.randint(0, 2**31 - 1) for _ in range(seeds)]
    character_prompt, motion_prompt = build_prompts(appearance, scene)
    if engine == "seedance":
        finishing = False  # Seedance already delivers its own resolution and sound
        seconds = (info.get("frames") or 120) / float(info.get("fps") or 24)
        minutes, credits, usd, _ = seedance_credits(presets, tier_name, seconds, seeds, seedance_mode)
    else:
        minutes = estimate_minutes(tier, presets["finishing"], chunks, finishing, seeds, engine, tier_name)
        usd = round(minutes / 60 * GPU_HOURLY_USD, 2)
    job = {
        "id": job_id,
        "created": datetime.now().isoformat(timespec="seconds"),
        "status": "queued",
        "engine": engine,
        "character": character_name,
        "scene": scene_name,
        "tier": tier_name,
        "tier_settings": {"model": tier.get("model", "distilled"), "width": tier["width"], "height": tier["height"], "steps": tier["steps"]},
        "options": {"chunks": chunks, "finishing": bool(finishing), "frames": info.get("frames"), "fps": info.get("fps"),
                    "video_w": info.get("video_w"), "video_h": info.get("video_h"),
                    **({"seedance_mode": seedance_mode if seedance_mode in SEEDANCE_MODES else "edit"} if engine == "seedance" else {})},
        "settings": {"identity_hold": float(scene.get("identity_hold", 1.0)), "motion_looseness": float(scene.get("motion_looseness", 1.0))},
        "inputs": {"image": stage_input(image_path, job_id, "ref"), "video": stage_input(video_path, job_id, "drive")},
        "prompts": {"character": character_prompt, "motion": motion_prompt, "negative": presets["negative_prompt"]},
        "runs": [{"seed": s, "status": "queued", "outputs": [], "error": None, "seconds": None, "sheets": []} for s in seed_list],
        "workflow_version": workflow_version(),
        "cost": {"estimate_usd": usd, "estimate_minutes": round(minutes, 1), "actual_usd": None, "credits": credits if engine == "seedance" else None},
        "note": note,
    }
    if engine == "scail" and info.get("video_w") and info.get("video_h"):
        padded = f"app_{job_id}_ref_pad.png"
        try:
            pad_reference(COMFY_INPUT / job["inputs"]["image"], COMFY_INPUT / padded, info["video_w"], info["video_h"])
            job["inputs"]["image_scail"] = padded
        except Exception as e:  # fall back to the raw reference; the node will centre-crop it
            job["pad_error"] = str(e)
    save_job(job)
    return job


# ---------- contact sheets ----------

def _picked_frames(path, count=6):
    """Decode a clip once to count frames, once more keeping only the picked ones (never the whole clip in RAM)."""
    import av

    with av.open(str(path)) as c:
        stream = c.streams.video[0]
        n = stream.frames or sum(1 for _ in c.decode(stream))
    if n < 2:
        return []
    wanted = sorted({0, n // 5, 2 * n // 5, 3 * n // 5, 4 * n // 5, n - 1})[:count]
    picked = {}
    with av.open(str(path)) as c:
        for i, f in enumerate(c.decode(c.streams.video[0])):
            if i in wanted:
                picked[i] = f.to_image()
            if i >= wanted[-1]:
                break
    return [picked[i] for i in wanted if i in picked]


def _side_by_side(result, driving, dst, max_seconds=10.0, height=640):
    """Write result | driving as one clip, frame by frame (never more than two frames in memory), driving frames
    picked by time so differing frame rates stay in sync. No audio: the compare is for scoring."""
    from fractions import Fraction

    import av
    from PIL import Image

    def scaled(frame):
        im = frame.to_image()
        w = max(2, round(im.width * height / im.height) // 2 * 2)
        return im.resize((w, height))

    with av.open(str(result)) as rc, av.open(str(driving)) as dc:
        rs, ds = rc.streams.video[0], dc.streams.video[0]
        fps = Fraction(rs.average_rate or 24).limit_denominator(1000)
        drive_frames = dc.decode(ds)
        current = next(drive_frames, None)
        out = None
        try:
            for i, rf in enumerate(rc.decode(rs)):
                t = rf.time if rf.time is not None else i / float(fps)
                if t > max_seconds:
                    break
                nxt = current
                while nxt is not None and (nxt.time is None or nxt.time <= t):
                    current = nxt
                    nxt = next(drive_frames, None)
                if nxt is not None and current is not None and nxt.time is not None and nxt.time <= t:
                    current = nxt
                a = scaled(rf)
                b = scaled(current) if current is not None else Image.new("RGB", (a.width, height), "black")
                canvas = Image.new("RGB", (a.width + b.width, height), "black")
                canvas.paste(a, (0, 0))
                canvas.paste(b, (a.width, 0))
                if out is None:
                    out = av.open(str(dst), "w")
                    vs = out.add_stream("libx264", rate=fps)
                    vs.width, vs.height, vs.pix_fmt = canvas.width, canvas.height, "yuv420p"
                    vs.options = {"crf": "20", "preset": "veryfast"}
                for pkt in vs.encode(av.VideoFrame.from_image(canvas)):
                    out.mux(pkt)
            if out is not None:
                for pkt in vs.encode():
                    out.mux(pkt)
        finally:
            if out is not None:
                out.close()
    return out is not None


def make_compare(job, max_seconds=10.0):
    """Seeds that have a result but no compare clip (Seedance, or a render whose compare failed) get one built here,
    streaming, instead of inside ComfyUI where it needs the whole clip in RAM."""
    for run in job["runs"]:
        if run["status"] != "done" or any("compare" in p for p in run["outputs"]):
            continue
        result = next((p for p in run["outputs"] if "_result" in p), None) or next((p for p in run["outputs"] if p.endswith(".mp4")), None)
        driving = COMFY_INPUT / job.get("inputs", {}).get("video", "")
        if not result or not output_path(result).exists() or not driving.is_file():
            continue
        rel = f"motion_swap/{job['id']}/s{run['seed']}_compare_app.mp4"
        try:
            output_path(rel).parent.mkdir(parents=True, exist_ok=True)
            if _side_by_side(output_path(result), driving, output_path(rel), max_seconds):
                run["outputs"].append(rel)
        except Exception as e:  # a missing compare is a pity, not a failure
            run["compare_error"] = str(e)


def make_sheets(job):
    """Six frames + a face strip per seed, from the compare clip (or the result when there is none),
    so picking a seed takes seconds."""
    try:
        from PIL import Image
    except ImportError:
        return
    for run in job["runs"]:
        compare = next((p for p in run["outputs"] if "compare" in p), None)
        source = compare or next((p for p in run["outputs"] if p.endswith(".mp4")), None)
        if not source or not output_path(source).exists():
            continue
        try:
            frames = _picked_frames(output_path(source))
            if not frames:
                continue
            w, h = frames[0].size
            if not compare:  # a plain result: treat it as the "left half" so the crops below still make sense
                w *= 2
            tw, th = w // 2, h // 2
            if not compare:
                frames = [fr.resize((tw, th)) for fr in frames]  # keep tiles the size a compare's left half would be
            sheet = Image.new("RGB", (tw * 3, th * 2), "black")
            for i, fr in enumerate(frames):
                sheet.paste(fr.resize((tw, th)), ((i % 3) * tw, (i // 3) * th))
            # Face strip: the result is the left half of the compare clip, the head is in its top third.
            fw, fh = w // 4, max(h // 3, w // 4)
            faces = Image.new("RGB", (fw * len(frames), fh), "black")
            for i, fr in enumerate(frames):
                faces.paste(fr.crop((w // 8, 0, w // 8 + fw, fh)), (i * fw, 0))
            d = job_dir(job["id"])
            d.mkdir(parents=True, exist_ok=True)
            sheet_path, faces_path = d / f"s{run['seed']}_sheet.jpg", d / f"s{run['seed']}_faces.jpg"
            sheet.save(sheet_path, quality=88)
            faces.save(faces_path, quality=90)
            run["sheets"] = [str(sheet_path), str(faces_path)]
            # Poster: the result half of a middle frame, the card thumbnail in the web app.
            mid = frames[len(frames) // 2]
            poster = mid.crop((0, 0, min(w // 2, mid.width), mid.height))
            if poster.width > 540:
                poster = poster.resize((540, round(h * 540 / (w // 2))))
            poster_path = d / f"s{run['seed']}_poster.jpg"
            poster.save(poster_path, quality=86)
            run["poster"] = str(poster_path)
        except Exception as e:  # sheets are a convenience; never fail the job over them
            run["sheets"] = []
            run["sheet_error"] = str(e)


# ---------- storage ----------

def job_dir(job_id):
    return JOBS_DIR / job_id


def save_job(job):
    job_dir(job["id"]).mkdir(parents=True, exist_ok=True)
    (job_dir(job["id"]) / "job.json").write_text(json.dumps(job, indent=2, ensure_ascii=False), encoding="utf-8")


def load_job(job_id):
    return json.loads((job_dir(job_id) / "job.json").read_text(encoding="utf-8"))


_JOB_CACHE = {}  # path -> (mtime_ns, job): the Jobs tab polls every few seconds; only re-read files that changed


def list_jobs(limit=200):
    if not JOBS_DIR.exists():
        return []
    jobs = []
    for p in sorted(JOBS_DIR.glob("*/job.json"), reverse=True)[:limit]:
        try:
            mtime = p.stat().st_mtime_ns
            cached = _JOB_CACHE.get(p)
            if cached and cached[0] == mtime:
                jobs.append(cached[1])
                continue
            job = json.loads(p.read_text(encoding="utf-8"))
            _JOB_CACHE[p] = (mtime, job)
            jobs.append(job)
        except (json.JSONDecodeError, OSError):
            continue
    return jobs


def output_path(rel):
    return COMFY_OUTPUT / rel


def resume_pending(submit):
    """Called at app start: jobs still queued, or cut off while running, go back on the queue (oldest first).
    Finished seeds are kept; a job whose seeds all finished is just closed. Returns the number of jobs resumed."""
    resumed = 0
    for job in reversed(list_jobs()):
        if job["status"] not in ("queued", "running"):
            continue
        if job["runs"] and all(r["status"] == "done" for r in job["runs"]):
            job["status"] = "done"
            job.setdefault("finished", datetime.now().isoformat(timespec="seconds"))
            save_job(job)
            continue
        for run in job["runs"]:
            if run["status"] != "done":
                run.update(status="queued", error=None)  # prompt_id/queued_at are kept so the runner can adopt the render
        job["status"] = "queued"
        job["resumed"] = job.get("resumed", 0) + 1
        save_job(job)
        submit(job["id"])
        resumed += 1
    return resumed


# ---------- cost summary (Phase 4.3) ----------

IDLE_ALLOWANCE_MIN = float(os.environ.get("IDLE_MINUTES", "20"))  # what the watchdog allows after the last job
SESSION_OVERHEAD_MIN = 5.0                                         # pod boot + setup + model load, per session


def cost_summary(jobs=None, hourly=None):
    """Per-month costs from the job history. Two numbers matter to the team:
    render cost (GPU time the clips actually took) and the estimated pod bill, which adds the gaps between jobs
    on the same day plus the watchdog's idle allowance, since the pod bills until it stops itself.
    Seedance jobs are billed in Comfy credits and listed separately. Returns rows sorted newest month first."""
    jobs = list_jobs(limit=100000) if jobs is None else jobs
    hourly = GPU_HOURLY_USD if hourly is None else hourly
    months = {}
    sessions = {}  # day -> (first start, last finish) for GPU engines
    for j in jobs:
        if j.get("status") in ("queued", "running"):
            continue
        month = (j.get("started") or j.get("created") or "")[:7]
        if not month:
            continue
        m = months.setdefault(month, {"month": month, "jobs": 0, "clips": 0, "render_minutes": 0.0, "render_usd": 0.0,
                                      "seedance_jobs": 0, "seedance_usd": 0.0, "sessions": 0, "pod_hours": 0.0, "pod_usd": 0.0})
        m["jobs"] += 1
        m["clips"] += sum(1 for r in j.get("runs", []) if r.get("status") == "done")
        cost = j.get("cost", {})
        if j.get("engine") == "seedance":
            m["seedance_jobs"] += 1
            m["seedance_usd"] += cost.get("actual_usd") or cost.get("estimate_usd") or 0
            continue
        secs = sum(r.get("seconds") or 0 for r in j.get("runs", []))
        m["render_minutes"] += secs / 60
        m["render_usd"] += cost.get("actual_usd") if cost.get("actual_usd") is not None else secs / 3600 * hourly
        start = j.get("started") or j.get("created")
        end = j.get("finished") or start
        if start:
            day = start[:10]
            lo, hi = sessions.get(day, (start, end))
            sessions[day] = (min(lo, start), max(hi, end))
    for day, (lo, hi) in sessions.items():
        m = months[day[:7]]
        try:
            span = (datetime.fromisoformat(hi) - datetime.fromisoformat(lo)).total_seconds() / 60
        except ValueError:
            span = 0
        m["sessions"] += 1
        m["pod_hours"] += (max(span, 0) + IDLE_ALLOWANCE_MIN + SESSION_OVERHEAD_MIN) / 60
    for m in months.values():
        m["pod_usd"] = round(m["pod_hours"] * hourly, 2)
        for k in ("render_minutes", "render_usd", "seedance_usd", "pod_hours"):
            m[k] = round(m[k], 2)
    return sorted(months.values(), key=lambda r: r["month"], reverse=True)


def cost_rows(summary):
    """Table rows for the app's Costs tab."""
    return [[m["month"], m["jobs"], m["clips"], f"{m['render_minutes']:.0f}", f"${m['render_usd']:.2f}", m["sessions"], f"{m['pod_hours']:.1f}",
             f"${m['pod_usd']:.2f}", f"${m['seedance_usd']:.2f}"] for m in summary]


COST_HEADERS = ["Month", "Jobs", "Clips", "Render min", "Render $", "Sessions", "Pod hours (est.)", "Pod $ (est.)", "Seedance $"]


# ---------- runner ----------

class Runner(threading.Thread):
    """Renders jobs one after another. ComfyUI serialises work anyway; this keeps job status simple."""

    POLL_SECONDS = 2
    MAX_POLL_ERRORS = 90        # ~3 minutes of ComfyUI being unreachable before giving up on a run
    LOST_CHECK_EVERY = 15       # polls between "does ComfyUI still have this prompt?" checks

    def __init__(self, server=SERVER):
        super().__init__(daemon=True)
        self.server = server
        self.pending = queue.Queue()
        self.current = None
        self.last_error = None

    def submit(self, job_id):
        self.pending.put(job_id)

    def run(self):
        while True:
            job_id = self.pending.get()
            try:
                self.render(job_id)
            except Exception as e:  # keep the runner alive whatever happens
                self.last_error = f"{job_id}: {e}"
                try:
                    job = load_job(job_id)
                    job["status"] = "error"
                    job["error"] = str(e)
                    save_job(job)
                except Exception:
                    pass
            finally:
                self.current = None

    def adopt(self, run):
        """After an app restart a run may already be finished, or still rendering, in ComfyUI.
        Returns (history entry or None, prompt_id or None): entry when finished, prompt_id when still in the
        queue (the caller waits for it), (None, None) when it must be queued afresh."""
        prompt_id = run.get("prompt_id")
        if not prompt_id:
            return None, None
        try:
            entry = poll(self.server, prompt_id)
            if entry is not None:
                return entry, prompt_id
            if queue_contains(self.server, prompt_id):
                return None, prompt_id
        except ComfyError:
            pass
        return None, None

    def wait(self, prompt_id):
        """Poll until the prompt finishes. Tolerates ComfyUI being briefly unreachable; returns None when ComfyUI
        no longer has the prompt at all (it was restarted or crashed), so the caller can queue it again."""
        errors = polls = 0
        while True:
            time.sleep(self.POLL_SECONDS)
            polls += 1
            try:
                entry = poll(self.server, prompt_id)
                errors = 0
            except ComfyError:
                errors += 1
                if errors >= self.MAX_POLL_ERRORS:
                    raise
                continue
            if entry is not None:
                return entry
            if polls % self.LOST_CHECK_EVERY == 0:
                try:
                    if not queue_contains(self.server, prompt_id):
                        return None
                except ComfyError:
                    pass

    def queue_run(self, job, run, base, client_id):
        """Build and queue one seed. Returns the prompt_id, or None after recording the error on the run."""
        extra = None
        if job.get("engine") == "seedance":
            if not COMFY_API_KEY:
                run["status"], run["error"] = "error", "Seedance needs a Comfy API key: set COMFY_API_KEY before starting the app."
                return None
            extra = {"api_key_comfy_org": COMFY_API_KEY}
        wf = build_workflow(base, job, run["seed"])
        try:
            prompt_id = queue_prompt(self.server, wf, client_id, extra)
        except ComfyError as e:
            run["status"], run["error"] = "error", str(e)
            return None
        run["queued_at"] = time.time()
        return prompt_id

    def render(self, job_id):
        job = load_job(job_id)
        self.current = job_id
        job["status"] = "running"
        job["started"] = datetime.now().isoformat(timespec="seconds")
        save_job(job)
        LOGS.mkdir(exist_ok=True)
        (LOGS / "keepalive").touch()

        base = json.loads(WORKFLOW.read_text(encoding="utf-8"))
        client_id = str(uuid.uuid4())
        t_job = time.time()
        for run in job["runs"]:
            if run["status"] == "done":  # already rendered before a restart
                continue
            entry, prompt_id = self.adopt(run)
            if entry is None and prompt_id is None:
                prompt_id = self.queue_run(job, run, base, client_id)
                if prompt_id is None:
                    save_job(job)
                    continue
            run["status"], run["prompt_id"] = "running", prompt_id
            save_job(job)
            if entry is None:
                entry = self.wait(prompt_id)
                if entry is None:  # ComfyUI lost it (restart/crash): queue it once more
                    run["requeued"] = run.get("requeued", 0) + 1
                    prompt_id = self.queue_run(job, run, base, client_id) if run["requeued"] <= 1 else None
                    if prompt_id is None:
                        run["status"] = "error"
                        run["error"] = run.get("error") or "ComfyUI lost this render (was it restarted?)."
                        save_job(job)
                        continue
                    run["prompt_id"] = prompt_id
                    save_job(job)
                    entry = self.wait(prompt_id)
                    if entry is None:
                        run["status"], run["error"] = "error", "ComfyUI lost this render twice."
                        save_job(job)
                        continue
            run["seconds"] = round(time.time() - run.get("queued_at", t_job), 1)
            if entry["status"].get("status_str") == "success":
                run["status"] = "done"
                run["outputs"] = collect_outputs(entry)
            else:
                run["status"] = "error"
                run["error"] = error_message(entry)
            save_job(job)

        make_compare(job)
        make_sheets(job)
        job["finished"] = datetime.now().isoformat(timespec="seconds")
        if job.get("engine") == "seedance":
            job["cost"]["actual_usd"] = job["cost"]["estimate_usd"]  # billed by Comfy credits, not GPU time
        else:
            job["cost"]["actual_usd"] = round(sum(r.get("seconds") or 0 for r in job["runs"]) / 3600 * GPU_HOURLY_USD, 2)
        job["status"] = "done" if all(r["status"] == "done" for r in job["runs"]) else ("error" if all(r["status"] == "error" for r in job["runs"]) else "partial")
        save_job(job)
