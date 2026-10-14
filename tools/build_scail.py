"""SCAIL-2 workflows: swap our character INTO the original driving video (keeps its background, lighting, camera).

  python tools/build_scail.py      # writes workflows/scail2_replace.json and scail2_replace_seg2.json

Wiring follows ComfyUI's official "SCAIL-2 Int8: Character Replacement" template: SAM3 tracks the person in the
driving video and in the reference image, SCAIL2ColoredMask turns the tracks into masks, WanSCAILToVideo builds the
conditioning (replacement_mode on), the int8 SCAIL-2 model runs with the DPO LoRA and the lightx2v distill LoRA
(6 steps, cfg 1). Longer videos use the template's Extend recipe: 81-frame segments overlapping by 5 frames, each
continuing from the previous frames, overlap dropped, colours matched to the previous segment's last frame.
"""
import copy
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SCAIL_MODEL = "wan2.1_14B_SCAIL_2_int8_convrot.safetensors"
DPO_LORA = "wan2.1_SCAIL_2_DPO_lora_bf16.safetensors"
DISTILL_LORA = "lightx2v_I2V_14B_480p_cfg_step_distill_rank64_bf16.safetensors"
SAM_MODEL = "sam3.1_multiplex_fp16.safetensors"
TEXT_ENCODER = "umt5_xxl_fp8_e4m3fn_scaled.safetensors"
CLIP_VISION = "clip_vision_h.safetensors"
VAE = "Wan2_1_VAE_bf16.safetensors"
SEGMENT_FRAMES = 81
OVERLAP = 5
ANIMATE_OUT = {"positive": 0, "negative": 1, "latent": 2}
SAMPLER_OUT = {"output": 0, "denoised": 1}

import sys  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from graph import add, by_title  # noqa: E402,F401


def frames_for_segments(segments):
    return SEGMENT_FRAMES + (segments - 1) * (SEGMENT_FRAMES - OVERLAP)


def segments_for_frames(frames, max_segments=3):
    """Segments a driving video of `frames` can feed; the official template uses ceil(frames / 76)."""
    return max(1, min(max_segments, math.ceil(frames / (SEGMENT_FRAMES - OVERLAP))))


