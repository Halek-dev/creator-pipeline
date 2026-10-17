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


class ScailEngineTests(unittest.TestCase):
    def test_scail_workflow_from_job(self):
        job = sample_job(options={"chunks": 2, "finishing": True, "frames": 150, "fps": 25.0, "video_w": 720, "video_h": 1280})
        job["engine"] = "scail"
        wf = J.build_workflow(None, job, 9)
        by_title = {n["_meta"]["title"]: n["inputs"] for n in wf.values()}
        self.assertEqual(by_title["Reference Image"]["image"], "app_j1_ref.png")
        self.assertEqual(by_title["Driving Video"]["file"], "app_j1_drive.mp4")
        self.assertTrue(by_title["Character Prompt"]["text"].startswith("A video of C."))  # long SCAIL paragraph built from the parts
        self.assertEqual((by_title["Resize Driving"]["resize_type.width"], by_title["Resize Driving"]["resize_type.height"]), (512, 896))
        self.assertEqual(by_title["Scheduler"]["steps"], J.SCAIL_TIERS["Standard"]["steps"])
        job["tier"] = "High"
        hi = {n["_meta"]["title"]: n["inputs"] for n in J.build_workflow(None, job, 9).values()}
        self.assertEqual((hi["Resize Driving"]["resize_type.width"], hi["Resize Driving"]["resize_type.height"]), (704, 1280))
        self.assertEqual(hi["Distill LoRA"]["strength_model"], J.SCAIL_TIERS["High"]["strength"])
        job["tier"] = "Standard"
        for t in ("Animate", "Animate 2"):
            self.assertEqual(by_title[t]["pose_end"], 0.7, t)
            self.assertTrue(by_title[t]["replacement_mode"])
        for t in ("Sampler", "Sampler 2"):
            self.assertEqual(by_title[t]["noise_seed"], 9, t)
        self.assertEqual(by_title["Trim Tail"]["length"], 150)  # min(157, 150 driving frames)
        self.assertEqual(by_title["Final Video"]["fps"], 50.0)
        self.assertEqual(by_title["Save Final"]["filename_prefix"], "motion_swap/j1/s9_final")

    def test_scail_landscape_and_length_choices(self):
        job = sample_job(options={"chunks": 1, "finishing": False, "frames": 100, "fps": 24.0, "video_w": 1280, "video_h": 720})
        job["engine"] = "scail"
        wf = J.build_workflow(None, job, 1)
        by_title = {n["_meta"]["title"]: n["inputs"] for n in wf.values()}
        self.assertEqual((by_title["Resize Driving"]["resize_type.width"], by_title["Resize Driving"]["resize_type.height"]), (896, 512))
        self.assertEqual([c[1] for c in J.length_choices(160, "scail")], [1, 2])   # 157 needed for 2 segments
        self.assertEqual([c[1] for c in J.length_choices(160, "wan")], [1])        # 161 needed for 2 passes


