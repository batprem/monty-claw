"""Google Cloud Storage blob backend (Application Default Credentials).

The google-cloud-storage client is synchronous; calls are offloaded to a
thread. Fine at personal-assistant scale.
"""

import asyncio

from google.api_core.exceptions import NotFound
from google.cloud import storage


class GcsStorage:
    def __init__(self, bucket_name: str, client: storage.Client | None = None) -> None:
        self._client = client or storage.Client()
        self._bucket = self._client.bucket(bucket_name)

    async def get_bytes(self, key: str) -> bytes | None:
        def read() -> bytes | None:
            try:
                return self._bucket.blob(key).download_as_bytes()
            except NotFound:
                return None

        return await asyncio.to_thread(read)

    async def put_bytes(self, key: str, data: bytes) -> None:
        await asyncio.to_thread(lambda: self._bucket.blob(key).upload_from_string(data))

    async def delete(self, key: str) -> None:
        def unlink() -> None:
            try:
                self._bucket.blob(key).delete()
            except NotFound:
                pass

        await asyncio.to_thread(unlink)
