"""Job engine for the team app: presets, preflight checks, workflow building, a sequential runner, and job history.

Jobs and their settings are stored as JSON under ComfyUI/output/_jobs/<job_id>/ (on the network volume
when one is mounted), next to the rendered clips in ComfyUI/output/motion_swap/<job_id>/.
"""
import copy
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
COMFY_API_KEY = os.environ.get("COMFY_API_KEY", "")

COMFY_INPUT = ROOT / "ComfyUI" / "input"
COMFY_OUTPUT = ROOT / "ComfyUI" / "output"
JOBS_DIR = COMFY_OUTPUT / "_jobs"
WORKFLOW = ROOT / "workflows" / "wan_animate2.json"
PRESETS_FILE = ROOT / "app" / "presets.yaml"
LOGS = ROOT / "logs"
SERVER = os.environ.get("COMFY_SERVER", "http://127.0.0.1:8188")
GPU_HOURLY_USD = float(os.environ.get("GPU_HOURLY_USD", "1.0"))
MIN_FRAMES = B.CHUNK_FRAMES
MAX_CHUNKS = 3


def load_presets():
    return yaml.safe_load(PRESETS_FILE.read_text(encoding="utf-8"))


def workflow_version():
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        sha = "unknown"
    digest = hashlib.sha256(WORKFLOW.read_bytes()).hexdigest()[:8]
    return f"{sha}/{WORKFLOW.name}@{digest}"


# ---------- preflight ----------

def preflight(image_path, video_path):
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
            passes = B.chunks_for_frames(frames, MAX_CHUNKS)
            usable = min(frames, B.frames_for_chunks(passes))
            messages.append(f"Video OK: {vw}×{vh}, {fps:.0f} fps, {frames} frames ({frames / fps:.1f} s). Up to {passes} pass(es) = {usable / fps:.1f} s of output.")
        if vw > vh:
            messages.append("Warning: driving video is landscape; the clip is rendered portrait, so the sides will be cropped.")
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


def build_scail_workflow(job, seed):
    """SCAIL-2 replacement: the driving video's own orientation decides the frame (512×896 or 896×512)."""
    opts = job["options"]
    portrait = (opts.get("video_h") or 1) >= (opts.get("video_w") or 0)
    width, height = (512, 896) if portrait else (896, 512)
    wf = S.build(segments=opts["chunks"], width=width, height=height, distill=True, replace=True, finishing=opts["finishing"])
    set_input(wf, "Reference Image.image", job["inputs"]["image"])
    set_input(wf, "Driving Video.file", job["inputs"]["video"])
    set_input(wf, "Character Prompt.text", job["prompts"]["character"])
    for node in wf.values():
        if node["class_type"] == "WanSCAILToVideo":
            node["inputs"]["pose_end"] = job["settings"]["motion_looseness"]
        elif node["class_type"] == "SamplerCustom":
            node["inputs"]["noise_seed"] = seed
    total = min(S.frames_for_segments(opts["chunks"]), opts.get("frames") or S.frames_for_segments(opts["chunks"]))
    set_input(wf, "Trim Tail.length", total)
    set_input(wf, "Driving Trimmed.length", total)
    prefix = f"motion_swap/{job['id']}/s{seed}"
    set_input(wf, "Save.filename_prefix", f"{prefix}_result")
    set_input(wf, "Save Compare.filename_prefix", f"{prefix}_compare")
    if opts["finishing"]:
        set_input(wf, "Final Video.fps", round(float(opts.get("fps") or 24) * 2, 3))
        set_input(wf, "Save Final.filename_prefix", f"{prefix}_final")
    return wf


SEEDANCE_RESOLUTION = {"Fast": "480p", "Standard": "720p", "High": "1080p"}


