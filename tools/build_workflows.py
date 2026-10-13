"""Derive workflow variants from workflows/wan_animate2.json.

  python tools/build_workflows.py      # writes wan_animate2_high.json, wan_animate2_chain2.json, wan_animate2_finish.json

The app imports the functions below and applies them per job; the files are for the batch runner and for checking
the graphs in ComfyUI. Wiring follows ComfyUI's official templates (Wan Animate 2 full model, SeedVR2 video
upscale, FILM frame interpolation).
"""
import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "workflows" / "wan_animate2.json"

FULL_MODEL = "wan_animate_2_int8_convrot.safetensors"
DISTILL_LORA = "lightx2v_I2V_14B_480p_cfg_step_distill_rank64_bf16.safetensors"
UPSCALE_MODEL = "seedvr2_3b_int8_convrot.safetensors"
UPSCALE_VAE = "seedvr2_ema_vae_fp16.safetensors"
FILM_MODEL = "film_net_fp16.safetensors"
CHUNK_FRAMES = 81          # frames per Wan Animate 2 pass
CHUNK_OVERLAP = 1          # frames shared between consecutive passes
ANIMATE_OUT = {"positive": 0, "negative": 1, "latent": 2, "trim_latent": 3, "trim_image": 4, "video_frame_offset": 5}


def load_base():
    return json.loads(BASE.read_text(encoding="utf-8"))


def by_title(wf):
    return {n["_meta"]["title"]: nid for nid, n in wf.items()}


def add(wf, class_type, title, inputs):
    if title in by_title(wf):
        raise ValueError(f"duplicate node title {title!r}")
    nid = str(max(int(k) for k in wf) + 1)
    wf[nid] = {"class_type": class_type, "_meta": {"title": title}, "inputs": inputs}
    return nid


def frames_for_chunks(chunks):
    return CHUNK_FRAMES + (chunks - 1) * (CHUNK_FRAMES - CHUNK_OVERLAP)


def chunks_for_frames(frames, max_chunks=3):
    """How many 81-frame passes a driving video of `frames` can feed (at least 1)."""
    n = 1
    while n < max_chunks and frames >= frames_for_chunks(n + 1):
        n += 1
    return n


def with_audio(wf):
    """Carry the driving video's sound into the result clip (the official template does this)."""
    t = by_title(wf)
    wf[t["Video"]]["inputs"]["audio"] = [t["Driving Frames"], 1]
    return wf


def with_high_tier(wf, steps=6):
    """Full Wan Animate 2 model + lightx2v step-distill LoRA, as in the official full template (6 steps, cfg 1)."""
    t = by_title(wf)
    wf[t["Model"]]["inputs"]["unet_name"] = FULL_MODEL
    lora = add(wf, "LoraLoaderModelOnly", "Distill LoRA", {"model": [t["Model"], 0], "lora_name": DISTILL_LORA, "strength_model": 1.0})
    wf[t["Model Cache"]]["inputs"]["model"] = [lora, 0]
    wf[t["Scheduler"]]["inputs"]["steps"] = steps
    return wf


def chained(wf, chunks):
    """Extend the clip over several 81-frame passes: each pass continues the previous one's motion and seeks
    further into the driving video; the first overlapping frame of every later pass is dropped; the result is
    trimmed to the driving video's length by the "Trim Tail" node (the app sets its length)."""
    if chunks < 2:
        return wf
    t = by_title(wf)
    prev_animate, prev_decode = t["Animate"], t["Decode"]
    pieces = [[t["Decode"], 0]]
    for k in range(2, chunks + 1):
        animate = add(wf, "WanAnimate2ToVideo", f"Animate {k}", {
            **copy.deepcopy(wf[t["Animate"]]["inputs"]),
            "continue_motion": [prev_decode, 0],
            "video_frame_offset": [prev_animate, ANIMATE_OUT["video_frame_offset"]],
        })
        sampler = add(wf, "SamplerCustom", f"Sampler {k}", {
            **copy.deepcopy(wf[t["Sampler"]]["inputs"]),
            "positive": [animate, ANIMATE_OUT["positive"]],
            "negative": [animate, ANIMATE_OUT["negative"]],
            "latent_image": [animate, ANIMATE_OUT["latent"]],
        })
        trim = add(wf, "TrimVideoLatent", f"Trim {k}", {"samples": [sampler, 0], "trim_amount": [animate, ANIMATE_OUT["trim_latent"]]})
        decode = add(wf, "VAEDecode", f"Decode {k}", {"samples": [trim, 0], "vae": wf[t["Decode"]]["inputs"]["vae"]})
        drop = add(wf, "ImageFromBatch", f"Drop Overlap {k}", {"image": [decode, 0], "batch_index": [animate, ANIMATE_OUT["trim_image"]], "length": 4096})
        pieces.append([drop, 0])
        prev_animate, prev_decode = animate, decode
    joined = pieces[0]
    for k, piece in enumerate(pieces[1:], 2):
        joined = [add(wf, "ImageBatch", f"Join {k}", {"image1": joined, "image2": piece}), 0]
    tail = add(wf, "ImageFromBatch", "Trim Tail", {"image": joined, "batch_index": 0, "length": frames_for_chunks(chunks)})
    wf[t["Video"]]["inputs"]["images"] = [tail, 0]
    wf[t["Compare"]]["inputs"]["image1"] = [tail, 0]
    wf[t["Driving Trimmed"]]["inputs"]["length"] = frames_for_chunks(chunks)
    return wf


