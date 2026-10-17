"""The endpoint backend against a fake RunPod API, the worker handler against a fake ComfyUI, and the Runner's
backend seam (a job rendered end to end through a fake endpoint, outputs fetched through a fake volume)."""
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for d in ("app", "tools", "worker"):
    sys.path.insert(0, str(ROOT / d))

import backends as BK  # noqa: E402
import jobs as J  # noqa: E402
import storage as ST  # noqa: E402


class FakeApi:
    """Stands in for EndpointBackend._call."""

    def __init__(self):
        self.jobs, self.calls = {}, []

    def __call__(self, method, path, body=None, timeout=60):
        self.calls.append((method, path, body))
        if path == "/run":
            jid = f"rp-{len(self.jobs) + 1}"
            self.jobs[jid] = {"id": jid, "status": "IN_QUEUE", "input": body["input"], "policy": body["policy"]}
            return {"id": jid, "status": "IN_QUEUE"}
        if path.startswith("/status/"):
            jid = path.split("/")[-1]
            if jid not in self.jobs:
                raise LookupError("not found")
            return self.jobs[jid]
        if path == "/health":
            return {"workers": {"idle": 0, "running": 1}, "jobs": {"inQueue": 0}}
        return {}


class EndpointBackendTests(unittest.TestCase):
    def setUp(self):
        self.be = BK.EndpointBackend("ep123", "key")
        self.api = FakeApi()
        self.be._call = self.api

    def test_submit_sets_timeout_and_seed(self):
        wf = {"1": {"class_type": "SamplerCustom", "_meta": {"title": "Sampler"}, "inputs": {"noise_seed": 77}}}
        h = self.be.submit(wf, "20260101-000001-a:client", {"api_key_comfy_org": "x"}, timeout_s=300)
        self.assertEqual(h, "rp-1")
        sent = self.api.jobs["rp-1"]
        self.assertEqual(sent["policy"]["executionTimeout"], BK.EndpointBackend.MIN_TIMEOUT_S * 1000)  # floor wins over 300 s
        self.assertEqual(sent["input"]["job_id"], "20260101-000001-a")
        self.assertEqual(sent["input"]["seed"], 77)
        self.assertEqual(sent["input"]["extra_data"], {"api_key_comfy_org": "x"})

    def test_poll_maps_states(self):
        h = self.be.submit({"1": {"class_type": "X", "_meta": {"title": "x"}, "inputs": {}}}, "j:c", None, 5000)
        self.assertIsNone(self.be.poll(h))                                   # IN_QUEUE
        self.api.jobs[h]["status"] = "IN_PROGRESS"
        self.assertIsNone(self.be.poll(h))
        self.api.jobs[h].update(status="COMPLETED", executionTime=4200, delayTime=90000,
                                output={"status": {"status_str": "success"}, "outputs": {"9": {"images": [{"filename": "s77_result_00001_.mp4", "subfolder": "motion_swap/j", "type": "output"}]}}})
        entry = self.be.poll(h)
        self.assertEqual(entry["status"]["status_str"], "success")
        self.assertEqual(entry["_endpoint"]["execution_ms"], 4200)
        self.assertEqual(J.collect_outputs(entry), ["motion_swap/j/s77_result_00001_.mp4"])
        self.api.jobs[h].update(status="FAILED", output={"error": "boom"})
        err = self.be.poll(h)
        self.assertEqual(err["status"]["status_str"], "error")
        self.assertIn("boom", J.error_message(err))
        self.assertFalse(self.be.lost(h))
        self.assertTrue(self.be.lost("rp-nope"))

    def test_make_backend_defaults_to_pod(self):
        self.assertEqual(BK.make_backend("pod", "http://x").name, "pod")
        with self.assertRaises(ValueError):
            BK.EndpointBackend("", "")


class HandlerTests(unittest.TestCase):
    def test_handler_queues_polls_and_records(self):
        import handler as H

        with tempfile.TemporaryDirectory() as d:
            H.DATA = Path(d)
            H.wait_for_comfy = lambda timeout=0: {"devices": [{"name": "fake", "vram_total": 1}]}
            H.queue_prompt = lambda server, wf, cid, extra: "pid-1"
            entry = {"status": {"status_str": "success"}, "outputs": {"2": {"images": [{"filename": "a.mp4", "subfolder": "motion_swap/j1", "type": "output"}]}}}
            polls = iter([None, entry])
            H.poll = lambda server, pid: next(polls)
            H.time.sleep = lambda s: None
            out = H.handler({"id": "rp", "input": {"workflow": {"1": {"class_type": "X", "inputs": {}}}, "job_id": "j1", "seed": 5}})
            self.assertEqual(out["status"]["status_str"], "success")
            self.assertEqual(out["seed"], 5)
            self.assertTrue((Path(d) / "output" / "motion_swap" / "j1" / "s5_worker.json").exists())
            self.assertIn("error", H.handler({"id": "rp", "input": {}}))


