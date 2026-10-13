"""Queue many runs of a ComfyUI workflow and collect the outputs.

The workflow must be in API format (ComfyUI: Workflow > Export (API)).
Inputs are addressed as "<node title or id>.<input name>".

Examples
  # 20 runs, walking through a folder of images one by one
  python tools/batch_run.py workflows/finish_folder.json --count 20 --step "Folder Image Loader.index=0"

  # 50 character images, new library scene and new noise each run
  python tools/batch_run.py workflows/sd_turbo_character.json --count 50 ^
      --step "Prompt Library.seed=0" --random "Sampler.noise_seed" --set "Character Prompt.trigger=zr4char"

  # one line of JSON per run: {"Character Prompt.scene": "on a rooftop", "Sampler.noise_seed": 5}
  python tools/batch_run.py workflows/sd_turbo_character.json --jobs my_jobs.jsonl
"""
import argparse
import copy
import json
import random
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from comfy_client import DEFAULT_SERVER, ComfyError, collect_outputs, error_message, poll, queue_prompt, set_input, start_hint  # noqa: E402


def parse_value(raw: str):
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def split_assignment(text: str) -> tuple[str, str]:
    if "=" not in text:
        raise SystemExit(f'Expected KEY=VALUE, got "{text}"')
    key, value = text.split("=", 1)
    return key.strip(), value


def build_jobs(args, workflow: dict) -> list[dict]:
    if args.jobs:
        lines = Path(args.jobs).read_text(encoding="utf-8").splitlines()
        per_job = [json.loads(line) for line in lines if line.strip()]
    else:
        per_job = [{} for _ in range(args.count)]

    rng = random.Random(args.random_seed)
    jobs = []
    for i, overrides in enumerate(per_job):
        values = {}
        for item in args.set:
            key, raw = split_assignment(item)
            values[key] = parse_value(raw)
        for item in args.step:
            key, raw = split_assignment(item)
            values[key] = int(raw) + i
        for key in args.random:
            values[key] = rng.randint(0, 2**31 - 1)
        values.update(overrides)

        job = copy.deepcopy(workflow)
        for key, value in values.items():
            try:
                set_input(job, key, value)
            except ComfyError as e:
                raise SystemExit(str(e)) from None
        jobs.append({"values": values, "prompt": job})
    return jobs


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("workflow", help="Workflow in ComfyUI API format (.json)")
    parser.add_argument("--count", type=int, default=1, help="Number of runs (ignored with --jobs)")
    parser.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", help="Same value for every run")
    parser.add_argument("--step", action="append", default=[], metavar="KEY=START", help="START, START+1, ... per run")
    parser.add_argument("--random", action="append", default=[], metavar="KEY", help="New random seed per run")
    parser.add_argument("--random-seed", type=int, default=None, help="Make --random values repeatable")
    parser.add_argument("--jobs", help="JSONL file: one object of KEY: value overrides per run")
    parser.add_argument("--server", default=DEFAULT_SERVER)
    parser.add_argument("--timeout", type=float, default=3600, help="Max seconds to wait for the whole batch")
    parser.add_argument("--dry-run", action="store_true", help="Print the runs without queueing them")
    args = parser.parse_args()

    workflow = json.loads(Path(args.workflow).read_text(encoding="utf-8"))
    if "nodes" in workflow and "links" in workflow:
        raise SystemExit("This is a UI workflow. In ComfyUI use Workflow > Export (API) and pass that file.")
    jobs = build_jobs(args, workflow)

    if args.dry_run:
        for i, job in enumerate(jobs, 1):
            print(f"run {i}: {json.dumps(job['values'])}")
        return

    client_id = str(uuid.uuid4())
    try:
        for i, job in enumerate(jobs, 1):
            job["prompt_id"] = queue_prompt(args.server, job["prompt"], client_id)
            print(f"queued {i}/{len(jobs)}  {json.dumps(job['values'])}")
    except ComfyError as e:
        msg = str(e)
        if "Can't reach" in msg:
            msg += f". Start it first ({start_hint()})."
        raise SystemExit(msg) from None

    start = time.time()
    pending = {job["prompt_id"]: job for job in jobs}
    while pending:
        if time.time() - start > args.timeout:
            print(f"Timed out with {len(pending)} runs still pending.", file=sys.stderr)
            break
        time.sleep(1.0)
        for prompt_id in list(pending):
            entry = poll(args.server, prompt_id)
            if entry is None:
                continue
            job = pending.pop(prompt_id)
            job["status"] = entry["status"].get("status_str", "unknown")
            job["outputs"] = collect_outputs(entry)
            if job["status"] == "error":
                job["error"] = error_message(entry)
            done = len(jobs) - len(pending)
            detail = ", ".join(job["outputs"]) or job.get("error", "")
            print(f"[{done}/{len(jobs)}] {job['status']:<7} {time.time() - start:6.1f}s  {detail}")

    log_dir = Path("batch_runs")
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / f"{datetime.now():%Y%m%d-%H%M%S}.json"
    summary = [{k: job.get(k) for k in ("prompt_id", "values", "status", "outputs", "error")} for job in jobs]
    log_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    ok = sum(1 for j in jobs if j.get("status") == "success")
    print(f"\n{ok}/{len(jobs)} succeeded in {time.time() - start:.0f}s. Log: {log_path}")
    if ok < len(jobs):
        sys.exit(1)


if __name__ == "__main__":
    main()
