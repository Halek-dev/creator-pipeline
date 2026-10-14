"""Phase 3A: describe a scene's background with Claude vision, in the style of our scene presets.

    from describe import describe_background
    text = describe_background("ref.png")                 # the reference image's surroundings
    text = describe_background("drive.mp4", frame=True)   # the driving video's first frame

Needs ANTHROPIC_API_KEY (or an `ant auth login` profile) in the environment of the app process.
One call costs well under a cent: a downsized image plus a one-sentence answer.
"""
import base64
import io
import os
from pathlib import Path

MODEL = "claude-opus-5-5"
MAX_SIDE = 1568  # the API downsizes beyond this anyway; sending less keeps the call cheap and fast

PROMPT = (
    "Describe only the surroundings in this picture for a video generator: the place, surfaces, objects, "
    "lighting and colour tone, and the camera feel (phone photo, studio, cinematic). Do not describe any person, "
    "their clothes, body or movement. Answer with one sentence of 15-40 words, no preamble, in the style of: "
    "'a bright modern apartment living room with big windows, wooden floor, soft daylight, phone-camera look'."
)


class DescribeError(RuntimeError):
    pass


def load_frame(path, frame=False):
    """A PIL image from an image file, or the first frame of a video when frame=True."""
    from PIL import Image

    if frame:
        import av

        with av.open(str(path)) as c:
            for f in c.decode(c.streams.video[0]):
                return f.to_image()
        raise DescribeError("The video has no frames.")
    with Image.open(path) as im:
        return im.convert("RGB")


def encode_jpeg(image, max_side=MAX_SIDE):
    """Downsize to max_side and return base64 JPEG bytes as str."""
    im = image.convert("RGB")
    im.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=88)
    return base64.standard_b64encode(buf.getvalue()).decode("ascii")


def _client():
    import anthropic

    return anthropic.Anthropic()


def credentials_available():
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")) or (Path.home() / ".config" / "anthropic").exists()


def describe_background(path, frame=False, client=None):
    """Return one background sentence for the image (or the video's first frame). Raises DescribeError with a
    plain message the app can show."""
    import anthropic

    data = encode_jpeg(load_frame(path, frame))
    client = client or _client()
    try:
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=1024,
            output_config={"effort": "low"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}},
                    {"type": "text", "text": PROMPT},
                ],
            }],
        )
    except anthropic.AuthenticationError:
        raise DescribeError("No valid Anthropic API key. Set ANTHROPIC_API_KEY before starting the app.") from None
    except anthropic.RateLimitError:
        raise DescribeError("Anthropic rate limit hit; try again in a minute.") from None
    except anthropic.APIStatusError as e:
        raise DescribeError(f"Anthropic API error {e.status_code}: {e.message}") from None
    except anthropic.APIConnectionError:
        raise DescribeError("Can't reach the Anthropic API (network).") from None
    if response.stop_reason == "refusal":
        raise DescribeError("The model declined to describe this image.")
    text = " ".join(b.text for b in response.content if b.type == "text").strip()
    if not text:
        raise DescribeError("Empty description returned.")
    return text.strip().strip('"').rstrip(".")
