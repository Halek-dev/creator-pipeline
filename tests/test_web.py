"""API tests for the team web app (app/web/server.py) against a temporary job store, no ComfyUI."""
import io
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "app" / "web"))
sys.path.insert(0, str(ROOT / "tests"))

import av  # noqa: E402
import numpy as np  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

import jobs as J  # noqa: E402
import server as S  # noqa: E402


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


class WebApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.saved = (J.COMFY_INPUT, J.COMFY_OUTPUT, J.JOBS_DIR, S.UPLOADS, J.COMFY_API_KEY)
        J.COMFY_INPUT, J.COMFY_OUTPUT, J.JOBS_DIR, S.UPLOADS = d / "input", d / "output", d / "output" / "_jobs", d / "uploads"
        for p in (J.COMFY_INPUT, J.COMFY_OUTPUT, S.UPLOADS):
            p.mkdir(parents=True)
        self.submitted = []
        S.RUNNER.submit = self.submitted.append  # never talk to ComfyUI
        self.client = TestClient(S.app)
        self.img = io.BytesIO()
        Image.new("RGB", (900, 1400), "gray").save(self.img, format="PNG")
        vid = d / "v.mp4"
        make_video(vid, 170, size=(96, 64))  # landscape driving video
        self.vid = vid.read_bytes()

    def tearDown(self):
        J.COMFY_INPUT, J.COMFY_OUTPUT, J.JOBS_DIR, S.UPLOADS, J.COMFY_API_KEY = self.saved
        self.tmp.cleanup()

    def upload(self, name, data, kind):
        r = self.client.post("/api/uploads", files={"file": (name, data)}, data={"kind": kind})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["id"]

    def test_bootstrap_and_status(self):
        b = self.client.get("/api/bootstrap").json()
        self.assertEqual([e["key"] for e in b["engines"]], ["wan", "scail", "seedance"])
        self.assertIn("Studio", b["scenes"])
        self.assertIn("Standard", b["tiers"])
        self.assertEqual(self.client.get("/api/status").json()["busy"], False)
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/static/app.js").status_code, 200)

    def test_upload_preflight_estimate_and_job(self):
        img = self.upload("ref.png", self.img.getvalue(), "ref")
        vid = self.upload("dance.mp4", self.vid, "drive")
        r = self.client.post("/api/preflight", json={"image": img, "video": vid, "engine": "scail"}).json()
        self.assertTrue(r["ok"], r)
        self.assertEqual([x["passes"] for x in r["lengths"]], [1, 2])
        self.assertTrue(any("896×512" in m["text"] for m in r["messages"]))  # landscape driving video, SCAIL frame
        e = self.client.get("/api/estimate", params={"engine": "wan", "tier": "Standard", "chunks": 2, "finishing": True, "seeds": 3}).json()
        self.assertEqual(e["currency"], "gpu")
        self.assertGreater(e["usd"], 0)
        e2 = self.client.get("/api/estimate", params={"engine": "seedance", "tier": "High", "seconds": 7.1, "seeds": 1}).json()
        self.assertEqual(e2["currency"], "credits")
        self.assertTrue(e2["over_cap"])  # 7.1 s swap at 1080p is about 1,460 credits, above the 900 cap
        e3 = self.client.get("/api/estimate", params={"engine": "seedance", "tier": "Fast", "seconds": 5, "seeds": 1}).json()
        self.assertFalse(e3["over_cap"])
        self.assertEqual(e3["credits"], round((5 + 5) * 9600 / 1000 * 1.931))
        body = {"image": img, "video": vid, "engine": "wan", "character": "X", "appearance": "a woman", "scene": "Studio", "background": "a studio",
                "motion": "dancing", "tier": "Standard", "seeds": 2, "chunks": 9, "finishing": False, "note": "t"}
        r = self.client.post("/api/jobs", json=body)
        self.assertEqual(r.status_code, 200, r.text)
        job = r.json()
        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["chunks"], 2)  # capped by the 170-frame video
        self.assertEqual(self.submitted, [job["id"]])
        self.assertEqual(self.client.get("/api/jobs").json()[0]["id"], job["id"])
        d = self.client.get("/api/jobs/" + job["id"]).json()
        self.assertEqual(len(d["runs"]), 2)
        self.assertIn("image", d["inputs_urls"])
        self.assertEqual(self.client.get(d["inputs_urls"]["image"]).status_code, 200)
        # reuse: an existing input name is accepted in place of an upload id
        r = self.client.post("/api/jobs", json={**body, "image": d["inputs"]["image"], "video": d["inputs"]["video"], "seeds": 1})
        self.assertEqual(r.status_code, 200, r.text)

    def test_rejections(self):
        self.assertEqual(self.client.post("/api/jobs", json={"image": "nope", "video": "nope", "character": "X", "appearance": "a", "scene": "S", "background": "b", "motion": "m"}).status_code, 400)
        self.assertEqual(self.client.post("/api/uploads", files={"file": ("x.exe", b"MZ")}, data={"kind": "ref"}).status_code, 400)
        self.assertEqual(self.client.get("/files/output/../secrets").status_code, 404)
        self.assertEqual(self.client.get("/api/jobs/..").status_code, 404)
        img = self.upload("ref.png", self.img.getvalue(), "ref")
        vid = self.upload("dance.mp4", self.vid, "drive")
        J.COMFY_API_KEY = ""
        r = self.client.post("/api/jobs", json={"image": img, "video": vid, "engine": "seedance", "character": "X", "appearance": "a", "scene": "S", "background": "b", "motion": "m"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("Comfy", r.json()["error"])

    def test_progress_and_costs(self):
        job = {"id": "20260101-000001-a", "status": "running", "engine": "wan", "runs": [{"seed": 1, "status": "done", "seconds": 300, "outputs": []}, {"seed": 2, "status": "running", "queued_at": __import__("time").time() - 300, "outputs": []}],
               "cost": {"estimate_minutes": 10, "estimate_usd": 1}, "options": {"chunks": 1}, "character": "c", "scene": "s", "tier": "Standard"}
        stage, frac = S.progress(job)
        self.assertIn("Seed 2 of 2", stage)
        self.assertGreater(frac, 0.5)
        self.assertLess(frac, 1)
        c = self.client.get("/api/costs").json()
        self.assertEqual(c["headers"], J.COST_HEADERS)

    def test_vram_guard_refuses_big_tier_on_small_gpu(self):
        S._GPU["value"], S._GPU["at"] = ("NVIDIA RTX 4090", 24.0), __import__("time").time()
        try:
            e = self.client.get("/api/estimate", params={"engine": "scail", "tier": "High"}).json()
            self.assertFalse(e["vram_ok"]); self.assertEqual(e["vram_needed_gib"], 40); self.assertIn("offloading", e["note"])
            e2 = self.client.get("/api/estimate", params={"engine": "wan", "tier": "Standard"}).json()
            self.assertTrue(e2["vram_ok"])
            img = self.upload("ref.png", self.img.getvalue(), "ref")
            vid = self.upload("dance.mp4", self.vid, "drive")
            body = {"image": img, "video": vid, "engine": "scail", "character": "X", "appearance": "a", "scene": "S", "background": "b", "motion": "m", "tier": "High", "seeds": 1}
            r = self.client.post("/api/jobs", json=body)
            self.assertEqual(r.status_code, 400); self.assertIn("Refused", r.json()["error"])
            r = self.client.post("/api/jobs", json={**body, "allow_offload": True})
            self.assertEqual(r.status_code, 200, r.text)
            d = self.client.get("/api/jobs/" + r.json()["id"]).json()
            self.assertEqual(self.client.get("/api/status").json()["gpu_vram_gib"], 24.0)
        finally:
            S._GPU["value"], S._GPU["at"] = (None, None), 0.0

    def test_basic_auth_when_password_set(self):
        S.APP_PASSWORD = "pw"
        try:
            self.assertEqual(self.client.get("/api/status").status_code, 401)
            self.assertEqual(self.client.get("/api/status", auth=("team", "pw")).status_code, 200)
            self.assertEqual(self.client.get("/api/status", auth=("team", "wrong")).status_code, 401)
            self.assertIn("Team password", self.client.get("/").text)  # browsers get the login page
            self.assertEqual(self.client.post("/login", data={"password": "nope"}).status_code, 401)
            r = self.client.post("/login", data={"password": "pw"}, follow_redirects=False)
            self.assertEqual(r.status_code, 303)
            self.assertIn(S.COOKIE, r.cookies)
            self.assertEqual(self.client.get("/api/status").status_code, 200)  # cookie kept by the client
        finally:
            S.APP_PASSWORD = ""


if __name__ == "__main__":
    unittest.main()