class ScailReferencePadTests(unittest.TestCase):
    def test_pad_reference_matches_video_aspect_without_cropping(self):
        with tempfile.TemporaryDirectory() as d:
            src, dst = Path(d) / "ref.png", Path(d) / "pad.png"
            Image.new("RGB", (1000, 1000), "gray").save(src)          # square photo
            size = J.pad_reference(src, dst, 720, 1280)                 # portrait 9:16 video
            self.assertEqual(size, (1000, 1778))                        # height added, nothing cropped
            with Image.open(dst) as im:
                self.assertEqual(im.getpixel((500, 10)), (0, 0, 0))     # black padding
                self.assertEqual(im.getpixel((500, 889)), (128, 128, 128))
            size = J.pad_reference(src, dst, 1280, 720)                 # landscape video: width added
            self.assertEqual(size, (1778, 1000))

    def test_new_job_pads_scail_reference(self):
        with tempfile.TemporaryDirectory() as d:
            old = J.COMFY_INPUT, J.JOBS_DIR
            J.COMFY_INPUT, J.JOBS_DIR = Path(d) / "input", Path(d) / "jobs"
            try:
                img = Path(d) / "ref.png"; Image.new("RGB", (640, 800), "gray").save(img)
                vid = Path(d) / "v.mp4"; make_video(vid, 5)
                p = J.load_presets()
                job = J.new_job(str(img), str(vid), "X", "a", "Studio", p["scenes"]["Studio"], "Standard", p["tiers"]["Standard"], 1,
                                info={"frames": 170, "fps": 24.0, "video_w": 720, "video_h": 1280}, engine="scail")
                self.assertTrue((J.COMFY_INPUT / job["inputs"]["image_scail"]).exists())
                wf = J.build_workflow(None, job, 1)
                self.assertEqual({n["_meta"]["title"]: n["inputs"] for n in wf.values()}["Reference Image"]["image"], job["inputs"]["image_scail"])
            finally:
                J.COMFY_INPUT, J.JOBS_DIR = old


class SeedanceEngineTests(unittest.TestCase):
    def test_seedance_workflow_from_job(self):
        job = sample_job(options={"chunks": 1, "finishing": False, "frames": 121, "fps": 24.0, "video_w": 720, "video_h": 1280})
        job["engine"], job["tier"] = "seedance", "High"
        wf = J.build_workflow(None, job, 3)
        by_title = {n["_meta"]["title"]: n["inputs"] for n in wf.values()}
        s = by_title["Seedance"]
        self.assertEqual(s["model"], "Seedance 2.5")
        self.assertEqual(s["model.resolution"], "1080p")
        self.assertEqual(s["model.ratio"], "9:16")
        self.assertEqual(s["model.duration"], 5)          # 121 frames / 24 fps, rounded, clamped to 4-30
        self.assertEqual(s["model.task_type"], "edit")  # default: swap the character into the driving video
        self.assertEqual(s["seed"], 3)
        self.assertTrue(s["model.prompt"].startswith("In @Video1, replace the person with the character from @Image1"))
        job["options"]["seedance_mode"], job["tier"] = "reference", "Fast"
        s2 = {n["_meta"]["title"]: n["inputs"] for n in J.build_workflow(None, job, 3).values()}["Seedance"]
        self.assertEqual(s2["model.task_type"], "reference")
        self.assertEqual(s2["model"], "Seedance 2.5 Draft")
        self.assertIn("same dance choreography", s2["model.prompt"])
        self.assertIn("@Video1", s2["model.prompt"])
        self.assertEqual(by_title["Reference Image"]["image"], "app_j1_ref.png")
        self.assertEqual(by_title["Save"]["filename_prefix"], "motion_swap/j1/s3_result")
        self.assertEqual([c[0] for c in J.length_choices(121, "seedance", 24.0)], ["5 s (whole clip)"])

    def test_seedance_estimate_uses_credits_not_gpu_time(self):
        p = J.load_presets()
        minutes, usd = J.seedance_estimate(p, "Standard", 5.0, 2)
        self.assertAlmostEqual(usd, round((5 + 5) * 21600 / 1000 * 1.931 * 2 / p["seedance"]["credits_per_usd"], 2))
        _, credits, usd, measured = J.seedance_credits(p, "High", 13.9, 1, "edit")
        self.assertAlmostEqual(credits, 2850, delta=20)  # the measured 14 s 1080p render billed 2,800
        self.assertGreater(credits, J.seedance_cap(p))  # that render is now refused by default
        _, c4, _, _ = J.seedance_credits(p, "High", 4.0, 1, "edit")
        self.assertLess(c4, J.seedance_cap(p))  # a 4 s 1080p swap (about 820) stays allowed
        self.assertEqual(minutes, p["seedance"]["minutes_per_clip"] * 2)


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
                self.assertEqual({k: job["options"][k] for k in ("chunks", "finishing", "frames", "fps")}, {"chunks": 2, "finishing": True, "frames": 170, "fps": 24.0})
                self.assertEqual(job["engine"], "wan")
                self.assertTrue((J.COMFY_INPUT / job["inputs"]["image"]).exists())
                self.assertEqual(J.load_job(job["id"])["id"], job["id"])
                self.assertEqual(J.list_jobs()[0]["id"], job["id"])
                # (tier minutes * 2 passes + finishing minutes * 2 passes) * 3 seeds
                tier, fin = p["tiers"]["Standard"]["minutes_per_clip"], p["finishing"]["minutes_per_clip"]
                self.assertAlmostEqual(job["cost"]["estimate_minutes"], (tier * 2 + fin * 2) * 3)
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


