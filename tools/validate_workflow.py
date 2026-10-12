"""Check an API-format workflow against a running ComfyUI without waiting for a full render.

  python tools/validate_workflow.py workflows/wan_animate2.json          # validation only
  python tools/validate_workflow.py workflows/wan_animate2.json --wait   # also queue it and report the result

Validation catches wrong node names, wrong input names, type mismatches and missing model files
before you spend GPU time. With --wait, the run is queued and its final status (or error) printed.
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path


def post(server, payload):
    req = urllib.request.Request(server + "/prompt", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")
    except urllib.error.URLError:
        sys.exit(f"Can't reach ComfyUI at {server}. Start it first.")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("workflow")
    parser.add_argument("--server", default="http://127.0.0.1:8188")
    parser.add_argument("--wait", action="store_true", help="Queue the run and wait for its result")
    parser.add_argument("--timeout", type=float, default=3600)
    args = parser.parse_args()

    workflow = json.loads(Path(args.workflow).read_text(encoding="utf-8"))
    server = args.server.rstrip("/")
    status, resp = post(server, {"prompt": workflow, "client_id": str(uuid.uuid4())})

    if status != 200:
        print(f"REJECTED ({status}): {resp.get('error', {}).get('message', resp)}")
        for node_id, err in resp.get("node_errors", {}).items():
            title = workflow.get(node_id, {}).get("_meta", {}).get("title", "?")
            print(f"  node {node_id} ({title}, {err.get('class_type')}):")
            for e in err.get("errors", []):
                print(f"    - {e.get('message')}: {e.get('details')}")
        sys.exit(1)

    prompt_id = resp["prompt_id"]
    print(f"ACCEPTED: all nodes, inputs and model names validated. prompt_id {prompt_id}")
    if not args.wait:
        # Pull it back out of the queue so validation alone doesn't start a render.
        urllib.request.urlopen(urllib.request.Request(server + "/queue", data=json.dumps({"delete": [prompt_id]}).encode(), headers={"Content-Type": "application/json"}), timeout=30)
        urllib.request.urlopen(urllib.request.Request(server + "/interrupt", data=b"", method="POST"), timeout=30)
        return

    start = time.time()
    while time.time() - start < args.timeout:
        time.sleep(2)
        with urllib.request.urlopen(f"{server}/history/{prompt_id}", timeout=30) as r:
            entry = json.loads(r.read()).get(prompt_id)
        if not entry:
            continue
        st = entry.get("status", {})
        if st.get("completed") or st.get("status_str") == "error":
            print(f"RESULT: {st.get('status_str')} after {time.time() - start:.0f}s")
            for m in st.get("messages", []):
                if m[0] == "execution_error":
                    d = m[1]
                    print(f"  failed at node {d.get('node_id')} ({d.get('node_type')}): {d.get('exception_message')}")
            for node_out in entry.get("outputs", {}).values():
                for f in node_out.get("images", []):
                    print(f"  output: {f.get('subfolder', '')}/{f['filename']}")
            sys.exit(0 if st.get("status_str") == "success" else 1)
    sys.exit("Timed out waiting for the run.")


if __name__ == "__main__":
    main()
