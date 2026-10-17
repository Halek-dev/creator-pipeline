"""Look at the network volume from anywhere, pod or no pod (RunPod S3-compatible API).

  export RUNPOD_VOLUME_ID=... RUNPOD_S3_ENDPOINT=https://s3api-eu-ro-1.runpod.io RUNPOD_S3_KEY=... RUNPOD_S3_SECRET=...
  python tools/volume.py ls                          # job folders under output/motion_swap
  python tools/volume.py ls output/motion_swap/<job> # files of one job
  python tools/volume.py get output/motion_swap/<job>/s1_compare_00001_.mp4 [local path]
  python tools/volume.py put local.mp4 input/dance.mp4
  python tools/volume.py jobs                        # every job.json: id, status, note, engine, tier

Paths are relative to the creator-data folder on the volume.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import storage as ST  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["ls", "get", "put", "jobs"])
    ap.add_argument("a", nargs="?")
    ap.add_argument("b", nargs="?")
    args = ap.parse_args()
    if not ST.configured():
        sys.exit("Set RUNPOD_VOLUME_ID, RUNPOD_S3_ENDPOINT, RUNPOD_S3_KEY and RUNPOD_S3_SECRET first (RunPod Settings, S3 API keys).")
    if args.command == "ls":
        prefix = f"{ST.PREFIX}/{(args.a or 'output/motion_swap').strip('/')}/"
        rows = ST.listing(prefix)
        if args.a is None:  # job folders only
            seen = sorted({k[len(prefix):].split("/", 1)[0] for k, _, _ in rows})
            print("\n".join(seen) or "(empty)")
        else:
            for k, size, ts in rows:
                print(f"{size / 1e6:8.1f} MB  {ts:%Y-%m-%d %H:%M}  {k[len(ST.PREFIX) + 1:]}")
    elif args.command == "get":
        key = f"{ST.PREFIX}/{args.a.strip('/')}"
        dst = Path(args.b or Path(args.a).name)
        ST.download(key, dst)
        print(f"saved {dst} ({dst.stat().st_size / 1e6:.1f} MB)")
    elif args.command == "put":
        key = f"{ST.PREFIX}/{args.b.strip('/')}"
        ST.upload(args.a, key)
        print(f"uploaded {args.a} -> {key}")
    elif args.command == "jobs":
        prefix = f"{ST.PREFIX}/output/_jobs/"
        for k, _, ts in sorted(ST.listing(prefix), key=lambda r: r[0]):
            if not k.endswith("/job.json"):
                continue
            tmp = Path(".volume_job.json")
            ST.download(k, tmp)
            j = json.loads(tmp.read_text(encoding="utf-8"))
            tmp.unlink(missing_ok=True)
            print(f"{j['id']}  {j.get('status', '?'):8}  {j.get('engine', '?'):8}  {j.get('tier', '?'):8}  {j.get('note', '')}")


if __name__ == "__main__":
    main()
