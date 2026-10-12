"""Photo finishing effects that make renders look like they came from a real camera.

Images are ComfyUI IMAGE tensors: float32, shape [batch, height, width, channels], values 0-1.
"""
import io

import numpy as np
import torch
from PIL import Image


def warmth(img: torch.Tensor, amount: float) -> torch.Tensor:
    """Positive warms (more red, less blue); negative cools."""
    if amount == 0:
        return img
    out = img.clone()
    out[..., 0] *= 1 + amount
    out[..., 2] *= 1 - amount
    return out


def chromatic_aberration(img: torch.Tensor, px: int) -> torch.Tensor:
    """Shift red right and blue left by px, like a cheap phone lens."""
    if px <= 0:
        return img
    out = img.clone()
    out[:, :, px:, 0] = img[:, :, :-px, 0]
    out[:, :, :-px, 2] = img[:, :, px:, 2]
    return out


def vignette(img: torch.Tensor, strength: float) -> torch.Tensor:
    """Darken the corners. 0 = off, 1 = corners near black."""
    if strength <= 0:
        return img
    _, h, w, _ = img.shape
    ys = torch.linspace(-1, 1, h, device=img.device).view(h, 1)
    xs = torch.linspace(-1, 1, w, device=img.device).view(1, w)
    dist = torch.sqrt(xs**2 + ys**2) / (2**0.5)
    mask = 1 - strength * dist.pow(2)
    return img * mask.view(1, h, w, 1)


def grain(img: torch.Tensor, amount: float, seed: int) -> torch.Tensor:
    """Monochrome film/sensor grain, stronger in the mid-tones."""
    if amount <= 0:
        return img
    b, h, w, _ = img.shape
    gen = torch.Generator(device="cpu").manual_seed(seed)
    noise = torch.randn((b, h, w, 1), generator=gen).to(img.device)
    luma = img[..., :3].mean(dim=-1, keepdim=True)
    midtone_weight = 1 - (2 * luma - 1).abs()
    return img + noise * amount * (0.5 + midtone_weight)


def jpeg(img: torch.Tensor, quality: int) -> torch.Tensor:
    """Round-trip through JPEG compression, like an image re-saved by an app."""
    if quality >= 100:
        return img
    frames = []
    for frame in img:
        arr = (frame[..., :3].clamp(0, 1).cpu().numpy() * 255).round().astype(np.uint8)
        buf = io.BytesIO()
        Image.fromarray(arr).save(buf, format="JPEG", quality=int(quality))
        buf.seek(0)
        decoded = np.asarray(Image.open(buf).convert("RGB"), dtype=np.float32) / 255.0
        frames.append(torch.from_numpy(decoded))
    return torch.stack(frames).to(img.device)


def reality_finish(
    img: torch.Tensor,
    grain_amount: float = 0.03,
    vignette_strength: float = 0.2,
    warmth_amount: float = 0.02,
    aberration_px: int = 1,
    jpeg_quality: int = 88,
    seed: int = 0,
) -> torch.Tensor:
    out = img[..., :3].float()
    out = warmth(out, warmth_amount)
    out = chromatic_aberration(out, aberration_px)
    out = vignette(out, vignette_strength)
    out = grain(out, grain_amount, seed)
    out = out.clamp(0, 1)
    out = jpeg(out, jpeg_quality)
    return out.clamp(0, 1)
