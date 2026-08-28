"""Channel abstraction: Telegram now, Line/Slack later."""

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class InboundMessage:
    channel: str
    chat_id: str
    text: str
    update_id: int | None = None


class Channel(Protocol):
    name: str

    def parse_update(self, payload: dict[str, Any]) -> InboundMessage | None: ...

    async def send_text(self, chat_id: str, text: str) -> None: ...

    async def send_typing(self, chat_id: str) -> None: ...
