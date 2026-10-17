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
DISTILL_LORA = "lightx2v_T2V_14B_cfg_step_distill_v2_lora_rank64_bf16.safetensors"  # T2V: the SCAIL-2 maintainers say the I2V one leaks the driver's clothes
DISTILL_LORA_I2V = "lightx2v_I2V_14B_480p_cfg_step_distill_rank64_bf16.safetensors"      # what the official template ships; kept for A/B
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


HIGH_STEPS, HIGH_CFG = 40, 4.0   # maintainers' non-distilled setting (shift 5): about 13x the turbo time, final renders only
BLACK = 0x000000                 # the authors require the reference cut out onto black, never grey


def build(segments=1, width=512, height=896, distill=True, replace=True, finishing=False, clean_reference=True, steps=None, cfg=None, identities="0",
          distill_lora=DISTILL_LORA, distill_strength=0.8, extra_views=0):
    """identities: which tracked people to swap, by size ("0" = only the biggest person in the reference and in the
    video). The reference tracker also detects dolls, posters and figurines as "human"; with all identities enabled the
    model composites every one of them into the clip (seen with a bookshelf of figurines, 17 Oct 2026).
    clean_reference: cut the character out onto BLACK (hard SAM mask, slightly grown) for the CLIP vision encoder, as the
    authors require for replacement mode. The WanSCAILToVideo node already blacks out the reference outside the mask
    before the VAE; this does the same for CLIP vision, so the photo's background never reaches the model.
    distill_lora / distill_strength: the LightX2V LoRA and its strength; lower strength with more steps buys texture.
    extra_views: number of additional photos of the SAME character (full body, back view, close-up) batched behind the
    primary reference; the node treats extra batch images as additional views. The app resizes them to the primary's
    size first. With extra views, identities is widened to all objects, since the extra photo's person is a second
    track object; use plain-background photos then."""
    width, height = snap32(width), snap32(height)
    wf = {}
    # models
    model = add(wf, "UNETLoader", "Model", {"unet_name": SCAIL_MODEL, "weight_dtype": "default"})
    dpo = add(wf, "LoraLoaderModelOnly", "DPO LoRA", {"model": [model, 0], "lora_name": DPO_LORA, "strength_model": 1.0})
    if distill:
        top = add(wf, "LoraLoaderModelOnly", "Distill LoRA", {"model": [dpo, 0], "lora_name": distill_lora, "strength_model": distill_strength})
        steps, cfg = steps or 6, cfg or 1.0
    else:
        top, steps, cfg = dpo, steps or HIGH_STEPS, cfg or HIGH_CFG
    shift = add(wf, "ModelSamplingSD3", "Shift", {"model": [top, 0], "shift": 5})
    clip = add(wf, "CLIPLoader", "Text Encoder", {"clip_name": TEXT_ENCODER, "type": "wan"})
    pos = add(wf, "CLIPTextEncode", "Character Prompt", {"clip": [clip, 0], "text": "A young woman with dark hair in a high bun, black crop top and wide cargo pants, dancing in front of a graffiti wall in daylight."})
    neg = add(wf, "CLIPTextEncode", "Negative Prompt", {"clip": [clip, 0], "text": ""})
    clipv = add(wf, "CLIPVisionLoader", "CLIP Vision", {"clip_name": CLIP_VISION})
    vae = add(wf, "VAELoader", "VAE", {"vae_name": VAE})
    sam = add(wf, "CheckpointLoaderSimple", "SAM Model", {"ckpt_name": SAM_MODEL})
    sam_drive_text = add(wf, "CLIPTextEncode", "SAM Driving Prompt", {"clip": [sam, 1], "text": "human"})  # same word on both tracks, as the template
    sam_ref_text = add(wf, "CLIPTextEncode", "SAM Reference Prompt", {"clip": [sam, 1], "text": "human"})
    # inputs
    ref = add(wf, "LoadImage", "Reference Image", {"image": "reference_streetwear_character.png"})
    vid = add(wf, "LoadVideo", "Driving Video", {"file": "street_dancer.mp4"})
    frames = add(wf, "GetVideoComponents", "Driving Frames", {"video": [vid, 0]})
    ref_batch = [ref, 0]
    for k in range(1, int(extra_views) + 1):
        extra = add(wf, "LoadImage", f"Extra View {k}", {"image": f"extra_view_{k}.png"})
        ref_batch = [add(wf, "ImageBatch", f"Reference Batch {k}", {"image1": ref_batch, "image2": [extra, 0]}), 0]
    if extra_views:
        identities = ""  # the extra view's person is another track object; keep them all
    ref_track = add(wf, "SAM3_VideoTrack", "SAM Reference Track", {"images": ref_batch, "model": [sam, 0], "conditioning": [sam_ref_text, 0], "detection_threshold": 0.5, "max_objects": 4, "detect_interval": 1})
    ref_in = ref_batch
    if clean_reference:
        ref_mask = add(wf, "SAM3_TrackToMask", "Reference Mask", {"track_data": [ref_track, 0], "object_indices": identities})
        first_mask = add(wf, "ImageFromBatch", "Reference Mask First", {"image": [add(wf, "MaskToImage", "Reference Mask Image", {"mask": [ref_mask, 0]}), 0], "batch_index": 0, "length": 1})
        first_mask_m = add(wf, "ImageToMask", "Reference Mask Primary", {"image": [first_mask, 0], "channel": "red"})
        grown = add(wf, "GrowMask", "Reference Mask Grow", {"mask": [first_mask_m, 0], "expand": 2, "tapered_corners": True})
        ref_size = add(wf, "GetImageSize", "Reference Size", {"image": [ref, 0]})
        canvas = add(wf, "EmptyImage", "Black Canvas", {"width": [ref_size, 0], "height": [ref_size, 1], "batch_size": 1, "color": BLACK})
        cut = add(wf, "ImageCompositeMasked", "Reference Cutout", {"destination": [canvas, 0], "source": [ref, 0], "x": 0, "y": 0, "resize_source": False, "mask": [grown, 0]})
        clip_in = [cut, 0]
    else:
        clip_in = ref_in
    enc_ref = add(wf, "CLIPVisionEncode", "Encode Reference", {"clip_vision": [clipv, 0], "image": clip_in, "crop": "none"})
    scheduler_model = [top, 0]

    pieces, prev_decode, prev_resized = [], None, None
    for k in range(1, segments + 1):
        sfx = "" if k == 1 else f" {k}"
        seg = add(wf, "ImageFromBatch", f"Segment Frames{sfx}", {"image": [frames, 0], "batch_index": (SEGMENT_FRAMES - OVERLAP) * (k - 1), "length": SEGMENT_FRAMES})
        resized = add(wf, "ResizeImageMaskNode", f"Resize Driving{sfx}", {"input": [seg, 0], "resize_type": "scale dimensions", "resize_type.width": width, "resize_type.height": height, "resize_type.crop": "center", "scale_method": "area"})
        size = add(wf, "GetImageSize", f"Driving Size{sfx}", {"image": [resized, 0]})
        drive_track = add(wf, "SAM3_VideoTrack", f"SAM Driving Track{sfx}", {"images": [resized, 0], "model": [sam, 0], "conditioning": [sam_drive_text, 0], "detection_threshold": 0.5, "max_objects": 4, "detect_interval": 1})
        masks = add(wf, "SCAIL2ColoredMask", f"Masks{sfx}", {"driving_track_data": [drive_track, 0], "ref_track_data": [ref_track, 0], "object_indices": identities, "sort_by": "area", "replacement_mode": replace})
        animate_inputs = {
            "positive": [pos, 0], "negative": [neg, 0], "vae": [vae, 0],
            "width": [size, 0], "height": [size, 1], "length": [size, 2], "batch_size": 1,
            "pose_strength": 1.0, "pose_start": 0.0, "pose_end": 1.0, "video_frame_offset": 0, "previous_frame_count": OVERLAP,
            "pose_video": [resized, 0], "pose_video_mask": [masks, 0], "replacement_mode": replace,
            "reference_image": ref_in, "reference_image_mask": [masks, 1], "clip_vision_output": [enc_ref, 0],
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
