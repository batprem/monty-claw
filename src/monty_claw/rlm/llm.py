"""LLM client: thin wrapper over any OpenAI-compatible endpoint."""

from typing import Any, Protocol

from openai import AsyncOpenAI


class LlmClient(Protocol):
    async def complete(self, messages: list[dict[str, Any]]) -> str: ...


class OpenAiLlmClient:
    def __init__(self, base_url: str, api_key: str, model: str) -> None:
        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key)
        self._model = model

    async def complete(self, messages: list[dict[str, Any]]) -> str:
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=messages,  # type: ignore[arg-type]
        )
        return response.choices[0].message.content or ''
