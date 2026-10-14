"""Start, stop and inspect the team's RunPod pod from a laptop (Phase 4.4, optional).

  export RUNPOD_API_KEY=...            # RunPod -> Settings -> API Keys (never paste it into chat or files)
  python tools/pod.py status           # list your pods, their GPU, state and price
  python tools/pod.py start            # resume a stopped creator pod, or deploy a new one on the network volume
  python tools/pod.py stop             # stop (keeps the pod, bills the disk) -- prefer terminate when data is on the volume
  python tools/pod.py terminate        # delete the pod (the network volume keeps everything)
  python tools/pod.py start --dry-run  # print the API call without sending it

Deploying needs the network volume id (RunPod -> Storage -> the volume -> id) in RUNPOD_VOLUME_ID or --volume.
The new pod runs setup.sh + start.sh by itself; the team link is then printed in the pod's logs
(RunPod -> Pods -> the pod -> Logs, look for trycloudflare.com) and the deterministic proxy address below.

Status: written against the RunPod GraphQL API docs (https://docs.runpod.io/sdks/graphql/manage-pods); not yet
exercised against a live account. Run `status` first; if it works, `start` uses the same connection.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

API = "https://api.runpod.io/graphql"
POD_NAME = "creator-pipeline"
IMAGE = "runpod/pytorch:2.8.0-py3.11-cuda12.8.1-cudnn-devel-ubuntu22.04"
REPO = "https://github.com/Halek-dev/creator-pipeline.git"
# What the pod runs on boot: clone or update the repo, install, start everything, then stay alive.
BOOT = ("bash -c 'cd /workspace && (test -d creator-pipeline || git clone " + REPO + ") && cd creator-pipeline && git pull -q && "
        "bash scripts/setup.sh wan-animate-2 && GPU_HOURLY_USD=${GPU_HOURLY_USD:-1.0} bash scripts/start.sh; sleep infinity'")


def gql(query, key, dry_run=False):
    body = json.dumps({"query": query}).encode()
    if dry_run:
        print(query)
        return {}
    req = urllib.request.Request(f"{API}?api_key={key}", data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        sys.exit(f"RunPod API HTTP {e.code}: {e.read().decode()[:300]}")
    if data.get("errors"):
        sys.exit("RunPod API error: " + "; ".join(err.get("message", str(err)) for err in data["errors"]))
    return data.get("data", {})


def list_pods(key):
    q = """{ myself { pods { id name desiredStatus costPerHr machine { gpuDisplayName } runtime { uptimeInSeconds } } } }"""
    return gql(q, key).get("myself", {}).get("pods", [])


def proxy_url(pod_id, port=7860):
    return f"https://{pod_id}-{port}.proxy.runpod.net"


def cmd_status(args, key):
    pods = list_pods(key)
    if not pods:
        print("No pods. `python tools/pod.py start` deploys one on the network volume.")
        return
    for p in pods:
        up = (p.get("runtime") or {}).get("uptimeInSeconds")
        print(f"{p['id']}  {p['name']:<20} {p['desiredStatus']:<8} {p['machine']['gpuDisplayName']:<22} ${p['costPerHr']:.2f}/h"
              + (f"  up {up // 60} min  app: {proxy_url(p['id'])}" if up else ""))


def creator_pods(key):
    return [p for p in list_pods(key) if p["name"] == POD_NAME]


def cmd_start(args, key):
    mine = [] if args.dry_run else creator_pods(key)
    running = [p for p in mine if p["desiredStatus"] == "RUNNING"]
    if running:
        print(f"Already running: {running[0]['id']}  app: {proxy_url(running[0]['id'])}")
        return
    stopped = [p for p in mine if p["desiredStatus"] == "EXITED"]
    if stopped:
        pid = stopped[0]["id"]
        gql(f'mutation {{ podResume(input: {{podId: "{pid}", gpuCount: 1}}) {{ id desiredStatus }} }}', key, args.dry_run)
        print(f"Resumed {pid}. Open the web terminal and run: cd /workspace/creator-pipeline && bash scripts/start.sh")
        return
    volume = args.volume or os.environ.get("RUNPOD_VOLUME_ID")
    if not volume:
        sys.exit("No stopped pod to resume. To deploy a new one, give the network volume id: --volume <id> or RUNPOD_VOLUME_ID.")
    env = [f'{{key: "GPU_HOURLY_USD", value: "{args.hourly}"}}']
    for name in ("APP_PASSWORD", "ANTHROPIC_API_KEY", "COMFY_API_KEY", "IDLE_MINUTES"):
        if os.environ.get(name):  # forwarded from this laptop's environment, never written to disk
            env.append(f'{{key: "{name}", value: {json.dumps(os.environ[name])}}}')
    q = f'''mutation {{ podFindAndDeployOnDemand(input: {{
        cloudType: SECURE, gpuCount: 1, gpuTypeId: {json.dumps(args.gpu)}, name: "{POD_NAME}",
        imageName: "{IMAGE}", containerDiskInGb: {args.disk}, volumeInGb: 0,
        networkVolumeId: "{volume}", volumeMountPath: "/workspace",
        ports: "7860/http,8888/http,22/tcp", dockerArgs: {json.dumps(BOOT)},
        env: [{", ".join(env)}]
    }}) {{ id desiredStatus machine {{ gpuDisplayName }} }} }}'''
    data = gql(q, key, args.dry_run)
    if args.dry_run:
        return
    pod = data["podFindAndDeployOnDemand"]
    print(f"Deployed {pod['id']} on {pod['machine']['gpuDisplayName']}. First boot installs and downloads (2-15 min).")
    print(f"Team app (RunPod proxy, try first):  {proxy_url(pod['id'])}")
    print("If the proxy address does not load, the trycloudflare.com link is in RunPod -> Pods -> this pod -> Logs.")


def cmd_stop(args, key, action="stop"):
    mine = [{"id": "<pod-id>"}] if args.dry_run else [p for p in creator_pods(key) if p["desiredStatus"] == "RUNNING"]
    if not mine:
        print("No running creator pod.")
        return
    mutation = "podTerminate" if action == "terminate" else "podStop"
    for p in mine:
        gql(f'mutation {{ {mutation}(input: {{podId: "{p["id"]}"}}) {{ id }} }}' if action == "stop"
            else f'mutation {{ podTerminate(input: {{podId: "{p["id"]}"}}) }}', key, args.dry_run)
        print(f"{action}: {p['id']}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["status", "start", "stop", "terminate"])
    ap.add_argument("--volume", help="network volume id (or RUNPOD_VOLUME_ID)")
    ap.add_argument("--gpu", default="NVIDIA L40S", help="RunPod gpuTypeId, e.g. 'NVIDIA L40S', 'NVIDIA A40', 'NVIDIA A100 80GB PCIe'")
    ap.add_argument("--disk", type=int, default=20, help="container disk GB (models live on the volume)")
    ap.add_argument("--hourly", default=os.environ.get("GPU_HOURLY_USD", "1.0"), help="pod price per hour, shown in the app")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    key = os.environ.get("RUNPOD_API_KEY")
    if not key and not args.dry_run:
        sys.exit("Set RUNPOD_API_KEY in this terminal first (RunPod -> Settings -> API Keys).")
    if args.command == "status":
        cmd_status(args, key)
    elif args.command == "start":
        cmd_start(args, key)
    else:
        cmd_stop(args, key, args.command)


if __name__ == "__main__":
    main()
