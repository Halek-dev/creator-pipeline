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
import build_workflows as B  # noqa: E402
from comfy_client import ComfyError, collect_outputs, error_message, poll, queue_prompt, set_input  # noqa: E402

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


def length_choices(frames):
    """Radio choices for clip length given the driving video's frame count: [(label, chunks), ...]."""
    out = []
    for n in range(1, MAX_CHUNKS + 1):
        need = B.frames_for_chunks(n)
        if frames >= need:
            out.append((f"{need} frames · {n} pass{'es' if n > 1 else ''}", n))
    return out


# ---------- building ----------

def build_prompts(appearance, scene):
    character_prompt = f"Character appearance description: {appearance.strip().rstrip('.')}. Background description: {scene['background'].strip().rstrip('.')}."
    return character_prompt, scene["motion"].strip()


def build_workflow(base, job, seed):
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


def new_job(image_path, video_path, character_name, appearance, scene_name, scene, tier_name, tier, seeds,
            note="", chunks=1, finishing=False, info=None):
    presets = load_presets()
    info = info or {}
    job_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
    rng = random.Random()
    seed_list = [rng.randint(0, 2**31 - 1) for _ in range(int(seeds))]
    character_prompt, motion_prompt = build_prompts(appearance, scene)
    minutes = estimate_minutes(tier, presets["finishing"], chunks, finishing, len(seed_list))
    job = {
        "id": job_id,
        "created": datetime.now().isoformat(timespec="seconds"),
        "status": "queued",
        "character": character_name,
        "scene": scene_name,
        "tier": tier_name,
        "tier_settings": {"model": tier.get("model", "distilled"), "width": tier["width"], "height": tier["height"], "steps": tier["steps"]},
        "options": {"chunks": int(chunks), "finishing": bool(finishing), "frames": info.get("frames"), "fps": info.get("fps")},
        "settings": {"identity_hold": float(scene.get("identity_hold", 1.0)), "motion_looseness": float(scene.get("motion_looseness", 1.0))},
        "inputs": {"image": stage_input(image_path, job_id, "ref"), "video": stage_input(video_path, job_id, "drive")},
        "prompts": {"character": character_prompt, "motion": motion_prompt, "negative": presets["negative_prompt"]},
        "runs": [{"seed": s, "status": "queued", "outputs": [], "error": None, "seconds": None, "sheets": []} for s in seed_list],
        "workflow_version": workflow_version(),
        "cost": {"estimate_usd": round(minutes / 60 * GPU_HOURLY_USD, 2), "estimate_minutes": round(minutes, 1), "actual_usd": None},
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
            wf = build_workflow(base, job, run["seed"])
            t0 = time.time()
            try:
                prompt_id = queue_prompt(self.server, wf, client_id)
            except ComfyError as e:
                run["status"], run["error"] = "error", str(e)
                save_job(job)
                continue
            run["status"], run["prompt_id"] = "running", prompt_id
            save_job(job)
            entry = None
            while entry is None:
                time.sleep(2)
                entry = poll(self.server, prompt_id)
            run["seconds"] = round(time.time() - t0, 1)
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
        job["cost"]["actual_usd"] = round(seconds / 3600 * GPU_HOURLY_USD, 2)
        job["status"] = "done" if all(r["status"] == "done" for r in job["runs"]) else ("error" if all(r["status"] == "error" for r in job["runs"]) else "partial")
        save_job(job)
