"""Tests for the team app's job engine. Run: .venv\\Scripts\\python -m unittest discover tests"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import av  # noqa: E402
import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

import jobs as J  # noqa: E402


def make_video(path, frames, fps=24, size=(64, 96)):
    with av.open(str(path), "w") as c:
        s = c.add_stream("libx264", rate=fps)
        s.width, s.height, s.pix_fmt = size[0], size[1], "yuv420p"
        for i in range(frames):
            arr = np.full((size[1], size[0], 3), i % 255, dtype=np.uint8)
            for pkt in s.encode(av.VideoFrame.from_ndarray(arr, format="rgb24")):
                c.mux(pkt)
        for pkt in s.encode():
            c.mux(pkt)


class PresetTests(unittest.TestCase):
    def test_presets_have_required_fields(self):
        p = J.load_presets()
        self.assertGreaterEqual(len(p["scenes"]), 6)
        for name, s in p["scenes"].items():
            self.assertTrue(s["background"].strip(), name)
            self.assertTrue(s["motion"].strip(), name)
        for name, t in p["tiers"].items():
            self.assertEqual(t["width"] % 16, 0, name)
            self.assertEqual(t["height"] % 16, 0, name)
        self.assertTrue(p["negative_prompt"])


class PromptAndWorkflowTests(unittest.TestCase):
    def test_build_prompts(self):
        c, m = J.build_prompts("a woman in red.", {"background": "a beach", "motion": "dancing"})
        self.assertEqual(c, "Character appearance description: a woman in red. Background description: a beach.")
        self.assertEqual(m, "dancing")

    def test_build_workflow_sets_every_field(self):
        base = json.loads(J.WORKFLOW.read_text(encoding="utf-8"))
        job = {
            "id": "j1",
            "tier_settings": {"width": 720, "height": 1280, "steps": 12},
            "settings": {"identity_hold": 1.2, "motion_looseness": 0.7},
            "inputs": {"image": "app_j1_ref.png", "video": "app_j1_drive.mp4"},
            "prompts": {"character": "C", "motion": "M", "negative": "N"},
        }
        wf = J.build_workflow(base, job, 42)
        by_title = {n["_meta"]["title"]: n["inputs"] for n in wf.values()}
        self.assertEqual(by_title["Reference Image"]["image"], "app_j1_ref.png")
        self.assertEqual(by_title["Driving Video"]["file"], "app_j1_drive.mp4")
        self.assertEqual(by_title["Animate"]["width"], 720)
        self.assertEqual(by_title["Animate"]["height"], 1280)
        self.assertEqual(by_title["Resize Driving"]["resize_type.height"], 1280)
        self.assertEqual(by_title["Scheduler"]["steps"], 12)
        self.assertEqual(by_title["Animate"]["reference_image_strength"], 1.2)
        self.assertEqual(by_title["Animate"]["pose_end_percent"], 0.7)
        self.assertEqual(by_title["Sampler"]["noise_seed"], 42)
        self.assertEqual(by_title["Save"]["filename_prefix"], "motion_swap/j1/s42_result")
        # the base workflow must not be mutated
        self.assertNotEqual(json.loads(J.WORKFLOW.read_text(encoding="utf-8"))["21"]["inputs"]["noise_seed"], 42)


class PreflightTests(unittest.TestCase):
    def test_blocks_on_short_video_and_passes_on_good_inputs(self):
        with tempfile.TemporaryDirectory() as d:
            img = Path(d) / "ref.png"
            Image.new("RGB", (1024, 1536), "gray").save(img)
            short = Path(d) / "short.mp4"
            make_video(short, 30)
            ok, msgs = J.preflight(str(img), str(short))
            self.assertFalse(ok)
            self.assertTrue(any("81" in m for m in msgs))
            good = Path(d) / "good.mp4"
            make_video(good, 100)
            ok, msgs = J.preflight(str(img), str(good))
            self.assertTrue(ok, msgs)
            self.assertTrue(any(m.startswith("Video OK") for m in msgs))

    def test_warns_on_small_landscape_image(self):
        with tempfile.TemporaryDirectory() as d:
            img = Path(d) / "ref.png"
            Image.new("RGB", (640, 400), "gray").save(img)
            vid = Path(d) / "v.mp4"
            make_video(vid, 90)
            ok, msgs = J.preflight(str(img), str(vid))
            self.assertTrue(ok)
            self.assertTrue(any("small" in m for m in msgs))
            self.assertTrue(any("landscape" in m for m in msgs))

    def test_missing_inputs(self):
        self.assertFalse(J.preflight(None, None)[0])


class JobStorageTests(unittest.TestCase):
    def test_new_job_stages_inputs_and_saves(self):
        with tempfile.TemporaryDirectory() as d:
            old_in, old_jobs = J.COMFY_INPUT, J.JOBS_DIR
            J.COMFY_INPUT, J.JOBS_DIR = Path(d) / "input", Path(d) / "jobs"
            try:
                img = Path(d) / "ref.png"
                Image.new("RGB", (64, 64), "gray").save(img)
                vid = Path(d) / "v.mp4"
                make_video(vid, 5)
                p = J.load_presets()
                scene = p["scenes"]["Studio"]
                job = J.new_job(str(img), str(vid), "X", "a woman", "Studio", scene, "Standard", p["tiers"]["Standard"], 3, "test")
                self.assertEqual(len(job["runs"]), 3)
                self.assertTrue((J.COMFY_INPUT / job["inputs"]["image"]).exists())
                self.assertTrue((J.COMFY_INPUT / job["inputs"]["video"]).exists())
                self.assertEqual(J.load_job(job["id"])["id"], job["id"])
                self.assertEqual(J.list_jobs()[0]["id"], job["id"])
                self.assertGreater(job["cost"]["estimate_usd"], 0)
            finally:
                J.COMFY_INPUT, J.JOBS_DIR = old_in, old_jobs


if __name__ == "__main__":
    unittest.main()
