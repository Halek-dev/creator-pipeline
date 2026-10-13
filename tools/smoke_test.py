"""Check that a running ComfyUI is ready for the team: nodes loaded, models present, a tiny render works.

  python tools/smoke_test.py                       # against http://127.0.0.1:8188
  python tools/smoke_test.py --profiles wan-animate-2 z-image-turbo

Prints one line per check and exits non-zero if anything required failed.
"""
import argparse
import json
import sys
import time
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_NODES = ["Creator_CharacterPrompt", "Creator_PromptLibrary", "Creator_FolderLoader", "Creator_RealityFinish", "WanAnimate2ToVideo"]
LATER_NODES = {"WanSCAILToVideo": "SCAIL-2 (Phase 3B)", "SeedVR2Conditioning": "SeedVR2 upscale (Phase 2)", "FrameInterpolate": "FILM interpolation (Phase 2)"}


def get(server, path):
    with urllib.request.urlopen(server + path, timeout=30) as r:
        return json.loads(r.read())


def post(server, path, payload):
    req = urllib.request.Request(server + path, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


class Report:
    def __init__(self):
        self.failed = False

    def line(self, ok, label, detail="", required=True):
        mark = "OK  " if ok else ("FAIL" if required else "skip")
        if not ok and required:
            self.failed = True
        print(f"  {mark}  {label:<34} {detail}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8188)
    parser.add_argument("--server", default=None)
    parser.add_argument("--profiles", nargs="*", default=["wan-animate-2"], help="Model profiles that must be present")
    parser.add_argument("--optional-profiles", nargs="*", default=["wan-animate-2-full", "finishing"], help="Reported, not required (High tier, Finish)")
    args = parser.parse_args()
    server = args.server or f"http://127.0.0.1:{args.port}"
    rep = Report()
    print(f"Smoke test against {server}")

    try:
        stats = get(server, "/system_stats")
    except Exception as e:
        rep.line(False, "ComfyUI reachable", str(e))
        sys.exit(1)
    dev = stats["devices"][0]
    vram = dev.get("vram_total", 0) / 1e9
    rep.line(True, "ComfyUI reachable", f"v{stats['system'].get('comfyui_version', '?')}, {dev['name'].split(':')[-1].strip()[:40]}, {vram:.0f} GB VRAM")
    if "cpu" in dev["type"]:
        rep.line(True, "GPU", "none (CPU mode): fine for a laptop test, not for video", required=False)

    info = get(server, "/object_info")
    missing = [n for n in REQUIRED_NODES if n not in info]
    rep.line(not missing, "Required nodes", "all present" if not missing else "missing: " + ", ".join(missing))
    for node, what in LATER_NODES.items():
        rep.line(node in info, f"{what}", "present" if node in info else "not in this ComfyUI build", required=False)

    profiles = json.loads((ROOT / "models.json").read_text(encoding="utf-8"))
    models_dir = ROOT / "ComfyUI" / "models"
    def missing_files(name):
        absent = []
        for f in profiles[name]["files"]:
            p = models_dir / f["dir"] / f["url"].rsplit("/", 1)[-1]
            if not p.exists() or p.stat().st_size < 1_000_000:
                absent.append(p.name)
        return absent

    for name in args.profiles:
        if name not in profiles:
            rep.line(False, f"Profile {name}", "unknown profile")
            continue
        absent = missing_files(name)
        rep.line(not absent, f"Models: {name}", "all present" if not absent else "missing: " + ", ".join(absent))
    for name in args.optional_profiles:
        if name not in profiles:
            continue
        absent = missing_files(name)
        rep.line(not absent, f"Models: {name} (optional)", "all present" if not absent else f"not installed: bash scripts/setup.sh {name}", required=False)

    # Tiny render through our own nodes: proves the queue, the node pack and file saving work.
    try:
        from PIL import Image
        smoke_dir = ROOT / "ComfyUI" / "input" / "smoke"
        smoke_dir.mkdir(parents=True, exist_ok=True)
        img = Image.new("RGB", (64, 64))
        img.putdata([(x * 4, y * 4, 128) for y in range(64) for x in range(64)])
        img.save(smoke_dir / "smoke.png")
        wf = json.loads((ROOT / "workflows" / "finish_folder.json").read_text(encoding="utf-8"))
        for node in wf.values():
            if node["class_type"] == "Creator_FolderLoader":
                node["inputs"]["folder"] = "smoke"
            if node["class_type"] == "SaveImage":
                node["inputs"]["filename_prefix"] = "smoke/test"
        pid = post(server, "/prompt", {"prompt": wf, "client_id": str(uuid.uuid4())})["prompt_id"]
        t0 = time.time()
        result = None
        while time.time() - t0 < 120:
            time.sleep(1)
            entry = get(server, f"/history/{pid}").get(pid)
            if entry and (entry["status"].get("completed") or entry["status"].get("status_str") == "error"):
                result = entry
                break
        if result is None:
            rep.line(False, "Tiny render", "timed out after 120 s")
        elif result["status"].get("status_str") == "success":
            rep.line(True, "Tiny render", f"{time.time() - t0:.1f} s, output saved")
        else:
            msgs = [m[1].get("exception_message", "") for m in result["status"].get("messages", []) if m[0] == "execution_error"]
            rep.line(False, "Tiny render", (msgs or ["failed"])[-1][:120])
    except Exception as e:
        rep.line(False, "Tiny render", f"{e.__class__.__name__}: {e}")

    print("RESULT:", "FAILED" if rep.failed else "READY")
    sys.exit(1 if rep.failed else 0)


if __name__ == "__main__":
    main()
