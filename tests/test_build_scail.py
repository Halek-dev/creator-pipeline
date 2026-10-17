import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import build_scail as S  # noqa: E402
from comfy_client import resolve  # noqa: E402


def titles(wf):
    return [n["_meta"]["title"] for n in wf.values()]


def links_ok(wf):
    for n in wf.values():
        for key, v in n["inputs"].items():
            if isinstance(v, list) and len(v) == 2 and isinstance(v[0], str):
                assert v[0] in wf, f"{n['_meta']['title']}.{key} -> missing node {v[0]}"
    return True


class ScailBuildTests(unittest.TestCase):
    def test_single_segment_graph(self):
        wf = S.build()
        t = S.by_title(wf)
        self.assertEqual(len(titles(wf)), len(set(titles(wf))))
        self.assertTrue(links_ok(wf))
        a = wf[t["Animate"]]["inputs"]
        self.assertTrue(a["replacement_mode"])
        self.assertEqual(a["pose_video_mask"], [t["Masks"], 0])
        self.assertEqual(a["reference_image_mask"], [t["Masks"], 1])
        self.assertEqual(a["previous_frame_count"], S.OVERLAP)
        self.assertNotIn("previous_frames", a)
        self.assertEqual(wf[t["Masks"]]["inputs"]["ref_track_data"], [t["SAM Reference Track"], 0])
        self.assertEqual(wf[t["Distill LoRA"]]["inputs"]["strength_model"], 0.8)
        self.assertEqual(wf[t["Distill LoRA"]]["inputs"]["lora_name"], S.DISTILL_LORA)
        self.assertIn("T2V", S.DISTILL_LORA)
        self.assertEqual(wf[t["Encode Reference"]]["inputs"]["image"], [t["Reference Cutout"], 0])  # CLIP vision sees the black cut-out
        self.assertEqual(wf[t["Animate"]]["inputs"]["reference_image"], [t["Reference Image"], 0])  # the node masks the raw reference itself
        self.assertEqual(wf[t["Black Canvas"]]["inputs"]["color"], 0)
        self.assertNotIn("Reference Mask Feather", t)
        self.assertEqual(wf[t["Masks"]]["inputs"]["object_indices"], "0")
        self.assertEqual(wf[t["Masks"]]["inputs"]["sort_by"], "area")
        self.assertEqual(wf[t["SAM Driving Prompt"]]["inputs"]["text"], wf[t["SAM Reference Prompt"]]["inputs"]["text"])
        wu = S.build(width=512, height=896, swap_below=0.6)
        tu = S.by_title(wu)
        self.assertTrue(links_ok(wu))
        self.assertEqual(wu[tu["Animate"]]["inputs"]["pose_video_mask"], [tu["Pose Mask Upper"], 0])
        self.assertEqual(wu[tu["Keep Region"]]["inputs"]["height"], 896 - int(896 * 0.6))
        self.assertEqual(wu[tu["Keep Below"]]["inputs"]["y"], int(896 * 0.6))
        self.assertEqual(wu[tu["White"]]["inputs"]["color"], 0xFFFFFF)
        self.assertEqual(wu[tu["Masks"]]["inputs"]["ref_track_data"], [tu["SAM Reference Track"], 0])  # reference identity untouched
        wi = S.build(identity_only=True)
        ti = S.by_title(wi)
        self.assertTrue(links_ok(wi))
        self.assertEqual(wi[ti["Animate"]]["inputs"]["pose_video_mask"], [ti["Pose Mask Identity"], 0])
        self.assertEqual(wi[ti["Keep Outfit"]]["inputs"]["mask"], [ti["Identity Grow"], 0])
        self.assertEqual({wi[ti[f"SAM {p} Prompt"]]["inputs"]["text"] for p in ("Head", "Hair", "Arm", "Hand")}, {"head", "hair", "arm", "hand"})
        wv = S.build(extra_views=2)
        tv = S.by_title(wv)
        self.assertTrue(links_ok(wv))
        self.assertEqual(wv[tv["Animate"]]["inputs"]["reference_image"], [tv["Reference Batch 2"], 0])
        self.assertEqual(wv[tv["SAM Reference Track"]]["inputs"]["images"], [tv["Reference Batch 2"], 0])
        self.assertEqual(wv[tv["Masks"]]["inputs"]["object_indices"], "")  # all identities when extra views are present
        self.assertEqual(wv[tv["Encode Reference"]]["inputs"]["image"], [tv["Reference Cutout"], 0])  # CLIP still sees only the primary cut-out
        self.assertEqual(wf[t["Scheduler"]]["inputs"]["steps"], 6)
        self.assertEqual(wf[t["Sampler"]]["inputs"]["cfg"], 1.0)
        self.assertEqual(wf[t["Decode"]]["inputs"]["samples"], [t["Sampler"], 1])  # denoised output, as in the template
        self.assertEqual(wf[t["Video"]]["inputs"]["audio"], [t["Driving Frames"], 1])
        self.assertEqual(wf[t["Resize Driving"]]["inputs"]["resize_type.width"], 512)
        self.assertEqual(wf[t["Resize Driving"]]["inputs"]["resize_type.height"], 896)
        self.assertEqual(resolve(wf, "Save.filename_prefix")[1], "filename_prefix")

    def test_two_segments_follow_the_extend_recipe(self):
        wf = S.build(segments=2)
        t = S.by_title(wf)
        self.assertTrue(links_ok(wf))
        self.assertEqual(wf[t["Segment Frames 2"]]["inputs"]["batch_index"], 76)
        a2 = wf[t["Animate 2"]]["inputs"]
        self.assertEqual(a2["previous_frames"], [t["Decode"], 0])
        self.assertEqual(wf[t["Drop Overlap 2"]]["inputs"]["batch_index"], 5)
        self.assertEqual(wf[t["Previous Last Frame 2"]]["inputs"]["batch_index"], -1)
        cm = wf[t["Color Match 2"]]["inputs"]
        self.assertEqual(cm["image_target"], [t["Drop Overlap 2"], 0])
        self.assertEqual(cm["image_ref"], [t["Previous Last Frame 2"], 0])
        self.assertEqual(wf[t["Join 2"]]["inputs"]["image2"], [t["Color Match 2"], 0])
        self.assertEqual(wf[t["Trim Tail"]]["inputs"]["length"], 157)
        self.assertEqual(wf[t["Driving Trimmed"]]["inputs"]["length"], 157)

    def test_no_distill_and_landscape_and_finishing(self):
        wf = S.build(distill=False, width=900, height=500, finishing=True)
        t = S.by_title(wf)
        self.assertNotIn("Distill LoRA", t)
        self.assertEqual(wf[t["Scheduler"]]["inputs"]["steps"], S.HIGH_STEPS)
        self.assertEqual(wf[t["Sampler"]]["inputs"]["cfg"], S.HIGH_CFG)
        self.assertEqual(wf[t["Shift"]]["inputs"]["model"], [t["DPO LoRA"], 0])
        self.assertEqual((wf[t["Resize Driving"]]["inputs"]["resize_type.width"], wf[t["Resize Driving"]]["inputs"]["resize_type.height"]), (896, 480))
        self.assertIn("Save Final", t)
        self.assertEqual(wf[t["Upscale Resize"]]["inputs"]["input"], [t["Trim Tail"], 0])

    def test_segment_math(self):
        self.assertEqual(S.frames_for_segments(1), 81)
        self.assertEqual(S.frames_for_segments(2), 157)
        self.assertEqual(S.segments_for_frames(81), 2)   # ceil(81/76) as in the template
        self.assertEqual(S.segments_for_frames(76), 1)
        self.assertEqual(S.segments_for_frames(1000), 3)


if __name__ == "__main__":
    unittest.main()