def snap32(v):
    return max(32, (int(v) // 32) * 32)


def build(segments=1, width=512, height=896, distill=True, replace=True, finishing=False):
    width, height = snap32(width), snap32(height)
    wf = {}
    # models
    model = add(wf, "UNETLoader", "Model", {"unet_name": SCAIL_MODEL, "weight_dtype": "default"})
    dpo = add(wf, "LoraLoaderModelOnly", "DPO LoRA", {"model": [model, 0], "lora_name": DPO_LORA, "strength_model": 1.0})
    if distill:
        top = add(wf, "LoraLoaderModelOnly", "Distill LoRA", {"model": [dpo, 0], "lora_name": DISTILL_LORA, "strength_model": 0.8})
        steps, cfg = 6, 1.0
    else:
        top, steps, cfg = dpo, 40, 5.0
    shift = add(wf, "ModelSamplingSD3", "Shift", {"model": [top, 0], "shift": 5})
    clip = add(wf, "CLIPLoader", "Text Encoder", {"clip_name": TEXT_ENCODER, "type": "wan"})
    pos = add(wf, "CLIPTextEncode", "Character Prompt", {"clip": [clip, 0], "text": "A young woman with dark hair in a high bun, black crop top and wide cargo pants, dancing in front of a graffiti wall in daylight."})
    neg = add(wf, "CLIPTextEncode", "Negative Prompt", {"clip": [clip, 0], "text": ""})
    clipv = add(wf, "CLIPVisionLoader", "CLIP Vision", {"clip_name": CLIP_VISION})
    vae = add(wf, "VAELoader", "VAE", {"vae_name": VAE})
    sam = add(wf, "CheckpointLoaderSimple", "SAM Model", {"ckpt_name": SAM_MODEL})
    sam_drive_text = add(wf, "CLIPTextEncode", "SAM Driving Prompt", {"clip": [sam, 1], "text": "person"})
    sam_ref_text = add(wf, "CLIPTextEncode", "SAM Reference Prompt", {"clip": [sam, 1], "text": "human"})
    # inputs
    ref = add(wf, "LoadImage", "Reference Image", {"image": "reference_streetwear_character.png"})
    vid = add(wf, "LoadVideo", "Driving Video", {"file": "street_dancer.mp4"})
    frames = add(wf, "GetVideoComponents", "Driving Frames", {"video": [vid, 0]})
    enc_ref = add(wf, "CLIPVisionEncode", "Encode Reference", {"clip_vision": [clipv, 0], "image": [ref, 0], "crop": "none"})
    ref_track = add(wf, "SAM3_VideoTrack", "SAM Reference Track", {"images": [ref, 0], "model": [sam, 0], "conditioning": [sam_ref_text, 0], "detection_threshold": 0.5, "max_objects": 4, "detect_interval": 1})
    scheduler_model = [top, 0]

    pieces, prev_decode, prev_resized = [], None, None
    for k in range(1, segments + 1):
        sfx = "" if k == 1 else f" {k}"
        seg = add(wf, "ImageFromBatch", f"Segment Frames{sfx}", {"image": [frames, 0], "batch_index": (SEGMENT_FRAMES - OVERLAP) * (k - 1), "length": SEGMENT_FRAMES})
        resized = add(wf, "ResizeImageMaskNode", f"Resize Driving{sfx}", {"input": [seg, 0], "resize_type": "scale dimensions", "resize_type.width": width, "resize_type.height": height, "resize_type.crop": "center", "scale_method": "area"})
        size = add(wf, "GetImageSize", f"Driving Size{sfx}", {"image": [resized, 0]})
        drive_track = add(wf, "SAM3_VideoTrack", f"SAM Driving Track{sfx}", {"images": [resized, 0], "model": [sam, 0], "conditioning": [sam_drive_text, 0], "detection_threshold": 0.5, "max_objects": 4, "detect_interval": 1})
        masks = add(wf, "SCAIL2ColoredMask", f"Masks{sfx}", {"driving_track_data": [drive_track, 0], "ref_track_data": [ref_track, 0], "object_indices": "", "sort_by": "left_to_right", "replacement_mode": replace})
        animate_inputs = {
            "positive": [pos, 0], "negative": [neg, 0], "vae": [vae, 0],
            "width": [size, 0], "height": [size, 1], "length": [size, 2], "batch_size": 1,
            "pose_strength": 1.0, "pose_start": 0.0, "pose_end": 1.0, "video_frame_offset": 0, "previous_frame_count": OVERLAP,
            "pose_video": [resized, 0], "pose_video_mask": [masks, 0], "replacement_mode": replace,
            "reference_image": [ref, 0], "reference_image_mask": [masks, 1], "clip_vision_output": [enc_ref, 0],
        }
        if prev_decode is not None:
            animate_inputs["previous_frames"] = [prev_decode, 0]
        animate = add(wf, "WanSCAILToVideo", f"Animate{sfx}", animate_inputs)
        sched = add(wf, "BasicScheduler", f"Scheduler{sfx}", {"model": scheduler_model, "scheduler": "simple", "steps": steps, "denoise": 1.0})
        stype = add(wf, "KSamplerSelect", f"Sampler Type{sfx}", {"sampler_name": "euler"})
        sampler = add(wf, "SamplerCustom", f"Sampler{sfx}", {"model": [shift, 0], "add_noise": True, "noise_seed": 1, "cfg": cfg, "positive": [animate, ANIMATE_OUT["positive"]], "negative": [animate, ANIMATE_OUT["negative"]], "sampler": [stype, 0], "sigmas": [sched, 0], "latent_image": [animate, ANIMATE_OUT["latent"]]})
        decode = add(wf, "VAEDecode", f"Decode{sfx}", {"samples": [sampler, SAMPLER_OUT["denoised"]], "vae": [vae, 0]})
        if k == 1:
            pieces.append([decode, 0])
        else:
            dropped = add(wf, "ImageFromBatch", f"Drop Overlap{sfx}", {"image": [decode, 0], "batch_index": OVERLAP, "length": 4096})
            last_prev = add(wf, "ImageFromBatch", f"Previous Last Frame{sfx}", {"image": [prev_decode, 0], "batch_index": -1, "length": 1})
            matched = add(wf, "ColorTransfer", f"Color Match{sfx}", {"image_target": [dropped, 0], "image_ref": [last_prev, 0], "method": "reinhard_lab", "source_stats": "per_frame", "strength": 1.0})
            pieces.append([matched, 0])
        prev_decode, prev_resized = decode, resized

    joined = pieces[0]
    for k, piece in enumerate(pieces[1:], 2):
        joined = [add(wf, "ImageBatch", f"Join {k}", {"image1": joined, "image2": piece}), 0]
    tail = add(wf, "ImageFromBatch", "Trim Tail", {"image": joined, "batch_index": 0, "length": frames_for_segments(segments)})
    video = add(wf, "CreateVideo", "Video", {"images": [tail, 0], "fps": [frames, 2], "audio": [frames, 1]})
    add(wf, "SaveVideo", "Save", {"video": [video, 0], "filename_prefix": "motion_swap/scail_result", "format": "mp4", "format.codec": "h264"})
    # side-by-side with the driving video, for scoring
    drive_all = add(wf, "ImageFromBatch", "Driving Trimmed", {"image": [frames, 0], "batch_index": 0, "length": frames_for_segments(segments)})
    drive_resized = add(wf, "ResizeImageMaskNode", "Resize Driving For Compare", {"input": [drive_all, 0], "resize_type": "scale dimensions", "resize_type.width": width, "resize_type.height": height, "resize_type.crop": "center", "scale_method": "area"})
    compare = add(wf, "ImageStitch", "Compare", {"image1": [tail, 0], "image2": [drive_resized, 0], "direction": "right", "match_image_size": True, "spacing_width": 0, "spacing_color": "white"})
    cvideo = add(wf, "CreateVideo", "Compare Video", {"images": [compare, 0], "fps": [frames, 2]})
    add(wf, "SaveVideo", "Save Compare", {"video": [cvideo, 0], "filename_prefix": "motion_swap/scail_compare", "format": "mp4", "format.codec": "h264"})
    if finishing:
        import build_workflows as B

        B.with_finishing(wf)
    return wf


def main():
    out = ROOT / "workflows"
    for name, kw in {"scail2_replace.json": {}, "scail2_replace_seg2.json": {"segments": 2}}.items():
        wf = build(**kw)
        with open(out / name, "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(wf, indent=2, ensure_ascii=False) + "\n")
        print("wrote", name, f"({len(wf)} nodes)")


if __name__ == "__main__":
    main()
