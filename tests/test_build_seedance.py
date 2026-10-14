import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import build_seedance as SD  # noqa: E402
from comfy_client import queue_prompt  # noqa: E402


class SeedanceBuildTests(unittest.TestCase):
    def test_graph(self):
        wf = SD.build(prompt="P", resolution="720p", ratio="9:16", duration=7.4, seed=5, task_type="edit")
        t = SD.by_title(wf)
        s = wf[t["Seedance"]]["inputs"]
        self.assertEqual(s["model.task_type"], "edit")
        self.assertEqual(s["model"], "Seedance 2.5")
        with self.assertRaises(ValueError):
            SD.build(task_type="remix")
        self.assertEqual(s["model.reference_images.image_1"], [t["Reference Image"], 0])
        self.assertEqual(s["model.reference_videos.video_1"], [t["Driving Video"], 0])
        self.assertEqual(s["model.duration"], 7)
        self.assertEqual(wf[t["Save"]]["inputs"]["video"], [t["Seedance"], 0])
        self.assertNotIn("GetVideoComponents", {n["class_type"] for n in wf.values()})  # never decode whole clips in ComfyUI
        for n in wf.values():
            for k, v in n["inputs"].items():
                if isinstance(v, list) and len(v) == 2 and isinstance(v[0], str):
                    self.assertIn(v[0], wf, f"{n['_meta']['title']}.{k}")

    def test_duration_clamp_and_validation(self):
        self.assertEqual(SD.clamp_duration(1), 4)
        self.assertEqual(SD.clamp_duration(99), 30)
        with self.assertRaises(ValueError):
            SD.build(resolution="4k")
        with self.assertRaises(ValueError):
            SD.build(ratio="2:1")

    def test_queue_prompt_sends_extra_data(self):
        import comfy_client as CC

        captured = {}

        def fake_request(server, path, payload=None, timeout=30):
            captured.update(payload)
            return {"prompt_id": "x"}

        old = CC.request
        CC.request = fake_request
        try:
            queue_prompt("http://s", {"1": {}}, "cid", {"api_key_comfy_org": "k"})
            self.assertEqual(captured["extra_data"], {"api_key_comfy_org": "k"})
            captured.clear()
            queue_prompt("http://s", {"1": {}}, "cid")
            self.assertNotIn("extra_data", captured)
        finally:
            CC.request = old


if __name__ == "__main__":
    unittest.main()
