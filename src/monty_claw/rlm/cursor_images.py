"""Image generation backed by a Cursor agent.

There is no image model here either: a Cursor cloud-SDK agent is given an
empty scratch directory and asked to *write and run* a Python script that
draws the picture with Pillow. What comes back is a real, prompt-specific
PNG — the agent chooses shapes, colours and composition — produced by code
rather than by a diffusion model.

Each call gets its own throwaway workspace and its own bridge process, so
concurrent calls cannot see or overwrite each other's files. The agent runs
with no repo and no access to this project's tree.

Cost of that honesty: a generation is a full agent turn, so it takes tens of
seconds. `Settings.cursor_image_timeout_secs` bounds it, and the engine falls
back to the mock-up renderer when the bound is hit.
"""

import asyncio
import logging
import tempfile
from io import BytesIO
from pathlib import Path

from PIL import Image

from monty_claw.config import Settings

logger = logging.getLogger(__name__)

OUTPUT_NAME = 'out.png'

_TASK = """\
Create an image file named `{name}` in the current directory: a {width}x{height} \
PNG illustration of: {prompt}

How: write a Python script that draws it with Pillow (already installed) and \
run it. Work offline — no network, no downloads, no image models. Compose the \
picture out of shapes, gradients and text so it actually depicts the subject; \
do not just render the prompt as words on a background.

Produce nothing but `{name}` and the script that made it, and stop as soon as \
the file exists.\
"""


class ImageGenerationError(RuntimeError):
    """The Cursor agent did not come back with a usable image."""


class CursorImageGenerator:
    """Generates a PNG per prompt by running a Cursor agent in a scratch dir."""

    def __init__(self, settings: Settings) -> None:
        if not settings.cursor_api_key:
            raise ValueError('IMAGE_BACKEND=cursor requires CURSOR_API_KEY')
        self._settings = settings

    async def generate(self, prompt: str, width: int = 1024, height: int = 1024) -> bytes:
        settings = self._settings
        prompt = prompt.strip() or 'an abstract picture'
        try:
            async with asyncio.timeout(settings.cursor_image_timeout_secs):
                with tempfile.TemporaryDirectory(prefix='monty-claw-image-') as workspace:
                    await self._run_agent(prompt, width, height, Path(workspace))
                    return _read_png(Path(workspace), width, height)
        except TimeoutError as error:
            raise ImageGenerationError(
                f'the Cursor agent did not finish within {settings.cursor_image_timeout_secs}s'
            ) from error

    async def _run_agent(self, prompt: str, width: int, height: int, workspace: Path) -> None:
        # Imported here so the dependency is only paid for by deployments that
        # actually use this backend (the SDK vendors a ~160 MB bridge).
        from cursor_sdk import AsyncAgent, AsyncClient, LocalAgentOptions

        settings = self._settings
        task = _TASK.format(name=OUTPUT_NAME, width=width, height=height, prompt=prompt)
        async with await AsyncClient.launch_bridge(workspace=workspace) as client:
            agent = await AsyncAgent.create(
                client=client,
                model=settings.cursor_model,
                api_key=settings.cursor_api_key,
                name='monty-claw image',
                local=LocalAgentOptions(cwd=workspace),
            )
            result = await (await agent.send(task)).wait()
        logger.info('cursor image run finished: status=%s', result.status)
        if result.status != 'finished':
            raise ImageGenerationError(f'the Cursor agent ended as {result.status}')


def _read_png(workspace: Path, width: int, height: int) -> bytes:
    """Load the agent's output, holding it to the size the caller asked for."""
    path = workspace / OUTPUT_NAME
    if not path.exists():
        # The agent occasionally names the file something else; any PNG it left
        # behind is still the picture it was asked for.
        candidates = sorted(workspace.glob('*.png'))
        if not candidates:
            raise ImageGenerationError('the Cursor agent left no PNG behind')
        path = candidates[0]

    try:
        image = Image.open(BytesIO(path.read_bytes()))
        image.load()
    except OSError as error:
        raise ImageGenerationError(f'{path.name} is not a readable image') from error

    image = image.convert('RGB')
    if image.size != (width, height):
        logger.info('cursor returned %s, resizing to %dx%d', image.size, width, height)
        image = image.resize((width, height), Image.Resampling.LANCZOS)

    buffer = BytesIO()
    image.save(buffer, format='PNG', optimize=True)
    return buffer.getvalue()
