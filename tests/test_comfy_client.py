import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from comfy_client import ComfyError, collect_outputs, resolve, set_input  # noqa: E402

WF = {
    "1": {"class_type": "LoadImage", "_meta": {"title": "Reference Image"}, "inputs": {"image": "a.png"}},
    "13": {"class_type": "ResizeImageMaskNode", "_meta": {"title": "Resize Driving"}, "inputs": {"input": ["1", 0], "resize_type": "scale dimensions", "resize_type.width": 480, "resize_type.height": 848}},
    "21": {"class_type": "SamplerCustom", "_meta": {"title": "Sampler"}, "inputs": {"noise_seed": 0}},
    "22": {"class_type": "SamplerCustom", "_meta": {"title": "Dup"}, "inputs": {"x": 0}},
    "23": {"class_type": "SamplerCustom", "_meta": {"title": "Dup"}, "inputs": {"x": 0}},
}


class ResolveTests(unittest.TestCase):
    def test_title_and_id(self):
        self.assertEqual(resolve(WF, "Reference Image.image"), ("1", "image"))
        self.assertEqual(resolve(WF, "21.noise_seed"), ("21", "noise_seed"))

    def test_dotted_input_name(self):
        self.assertEqual(resolve(WF, "Resize Driving.resize_type.width"), ("13", "resize_type.width"))
        self.assertEqual(resolve(WF, "Resize Driving.resize_type"), ("13", "resize_type"))

    def test_errors_are_helpful(self):
        with self.assertRaises(ComfyError) as cm:
            resolve(WF, "Sampler.cfg")
        self.assertIn("has no input", str(cm.exception))
        with self.assertRaises(ComfyError) as cm:
            resolve(WF, "Nope.x")
        self.assertIn("Titles in this workflow", str(cm.exception))
        with self.assertRaises(ComfyError):
            resolve(WF, "Dup.x")
        with self.assertRaises(ComfyError):
            resolve(WF, "nodot")

    def test_set_input(self):
        wf = {k: {**v, "inputs": dict(v["inputs"])} for k, v in WF.items()}
        set_input(wf, "Resize Driving.resize_type.height", 1280)
        self.assertEqual(wf["13"]["inputs"]["resize_type.height"], 1280)


class OutputTests(unittest.TestCase):
    def test_collect_outputs_only_output_type(self):
        entry = {"outputs": {"25": {"images": [{"filename": "a.mp4", "subfolder": "x", "type": "output"}, {"filename": "t.png", "subfolder": "", "type": "temp"}]}}}
        self.assertEqual(collect_outputs(entry), [str(Path("x") / "a.mp4")])


if __name__ == "__main__":
    unittest.main()
