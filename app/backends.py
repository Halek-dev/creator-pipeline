"""Where renders run: a ComfyUI server next to the app (pod, laptop) or a RunPod Serverless endpoint.

Both backends speak the same four verbs the Runner uses, with "handle" being a ComfyUI prompt_id or a RunPod job id:
  submit(workflow, client_id, extra_data, timeout_s) -> handle
  poll(handle) -> ComfyUI-style history entry when finished, else None
  lost(handle) -> True when the backend no longer knows the handle at all
  name
"""
import json
import time
import os
import urllib.error
import urllib.request

from comfy_client import ComfyError, poll, queue_contains, queue_prompt

RUNPOD_API = "https://api.runpod.ai/v2"


class PodBackend:
    """ComfyUI reachable over HTTP (the pod's own ComfyUI, or the laptop's CPU one for Seedance)."""

    name = "pod"

    def __init__(self, server):
        self.server = server

    def submit(self, workflow, client_id, extra_data=None, timeout_s=None):
        return queue_prompt(self.server, workflow, client_id, extra_data)

    def poll(self, handle):
        return poll(self.server, handle)

    def lost(self, handle):
        return not queue_contains(self.server, handle)


class EndpointBackend:
    """RunPod Serverless endpoint running worker/handler.py. The handler returns the ComfyUI history entry, so the
    Runner's collect_outputs / error_message see exactly what they see with a pod."""

    name = "endpoint"
    MIN_TIMEOUT_S = 1200  # the endpoint default of 600 s would kill most of our renders

    def __init__(self, endpoint_id, api_key, api=RUNPOD_API):
        if not endpoint_id or not api_key:
            raise ValueError("RUNPOD_ENDPOINT_ID and RUNPOD_API_KEY are needed for the endpoint backend")
        self.base = f"{api.rstrip('/')}/{endpoint_id}"
        self.api_key = api_key
        self.progress = {}  # handle -> {"state": IN_QUEUE|IN_PROGRESS, "text": worker progress, "started": first seen running}

    def _call(self, method, path, body=None, timeout=60):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:300]
            if e.code == 404:
                raise LookupError(detail)
            raise ComfyError(f"RunPod endpoint HTTP {e.code}: {detail}")
        except urllib.error.URLError as e:
            raise ComfyError(f"RunPod endpoint unreachable: {e.reason}")

    def submit(self, workflow, client_id, extra_data=None, timeout_s=None):
        job_id = client_id.split(":", 1)[0]
        seed = None
        for node in workflow.values():
            if node.get("class_type") == "SamplerCustom":
                seed = node["inputs"].get("noise_seed")
                break
        body = {"input": {"workflow": workflow, "extra_data": extra_data, "job_id": job_id, "seed": seed},
                "policy": {"executionTimeout": int(max(self.MIN_TIMEOUT_S, timeout_s or 0) * 1000)}}
        r = self._call("POST", "/run", body)
        if "id" not in r:
            raise ComfyError(f"RunPod endpoint did not accept the job: {r}")
        return r["id"]

    def status(self, handle):
        try:
            return self._call("GET", f"/status/{handle}")
        except LookupError:
            return None

    def poll(self, handle):
        r = self.status(handle)
        if r is None:
            return None
        st = r.get("status")
        if st in ("IN_QUEUE", "IN_PROGRESS"):
            prev = self.progress.get(handle) or {}
            started = prev.get("started") or (time.time() if st == "IN_PROGRESS" else None)
            self.progress[handle] = {"state": st, "text": r.get("output") if isinstance(r.get("output"), str) else None, "started": started}
            return None
        self.progress.pop(handle, None)
        out = r.get("output") if isinstance(r.get("output"), dict) else {}
        if st == "COMPLETED" and out.get("status", {}).get("status_str") == "success":
            return {**out, "_endpoint": {"delay_ms": r.get("delayTime"), "execution_ms": r.get("executionTime")}}
        err = out.get("error") or r.get("error") or f"endpoint job {st}"
        return {"status": {"status_str": "error", "messages": [["execution_error", {"node_type": "endpoint", "exception_message": str(err)}]]},
                "outputs": {}, "_endpoint": {"delay_ms": r.get("delayTime"), "execution_ms": r.get("executionTime")}}

    def lost(self, handle):
        return self.status(handle) is None

    def health(self):
        try:
            return self._call("GET", "/health", timeout=15)
        except Exception:
            return None

    def cancel(self, handle):
        try:
            return self._call("POST", f"/cancel/{handle}")
        except Exception:
            return None


def make_backend(kind=None, server=None):
    kind = (kind or os.environ.get("RENDER_BACKEND", "pod")).lower()
    if kind == "endpoint":
        eid, key = os.environ.get("RUNPOD_ENDPOINT_ID", ""), os.environ.get("RUNPOD_API_KEY", "")
        if eid and key:
            return EndpointBackend(eid, key)
        print("WARNING: RENDER_BACKEND=endpoint but RUNPOD_ENDPOINT_ID or RUNPOD_API_KEY is not set; using the pod backend until they are "
              "(set them in the host's variables, or connect serverless from the Costs page).", flush=True)
    return PodBackend(server or os.environ.get("COMFY_SERVER", "http://127.0.0.1:8188"))
