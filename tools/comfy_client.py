"""Small client for a running ComfyUI, shared by the batch runner and the team app."""
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_SERVER = "http://127.0.0.1:8188"


class ComfyError(RuntimeError):
    pass


def request(server, path, payload=None, timeout=30):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(server.rstrip("/") + path, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise ComfyError(_rejection_message(e.code, body)) from None
    except urllib.error.URLError as e:
        raise ComfyError(f"Can't reach ComfyUI at {server}: {e.reason}") from None


def _rejection_message(code, body):
    """Turn ComfyUI's validation JSON into one readable sentence, e.g. which node and input failed."""
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return f"ComfyUI rejected the request ({code}): {body[:300]}"
    parts = []
    for node_id, err in (data.get("node_errors") or {}).items():
        title = err.get("class_type", node_id)
        for e in err.get("errors", []):
            detail = e.get("details") or ""
            parts.append(f"{title}: {e.get('message', '')}{' (' + detail[:120] + ')' if detail else ''}")
    if not parts:
        parts.append((data.get("error") or {}).get("message") or body[:300])
    return "ComfyUI rejected the job. " + " | ".join(parts[:3])


def _find_node(workflow, node_ref):
    """Node id for an id or a title; None if unknown. Raises if a title is ambiguous."""
    if node_ref in workflow:
        return node_ref
    matches = [nid for nid, n in workflow.items() if n.get("_meta", {}).get("title") == node_ref]
    if len(matches) > 1:
        raise ComfyError(f'{len(matches)} nodes are titled "{node_ref}" (ids {matches}). Use the id instead.')
    return matches[0] if matches else None


def resolve(workflow, key):
    """Turn "Node title.input" or "12.input" into (node_id, input_name).

    Input names may themselves contain dots (dynamic-combo sub-inputs such as "resize_type.width"),
    so every split point is tried and the first one that names a real node wins.
    """
    if "." not in key:
        raise ComfyError(f'"{key}" must look like "Node title.input_name"')
    parts = key.split(".")
    for i in range(1, len(parts)):
        node_ref, input_name = ".".join(parts[:i]), ".".join(parts[i:])
        node_id = _find_node(workflow, node_ref)
        if node_id is None:
            continue
        if input_name not in workflow[node_id]["inputs"]:
            known = ", ".join(workflow[node_id]["inputs"])
            raise ComfyError(f'Node "{node_ref}" has no input "{input_name}". It has: {known}')
        return node_id, input_name
    titles = sorted({n.get("_meta", {}).get("title", "?") for n in workflow.values()})
    raise ComfyError(f'No node matches "{key}". Titles in this workflow: {", ".join(titles)}')


def set_input(workflow, key, value):
    node_id, input_name = resolve(workflow, key)
    workflow[node_id]["inputs"][input_name] = value


def queue_prompt(server, workflow, client_id, extra_data=None):
    """Queue a workflow. extra_data carries per-request values such as the Comfy API key for paid API nodes
    ({"api_key_comfy_org": "..."}), which ComfyUI never stores in the history."""
    payload = {"prompt": workflow, "client_id": client_id}
    if extra_data:
        payload["extra_data"] = extra_data
    return request(server, "/prompt", payload)["prompt_id"]


def poll(server, prompt_id):
    """Return the history entry once the run has finished (success or error), else None."""
    entry = request(server, f"/history/{prompt_id}").get(prompt_id)
    if not entry:
        return None
    status = entry.get("status", {})
    if status.get("completed") or status.get("status_str") == "error":
        return entry
    return None


def error_message(entry):
    messages = [m for m in entry.get("status", {}).get("messages", []) if m[0] == "execution_error"]
    if not messages:
        return "unknown error"
    d = messages[-1][1]
    return f"{d.get('node_type', '?')}: {d.get('exception_message', '')}".strip()


def collect_outputs(entry):
    """Relative output paths (under ComfyUI/output) saved by the run: images and videos."""
    files = []
    for node_out in entry.get("outputs", {}).values():
        for kind in ("images", "gifs", "videos"):
            for f in node_out.get(kind, []):
                if f.get("type") == "output":
                    sub = str(f.get("subfolder", "")).replace("\\", "/").strip("/")
                    files.append(f"{sub}/{f['filename']}" if sub else f["filename"])  # always forward slashes: these are ComfyUI paths, also used as S3 keys
    return files


def queue_is_idle(server):
    q = request(server, "/queue")
    return not q.get("queue_running") and not q.get("queue_pending")


def queue_contains(server, prompt_id):
    """True while ComfyUI still has this prompt running or waiting (queue items are [number, prompt_id, ...])."""
    q = request(server, "/queue")
    for item in list(q.get("queue_running", [])) + list(q.get("queue_pending", [])):
        if len(item) > 1 and item[1] == prompt_id:
            return True
    return False


def start_hint():
    return "scripts\\start.ps1 -Cpu" if sys.platform == "win32" else "bash scripts/start.sh"
