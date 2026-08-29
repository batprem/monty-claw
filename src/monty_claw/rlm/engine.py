"""The agent engine: one turn = one Pydantic AI run in code mode.

Instead of a hand-rolled "LLM writes code → we exec it → feed stdout back"
loop, the turn is a single `Agent.run` with the
[code mode](https://github.com/pydantic/monty#pydanticai-integration)
capability: the model writes Python that calls our tools as functions, and
Pydantic AI executes it in a Monty sandbox and dispatches the nested calls.

REPL state lives for one run only (that is code mode's contract), so the
assistant's memory across turns is the serialized message history — kept as
a blob per chat (GCS or a local dir), with MongoDB holding the record that
points at it.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import uuid4

from pydantic_ai import Agent, RunContext
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    UserPromptPart,
)
from pydantic_ai.models import Model
from pydantic_ai.usage import UsageLimits
from pydantic_ai_harness import CodeMode

from monty_claw.config import Settings
from monty_claw.db.base import ChatRecord, ChatRepo
from monty_claw.rlm import images, prompts
from monty_claw.rlm.images import ImageGenerator
from monty_claw.rlm.llm import build_model
from monty_claw.storage.base import BlobStorage

logger = logging.getLogger(__name__)

SendProgress = Callable[[str], Awaitable[None]]
SendPhoto = Callable[[bytes, str], Awaitable[None]]

FALLBACK_EXHAUSTED = "I couldn't finish working through that (ran out of steps). Try rephrasing or breaking it up."
FALLBACK_TIMEOUT = 'That took longer than I\'m allowed per message — try a smaller request.'


@dataclass
class TurnDeps:
    """Per-turn dependencies handed to the agent's tools."""

    chat_key: str
    send_progress: SendProgress | None = None
    send_photo: SendPhoto | None = None


