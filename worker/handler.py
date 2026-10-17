"""RunPod Serverless handler: one request = one ComfyUI API-format workflow rendered to the network volume.

Request input:
  {"workflow": {...API-format graph...}, "extra_data": {...} | null, "job_id": "...", "seed": 123}
Result output (the ComfyUI history entry, so the app's collect_outputs / error_message work unchanged):
  {"status": {...}, "outputs": {...}, "job_id": ..., "seed": ..., "render_seconds": ...}

Inputs are read from and outputs written to CREATOR_DATA on the volume; nothing large passes through the request.
"""
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

import runpod

sys.path.insert(0, str(Path(__file__).resolve().parent))
from comfy_client import ComfyError, error_message, poll, queue_prompt  # noqa: E402

SERVER = f"http://127.0.0.1:{os.environ.get('COMFY_PORT', '8188')}"
DATA = Path(os.environ.get("CREATOR_DATA", "/runpod-volume/creator-data"))
BOOT_TIMEOUT = int(os.environ.get("COMFY_BOOT_TIMEOUT", "780"))   # under RunPod's 7 minute default init cutoff
PROGRESS_EVERY = 30


def comfy_log_tail(n=25):
    try:
        lines = Path("/tmp/comfy.log").read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-n:])
    except OSError:
        return "(no ComfyUI log)"


def wait_for_comfy(timeout=BOOT_TIMEOUT):
    """ComfyUI imports every node pack on first start; poll as long as it is alive rather than a fixed 25 s.
    Gives up early when the ComfyUI process has died, and reports its last log lines either way."""
    if os.environ.get("WORKER_FATAL"):
        raise RuntimeError(os.environ["WORKER_FATAL"])
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(SERVER + "/system_stats", timeout=5) as r:
                return json.loads(r.read())
        except Exception as e:
            last = e
            if time.time() - t0 > 30 and "main.py" not in os.popen("ps -eo args").read():
                raise RuntimeError(f"ComfyUI process exited during start-up. Last log lines:\n{comfy_log_tail()}")
            time.sleep(2)
    raise RuntimeError(f"ComfyUI did not answer within {timeout}s: {last}. Last log lines:\n{comfy_log_tail()}")


def handler(job):
    inp = job.get("input") or {}
    wf = inp.get("workflow")
    if not isinstance(wf, dict) or not wf:
        return {"error": "input.workflow (API-format graph) is required"}
    job_id, seed = inp.get("job_id", "unknown"), inp.get("seed")
    stats = wait_for_comfy()
    dev = (stats.get("devices") or [{}])[0]
    t0 = time.time()
    try:
        prompt_id = queue_prompt(SERVER, wf, f"worker-{job['id']}", inp.get("extra_data"))
    except ComfyError as e:
        return {"error": f"ComfyUI rejected the workflow: {e}"}
    last_progress = time.time()
    while True:
        time.sleep(2)
        try:
            entry = poll(SERVER, prompt_id)
        except ComfyError:
            entry = None
        if entry is not None:
            break
        if time.time() - last_progress > PROGRESS_EVERY:
            runpod.serverless.progress_update(job, f"rendering {round((time.time() - t0) / 60)} min")
            last_progress = time.time()
    seconds = round(time.time() - t0, 1)
    result = {"status": entry.get("status", {}), "outputs": entry.get("outputs", {}), "job_id": job_id, "seed": seed,
              "render_seconds": seconds, "gpu": {"name": dev.get("name"), "vram_total": dev.get("vram_total")}}
    if entry.get("status", {}).get("status_str") != "success":
        result["error"] = error_message(entry)
    # Durable record on the volume: RunPod keeps /status results for 30 minutes only.
    try:
        d = DATA / "output" / "motion_swap" / str(job_id)
        d.mkdir(parents=True, exist_ok=True)
        (d / f"s{seed}_worker.json").write_text(json.dumps(result), encoding="utf-8")
    except OSError:
        pass
    return result


if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})