def build_seedance_workflow(job, seed):
    """Seedance renders the whole driving clip's length (4-30 s) in one call; tier picks the resolution."""
    opts = job["options"]
    portrait = (opts.get("video_h") or 1) >= (opts.get("video_w") or 0)
    fps = float(opts.get("fps") or 24)
    seconds = (opts.get("frames") or 5 * fps) / fps
    prompt = f"{job['prompts']['character']} The person is {job['prompts']['motion'].rstrip('.')}."
    wf = SD.build(prompt=prompt, resolution=SEEDANCE_RESOLUTION.get(job["tier"], "720p"), ratio="9:16" if portrait else "16:9",
                  duration=seconds, audio=True, seed=seed)
    set_input(wf, "Reference Image.image", job["inputs"]["image"])
    set_input(wf, "Driving Video.file", job["inputs"]["video"])
    prefix = f"motion_swap/{job['id']}/s{seed}"
    set_input(wf, "Save.filename_prefix", f"{prefix}_result")
    set_input(wf, "Save Compare.filename_prefix", f"{prefix}_compare")
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
    prefix = f"motion_swap/{job['id']}/s{seed}"
    set_input(wf, "Save.filename_prefix", f"{prefix}_result")
    set_input(wf, "Save Compare.filename_prefix", f"{prefix}_compare")
    if opts["finishing"]:
        set_input(wf, "Final Video.fps", round(float(opts.get("fps") or 24) * 2, 3))
        set_input(wf, "Save Final.filename_prefix", f"{prefix}_final")
    return wf


def stage_input(src, job_id, kind):
    """Copy an uploaded file into ComfyUI/input with a unique, safe name. Returns the name ComfyUI sees."""
    ext = Path(src).suffix.lower() or (".png" if kind == "ref" else ".mp4")
    name = f"app_{job_id}_{kind}{ext}"
    COMFY_INPUT.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, COMFY_INPUT / name)
    return name


def estimate_minutes(tier, finishing_cfg, chunks, finishing, seeds):
    per_clip = tier["minutes_per_clip"] * chunks + (finishing_cfg["minutes_per_clip"] * chunks if finishing else 0)
    return per_clip * seeds


def seedance_estimate(presets, tier_name, seconds, seeds):
    """(minutes, usd) for Seedance: API credits per second of video, by resolution (approximate; the exact price
    is shown on ComfyUI's node badge and on your Comfy account)."""
    cfg = presets.get("seedance", {})
    rate = cfg.get("usd_per_second", {}).get(SEEDANCE_RESOLUTION.get(tier_name, "720p"), 0.35)
    duration = SD.clamp_duration(seconds)
    return cfg.get("minutes_per_clip", 3) * seeds, round(duration * rate * seeds, 2)


def new_job(image_path, video_path, character_name, appearance, scene_name, scene, tier_name, tier, seeds,
            note="", chunks=1, finishing=False, info=None, engine="wan"):
    presets = load_presets()
    info = info or {}
    job_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
    rng = random.Random()
    seed_list = [rng.randint(0, 2**31 - 1) for _ in range(int(seeds))]
    character_prompt, motion_prompt = build_prompts(appearance, scene)
    if engine == "seedance":
        finishing = False  # Seedance already delivers its own resolution and sound
        seconds = (info.get("frames") or 120) / float(info.get("fps") or 24)
        minutes, usd = seedance_estimate(presets, tier_name, seconds, len(seed_list))
    else:
        minutes = estimate_minutes(tier, presets["finishing"], chunks, finishing, len(seed_list))
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
        "options": {"chunks": int(chunks), "finishing": bool(finishing), "frames": info.get("frames"), "fps": info.get("fps"),
                    "video_w": info.get("video_w"), "video_h": info.get("video_h")},
        "settings": {"identity_hold": float(scene.get("identity_hold", 1.0)), "motion_looseness": float(scene.get("motion_looseness", 1.0))},
        "inputs": {"image": stage_input(image_path, job_id, "ref"), "video": stage_input(video_path, job_id, "drive")},
        "prompts": {"character": character_prompt, "motion": motion_prompt, "negative": presets["negative_prompt"]},
        "runs": [{"seed": s, "status": "queued", "outputs": [], "error": None, "seconds": None, "sheets": []} for s in seed_list],
        "workflow_version": workflow_version(),
        "cost": {"estimate_usd": usd, "estimate_minutes": round(minutes, 1), "actual_usd": None},
        "note": note,
    }
    save_job(job)
    return job


# ---------- contact sheets ----------

