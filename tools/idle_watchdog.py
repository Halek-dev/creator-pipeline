"""Stop or terminate the RunPod pod after ComfyUI has been idle for a while.

  python tools/idle_watchdog.py --minutes 20 --action stop

Idle means: nothing running, nothing queued, and no job finished since the last check.
Touch logs/keepalive to reset the timer from outside. Status is written to logs/watchdog.status
so the app can show "stops in N min".

Stopping uses runpodctl if present, else the RunPod API with RUNPOD_API_KEY. Outside RunPod
(no RUNPOD_POD_ID) it only logs what it would have done.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOGS = ROOT / "logs"


def get(server, path):
    with urllib.request.urlopen(server + path, timeout=15) as r:
        return json.loads(r.read())


def comfy_busy(server):
    q = get(server, "/queue")
    return bool(q.get("queue_running")) or bool(q.get("queue_pending"))


def history_count(server):
    return len(get(server, "/history?max_items=1000"))


def log(msg):
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}", flush=True)


def stop_pod(action):
    """Try to stop/terminate the pod. Returns True when a stop was issued and accepted, False to retry later."""
    pod_id = os.environ.get("RUNPOD_POD_ID")
    if not pod_id:
        log(f"not on RunPod (no RUNPOD_POD_ID); would {action} the pod now")
        return True
    if shutil.which("runpodctl"):
        verb = "remove" if action == "terminate" else "stop"
        log(f"runpodctl {verb} pod {pod_id}")
        result = subprocess.run(["runpodctl", verb, "pod", pod_id], capture_output=True, text=True, check=False)
        if result.returncode == 0:
            return True
        log(f"runpodctl failed ({result.returncode}): {(result.stderr or result.stdout).strip()[:200]}")
    key = os.environ.get("RUNPOD_API_KEY")
    if not key:
        log("no working runpodctl and no RUNPOD_API_KEY; cannot stop the pod")
        return False
    mutation = "podTerminate" if action == "terminate" else "podStop"
    body = json.dumps({"query": f'mutation {{ {mutation}(input: {{podId: "{pod_id}"}}) {{ id }} }}'}).encode()
    req = urllib.request.Request(f"https://api.runpod.io/graphql?api_key={key}", data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            text = r.read().decode()
        log(f"api {mutation}: {text[:200]}")
        return '"errors"' not in text
    except Exception as e:
        log(f"api {mutation} failed: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--minutes", type=float, default=20)
    parser.add_argument("--grace", type=float, default=45, help="Idle minutes allowed before the first job (setup time)")
    parser.add_argument("--port", type=int, default=8188)
    parser.add_argument("--action", choices=["stop", "terminate"], default="stop")
    parser.add_argument("--interval", type=float, default=30, help="Seconds between checks")
    args = parser.parse_args()

    server = f"http://127.0.0.1:{args.port}"
    LOGS.mkdir(exist_ok=True)
    keepalive = LOGS / "keepalive"
    status = LOGS / "watchdog.status"
    last_active = time.time()
    last_history = None
    seen_job = False
    log(f"watching ComfyUI on port {args.port}; {args.action} after {args.grace:g} idle minutes before the first job, {args.minutes:g} after")

    while True:
        try:
            busy = comfy_busy(server)
            count = history_count(server)
            if busy or (last_history is not None and count != last_history):
                last_active = time.time()
                seen_job = True
            last_history = count
        except Exception as e:  # ComfyUI restarting or down: don't count that as idle
            log(f"ComfyUI not reachable ({e.__class__.__name__}); timer reset")
            last_active = time.time()
        if keepalive.exists() and keepalive.stat().st_mtime > last_active:
            last_active = keepalive.stat().st_mtime
        idle_min = (time.time() - last_active) / 60
        allowed = args.minutes if seen_job else max(args.minutes, args.grace)
        remaining = max(0.0, allowed - idle_min)
        status.write_text(json.dumps({"idle_minutes": round(idle_min, 1), "stops_in_minutes": round(remaining, 1), "action": args.action, "checked": datetime.now().isoformat(timespec="seconds")}))
        if idle_min >= allowed:
            log(f"idle for {idle_min:.1f} min; {args.action} now")
            if stop_pod(args.action):
                sys.exit(0)
            log("stop failed; retrying in 5 minutes (the pod is still billing)")
            time.sleep(300)
            continue
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
