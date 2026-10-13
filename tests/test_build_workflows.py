import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import build_workflows as B  # noqa: E402
from comfy_client import resolve  # noqa: E402


def titles(wf):
    return [n["_meta"]["title"] for n in wf.values()]


class BuildTests(unittest.TestCase):
    def test_titles_stay_unique_in_every_variant(self):
        for kw in ({}, {"model": "full"}, {"chunks": 3}, {"finishing": True}, {"model": "full", "chunks": 2, "finishing": True}):
            wf = B.build(**kw)
            self.assertEqual(len(titles(wf)), len(set(titles(wf))), kw)

    def test_base_file_not_mutated(self):
        before = json.dumps(B.load_base(), sort_keys=True)
        B.build(model="full", chunks=2, finishing=True)
        self.assertEqual(before, json.dumps(B.load_base(), sort_keys=True))

    def test_audio_passthrough(self):
        wf = B.build()
        t = B.by_title(wf)
        self.assertEqual(wf[t["Video"]]["inputs"]["audio"], [t["Driving Frames"], 1])

    def test_high_tier(self):
        wf = B.build(model="full")
        t = B.by_title(wf)
        self.assertEqual(wf[t["Model"]]["inputs"]["unet_name"], B.FULL_MODEL)
        self.assertEqual(wf[t["Distill LoRA"]]["inputs"]["lora_name"], B.DISTILL_LORA)
        self.assertEqual(wf[t["Model Cache"]]["inputs"]["model"], [t["Distill LoRA"], 0])
        self.assertEqual(wf[t["Scheduler"]]["inputs"]["steps"], 6)

    def test_chained_wiring(self):
        wf = B.build(chunks=3)
        t = B.by_title(wf)
        a2, a3 = wf[t["Animate 2"]]["inputs"], wf[t["Animate 3"]]["inputs"]
        self.assertEqual(a2["continue_motion"], [t["Decode"], 0])
        self.assertEqual(a2["video_frame_offset"], [t["Animate"], 5])
        self.assertEqual(a3["continue_motion"], [t["Decode 2"], 0])
        self.assertEqual(a3["video_frame_offset"], [t["Animate 2"], 5])
        self.assertEqual(wf[t["Sampler 2"]]["inputs"]["latent_image"], [t["Animate 2"], 2])
        self.assertEqual(wf[t["Drop Overlap 2"]]["inputs"]["batch_index"], [t["Animate 2"], 4])
        self.assertEqual(wf[t["Join 2"]]["inputs"]["image1"], [t["Decode"], 0])
        self.assertEqual(wf[t["Join 3"]]["inputs"]["image1"], [t["Join 2"], 0])
        self.assertEqual(wf[t["Trim Tail"]]["inputs"]["length"], 241)
        self.assertEqual(wf[t["Video"]]["inputs"]["images"], [t["Trim Tail"], 0])
        self.assertEqual(wf[t["Compare"]]["inputs"]["image1"], [t["Trim Tail"], 0])
        self.assertEqual(wf[t["Driving Trimmed"]]["inputs"]["length"], 241)

    def test_chunk_math(self):
        self.assertEqual(B.frames_for_chunks(1), 81)
        self.assertEqual(B.frames_for_chunks(2), 161)
        self.assertEqual(B.chunks_for_frames(121), 1)
        self.assertEqual(B.chunks_for_frames(161), 2)
        self.assertEqual(B.chunks_for_frames(1000), 3)
        self.assertEqual(B.chunks_for_frames(1000, max_chunks=5), 5)

    def test_finishing_chain(self):
        wf = B.build(chunks=2, finishing=True)
        t = B.by_title(wf)
        self.assertEqual(wf[t["Upscale Resize"]]["inputs"]["input"], [t["Trim Tail"], 0])
        self.assertEqual(wf[t["Upscale Sampler"]]["inputs"]["steps"], 1)
        self.assertEqual(wf[t["Interpolate"]]["inputs"]["images"], [t["Upscale Post"], 0])
        self.assertEqual(wf[t["Finish"]]["inputs"]["image"], [t["Interpolate"], 0])
        self.assertEqual(wf[t["Final Video"]]["inputs"]["audio"], [t["Driving Frames"], 1])
        self.assertEqual(wf[t["Save Final"]]["inputs"]["video"], [t["Final Video"], 0])
        # every link points at an existing node and output slot 0..5
        for nid, n in wf.items():
            for key, v in n["inputs"].items():
                if isinstance(v, list) and len(v) == 2 and isinstance(v[0], str):
                    self.assertIn(v[0], wf, f"{n['_meta']['title']}.{key} -> missing node {v[0]}")
        # the client can still address nodes by title
        self.assertEqual(resolve(wf, "Save Final.filename_prefix")[1], "filename_prefix")


if __name__ == "__main__":
    unittest.main()
