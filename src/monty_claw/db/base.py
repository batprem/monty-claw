"""Database abstraction: structured per-chat state lives in MongoDB.

The Monty session dump itself is a blob in BlobStorage; the chat record
only holds its key.
"""

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ChatRecord:
    chat_key: str  # f'{channel}:{chat_id}'
    history: list[dict[str, Any]] = field(default_factory=list)
    session_blob_key: str | None = None
    last_update_id: int | None = None
    # User-facing exchange log (role: user|assistant). Unlike `history` (the
    # RLM's internal code/stdout log), this is what a chat UI renders.
    transcript: list[dict[str, Any]] = field(default_factory=list)


class ChatRepo(Protocol):
    async def get(self, chat_key: str) -> ChatRecord | None: ...

    async def save(self, record: ChatRecord) -> None: ...

    async def delete(self, chat_key: str) -> None: ...
