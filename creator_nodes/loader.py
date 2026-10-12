"""Folder image loading for batch work."""
from pathlib import Path

import numpy as np
import torch
from PIL import Image, ImageOps

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


def resolve_folder(folder: str, input_dir: str | None = None) -> Path:
    """Absolute paths are used as-is; relative paths are relative to ComfyUI's input folder."""
    path = Path(folder.strip().strip('"'))
    if not path.is_absolute() and input_dir:
        path = Path(input_dir) / path
    if not path.is_dir():
        raise ValueError(f"Folder not found: {path}")
    return path


def list_images(folder: Path) -> list[Path]:
    return sorted(
        (p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS),
        key=lambda p: p.name.lower(),
    )


def pick(files: list[Path], index: int) -> Path:
    """Wrap around so an ever-increasing index keeps cycling through the folder."""
    if not files:
        raise ValueError("No images found in the folder (png, jpg, jpeg, webp, bmp).")
    return files[index % len(files)]


def load_image_tensor(path: Path) -> torch.Tensor:
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        arr = np.asarray(im, dtype=np.float32) / 255.0
    return torch.from_numpy(arr).unsqueeze(0)
