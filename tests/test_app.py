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


def sample_job(**overrides):
    job = {
        "id": "j1",
        "tier_settings": {"model": "distilled", "width": 480, "height": 848, "steps": 10},
        "options": {"chunks": 1, "finishing": False, "frames": 121, "fps": 24.0},
        "settings": {"identity_hold": 1.2, "motion_looseness": 0.7},
        "inputs": {"image": "app_j1_ref.png", "video": "app_j1_drive.mp4"},
        "prompts": {"character": "C", "motion": "M", "negative": "N"},
    }
    for k, v in overrides.items():
        job[k].update(v) if isinstance(v, dict) else job.__setitem__(k, v)
    return job


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
            self.assertIn(t["model"], ("distilled", "full"), name)
        self.assertTrue(p["negative_prompt"])
        self.assertGreater(p["finishing"]["minutes_per_clip"], 0)


class PromptAndWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.base = json.loads(J.WORKFLOW.read_text(encoding="utf-8"))

    def test_build_prompts(self):
        c, m = J.build_prompts("a woman in red.", {"background": "a beach", "motion": "dancing"})
        self.assertEqual(c, "Character appearance description: a woman in red. Background description: a beach.")
        self.assertEqual(m, "dancing")

    def test_build_workflow_sets_every_field(self):
        job = sample_job(tier_settings={"width": 720, "height": 1280, "steps": 12})
        wf = J.build_workflow(self.base, job, 42)
        by_title = {n["_meta"]["title"]: n["inputs"] for n in wf.values()}
        self.assertEqual(by_title["Reference Image"]["image"], "app_j1_ref.png")
        self.assertEqual(by_title["Driving Video"]["file"], "app_j1_drive.mp4")
        self.assertEqual(by_title["Animate"]["width"], 720)
        self.assertEqual(by_title["Resize Driving"]["resize_type.height"], 1280)
        self.assertEqual(by_title["Scheduler"]["steps"], 12)
        self.assertEqual(by_title["Animate"]["reference_image_strength"], 1.2)
        self.assertEqual(by_title["Animate"]["pose_end_percent"], 0.7)
        self.assertEqual(by_title["Sampler"]["noise_seed"], 42)
        self.assertEqual(by_title["Save"]["filename_prefix"], "motion_swap/j1/s42_result")
        self.assertEqual(by_title["Video"]["audio"], [next(k for k, n in wf.items() if n["_meta"]["title"] == "Driving Frames"), 1])
        self.assertNotIn("Save Final", by_title)
        self.assertNotEqual(self.base["21"]["inputs"]["noise_seed"], 42)  # base untouched

    def test_high_tier_passes_and_finishing(self):
        job = sample_job(tier_settings={"model": "full", "width": 720, "height": 1280, "steps": 6},
                         options={"chunks": 2, "finishing": True, "frames": 150, "fps": 25.0})
        wf = J.build_workflow(self.base, job, 7)
        by_title = {n["_meta"]["title"]: n["inputs"] for n in wf.values()}
        self.assertEqual(by_title["Model"]["unet_name"], J.B.FULL_MODEL)
        self.assertEqual(by_title["Distill LoRA"]["lora_name"], J.B.DISTILL_LORA)
        for t in ("Animate", "Animate 2"):
            self.assertEqual((by_title[t]["width"], by_title[t]["height"]), (720, 1280), t)
            self.assertEqual(by_title[t]["reference_image_strength"], 1.2, t)
        for t in ("Sampler", "Sampler 2"):
            self.assertEqual(by_title[t]["noise_seed"], 7, t)
        self.assertEqual(by_title["Trim Tail"]["length"], 150)       # min(161, 150 driving frames)
        self.assertEqual(by_title["Driving Trimmed"]["length"], 150)
        self.assertEqual(by_title["Final Video"]["fps"], 50.0)
        self.assertEqual(by_title["Save Final"]["filename_prefix"], "motion_swap/j1/s7_final")


