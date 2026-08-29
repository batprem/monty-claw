"""Image generation: the backend protocol, plus the mock-up fallback.

The real generator is `cursor_images.CursorImageGenerator`; `build_image_generator`
picks it when one is configured. What stays here is the fallback: `render_mockup`
draws the prompt onto a background derived from the prompt's own hash, so the
same prompt always gives the same picture and different prompts look different.
It is what the agent gets when no backend is configured, and what the engine
falls back to when the real one fails or runs out of time.

PNG rather than SVG because Telegram's `sendPhoto` only takes raster images,
and the point of a generated picture is that it arrives inline in the chat.
"""

import colorsys
import hashlib
import logging
from io import BytesIO
from typing import Protocol

from PIL import Image, ImageDraw, ImageFont

from monty_claw.config import Settings

logger = logging.getLogger(__name__)

MIME_TYPE = 'image/png'
EXTENSION = '.png'

MIN_SIZE = 64
MAX_SIZE = 2048

# ASCII only: Pillow's bundled font has no em dash or ellipsis glyph.
BADGE = 'MOCK-UP - not a generated image'

_MAX_LINES = 6
_CHARS_PER_LINE = 26


class ImageGenerator(Protocol):
    async def generate(self, prompt: str, width: int = 1024, height: int = 1024) -> bytes:
        """Return PNG bytes of `width` x `height` depicting `prompt`."""
        ...


class MockImageGenerator:
    """The placeholder backend: no model, no network, always fast."""

    async def generate(self, prompt: str, width: int = 1024, height: int = 1024) -> bytes:
        return render_mockup(prompt, width, height)


def build_image_generator(settings: Settings) -> ImageGenerator:
    if settings.image_backend == 'cursor':
        if not settings.cursor_api_key:
            logger.warning('IMAGE_BACKEND=cursor but CURSOR_API_KEY is unset; using mock-ups')
            return MockImageGenerator()
        from monty_claw.rlm.cursor_images import CursorImageGenerator

        return CursorImageGenerator(settings)
    return MockImageGenerator()


def render_mockup(prompt: str, width: int = 1024, height: int = 1024) -> bytes:
    """Render a placeholder image for `prompt` as PNG bytes."""
    width = _clamp(width)
    height = _clamp(height)
    digest = hashlib.sha256(prompt.strip().encode()).digest()

    image = _gradient(digest, width, height)
    _draw_blobs(image, digest)
    _draw_text(image, prompt.strip() or 'untitled', width, height)

    buffer = BytesIO()
    image.save(buffer, format='PNG', optimize=True)
    return buffer.getvalue()


def _clamp(size: int) -> int:
    return max(MIN_SIZE, min(MAX_SIZE, int(size)))


def _hsl(hue: int, saturation: float, lightness: float) -> tuple[int, int, int]:
    r, g, b = colorsys.hls_to_rgb(hue / 360, lightness, saturation)
    return round(r * 255), round(g * 255), round(b * 255)


def _gradient(digest: bytes, width: int, height: int) -> Image.Image:
    """Two-tone diagonal wash, upscaled from a 2x2 image by the resampler."""
    hue = digest[0] * 360 // 256
    hue_b = (hue + 60 + digest[1] % 180) % 360
    start = _hsl(hue, 0.62, 0.42)
    end = _hsl(hue_b, 0.58, 0.22)
    middle = tuple((a + b) // 2 for a, b in zip(start, end))

    corners = Image.new('RGB', (2, 2))
    corners.putdata([start, middle, middle, end])  # type: ignore[arg-type]
    return corners.resize((width, height), Image.Resampling.BICUBIC)


def _draw_blobs(image: Image.Image, digest: bytes) -> None:
    """Four translucent circles, positioned and sized from the prompt's hash."""
    width, height = image.size
    overlay = Image.new('RGBA', image.size, (255, 255, 255, 0))
    draw = ImageDraw.Draw(overlay)
    for index in range(4):
        offset = 2 + index * 3
        cx = digest[offset] * width // 256
        cy = digest[offset + 1] * height // 256
        r = (digest[offset + 2] % 40 + 10) * min(width, height) // 200
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(255, 255, 255, 30))
    image.paste(Image.alpha_composite(image.convert('RGBA'), overlay).convert('RGB'))


def _draw_text(image: Image.Image, prompt: str, width: int, height: int) -> None:
    draw = ImageDraw.Draw(image)
    text_size = max(14, min(width, height) // 18)
    line_height = round(text_size * 1.35)
    lines = wrap(prompt)
    first_line_y = height / 2 - (len(lines) - 1) * line_height / 2
    # Pillow's bundled font, sized: no system font files to depend on in the
    # runtime image.
    title = ImageFont.load_default(size=text_size)
    for index, line in enumerate(lines):
        draw.text(
            (width / 2, first_line_y + index * line_height),
            line,
            font=title,
            fill=(255, 255, 255),
            anchor='mm',
        )

    badge_size = max(11, min(width, height) // 40)
    draw.text(
        (badge_size * 2, height - badge_size * 2),
        BADGE,
        font=ImageFont.load_default(size=badge_size),
        fill=(255, 255, 255),
        anchor='ls',
    )


def wrap(prompt: str) -> list[str]:
    """Greedy word wrap, capped at `_MAX_LINES` with an ellipsis if it overflows."""
    lines: list[str] = []
    current = ''
    for word in prompt.split():
        candidate = f'{current} {word}'.strip()
        if len(candidate) <= _CHARS_PER_LINE:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = word if len(word) <= _CHARS_PER_LINE else word[: _CHARS_PER_LINE - 3] + '...'
        if len(lines) == _MAX_LINES:
            break
    if current and len(lines) < _MAX_LINES:
        lines.append(current)
    if len(lines) == _MAX_LINES and len(' '.join(lines)) < len(prompt):
        lines[-1] = lines[-1][: _CHARS_PER_LINE - 3] + '...'
    return lines
