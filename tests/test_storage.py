from pathlib import Path

import pytest

from monty_claw.storage.local import LocalStorage


async def test_roundtrip(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    assert await storage.get_bytes('a/b.bin') is None
    await storage.put_bytes('a/b.bin', b'hello')
    assert await storage.get_bytes('a/b.bin') == b'hello'
    await storage.put_bytes('a/b.bin', b'world')
    assert await storage.get_bytes('a/b.bin') == b'world'
    await storage.delete('a/b.bin')
    assert await storage.get_bytes('a/b.bin') is None
    await storage.delete('a/b.bin')  # idempotent


async def test_no_temp_files_left(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    await storage.put_bytes('x.bin', b'data')
    files = [p.name for p in tmp_path.iterdir()]
    assert files == ['x.bin']


async def test_key_escape_rejected(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    with pytest.raises(ValueError):
        await storage.put_bytes('../evil.bin', b'x')
