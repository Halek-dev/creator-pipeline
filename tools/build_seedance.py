"""Seedance 2.5 reference-to-video (paid API node, no GPU): reference image + driving video + prompt -> clip.

  python tools/build_seedance.py      # writes workflows/seedance25_reference.json

Uses ComfyUI's built-in "ByteDance Seedance 2.5 Reference to Video" node (model option "Seedance 2.5", task
"reference"). The job must be queued with extra_data {"api_key_comfy_org": <Comfy API key>}; the app does that from
the COMFY_API_KEY environment variable. A side-by-side compare clip is produced for scoring.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPARE_FRAMES = 81
RESOLUTIONS = ("480p", "720p", "1080p")
RATIOS = ("16:9", "4:3", "1:1", "3:4", "9:16", "21:9", "adaptive")

import sys  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from graph import add, by_title  # noqa: E402,F401


def clamp_duration(seconds):
    return int(min(30, max(4, round(seconds))))


def build(prompt="A young woman with dark hair in a high bun, black crop top and wide cargo pants, dancing energetically in a bright photo studio.",
          resolution="720p", ratio="9:16", duration=5, audio=True, seed=0):
    if resolution not in RESOLUTIONS or ratio not in RATIOS:
        raise ValueError(f"resolution must be one of {RESOLUTIONS}, ratio one of {RATIOS}")
    wf = {}
    ref = add(wf, "LoadImage", "Reference Image", {"image": "reference_streetwear_character.png"})
    vid = add(wf, "LoadVideo", "Driving Video", {"file": "street_dancer.mp4"})
    gen = add(wf, "ByteDance2ReferenceNodeV2", "Seedance", {
        "model": "Seedance 2.5",
        "model.prompt": prompt,
        "model.resolution": resolution,
        "model.ratio": ratio,
        "model.duration": clamp_duration(duration),
        "model.generate_audio": bool(audio),
        "model.task_type": "reference",
        "model.output_format": "mp4",
        "model.reference_images.image_1": [ref, 0],
        "model.reference_videos.video_1": [vid, 0],
        "model.auto_downscale": True,
        "model.auto_upscale": False,
        "seed": int(seed),
        "watermark": False,
    })
    add(wf, "SaveVideo", "Save", {"video": [gen, 0], "filename_prefix": "motion_swap/seedance_result", "format": "mp4", "format.codec": "h264"})
    # No compare clip here: decoding a long 1080p result inside ComfyUI needs the whole clip in RAM (8 GB for 14 s),
    # which killed a laptop run. The app builds the side-by-side itself, streaming (jobs.make_compare).
    return wf


def main():
    wf = build()
    with open(ROOT / "workflows" / "seedance25_reference.json", "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(wf, indent=2, ensure_ascii=False) + "\n")
    print("wrote seedance25_reference.json", f"({len(wf)} nodes)")


if __name__ == "__main__":
    main()
