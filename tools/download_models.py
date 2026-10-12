"""Download a model profile from models.json into ComfyUI/models.

  python tools/download_models.py --list
  python tools/download_models.py cpu-test
  python tools/download_models.py z-image-turbo --comfy /workspace/ComfyUI

Files that already exist with the right size are skipped. Partial downloads resume.
Set HF_TOKEN for gated Hugging Face repos.
"""
import argparse
import json
import os
import shutil
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def remote_size(url: str, headers: dict) -> int | None:
    req = urllib.request.Request(url, method="HEAD", headers=headers)
    with urllib.request.urlopen(req, timeout=60) as resp:
        size = resp.headers.get("Content-Length")
        return int(size) if size else None


def download(url: str, dest: Path, headers: dict) -> None:
    total = remote_size(url, headers)
    if dest.exists() and total and dest.stat().st_size == total:
        print(f"  have  {dest.name}")
        return
    if total and shutil.disk_usage(dest.parent).free < total * 1.05:
        sys.exit(f"Not enough disk space for {dest.name} ({total / 1e9:.1f} GB).")

    part = dest.with_suffix(dest.suffix + ".part")
    done = part.stat().st_size if part.exists() else 0
    req_headers = dict(headers)
    if done:
        req_headers["Range"] = f"bytes={done}-"
    req = urllib.request.Request(url, headers=req_headers)
    with urllib.request.urlopen(req, timeout=60) as resp, open(part, "ab" if done else "wb") as out:
        if done and resp.status != 206:
            out.truncate(0)
            done = 0
        last = 0.0
        while chunk := resp.read(1 << 20):
            out.write(chunk)
            done += len(chunk)
            if time.time() - last > 2:
                pct = f"{100 * done / total:5.1f}%" if total else ""
                print(f"\r  get   {dest.name} {done / 1e9:6.2f} GB {pct}", end="", flush=True)
                last = time.time()
    part.replace(dest)
    print(f"\r  saved {dest.name} ({done / 1e9:.2f} GB)" + " " * 20)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("profile", nargs="?")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--comfy", default=str(ROOT / "ComfyUI"), help="ComfyUI folder")
    args = parser.parse_args()

    profiles = json.loads((ROOT / "models.json").read_text(encoding="utf-8"))
    if args.list or not args.profile:
        for name, p in profiles.items():
            print(f"{name:<16} {p['description']}")
        return
    if args.profile not in profiles:
        sys.exit(f"Unknown profile '{args.profile}'. Options: {', '.join(profiles)}")

    headers = {"User-Agent": "creator-pipeline"}
    if os.environ.get("HF_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['HF_TOKEN']}"

    models_dir = Path(args.comfy) / "models"
    print(f"Profile {args.profile} -> {models_dir}")
    for f in profiles[args.profile]["files"]:
        dest_dir = models_dir / f["dir"]
        dest_dir.mkdir(parents=True, exist_ok=True)
        download(f["url"], dest_dir / f["url"].rsplit("/", 1)[-1], headers)


if __name__ == "__main__":
    main()
