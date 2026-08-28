"""Telegram channel over the raw Bot API (httpx, no framework)."""

import logging
from typing import Any

import httpx

from monty_claw.channels.base import InboundMessage

logger = logging.getLogger(__name__)

MAX_MESSAGE_CHARS = 4096
MAX_CAPTION_CHARS = 1024


def chunk_text(text: str, limit: int = MAX_MESSAGE_CHARS) -> list[str]:
    if not text:
        return []
    chunks = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        # Prefer breaking at a newline inside the window.
        cut = text.rfind('\n', 1, limit)
        if cut == -1:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip('\n')
    return chunks


class TelegramChannel:
    name = 'telegram'

    def __init__(self, bot_token: str, client: httpx.AsyncClient | None = None) -> None:
        self._base = f'https://api.telegram.org/bot{bot_token}'
        self._client = client or httpx.AsyncClient(timeout=30.0)

    def parse_update(self, payload: dict[str, Any]) -> InboundMessage | None:
        message = payload.get('message') or payload.get('edited_message')
        if not message:
            return None
        text = message.get('text')
        chat = message.get('chat') or {}
        if not text or 'id' not in chat:
            return None
        return InboundMessage(
            channel=self.name,
            chat_id=str(chat['id']),
            text=text,
            update_id=payload.get('update_id'),
        )

    async def send_text(self, chat_id: str, text: str) -> None:
        for chunk in chunk_text(text):
            await self._call('sendMessage', {'chat_id': chat_id, 'text': chunk})

    async def send_photo(self, chat_id: str, data: bytes, caption: str = '') -> None:
        """Upload an image so it renders inline in the chat.

        The bytes are posted directly rather than handed to Telegram as a URL,
        so this works whether or not the deployment has a public origin.
        """
        response = await self._client.post(
            f'{self._base}/sendPhoto',
            data={'chat_id': chat_id, 'caption': caption[:MAX_CAPTION_CHARS]},
            files={'photo': ('image.png', data, 'image/png')},
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get('ok'):
            raise RuntimeError(f'telegram sendPhoto failed: {payload}')

    async def send_typing(self, chat_id: str) -> None:
        try:
            await self._call('sendChatAction', {'chat_id': chat_id, 'action': 'typing'})
        except httpx.HTTPError:
            logger.warning('sendChatAction failed', exc_info=True)

    async def set_webhook(self, url: str, secret_token: str = '') -> dict[str, Any]:
        params: dict[str, Any] = {'url': url}
        if secret_token:
            params['secret_token'] = secret_token
        return await self._call('setWebhook', params)

    async def delete_webhook(self) -> dict[str, Any]:
        return await self._call('deleteWebhook', {})

    async def get_updates(self, offset: int | None, timeout_secs: int = 30) -> list[dict[str, Any]]:
        params: dict[str, Any] = {'timeout': timeout_secs}
        if offset is not None:
            params['offset'] = offset
        result = await self._call('getUpdates', params, read_timeout=timeout_secs + 10)
        return result.get('result', [])

    async def _call(
        self, method: str, params: dict[str, Any], read_timeout: float | None = None
    ) -> dict[str, Any]:
        response = await self._client.post(
            f'{self._base}/{method}',
            json=params,
            timeout=read_timeout,
        )
        response.raise_for_status()
        data = response.json()
        if not data.get('ok'):
            raise RuntimeError(f'telegram {method} failed: {data}')
        return data

    async def aclose(self) -> None:
        await self._client.aclose()
