"""Unit tests for the node pack's logic. Run: .venv\\Scripts\\python -m unittest discover tests"""
import sys
import tempfile
import unittest
from pathlib import Path

# Import the logic modules directly so the tests don't need ComfyUI running.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "creator_nodes"))

import torch  # noqa: E402
from PIL import Image  # noqa: E402

import finish  # noqa: E402
import loader  # noqa: E402
import prompts  # noqa: E402


class PromptTests(unittest.TestCase):
    def test_character_prompt_starts_with_trigger(self):
        p = prompts.build_character_prompt("zr4char", "medium shot", " in a cafe, ", "")
        self.assertEqual(p, "zr4char, medium shot, in a cafe")

    def test_empty_trigger_rejected(self):
        with self.assertRaises(ValueError):
            prompts.build_character_prompt("  ", "medium shot", "cafe")

    def test_library_loads_and_counts(self):
        self.assertIn("lifestyle_realistic", prompts.list_libraries())
        lib = prompts.load_library("lifestyle_realistic")
        self.assertEqual(prompts.combination_count(lib), 10 * 10 * 6 * 6 * 5 * 5)

    def test_random_mode_is_seeded(self):
        lib = prompts.load_library("lifestyle_realistic")
        self.assertEqual(prompts.fill_template(lib, "random", 42), prompts.fill_template(lib, "random", 42))

    def test_sequential_mode_never_repeats_within_count(self):
        lib = {"template": "{a} {b}", "slots": {"a": ["1", "2", "3"], "b": ["x", "y"]}}
        seen = {prompts.fill_template(lib, "sequential", i) for i in range(6)}
        self.assertEqual(len(seen), 6)
        self.assertEqual(prompts.fill_template(lib, "sequential", 6), prompts.fill_template(lib, "sequential", 0))


class FinishTests(unittest.TestCase):
    def setUp(self):
        self.img = torch.full((2, 64, 96, 3), 0.5)

    def test_shape_and_range_preserved(self):
        out = finish.reality_finish(self.img, 0.05, 0.3, 0.05, 2, 80, seed=1)
        self.assertEqual(out.shape, self.img.shape)
        self.assertGreaterEqual(out.min().item(), 0.0)
        self.assertLessEqual(out.max().item(), 1.0)

    def test_all_off_is_identity(self):
        out = finish.reality_finish(self.img, 0, 0, 0, 0, 100, seed=0)
        self.assertTrue(torch.allclose(out, self.img))

    def test_vignette_darkens_corners_not_centre(self):
        out = finish.vignette(self.img, 0.5)
        self.assertLess(out[0, 0, 0, 0].item(), out[0, 32, 48, 0].item())

    def test_grain_is_deterministic_per_seed(self):
        self.assertTrue(torch.equal(finish.grain(self.img, 0.05, 7), finish.grain(self.img, 0.05, 7)))

    def test_alpha_channel_dropped(self):
        rgba = torch.rand(1, 16, 16, 4)
        self.assertEqual(finish.reality_finish(rgba).shape[-1], 3)


class LoaderTests(unittest.TestCase):
    def test_lists_images_sorted_and_wraps(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ["b.png", "A.jpg", "notes.txt"]:
                path = Path(d) / name
                if name.endswith(".txt"):
                    path.write_text("x")
                else:
                    Image.new("RGB", (8, 6), "red").save(path)
            files = loader.list_images(loader.resolve_folder(d))
            self.assertEqual([f.name for f in files], ["A.jpg", "b.png"])
            self.assertEqual(loader.pick(files, 3).name, "b.png")
            t = loader.load_image_tensor(files[0])
            self.assertEqual(tuple(t.shape), (1, 6, 8, 3))

    def test_relative_folder_uses_input_dir(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "batch").mkdir()
            self.assertEqual(loader.resolve_folder("batch", d), Path(d) / "batch")

    def test_missing_folder_and_empty_folder(self):
        with self.assertRaises(ValueError):
            loader.resolve_folder("Z:/does/not/exist")
        with self.assertRaises(ValueError):
            loader.pick([], 0)


if __name__ == "__main__":
    unittest.main()
