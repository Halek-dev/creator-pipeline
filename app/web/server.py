"""Team web app: a small JSON API over the job engine plus a hand-written front end (app/web/static).

  .venv/bin/python app/web/server.py          # http://0.0.0.0:7860 (start.sh runs this and opens a public link)

Environment: COMFY_SERVER (default http://127.0.0.1:8188), APP_PORT (7860), GPU_HOURLY_USD (1.0),
APP_PASSWORD (optional; HTTP Basic login as user "team"), ANTHROPIC_API_KEY (Describe), COMFY_API_KEY (Seedance).
"""
import base64
import csv
import io
import os
import secrets
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import describe as D  # noqa: E402
import jobs as J  # noqa: E402

STATIC = HERE / "static"
UPLOADS = J.ROOT / "uploads"  # files land here from the browser, then new_job copies them into ComfyUI/input
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
MAX_UPLOAD = 600 * 1024 * 1024

app = FastAPI(title="Creator Pipeline", docs_url=None, redoc_url=None)
RUNNER = J.Runner()


# ---------- auth ----------

SESSION_SECRET = secrets.token_hex(32)  # new on every app start: a restart logs everyone out, which is fine
COOKIE = "cp_session"

LOGIN_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Creator Pipeline</title><style>
body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0e0f12;color:#f2f3f5;font:14px/1.5 "Manrope",ui-sans-serif,system-ui,sans-serif}
form{width:min(360px,90vw);background:#15171b;border:1px solid #2a2e36;border-radius:14px;padding:24px;display:flex;flex-direction:column;gap:12px}
h1{font-size:16px;margin:0 0 4px;display:flex;align-items:center;gap:10px}h1 i{width:22px;height:22px;border-radius:6px;background:#4c8dff;display:inline-block}
input{background:#1c1f25;border:1px solid #2a2e36;border-radius:8px;padding:10px 12px;color:#f2f3f5;font:inherit}input:focus{border-color:#4c8dff;outline:none}
button{border:0;border-radius:9px;background:#4c8dff;color:#fff;font:inherit;font-weight:800;padding:11px;cursor:pointer}
p{margin:0;color:#9aa0aa;font-size:12.5px}.bad{color:#f06565}
</style></head><body><form method="post" action="/login"><h1><i></i>Creator Pipeline</h1>
<p>Team password</p><input type="password" name="password" autofocus autocomplete="current-password">
<button type="submit">Open the app</button>{msg}</form></body></html>"""


def _token():
    import hashlib
    import hmac

    return hmac.new(SESSION_SECRET.encode(), APP_PASSWORD.encode(), hashlib.sha256).hexdigest()


def _authed(request: Request):
    if request.cookies.get(COOKIE) == _token():
        return True
    header = request.headers.get("authorization", "")
    if header.startswith("Basic "):
        try:
            user, _, pw = base64.b64decode(header[6:]).decode().partition(":")
            return user == "team" and secrets.compare_digest(pw, APP_PASSWORD)
        except Exception:
            return False
    return False


@app.middleware("http")
async def auth(request: Request, call_next):
    """APP_PASSWORD set: a login page sets a session cookie (browsers), HTTP Basic also works (scripts)."""
    if APP_PASSWORD and request.url.path not in ("/login",) and not _authed(request):
        if request.url.path.startswith(("/api/", "/files/")):
            return JSONResponse({"error": "Login required"}, status_code=401)
        return Response(LOGIN_PAGE.replace("{msg}", ""), media_type="text/html", status_code=401)
    return await call_next(request)


@app.post("/login")
async def login(request: Request):
    form = await request.form()
    pw = str(form.get("password", ""))
    if APP_PASSWORD and secrets.compare_digest(pw, APP_PASSWORD):
        resp = Response(status_code=303, headers={"Location": "/"})
        resp.set_cookie(COOKIE, _token(), httponly=True, samesite="lax", secure=request.url.scheme == "https", max_age=14 * 24 * 3600)
        return resp
    return Response(LOGIN_PAGE.replace("{msg}", '<p class="bad">Wrong password.</p>'), media_type="text/html", status_code=401)


@app.get("/login")
def login_page():
    return Response(LOGIN_PAGE.replace("{msg}", ""), media_type="text/html")


@app.on_event("startup")
def startup():
    UPLOADS.mkdir(exist_ok=True)
    if not RUNNER.is_alive():
        RUNNER.start()
    resumed = J.resume_pending(RUNNER.submit)
    if resumed:
        print(f"resumed {resumed} unfinished job(s) from disk", flush=True)
    if not APP_PASSWORD:
        print("WARNING: APP_PASSWORD is not set; anyone with the link can queue jobs.", flush=True)


# ---------- helpers ----------

def presets():
    return J.load_presets()


def touch_keepalive():
    try:
        J.LOGS.mkdir(exist_ok=True)
        (J.LOGS / "keepalive").touch()
    except OSError:
        pass


_GPU = {"at": 0.0, "value": (None, None)}


def gpu():
    """ComfyUI's GPU name and VRAM, re-read at most once a minute."""
    if time.time() - _GPU["at"] > 60:
        _GPU["value"], _GPU["at"] = J.gpu_info(J.SERVER), time.time()
    return _GPU["value"]


def vram_check(p, engine, tier, finishing):
    """{'needed', 'gpu', 'ok'}; ok is None when the GPU is unknown (ComfyUI down or no device)."""
    name, have = gpu()
    needed = J.vram_needed_gib(p, engine, tier, finishing)
    ok = None if have is None or needed == 0 else have >= needed
    return {"vram_needed_gib": needed, "gpu_name": name, "gpu_vram_gib": have, "vram_ok": ok}


def status_info():
    name, vram = gpu()
    out = {"busy": False, "text": "Idle", "current": RUNNER.current, "pending": RUNNER.pending.qsize(), "stops_in": None, "runner_error": RUNNER.last_error,
           "gpu_name": name, "gpu_vram_gib": vram}
    try:
        import json

        wd = json.loads((J.LOGS / "watchdog.status").read_text())
        age = (datetime.now() - datetime.fromisoformat(wd["checked"])).total_seconds()
        if age < 180:
            out["stops_in"] = wd["stops_in_minutes"]
    except Exception:
        pass
    if RUNNER.current:
        out.update(busy=True, text=f"Rendering {RUNNER.current}")
    elif out["pending"]:
        out.update(busy=True, text=f"{out['pending']} job(s) waiting")
    elif out["stops_in"] is not None:
        out["text"] = f"Pod ready, stops in {out['stops_in']:.0f} min if idle"
    return out


def resolve_input(ref):
    """An upload id from /api/uploads, or the name of a file already in ComfyUI/input (reuse of an earlier job)."""
    if not ref or "/" in ref or "\\" in ref or ref.startswith("."):
        return None
    for base in (UPLOADS, J.COMFY_INPUT):
        p = base / ref
        if p.is_file():
            return p
    return None


def out_url(path):
    """URL for a file under ComfyUI/output (clips, sheets, posters)."""
    p = Path(path)
    try:
        rel = p.resolve().relative_to(J.COMFY_OUTPUT.resolve())
    except ValueError:
        try:
            rel = p.relative_to(J.COMFY_OUTPUT)
        except ValueError:
            return None
    return "/files/output/" + rel.as_posix()


def poster_url(job):
    for r in job.get("runs", []):
        if r.get("poster") and Path(r["poster"]).exists():
            return out_url(r["poster"])
    return None


def progress(job):
    """(stage text, fraction 0..1) for the cards. Running jobs estimate from elapsed time against the estimate."""
    runs = job.get("runs", [])
    n = max(1, len(runs))
    done = sum(1 for r in runs if r["status"] == "done")
    failed = sum(1 for r in runs if r["status"] == "error")
    status = job.get("status")
    if status == "queued":
        return "Queued", 0.0
    if status == "running":
        per_run = max(60.0, job.get("cost", {}).get("estimate_minutes", 5) * 60 / n)
        current = next((r for r in runs if r["status"] == "running"), None)
        elapsed = time.time() - (current.get("queued_at") or time.time()) if current else 0
        part = min(0.95, elapsed / per_run)
        k = done + failed + 1
        left = max(0, round((per_run * (n - done - failed) - elapsed) / 60))
        return f"Seed {min(k, n)} of {n} · {round(elapsed / 60)} min in, about {left} left", min(0.98, (done + failed + part) / n)
    if status == "done":
        return "Done", 1.0
    if status == "partial":
        return f"{done} of {n} seeds done", 1.0
    return "Failed", 1.0


def output_seconds(job):
    """Length of the rendered clip in seconds: passes x 81 frames (minus overlaps) at the driving video's fps, capped by the video."""
    opts = job.get("options", {})
    frames, fps = opts.get("frames"), float(opts.get("fps") or 24)
    if not frames:
        return None
    engine, chunks = job.get("engine", "wan"), opts.get("chunks", 1)
    if engine == "seedance":
        return float(J.SD.clamp_duration(frames / fps))
    need = J.S.frames_for_segments(chunks) if engine == "scail" else J.B.frames_for_chunks(chunks)
    return round(min(need, frames) / fps, 1)


def elapsed_minutes(job):
    """Minutes since the current seed was queued, for running jobs; None otherwise."""
    if job.get("status") != "running":
        return None
    current = next((r for r in job.get("runs", []) if r.get("status") == "running"), None)
    if not current or not current.get("queued_at"):
        return None
    return round((time.time() - current["queued_at"]) / 60)


def summary(job):
    opts = job.get("options", {})
    cost = job.get("cost", {})
    stage, frac = progress(job)
    return {
        "id": job["id"], "created": job.get("created"), "started": job.get("started"), "finished": job.get("finished"),
        "status": job.get("status"), "engine": job.get("engine", "wan"), "engine_label": J.ENGINE_LABELS.get(job.get("engine", "wan"), "Wan Animate 2"),
        "character": job.get("character"), "scene": job.get("scene"), "tier": job.get("tier"),
        "chunks": opts.get("chunks", 1), "finishing": bool(opts.get("finishing")), "seconds_out": output_seconds(job), "elapsed_min": elapsed_minutes(job),
        "seeds": len(job.get("runs", [])), "seeds_done": sum(1 for r in job.get("runs", []) if r["status"] == "done"),
        "estimate_usd": cost.get("estimate_usd"), "actual_usd": cost.get("actual_usd"), "estimate_minutes": cost.get("estimate_minutes"),
        "poster": poster_url(job), "stage": stage, "fraction": frac, "note": job.get("note", ""), "error": job.get("error"),
    }


def details(job):
    d = summary(job)
    d.update(prompts=job.get("prompts", {}), settings=job.get("settings", {}), settings_line=J.render_settings(job),
             workflow_version=job.get("workflow_version"), inputs=job.get("inputs", {}), tier_settings=job.get("tier_settings", {}), gpu=job.get("gpu"),
             options=job.get("options", {}), resumed=job.get("resumed", 0))
    runs = []
    for r in job.get("runs", []):
        outputs = []
        for rel in r.get("outputs", []):
            p = J.output_path(rel)
            if p.exists():
                kind = "final" if "_final" in p.name else ("compare" if "compare" in p.name else "result")
                outputs.append({"name": p.name, "url": out_url(p), "kind": kind, "bytes": p.stat().st_size})
        sheets = [{"url": out_url(s), "kind": "faces" if "faces" in s else "frames"} for s in r.get("sheets", []) if Path(s).exists()]
        runs.append({"seed": r["seed"], "status": r["status"], "seconds": r.get("seconds"), "error": r.get("error"), "outputs": outputs, "sheets": sheets,
                     "poster": out_url(r["poster"]) if r.get("poster") and Path(r["poster"]).exists() else None})
    d["runs"] = runs
    d["inputs_urls"] = {k: f"/files/input/{v}" for k, v in job.get("inputs", {}).items() if (J.COMFY_INPUT / v).exists()}
    return d


# ---------- API ----------

@app.get("/api/bootstrap")
def bootstrap():
    p = presets()
    return {
        "engines": [{"key": v, "label": k} for k, v in J.ENGINES.items()],
        "characters": {k: v.get("appearance", "") for k, v in p["characters"].items()},
        "scenes": {k: {"background": v.get("background", ""), "motion": v.get("motion", ""), "identity_hold": float(v.get("identity_hold", 1.0)),
                       "motion_looseness": float(v.get("motion_looseness", 1.0))} for k, v in p["scenes"].items()},
        "tiers": {k: {"note": v.get("note", ""), "minutes_per_clip": v.get("minutes_per_clip"), "width": v.get("width"), "height": v.get("height")} for k, v in p["tiers"].items()},
        "gpu_hourly_usd": J.GPU_HOURLY_USD, "max_seeds": J.MAX_SEEDS, "max_chunks": J.MAX_CHUNKS, "seedance_modes": J.SEEDANCE_MODES,
        "describe_available": D.credentials_available(), "seedance_available": bool(J.COMFY_API_KEY),
        "auth": bool(APP_PASSWORD), "status": status_info(),
    }


@app.get("/api/status")
def status():
    return status_info()


class KeysIn(BaseModel):
    comfy_api_key: str | None = None
    anthropic_api_key: str | None = None


@app.post("/api/keys")
def set_keys(body: KeysIn):
    """Keys pasted into the app. Kept in this process's memory only (never written to disk); gone when the app restarts.
    Lets a teammate connect the Comfy or Anthropic account without a terminal."""
    if body.comfy_api_key is not None:
        J.COMFY_API_KEY = body.comfy_api_key.strip()
        os.environ["COMFY_API_KEY"] = J.COMFY_API_KEY
    if body.anthropic_api_key is not None:
        os.environ["ANTHROPIC_API_KEY"] = body.anthropic_api_key.strip()
    return {"seedance_available": bool(J.COMFY_API_KEY), "describe_available": D.credentials_available()}


@app.post("/api/uploads")
async def upload(file: UploadFile = File(...), kind: str = Form("ref")):
    touch_keepalive()
    ext = Path(file.filename or "").suffix.lower() or (".png" if kind == "ref" else ".mp4")
    if ext not in (".png", ".jpg", ".jpeg", ".webp", ".mp4", ".mov", ".webm", ".mkv", ".m4v"):
        raise HTTPException(400, f"Unsupported file type {ext}")
    name = f"u_{uuid.uuid4().hex[:10]}{ext}"
    UPLOADS.mkdir(exist_ok=True)
    size = 0
    with open(UPLOADS / name, "wb") as out:
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            if size > MAX_UPLOAD:
                out.close()
                (UPLOADS / name).unlink(missing_ok=True)
                raise HTTPException(413, "File larger than 600 MB")
            out.write(chunk)
    return {"id": name, "name": file.filename, "bytes": size}


class PreflightIn(BaseModel):
    image: str | None = None
    video: str | None = None
    engine: str = "wan"


def level(msg):
    if msg.startswith(("Image OK", "Video OK")):
        return "ok"
    if msg.startswith(("Warning", "Note")):
        return "warn"
    if msg.startswith("Reminder"):
        return "info"
    return "bad"


@app.post("/api/preflight")
def preflight(body: PreflightIn):
    touch_keepalive()
    image, video = resolve_input(body.image), resolve_input(body.video)
    if body.image and not image:
        raise HTTPException(400, "Unknown image upload")
    if body.video and not video:
        raise HTTPException(400, "Unknown video upload")
    engine = body.engine if body.engine in J.ENGINE_LABELS else "wan"
    ok, msgs, info = J.preflight(str(image) if image else None, str(video) if video else None, engine)
    lengths = []
    if info.get("frames"):
        fps = float(info.get("fps") or 24)
        for label, passes in J.length_choices(info["frames"], engine, fps):
            if engine == "seedance":
                seconds = J.SD.clamp_duration(info["frames"] / fps)
            else:
                need = J.S.frames_for_segments(passes) if engine == "scail" else J.B.frames_for_chunks(passes)
                seconds = round(min(need, info["frames"]) / fps, 1)
            lengths.append({"label": label, "passes": passes, "seconds": seconds})
    return {"ok": ok, "messages": [{"level": level(m), "text": m} for m in msgs], "info": info, "lengths": lengths}


@app.get("/api/estimate")
def estimate(engine: str = "wan", tier: str = "Standard", chunks: int = 1, finishing: bool = False, seeds: int = 1, seconds: float = 5.0, seedance_mode: str = "edit"):
    p = presets()
    if tier not in p["tiers"]:
        raise HTTPException(400, "Unknown tier")
    seeds = max(1, min(J.MAX_SEEDS, seeds))
    chunks = max(1, min(J.MAX_CHUNKS, chunks))
    if engine == "seedance":
        minutes, credits, usd, measured = J.seedance_credits(p, tier, seconds, seeds, seedance_mode)
        cap = J.seedance_cap(p)
        res = J.SEEDANCE_RESOLUTION.get(tier, "720p")
        note = (f"Seedance 2.5 at {res}, {J.SD.clamp_duration(seconds)} s with sound: {credits:,} Comfy credits (about ${usd:.2f}). "
                f"The driving video's length is billed as input on top of the output. Jobs above {cap:,} credits are refused.")
        if credits > cap:
            note = f"Over the cap: {credits:,} credits (about ${usd:.2f}) is above the limit of {cap:,}. Use a lower tier, fewer seeds or a shorter clip."
        return {"minutes": round(minutes, 1), "usd": usd, "credits": credits, "cap": cap, "over_cap": credits > cap, "currency": "credits", "note": note}
    minutes = J.estimate_minutes(p["tiers"][tier], p["finishing"], chunks, finishing, seeds, engine, tier)
    check = vram_check(p, engine, tier, finishing)
    note = p["tiers"][tier].get("note", "")
    if engine == "scail":
        st = J.SCAIL_TIERS.get(tier, J.SCAIL_TIERS["Standard"])
        note = (f"SCAIL-2 {tier}: {st['size'][0]}×{st['size'][1]} (or landscape), {st['steps']} steps, speed LoRA {st['strength']}. "
                + ("About 3.5x the Standard time, more texture in skin and cloth. " if tier == "High" else "") + "Identity hold does not apply.")
    if check["vram_ok"] is False:
        note += (f" This GPU ({check['gpu_name']}, {check['gpu_vram_gib']:.0f} GiB) is below the {check['vram_needed_gib']} GiB this tier needs to run "
                 "without offloading: pick a lower tier, or allow offloading (much slower, may fail).")
    return {"minutes": round(minutes, 1), "usd": round(minutes / 60 * J.GPU_HOURLY_USD, 2), "currency": "gpu", "note": note, **check}


class JobIn(BaseModel):
    image: str
    video: str
    engine: str = "wan"
    character: str
    appearance: str
    scene: str
    background: str
    motion: str
    identity_hold: float = 1.0
    motion_looseness: float = 1.0
    tier: str = "Standard"
    seeds: int = 2
    chunks: int = 1
    finishing: bool = False
    note: str = ""
    seedance_mode: str = "edit"
    extra_views: list[str] = []
    allow_offload: bool = False
    swap_below: float = 1.0


@app.post("/api/jobs")
def create_job(body: JobIn):
    touch_keepalive()
    image, video = resolve_input(body.image), resolve_input(body.video)
    if not image or not video:
        raise HTTPException(400, "Add a reference image and a driving video first.")
    engine = body.engine if body.engine in J.ENGINE_LABELS else "wan"
    if engine == "seedance" and not J.COMFY_API_KEY:
        raise HTTPException(400, "Seedance needs a Comfy API key: connect your Comfy account first.")
    ok, msgs, info = J.preflight(str(image), str(video), engine)
    if not ok:
        raise HTTPException(400, " ".join(m for m in msgs if level(m) == "bad"))
    if engine == "seedance":
        p0 = presets()
        seconds = (info.get("frames") or 120) / float(info.get("fps") or 24)
        _, credits, usd, _ = J.seedance_credits(p0, body.tier, seconds, max(1, min(J.MAX_SEEDS, body.seeds)), body.seedance_mode)
        if credits > J.seedance_cap(p0):
            raise HTTPException(400, f"Refused: this would cost about {credits:,} Comfy credits (about ${usd:.2f}), above the cap of {J.seedance_cap(p0):,}. "
                                     "Use a lower tier, fewer seeds or a shorter driving video, or raise max_credits in presets.yaml.")
    if not body.appearance.strip():
        raise HTTPException(400, "Describe the character's looks, or pick a character preset.")
    p = presets()
    if body.tier not in p["tiers"]:
        raise HTTPException(400, "Unknown tier")
    check = vram_check(p, engine, body.tier, body.finishing)
    if check["vram_ok"] is False and not body.allow_offload:
        raise HTTPException(400, f"Refused: {check['gpu_name']} has {check['gpu_vram_gib']:.0f} GiB and {body.tier} on this engine needs about "
                                 f"{check['vram_needed_gib']} GiB to run without offloading. Pick a lower tier or allow offloading.")
    allowed = [c[1] for c in J.length_choices(info.get("frames", 0), engine, info.get("fps", 24.0))]
    chunks = min(max(1, body.chunks), max(allowed) if allowed else 1)
    scene = {"background": body.background, "motion": body.motion, "identity_hold": body.identity_hold, "motion_looseness": body.motion_looseness}
    job = J.new_job(str(image), str(video), body.character, body.appearance, body.scene, scene, body.tier, p["tiers"][body.tier], body.seeds, body.note,
                    chunks=chunks, finishing=body.finishing, info=info, engine=engine, seedance_mode=body.seedance_mode,
                    extra_views=[str(p) for p in (resolve_input(v) for v in body.extra_views[:2]) if p],
                    allow_offload=body.allow_offload, gpu=gpu(), swap_below=body.swap_below)
    RUNNER.submit(job["id"])
    return summary(job)


@app.get("/api/jobs")
def list_jobs(limit: int = 200):
    return [summary(j) for j in J.list_jobs(limit=limit)]


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    if "/" in job_id or "\\" in job_id or job_id.startswith("."):
        raise HTTPException(404, "No such job")
    try:
        job = J.load_job(job_id)
    except FileNotFoundError:
        raise HTTPException(404, "No such job")
    return details(job)


class DescribeIn(BaseModel):
    ref: str
    source: str = "image"  # image | video


@app.post("/api/describe")
def describe(body: DescribeIn):
    touch_keepalive()
    path = resolve_input(body.ref)
    if not path:
        raise HTTPException(400, "Add the file first.")
    if not D.credentials_available():
        raise HTTPException(400, "Describe needs an Anthropic API key: set ANTHROPIC_API_KEY before starting the app.")
    try:
        return {"text": D.describe_background(str(path), frame=(body.source == "video"))}
    except D.DescribeError as e:
        raise HTTPException(502, str(e))


@app.post("/api/batch")
async def batch(file: UploadFile = File(...), tier: str = Form("Standard"), finishing: bool = Form(False), engine: str = Form("wan")):
    """CSV columns: image,video,character,scene,seeds,passes,note[,engine]; image/video are names in ComfyUI/input."""
    touch_keepalive()
    p = presets()
    if tier not in p["tiers"]:
        raise HTTPException(400, "Unknown tier")
    text = (await file.read()).decode("utf-8-sig", errors="replace")
    made, problems = [], []
    for i, row in enumerate(csv.DictReader(io.StringIO(text)), 1):
        try:
            eng = (row.get("engine") or "").strip().lower() or engine
            if eng not in J.ENGINE_LABELS:
                raise ValueError(f"engine must be one of {', '.join(J.ENGINE_LABELS)}")
            image, video = resolve_input((row.get("image") or "").strip()), resolve_input((row.get("video") or "").strip())
            if not image or not video:
                raise ValueError("image or video not found in ComfyUI/input")
            ok, msgs, info = J.preflight(str(image), str(video), eng)
            if not ok:
                raise ValueError(" ".join(m for m in msgs if level(m) == "bad"))
            ch = p["characters"][row["character"].strip()]
            sc = p["scenes"][row["scene"].strip()]
            allowed = [c[1] for c in J.length_choices(info["frames"], eng, info["fps"])]
            chunks = min(int(row.get("passes") or 1), max(allowed))
            job = J.new_job(str(image), str(video), row["character"].strip(), ch["appearance"], row["scene"].strip(), sc, tier, p["tiers"][tier],
                            int(row.get("seeds") or 1), row.get("note", ""), chunks=chunks, finishing=finishing, info=info, engine=eng)
            RUNNER.submit(job["id"])
            made.append(job["id"])
        except Exception as e:
            problems.append(f"row {i}: {e}")
    return {"made": made, "problems": problems}


@app.get("/api/costs")
def costs():
    return {"headers": J.COST_HEADERS, "rows": J.cost_rows(J.cost_summary()), "months": J.cost_summary(), "gpu_hourly_usd": J.GPU_HOURLY_USD,
            "idle_minutes": J.IDLE_ALLOWANCE_MIN}


# ---------- files ----------

def _serve(base, rel):
    if ".." in rel.split("/") or rel.startswith("/"):
        raise HTTPException(404)
    p = (base / rel)
    try:
        p.resolve().relative_to(base.resolve())
    except ValueError:
        raise HTTPException(404)
    if not p.is_file():
        raise HTTPException(404)
    return FileResponse(p)


@app.get("/files/output/{rel:path}")
def file_output(rel: str):
    return _serve(J.COMFY_OUTPUT, rel)


@app.get("/files/input/{rel:path}")
def file_input(rel: str):
    return _serve(J.COMFY_INPUT, rel)


@app.get("/files/upload/{rel:path}")
def file_upload(rel: str):
    return _serve(UPLOADS, rel)


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


class NoCacheStatic(StaticFiles):
    """The front end is three small files; always revalidate so a git pull on the pod shows up on the next reload."""

    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


app.mount("/static", NoCacheStatic(directory=str(STATIC)), name="static")


@app.exception_handler(HTTPException)
async def http_error(request, exc):
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code, headers=getattr(exc, "headers", None))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("APP_PORT", "7860")), log_level="warning")
