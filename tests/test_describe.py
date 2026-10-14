import base64
import io
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from PIL import Image  # noqa: E402

import describe as D  # noqa: E402


class FakeMessages:
    def __init__(self, text, stop="end_turn"):
        self.text, self.stop, self.calls = text, stop, []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(stop_reason=self.stop, content=[SimpleNamespace(type="text", text=self.text)])


def fake_client(text, stop="end_turn"):
    m = FakeMessages(text, stop)
    return SimpleNamespace(beta=SimpleNamespace(messages=m)), m


class DescribeTests(unittest.TestCase):
    def test_encode_downsizes_and_is_jpeg(self):
        data = D.encode_jpeg(Image.new("RGB", (4000, 2000), "gray"), max_side=800)
        im = Image.open(io.BytesIO(base64.standard_b64decode(data)))
        self.assertEqual(im.format, "JPEG")
        self.assertEqual(max(im.size), 800)

    def test_describe_sends_image_and_prompt_and_cleans_text(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "ref.png"
            Image.new("RGB", (640, 960), "white").save(p)
            client, m = fake_client('"A bright studio with grey backdrop, soft light."')
            out = D.describe_background(str(p), client=client)
            self.assertEqual(out, "A bright studio with grey backdrop, soft light")
            call = m.calls[0]
            self.assertEqual(call["model"], D.MODEL)
            self.assertEqual(call["fallbacks"], "default")
            content = call["messages"][0]["content"]
            self.assertEqual(content[0]["type"], "image")
            self.assertEqual(content[0]["source"]["media_type"], "image/jpeg")
            self.assertIn("Do not describe any person", content[1]["text"])

    def test_refusal_and_empty_raise_plain_errors(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "ref.png"
            Image.new("RGB", (64, 64), "white").save(p)
            client, _ = fake_client("x", stop="refusal")
            with self.assertRaises(D.DescribeError):
                D.describe_background(str(p), client=client)
            client, _ = fake_client("   ")
            with self.assertRaises(D.DescribeError):
                D.describe_background(str(p), client=client)

    def test_video_first_frame(self):
        import av
        import numpy as np

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "v.mp4"
            with av.open(str(p), "w") as c:
                s = c.add_stream("libx264", rate=24)
                s.width, s.height, s.pix_fmt = 64, 64, "yuv420p"
                for i in range(5):
                    for pkt in s.encode(av.VideoFrame.from_ndarray(np.full((64, 64, 3), 100, dtype=np.uint8), format="rgb24")):
                        c.mux(pkt)
                for pkt in s.encode():
                    c.mux(pkt)
            im = D.load_frame(p, frame=True)
            self.assertEqual(im.size, (64, 64))


if __name__ == "__main__":
    unittest.main()