def with_finishing(wf, scale=2, fps_multiplier=2, fps=48.0, grain=0.01):
    """Second pass on the finished frames: SeedVR2 upscale x2 -> FILM frame interpolation x2 -> Reality Finish ->
    a third output video ("Save Final") with the driving video's audio. Wiring follows the official SeedVR2 and
    FILM templates (one-step euler, tiled VAE, lab colour correction)."""
    t = by_title(wf)
    src = wf[t["Video"]]["inputs"]["images"]
    up_model = add(wf, "UNETLoader", "Upscale Model", {"unet_name": UPSCALE_MODEL, "weight_dtype": "default"})
    up_vae = add(wf, "VAELoader", "Upscale VAE", {"vae_name": UPSCALE_VAE})
    resized = add(wf, "ResizeImageMaskNode", "Upscale Resize", {"input": src, "resize_type": "scale by multiplier", "resize_type.multiplier": float(scale), "scale_method": "lanczos"})
    prep = add(wf, "SeedVR2Preprocess", "Upscale Prep", {"resized_images": [resized, 0]})
    enc = add(wf, "VAEEncodeTiled", "Upscale Encode", {"pixels": [prep, 0], "vae": [up_vae, 0], "tile_size": 512, "overlap": 128, "temporal_size": 64, "temporal_overlap": 8})
    cond = add(wf, "SeedVR2Conditioning", "Upscale Cond", {"model": [up_model, 0], "vae_conditioning": [enc, 0]})
    ks = add(wf, "KSampler", "Upscale Sampler", {"model": [up_model, 0], "seed": 0, "steps": 1, "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple", "positive": [cond, 0], "negative": [cond, 1], "latent_image": [enc, 0], "denoise": 1.0})
    dec = add(wf, "VAEDecodeTiled", "Upscale Decode", {"samples": [ks, 0], "vae": [up_vae, 0], "tile_size": 512, "overlap": 128, "temporal_size": 64, "temporal_overlap": 8})
    post = add(wf, "SeedVR2PostProcessing", "Upscale Post", {"images": [dec, 0], "original_resized_images": [resized, 0], "color_correction_method": "lab"})
    film = add(wf, "FrameInterpolationModelLoader", "FILM Model", {"model_name": FILM_MODEL})
    interp = add(wf, "FrameInterpolate", "Interpolate", {"interp_model": [film, 0], "images": [post, 0], "multiplier": fps_multiplier})
    finish = add(wf, "Creator_RealityFinish", "Finish", {"image": [interp, 0], "grain": grain, "vignette": 0.1, "warmth": 0.01, "lens_fringe_px": 0, "jpeg_quality": 100, "seed": 0})
    final = add(wf, "CreateVideo", "Final Video", {"images": [finish, 0], "fps": float(fps), "audio": [t["Driving Frames"], 1]})
    add(wf, "SaveVideo", "Save Final", {"video": [final, 0], "filename_prefix": "motion_swap/final", "format": "mp4", "format.codec": "h264"})
    return wf


def build(model="distilled", chunks=1, finishing=False, base=None):
    wf = copy.deepcopy(base) if base is not None else load_base()
    with_audio(wf)
    if model == "full":
        with_high_tier(wf)
    chained(wf, chunks)
    if finishing:
        with_finishing(wf)
    return wf


def main():
    out = ROOT / "workflows"
    base = load_base()
    if "audio" not in base[by_title(base)["Video"]]["inputs"]:
        with_audio(base)
        BASE.write_text(json.dumps(base, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print("updated", BASE.name, "(audio passthrough)")
    for name, kw in {
        "wan_animate2_high.json": {"model": "full"},
        "wan_animate2_chain2.json": {"chunks": 2},
        "wan_animate2_finish.json": {"finishing": True},
    }.items():
        wf = build(**kw)
        (out / name).write_text(json.dumps(wf, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print("wrote", name, f"({len(wf)} nodes)")


if __name__ == "__main__":
    main()
