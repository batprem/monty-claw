"""Blob storage abstraction: session dumps and other opaque bytes."""

from typing import Protocol


class BlobStorage(Protocol):
    async def get_bytes(self, key: str) -> bytes | None: ...

    async def put_bytes(self, key: str, data: bytes) -> None: ...

    async def delete(self, key: str) -> None: ...
