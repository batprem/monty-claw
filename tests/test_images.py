import re
from io import BytesIO

from PIL import Image

from monty_claw.rlm import images
from monty_claw.rlm.engine import RlmEngine, TurnDeps
from monty_claw.storage.local import LocalStorage

from .conftest import ScriptedModel
from .test_engine import run_code, say


def open_png(data: bytes) -> Image.Image:
    assert data.startswith(b'\x89PNG\r\n\x1a\n')  # a real PNG, as Telegram requires
    return Image.open(BytesIO(data))


def test_mockup_is_a_png_of_the_requested_size() -> None:
    image = open_png(images.render_mockup('a cat wearing a tiny hat', width=512, height=256))
    assert image.format == 'PNG'
    assert image.size == (512, 256)


def test_mockup_is_deterministic_but_prompt_specific() -> None:
    assert images.render_mockup('sunset') == images.render_mockup('sunset')
    assert images.render_mockup('sunset') != images.render_mockup('sunrise')


def test_mockup_clamps_absurd_sizes() -> None:
    image = open_png(images.render_mockup('x', width=99999, height=1))
    assert image.size == (images.MAX_SIZE, images.MIN_SIZE)


def test_mockup_draws_the_prompt_over_the_background() -> None:
    image = open_png(images.render_mockup('hello')).convert('RGB')
    colors = [color for _, color in image.getcolors(maxcolors=1 << 24) or []]
    assert (255, 255, 255) in colors  # white title text; a bare gradient has none
    assert images.render_mockup('hello') != images.render_mockup('')


def test_badge_is_ascii_so_the_bundled_font_can_draw_it() -> None:
    assert images.BADGE.isascii()
    assert all(line.isascii() for line in images.wrap('word ' * 200))


def test_long_prompt_is_wrapped_and_truncated() -> None:
    lines = images.wrap('word ' * 200)
    assert len(lines) == 6
    assert lines[-1].endswith('...')
    assert all(len(line) <= 26 for line in lines)


async def test_generate_image_from_the_sandbox(settings, repo) -> None:
    settings.public_base_url = 'https://example.test/'
    model = ScriptedModel([run_code('await generate_image(prompt="a red bicycle")'), say('here you go')])
    storage = LocalStorage(settings.local_storage_dir)
    engine = RlmEngine(repo=repo, storage=storage, settings=settings, model=model)

    assert await engine.run_turn('t:1', 'draw me a bicycle') == 'here you go'

    url = re.search(r'https://example\.test/media/\S+?\.png', str(model.requests[1][-1].parts))
    assert url is not None, 'the tool did not hand a URL back to the model'
    key = url.group(0).removeprefix('https://example.test/')
    stored = await storage.get_bytes(key)
    assert stored is not None
    assert open_png(stored).size == (1024, 1024)


async def test_generated_image_is_pushed_to_the_chat(settings, repo) -> None:
    photos: list[tuple[bytes, str]] = []

    async def send_photo(data: bytes, caption: str) -> None:
        photos.append((data, caption))

    model = ScriptedModel([run_code('await generate_image(prompt="a red bicycle")'), say('sent')])
    engine = RlmEngine(
        repo=repo,
        storage=LocalStorage(settings.local_storage_dir),
        settings=settings,
        model=model,
    )
    assert await engine.run_turn('t:1', 'draw', send_photo=send_photo) == 'sent'
    assert len(photos) == 1
    data, caption = photos[0]
    assert caption == 'a red bicycle'
    assert open_png(data).size == (1024, 1024)


async def test_failed_photo_upload_does_not_break_the_turn(settings, repo) -> None:
    async def send_photo(data: bytes, caption: str) -> None:
        raise RuntimeError('telegram is down')

    model = ScriptedModel([run_code('await generate_image(prompt="a bicycle")'), say('here is the link')])
    engine = RlmEngine(
        repo=repo,
        storage=LocalStorage(settings.local_storage_dir),
        settings=settings,
        model=model,
    )
    assert await engine.run_turn('t:1', 'draw', send_photo=send_photo) == 'here is the link'
    assert '/media/t/1/' in str(model.requests[1][-1].parts)  # the URL still came back


async def test_image_url_is_relative_without_a_public_base_url(settings, repo) -> None:
    engine = RlmEngine(
        repo=repo,
        storage=LocalStorage(settings.local_storage_dir),
        settings=settings,
        model=ScriptedModel([say('ok')]),
    )
    url = await engine._store_image(TurnDeps(chat_key='t:1'), 'a bicycle', 256, 256)
    assert url.startswith('/media/t/1/') and url.endswith('.png')


def test_backend_choice_falls_back_to_mock_without_a_key(settings) -> None:
    settings.image_backend = 'cursor'
    settings.cursor_api_key = ''
    assert isinstance(images.build_image_generator(settings), images.MockImageGenerator)

    settings.image_backend = 'mock'
    settings.cursor_api_key = 'crsr_test'
    assert isinstance(images.build_image_generator(settings), images.MockImageGenerator)


def test_cursor_backend_is_chosen_when_configured(settings) -> None:
    from monty_claw.rlm.cursor_images import CursorImageGenerator

    settings.image_backend = 'cursor'
    settings.cursor_api_key = 'crsr_test'
    assert isinstance(images.build_image_generator(settings), CursorImageGenerator)


async def test_engine_uses_the_configured_generator(settings, repo) -> None:
    class StubGenerator:
        def __init__(self) -> None:
            self.calls: list[tuple[str, int, int]] = []

        async def generate(self, prompt: str, width: int = 1024, height: int = 1024) -> bytes:
            self.calls.append((prompt, width, height))
            return images.render_mockup('stub', width, height)

    generator = StubGenerator()
    engine = RlmEngine(
        repo=repo,
        storage=LocalStorage(settings.local_storage_dir),
        settings=settings,
        model=ScriptedModel([say('ok')]),
        image_generator=generator,
    )
    await engine._store_image(TurnDeps(chat_key='t:1'), 'a red bicycle', 256, 256)
    assert generator.calls == [('a red bicycle', 256, 256)]


async def test_a_broken_generator_still_yields_a_picture(settings, repo) -> None:
    class BrokenGenerator:
        async def generate(self, prompt: str, width: int = 1024, height: int = 1024) -> bytes:
            raise RuntimeError('cursor is down')

    photos: list[bytes] = []

    async def send_photo(data: bytes, caption: str) -> None:
        photos.append(data)

    engine = RlmEngine(
        repo=repo,
        storage=LocalStorage(settings.local_storage_dir),
        settings=settings,
        model=ScriptedModel([say('ok')]),
        image_generator=BrokenGenerator(),
    )
    deps = TurnDeps(chat_key='t:1', send_photo=send_photo)
    assert (await engine._store_image(deps, 'a bicycle', 256, 256)).endswith('.png')
    assert open_png(photos[0]).size == (256, 256)  # the mock-up took over
