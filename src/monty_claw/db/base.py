"""Database abstraction: structured per-chat state lives in MongoDB.

The agent's message history is a blob in BlobStorage; the chat record only
holds its key.
"""

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ChatRecord:
    chat_key: str  # f'{channel}:{chat_id}'
    session_blob_key: str | None = None
    last_update_id: int | None = None
    # User-facing exchange log (role: user|assistant). Unlike the message
    # history blob (the agent's own code/tool log), this is what a UI renders.
    transcript: list[dict[str, Any]] = field(default_factory=list)


class ChatRepo(Protocol):
    async def get(self, chat_key: str) -> ChatRecord | None: ...

    async def save(self, record: ChatRecord) -> None: ...

    async def delete(self, chat_key: str) -> None: ...