class ReviewFixTests(unittest.TestCase):
    """Behaviours added by the Phase 3 code review."""

    def test_preflight_messages_follow_the_engine(self):
        with tempfile.TemporaryDirectory() as d:
            img = Path(d) / "ref.png"
            Image.new("RGB", (1024, 1600), "gray").save(img)
            vid = Path(d) / "wide.mp4"
            make_video(vid, 170, size=(96, 64))
            for engine, needle in [("wan", "cropped"), ("scail", "896×512"), ("seedance", "16:9")]:
                ok, msgs, info = J.preflight(str(img), str(vid), engine)
                self.assertTrue(ok, msgs)
                self.assertTrue(any(needle in m for m in msgs), (engine, msgs))
            ok, msgs, _ = J.preflight(str(img), str(vid), "scail")
            self.assertIn("Up to 2 pass(es)", " ".join(msgs))  # 170 frames: SCAIL 2 segments (157), Wan 2 chunks (161)

    def test_render_settings_per_engine(self):
        wan = sample_job()
        self.assertIn("480×848", J.render_settings(wan))
        scail = sample_job(engine="scail", options={"video_w": 1920, "video_h": 1080})
        self.assertIn("896×512", J.render_settings(scail))
        self.assertNotIn("480×848", J.render_settings(scail))
        seed = sample_job(engine="seedance", tier="High", options={"video_w": 1080, "video_h": 1920})
        self.assertIn("1080p", J.render_settings(seed))
        self.assertIn("swap into the video", J.render_settings(seed))

    def test_new_job_clamps_seeds_and_passes(self):
        with tempfile.TemporaryDirectory() as d:
            old_in, old_jobs = J.COMFY_INPUT, J.JOBS_DIR
            J.COMFY_INPUT, J.JOBS_DIR = Path(d) / "input", Path(d) / "jobs"
            try:
                img = Path(d) / "ref.png"
                Image.new("RGB", (64, 64), "gray").save(img)
                vid = Path(d) / "v.mp4"
                make_video(vid, 5)
                p = J.load_presets()
                job = J.new_job(str(img), str(vid), "X", "a", "Studio", p["scenes"]["Studio"], "Standard", p["tiers"]["Standard"], 0, chunks=9)
                self.assertEqual(len(job["runs"]), 1)
                self.assertEqual(job["options"]["chunks"], J.MAX_CHUNKS)
            finally:
                J.COMFY_INPUT, J.JOBS_DIR = old_in, old_jobs

    def test_list_jobs_cache_notices_changes(self):
        with tempfile.TemporaryDirectory() as d:
            old_jobs = J.JOBS_DIR
            J.JOBS_DIR = Path(d) / "jobs"
            try:
                J.save_job({"id": "20260101-000001-a", "status": "queued", "runs": []})
                self.assertEqual(J.list_jobs()[0]["status"], "queued")
                import os, time
                j = J.load_job("20260101-000001-a")
                j["status"] = "done"
                J.save_job(j)
                f = J.JOBS_DIR / "20260101-000001-a" / "job.json"
                os.utime(f, ns=(time.time_ns() + 10**9, time.time_ns() + 10**9))  # force a different mtime on coarse filesystems
                self.assertEqual(J.list_jobs()[0]["status"], "done")
            finally:
                J.JOBS_DIR = old_jobs

    def test_resume_pending_closes_jobs_whose_seeds_all_finished(self):
        with tempfile.TemporaryDirectory() as d:
            old_jobs = J.JOBS_DIR
            J.JOBS_DIR = Path(d) / "jobs"
            try:
                J.save_job({"id": "20260101-000001-a", "status": "running", "cost": {"actual_usd": 0.42},
                            "runs": [{"seed": 1, "status": "done", "outputs": [], "error": None, "seconds": 300}]})
                submitted = []
                self.assertEqual(J.resume_pending(submitted.append), 0)
                self.assertEqual(submitted, [])
                j = J.load_job("20260101-000001-a")
                self.assertEqual(j["status"], "done")
                self.assertEqual(j["cost"]["actual_usd"], 0.42)  # cost of a finished job is never rewritten
            finally:
                J.JOBS_DIR = old_jobs

    def test_runner_wait_returns_none_when_comfy_lost_the_prompt(self):
        r = J.Runner(server="http://test")
        r.POLL_SECONDS, r.LOST_CHECK_EVERY = 0, 2
        calls = {"poll": 0}

        def fake_poll(server, pid):
            calls["poll"] += 1
            if calls["poll"] == 1:
                raise J.ComfyError("connection refused")  # one transient error must not fail the run
            return None

        old = J.poll, J.queue_contains
        J.poll, J.queue_contains = fake_poll, lambda server, pid: False
        try:
            self.assertIsNone(r.wait("p1"))
            self.assertGreaterEqual(calls["poll"], 2)
        finally:
            J.poll, J.queue_contains = old

    def test_runner_wait_gives_up_after_many_errors(self):
        r = J.Runner(server="http://test")
        r.POLL_SECONDS, r.MAX_POLL_ERRORS = 0, 3

        def fake_poll(server, pid):
            raise J.ComfyError("down")

        old = J.poll
        J.poll = fake_poll
        try:
            with self.assertRaises(J.ComfyError):
                r.wait("p1")
        finally:
            J.poll = old