class RlmEngine:
    def __init__(
        self,
        repo: ChatRepo,
        storage: BlobStorage,
        settings: Settings,
        model: Model | str | None = None,
        image_generator: ImageGenerator | None = None,
    ) -> None:
        self._repo = repo
        self._storage = storage
        self._settings = settings
        self._images = (
            image_generator if image_generator is not None else images.build_image_generator(settings)
        )
        self._model = model if model is not None else build_model(settings)
        # Sub-model for `llm_query`: no tools, no code mode — just an answer.
        self._sub_agent = Agent(self._model, instructions=prompts.SUB_LLM_INSTRUCTIONS)
        self._agent = self._build_agent()

    def _build_agent(self) -> Agent[TurnDeps, str]:
        settings = self._settings
        agent = Agent(
            self._model,
            deps_type=TurnDeps,
            instructions=prompts.INSTRUCTIONS,
            capabilities=[
                CodeMode(
                    max_tool_calls=settings.max_tool_calls,
                    resource_limits={
                        'max_duration_secs': settings.monty_max_duration_secs,
                        'max_memory': settings.monty_max_memory,
                    },
                )
            ],
        )

        @agent.tool
        async def send_message(ctx: RunContext[TurnDeps], text: str) -> None:
            """Send an interim message to the user while you keep working."""
            if ctx.deps.send_progress is not None:
                await ctx.deps.send_progress(text)

        @agent.tool_plain
        async def llm_query(prompt: str) -> str:
            """Ask a sub-model a self-contained question and get its answer back."""
            result = await self._sub_agent.run(prompt)
            return result.output

        @agent.tool
        async def generate_image(
            ctx: RunContext[TurnDeps], prompt: str, width: int = 1024, height: int = 1024
        ) -> str:
            """Make an image for a prompt and return a URL the user can open.

            Slow — a picture takes tens of seconds — so ask for one only when
            the user wants a picture, and pass the URL back to them as-is.
            """
            return await self._store_image(ctx.deps, prompt, width, height)

        return agent

    async def _store_image(self, deps: TurnDeps, prompt: str, width: int, height: int) -> str:
        """Generate the image, store it, push it to the chat, and return its URL."""
        data = await self._generate_image(prompt, width, height)
        # Unguessable name: the media route serves these without auth so the
        # URL can be shared, and the key is the only thing protecting them.
        key = f'media/{deps.chat_key.replace(":", "/")}/{uuid4().hex}{images.EXTENSION}'
        await self._storage.put_bytes(key, data)
        logger.info('chat %s: stored image %s (%d bytes)', deps.chat_key, key, len(data))
        if deps.send_photo is not None:
            # Best-effort: the URL still works if the channel upload fails.
            try:
                await deps.send_photo(data, prompt)
            except Exception:
                logger.warning('could not send the image to the chat', exc_info=True)
        return f'{self._settings.public_base_url.rstrip("/")}/{key}'

    async def _generate_image(self, prompt: str, width: int, height: int) -> bytes:
        """A failed backend costs the user their picture, not their turn."""
        try:
            return await self._images.generate(prompt, width, height)
        except Exception:
            logger.warning('image generation failed; falling back to a mock-up', exc_info=True)
            return images.render_mockup(prompt, width, height)

    async def run_turn(
        self,
        chat_key: str,
        user_text: str,
        send_progress: SendProgress | None = None,
        send_photo: SendPhoto | None = None,
    ) -> str:
        settings = self._settings
        record = await self._repo.get(chat_key) or ChatRecord(chat_key=chat_key)
        history = await self._load_history(record)

        try:
            async with asyncio.timeout(settings.turn_deadline_secs):
                result = await self._agent.run(
                    user_text,
                    deps=TurnDeps(
                        chat_key=chat_key, send_progress=send_progress, send_photo=send_photo
                    ),
                    message_history=history,
                    usage_limits=UsageLimits(request_limit=settings.max_iterations),
                )
        except TimeoutError:
            # Drop this turn entirely; the stored history stays valid.
            logger.warning('turn for %s hit the deadline', chat_key)
            return FALLBACK_TIMEOUT
        except UsageLimitExceeded:
            logger.warning('turn for %s exceeded %d requests', chat_key, settings.max_iterations)
            return FALLBACK_EXHAUSTED

        await self._save(record, result.all_messages())
        return result.output

    async def _load_history(self, record: ChatRecord) -> list[ModelMessage] | None:
        if record.session_blob_key is None:
            return None
        raw = await self._storage.get_bytes(record.session_blob_key)
        if raw is None:
            return None
        try:
            return ModelMessagesTypeAdapter.validate_json(raw)
        except Exception:
            # Corrupt or legacy (pre-code-mode session dump) blob: start over
            # rather than failing the turn.
            logger.exception('discarding unreadable history for %s', record.chat_key)
            return None

    async def _save(self, record: ChatRecord, messages: list[ModelMessage]) -> None:
        messages = trim_history(messages, self._settings.history_max_turns)
        raw = ModelMessagesTypeAdapter.dump_json(messages)
        key = f'sessions/{record.chat_key.replace(":", "/")}.json'
        if record.session_blob_key is not None and record.session_blob_key != key:
            await self._storage.delete(record.session_blob_key)
        await self._storage.put_bytes(key, raw)
        record.session_blob_key = key
        logger.info('chat %s: history %d messages, %d bytes', record.chat_key, len(messages), len(raw))
        await self._repo.save(record)


def trim_history(messages: list[ModelMessage], max_turns: int) -> list[ModelMessage]:
    """Keep the last `max_turns` user turns.

    Cuts only at user-prompt boundaries so a tool call is never separated
    from its return — providers reject a history where they are split.
    """
    starts = [
        i
        for i, message in enumerate(messages)
        if isinstance(message, ModelRequest) and any(isinstance(p, UserPromptPart) for p in message.parts)
    ]
    if len(starts) <= max_turns:
        return messages
    return messages[starts[-max_turns] :]