class RunnerEndpointTests(unittest.TestCase):
    def test_job_renders_through_endpoint_and_fetches_outputs(self):
        with tempfile.TemporaryDirectory() as d:
            old = J.COMFY_INPUT, J.COMFY_OUTPUT, J.JOBS_DIR
            J.COMFY_INPUT, J.COMFY_OUTPUT, J.JOBS_DIR = Path(d) / "input", Path(d) / "output", Path(d) / "jobs"
            J.COMFY_INPUT.mkdir(); J.COMFY_OUTPUT.mkdir()
            be = BK.EndpointBackend("ep", "k"); api = FakeApi(); be._call = api
            r = J.Runner(backend=be); r.POLL_SECONDS = 0
            uploaded, downloaded = [], []
            ST.configured = lambda: True
            ST.exists = lambda key: False
            ST.upload = lambda local, key: uploaded.append(key)
            def fake_download(key, local):
                Path(local).parent.mkdir(parents=True, exist_ok=True); Path(local).write_bytes(b"x"); downloaded.append(key)
            ST.download = fake_download
            # a queued job whose inputs are staged locally
            (J.COMFY_INPUT / "app_j_ref.png").write_bytes(b"i"); (J.COMFY_INPUT / "app_j_drive.mp4").write_bytes(b"v")
            job = {"id": "20260101-000001-j", "status": "queued", "engine": "wan", "tier": "Standard", "character": "c", "scene": "s",
                   "tier_settings": {"model": "distilled", "width": 480, "height": 848, "steps": 10}, "options": {"chunks": 1, "finishing": False, "frames": 81, "fps": 24.0},
                   "settings": {"identity_hold": 1.0, "motion_looseness": 1.0}, "inputs": {"image": "app_j_ref.png", "video": "app_j_drive.mp4"},
                   "prompts": {"character": "C", "motion": "M", "negative": "N"}, "runs": [{"seed": 3, "status": "queued", "outputs": [], "error": None, "seconds": None, "sheets": []}],
                   "workflow_version": "t", "cost": {"estimate_usd": 0.1, "estimate_minutes": 6.0, "actual_usd": None}, "note": ""}
            J.save_job(job)
            # the fake endpoint completes the job on the second status call
            calls = {"n": 0}
            real_call = api
            def call(method, path, body=None, timeout=60):
                res = real_call(method, path, body, timeout)
                if path.startswith("/status/"):
                    calls["n"] += 1
                    if calls["n"] >= 2:
                        res.update(status="COMPLETED", executionTime=360000, delayTime=60000,
                                   output={"status": {"status_str": "success"}, "outputs": {"5": {"images": [{"filename": "s3_result_00001_.mp4", "subfolder": "motion_swap/20260101-000001-j", "type": "output"}]}}})
                return res
            be._call = call
            J.make_compare = lambda job: None; J.make_sheets = lambda job: None
            try:
                r.render(job["id"])
                done = J.load_job(job["id"])
                self.assertEqual(done["status"], "done")
                self.assertEqual(done["backend"], "endpoint")
                self.assertEqual(done["runs"][0]["execution_seconds"], 360.0)
                self.assertEqual(done["runs"][0]["delay_seconds"], 60.0)
                self.assertEqual(done["cost"]["actual_usd"], round(360 / 3600 * J.GPU_HOURLY_USD, 2))  # billed seconds, not wall clock
                self.assertEqual(sorted(uploaded), ["creator-data/input/app_j_drive.mp4", "creator-data/input/app_j_ref.png"])
                self.assertEqual(downloaded, ["creator-data/output/motion_swap/20260101-000001-j/s3_result_00001_.mp4"])
                self.assertTrue(J.output_path("motion_swap/20260101-000001-j/s3_result_00001_.mp4").exists())
                sent = api.jobs["rp-1"]
                self.assertEqual(sent["policy"]["executionTimeout"], 1200 * 1000)  # 2 x 6 min = 12 min floor
            finally:
                J.COMFY_INPUT, J.COMFY_OUTPUT, J.JOBS_DIR = old


if __name__ == "__main__":
    unittest.main()