class VramGuardTests(unittest.TestCase):
    def test_needed_vram_per_engine_and_tier(self):
        p = J.load_presets()
        self.assertEqual(J.vram_needed_gib(p, "wan", "Standard"), 22)
        self.assertEqual(J.vram_needed_gib(p, "wan", "High"), 46)
        self.assertEqual(J.vram_needed_gib(p, "scail", "Standard"), 28)
        self.assertEqual(J.vram_needed_gib(p, "scail", "High"), 40)
        self.assertEqual(J.vram_needed_gib(p, "wan", "Fast", finishing=True), 22)   # chunked Finish fits under the main model's need
        self.assertEqual(J.vram_needed_gib(p, "seedance", "High"), 0)

    def test_gpu_info_unreachable_is_none(self):
        self.assertEqual(J.gpu_info("http://127.0.0.1:1", timeout=1), (None, None))


class CostSummaryTests(unittest.TestCase):
    def test_monthly_summary_groups_sessions_and_engines(self):
        jobs = [
            {"id": "a", "status": "done", "engine": "wan", "started": "2026-10-13T10:00:00", "finished": "2026-10-13T10:10:00",
             "cost": {"actual_usd": 0.30, "estimate_usd": 0.2}, "runs": [{"status": "done", "seconds": 600}]},
            {"id": "b", "status": "partial", "engine": "scail", "started": "2026-10-13T11:00:00", "finished": "2026-10-13T11:30:00",
             "cost": {"actual_usd": None, "estimate_usd": 0.5}, "runs": [{"status": "done", "seconds": 900}, {"status": "error", "seconds": 900}]},
            {"id": "c", "status": "done", "engine": "seedance", "started": "2026-10-13T12:00:00", "finished": "2026-10-13T12:03:00",
             "cost": {"actual_usd": 2.1, "estimate_usd": 2.1}, "runs": [{"status": "done", "seconds": 180}]},
            {"id": "d", "status": "done", "engine": "wan", "started": "2026-09-02T09:00:00", "finished": "2026-09-02T09:06:00",
             "cost": {"actual_usd": 0.1, "estimate_usd": 0.1}, "runs": [{"status": "done", "seconds": 360}]},
            {"id": "e", "status": "running", "engine": "wan", "started": "2026-10-14T09:00:00", "cost": {"actual_usd": None, "estimate_usd": 1}, "runs": []},
        ]
        rows = J.cost_summary(jobs, hourly=2.0)
        self.assertEqual([r["month"] for r in rows], ["2026-10", "2026-09"])
        oct_ = rows[0]
        self.assertEqual((oct_["jobs"], oct_["clips"], oct_["seedance_jobs"]), (3, 3, 1))
        self.assertAlmostEqual(oct_["render_minutes"], 40.0)          # 600 + 1800 seconds
        self.assertAlmostEqual(oct_["render_usd"], 0.30 + 1800 / 3600 * 2.0)  # actual where known, else time x rate
        self.assertAlmostEqual(oct_["seedance_usd"], 2.1)
        self.assertEqual(oct_["sessions"], 1)                         # one day of GPU work (Seedance does not need the pod)
        span = 90  # 10:00 -> 11:30
        self.assertAlmostEqual(oct_["pod_hours"], (span + J.IDLE_ALLOWANCE_MIN + J.SESSION_OVERHEAD_MIN) / 60, places=2)
        self.assertEqual(len(J.cost_rows(rows)[0]), len(J.COST_HEADERS))