def make_sheets(job):
    """Six frames + a face strip per seed, from the compare clip, so picking a seed takes seconds."""
    try:
        import av
        from PIL import Image
    except ImportError:
        return
    for run in job["runs"]:
        compare = next((p for p in run["outputs"] if "compare" in p), None)
        if not compare or not output_path(compare).exists():
            continue
        try:
            with av.open(str(output_path(compare))) as c:
                frames = [f.to_image() for f in c.decode(c.streams.video[0])]
            n = len(frames)
            if n < 2:
                continue
            w, h = frames[0].size
            picks = sorted({0, n // 5, 2 * n // 5, 3 * n // 5, 4 * n // 5, n - 1})
            tw, th = w // 2, h // 2
            sheet = Image.new("RGB", (tw * 3, th * 2), "black")
            for i, k in enumerate(picks[:6]):
                sheet.paste(frames[k].resize((tw, th)), ((i % 3) * tw, (i // 3) * th))
            fw = w // 4
            faces = Image.new("RGB", (fw * len(picks), fw), "black")
            for i, k in enumerate(picks):
                faces.paste(frames[k].crop((w // 8, 0, w // 8 + fw, fw)), (i * fw, 0))
            d = job_dir(job["id"])
            d.mkdir(parents=True, exist_ok=True)
            sheet_path, faces_path = d / f"s{run['seed']}_sheet.jpg", d / f"s{run['seed']}_faces.jpg"
            sheet.save(sheet_path, quality=88)
            faces.save(faces_path, quality=90)
            run["sheets"] = [str(sheet_path), str(faces_path)]
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


def list_jobs(limit=200):
    if not JOBS_DIR.exists():
        return []
    jobs = []
    for p in sorted(JOBS_DIR.glob("*/job.json"), reverse=True)[:limit]:
        try:
            jobs.append(json.loads(p.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
    return jobs


def output_path(rel):
    return COMFY_OUTPUT / rel


def resume_pending(submit):
    """Called at app start: jobs still queued, or cut off while running, go back on the queue (oldest first).
    Finished seeds are kept; only unfinished runs are rendered again. Returns the number of jobs resumed."""
    resumed = 0
    for job in reversed(list_jobs()):
        if job["status"] not in ("queued", "running"):
            continue
        for run in job["runs"]:
            if run["status"] != "done":
                run.update(status="queued", error=None, seconds=None)  # prompt_id is kept so the runner can adopt it
        job["status"] = "queued"
        job["resumed"] = job.get("resumed", 0) + 1
        save_job(job)
        submit(job["id"])
        resumed += 1
    return resumed


# ---------- runner ----------

class Runner(threading.Thread):
    """Renders jobs one after another. ComfyUI serialises work anyway; this keeps job status simple."""

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
            t0 = time.time()
            entry, prompt_id = self.adopt(run)
            if entry is None and prompt_id is None:
                extra = None
                if job.get("engine") == "seedance":
                    if not COMFY_API_KEY:
                        run["status"], run["error"] = "error", "Seedance needs a Comfy API key: set COMFY_API_KEY before starting the app."
                        save_job(job)
                        continue
                    extra = {"api_key_comfy_org": COMFY_API_KEY}
                wf = build_workflow(base, job, run["seed"])
                try:
                    prompt_id = queue_prompt(self.server, wf, client_id, extra)
                except ComfyError as e:
                    run["status"], run["error"] = "error", str(e)
                    save_job(job)
                    continue
            run["status"], run["prompt_id"] = "running", prompt_id
            save_job(job)
            while entry is None:
                time.sleep(2)
                entry = poll(self.server, prompt_id)
            run["seconds"] = round(time.time() - t0, 1) if run.get("seconds") is None else run["seconds"]
            if entry["status"].get("status_str") == "success":
                run["status"] = "done"
                run["outputs"] = collect_outputs(entry)
            else:
                run["status"] = "error"
                run["error"] = error_message(entry)
            save_job(job)

        make_sheets(job)
        job["finished"] = datetime.now().isoformat(timespec="seconds")
        seconds = time.time() - t_job
        if job.get("engine") == "seedance":
            job["cost"]["actual_usd"] = job["cost"]["estimate_usd"]  # billed by Comfy credits, not GPU time
        else:
            job["cost"]["actual_usd"] = round(seconds / 3600 * GPU_HOURLY_USD, 2)
        job["status"] = "done" if all(r["status"] == "done" for r in job["runs"]) else ("error" if all(r["status"] == "error" for r in job["runs"]) else "partial")
        save_job(job)