class PreflightTests(unittest.TestCase):
    def test_blocks_on_short_video_and_passes_on_good_inputs(self):
        with tempfile.TemporaryDirectory() as d:
            img = Path(d) / "ref.png"
            Image.new("RGB", (1024, 1536), "gray").save(img)
            short = Path(d) / "short.mp4"
            make_video(short, 30)
            ok, msgs, info = J.preflight(str(img), str(short))
            self.assertFalse(ok)
            self.assertTrue(any("81" in m for m in msgs))
            good = Path(d) / "good.mp4"
            make_video(good, 170)
            ok, msgs, info = J.preflight(str(img), str(good))
            self.assertTrue(ok, msgs)
            self.assertEqual(info["frames"], 170)
            self.assertEqual([c[1] for c in J.length_choices(info["frames"])], [1, 2])
            self.assertTrue(any("2 pass" in m for m in msgs))

    def test_warns_on_small_landscape_image(self):
        with tempfile.TemporaryDirectory() as d:
            img = Path(d) / "ref.png"
            Image.new("RGB", (640, 400), "gray").save(img)
            vid = Path(d) / "v.mp4"
            make_video(vid, 90)
            ok, msgs, _ = J.preflight(str(img), str(vid))
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
                job = J.new_job(str(img), str(vid), "X", "a woman", "Studio", scene, "Standard", p["tiers"]["Standard"], 3, "test",
                                chunks=2, finishing=True, info={"frames": 170, "fps": 24.0})
                self.assertEqual(len(job["runs"]), 3)
                self.assertEqual(job["options"], {"chunks": 2, "finishing": True, "frames": 170, "fps": 24.0})
                self.assertTrue((J.COMFY_INPUT / job["inputs"]["image"]).exists())
                self.assertEqual(J.load_job(job["id"])["id"], job["id"])
                self.assertEqual(J.list_jobs()[0]["id"], job["id"])
                # (6 min * 2 passes + 3 min finishing * 2 passes) * 3 seeds = 54 min
                self.assertAlmostEqual(job["cost"]["estimate_minutes"], 54.0)
            finally:
                J.COMFY_INPUT, J.JOBS_DIR = old_in, old_jobs

    def test_resume_pending_requeues_unfinished_jobs_oldest_first(self):
        with tempfile.TemporaryDirectory() as d:
            old_jobs = J.JOBS_DIR
            J.JOBS_DIR = Path(d) / "jobs"
            try:
                def job(jid, status, runs):
                    j = {"id": jid, "status": status, "runs": [{"seed": i, "status": s, "outputs": [], "error": None, "seconds": 1.0} for i, s in enumerate(runs)]}
                    J.save_job(j)
                for jid, st, runs in [("20260101-000001-a", "done", ["done"]), ("20260101-000002-b", "running", ["done", "running"]),
                                      ("20260101-000003-c", "queued", ["queued", "queued"]), ("20260101-000004-d", "error", ["error"])]:
                    job(jid, st, runs)
                submitted = []
                self.assertEqual(J.resume_pending(submitted.append), 2)
                self.assertEqual(submitted, ["20260101-000002-b", "20260101-000003-c"])
                b = J.load_job("20260101-000002-b")
                self.assertEqual(b["status"], "queued")
                self.assertEqual([r["status"] for r in b["runs"]], ["done", "queued"])  # finished seed kept
                self.assertEqual(b["resumed"], 1)
            finally:
                J.JOBS_DIR = old_jobs

    def test_make_sheets_from_compare_clip(self):
        with tempfile.TemporaryDirectory() as d:
            old_out, old_jobs = J.COMFY_OUTPUT, J.JOBS_DIR
            J.COMFY_OUTPUT, J.JOBS_DIR = Path(d) / "output", Path(d) / "jobs"
            try:
                (J.COMFY_OUTPUT / "motion_swap" / "j9").mkdir(parents=True)
                make_video(J.COMFY_OUTPUT / "motion_swap" / "j9" / "s5_compare_00001_.mp4", 40, size=(192, 96))
                job = {"id": "j9", "runs": [{"seed": 5, "status": "done", "outputs": ["motion_swap/j9/s5_compare_00001_.mp4"], "sheets": []}]}
                J.make_sheets(job)
                self.assertEqual(len(job["runs"][0]["sheets"]), 2)
                self.assertTrue(all(Path(s).exists() for s in job["runs"][0]["sheets"]))
            finally:
                J.COMFY_OUTPUT, J.JOBS_DIR = old_out, old_jobs


if __name__ == "__main__":
    unittest.main()