class CompareClipTests(unittest.TestCase):
    def test_make_compare_streams_a_side_by_side_and_sheets_use_it(self):
        with tempfile.TemporaryDirectory() as d:
            old = J.COMFY_INPUT, J.COMFY_OUTPUT, J.JOBS_DIR
            J.COMFY_INPUT, J.COMFY_OUTPUT, J.JOBS_DIR = Path(d) / "input", Path(d) / "output", Path(d) / "jobs"
            try:
                J.COMFY_INPUT.mkdir()
                (J.COMFY_OUTPUT / "motion_swap" / "j7").mkdir(parents=True)
                make_video(J.COMFY_INPUT / "drive.mp4", 90, fps=30, size=(96, 160))   # 3 s at 30 fps
                make_video(J.COMFY_OUTPUT / "motion_swap" / "j7" / "s3_result_00001_.mp4", 48, fps=24, size=(128, 224))  # 2 s at 24 fps
                job = {"id": "j7", "inputs": {"video": "drive.mp4"}, "runs": [{"seed": 3, "status": "done", "outputs": ["motion_swap/j7/s3_result_00001_.mp4"], "sheets": []}]}
                J.make_compare(job)
                rel = "motion_swap/j7/s3_compare_app.mp4"
                self.assertIn(rel, job["runs"][0]["outputs"])
                with av.open(str(J.output_path(rel))) as c:
                    s = c.streams.video[0]
                    self.assertEqual(s.height, 640)
                    self.assertEqual(s.width, round(128 * 640 / 224) // 2 * 2 + round(96 * 640 / 160) // 2 * 2)  # 366 + 384
                    self.assertEqual(float(s.average_rate), 24.0)
                    self.assertEqual(s.frames, 48)
                J.make_sheets(job)
                self.assertEqual(len(job["runs"][0]["sheets"]), 2)
                self.assertTrue(Path(job["runs"][0]["poster"]).exists())
            finally:
                J.COMFY_INPUT, J.COMFY_OUTPUT, J.JOBS_DIR = old


if __name__ == "__main__":
    unittest.main()
