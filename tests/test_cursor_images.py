"""The Cursor backend's plumbing, exercised without calling Cursor."""

from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from monty_claw.rlm.cursor_images import (
    OUTPUT_NAME,
    CursorImageGenerator,
    ImageGenerationError,
    _read_png,
)


def write_png(path: Path, size: tuple[int, int], color: str = 'red') -> None:
    buffer = BytesIO()
    Image.new('RGB', size, color).save(buffer, format='PNG')
    path.write_bytes(buffer.getvalue())


def test_a_key_is_required(settings) -> None:
    settings.cursor_api_key = ''
    with pytest.raises(ValueError, match='CURSOR_API_KEY'):
        CursorImageGenerator(settings)


def test_output_is_read_back_as_png(tmp_path: Path) -> None:
    write_png(tmp_path / OUTPUT_NAME, (256, 256))
    image = Image.open(BytesIO(_read_png(tmp_path, 256, 256)))
    assert image.format == 'PNG'
    assert image.size == (256, 256)


def test_a_wrongly_sized_output_is_resized_to_the_request(tmp_path: Path) -> None:
    write_png(tmp_path / OUTPUT_NAME, (512, 128))
    assert Image.open(BytesIO(_read_png(tmp_path, 256, 256))).size == (256, 256)


def test_any_png_is_accepted_when_the_agent_renames_the_file(tmp_path: Path) -> None:
    write_png(tmp_path / 'cat.png', (64, 64))
    assert _read_png(tmp_path, 64, 64).startswith(b'\x89PNG\r\n\x1a\n')


def test_an_empty_workspace_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ImageGenerationError, match='no PNG'):
        _read_png(tmp_path, 64, 64)


def test_a_corrupt_output_is_an_error(tmp_path: Path) -> None:
    (tmp_path / OUTPUT_NAME).write_bytes(b'not an image')
    with pytest.raises(ImageGenerationError, match='readable image'):
        _read_png(tmp_path, 64, 64)


async def test_a_slow_agent_times_out(settings, monkeypatch) -> None:
    import asyncio

    settings.cursor_api_key = 'crsr_test'
    settings.cursor_image_timeout_secs = 0.05
    generator = CursorImageGenerator(settings)

    async def never_finish(*args, **kwargs) -> None:
        await asyncio.sleep(10)

    monkeypatch.setattr(generator, '_run_agent', never_finish)
    with pytest.raises(ImageGenerationError, match='did not finish'):
        await generator.generate('a bicycle')
