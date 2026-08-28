"""The RLM engine: one turn = root LLM writes code, Monty executes it.

Per-chat interpreter state (the assistant's memory) lives as sandbox
globals, dumped to blob storage between turns; the root LM only ever sees
metadata and truncated stdout, never the full state (arXiv:2512.24601).
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic_monty import AsyncMonty, CollectString, MontyCrashedError, MontyError

from monty_claw.config import Settings
from monty_claw.db.base import ChatRecord, ChatRepo
from monty_claw.rlm import prompts
from monty_claw.rlm.llm import LlmClient
from monty_claw.storage.base import BlobStorage

logger = logging.getLogger(__name__)

SendProgress = Callable[[str], Awaitable[None]]

FALLBACK_EXHAUSTED = "I couldn't finish working through that (ran out of steps). Try rephrasing or breaking it up."
FALLBACK_TIMEOUT = 'That took longer than I\'m allowed per message — try a smaller request.'
FALLBACK_CRASHED = 'My working environment crashed and was reset. Please try again.'


class RlmEngine:
    def __init__(
        self,
        pool: AsyncMonty,
        llm: LlmClient,
        repo: ChatRepo,
        storage: BlobStorage,
        settings: Settings,
    ) -> None:
        self._pool = pool
        self._llm = llm
        self._repo = repo
        self._storage = storage
        self._settings = settings

    async def run_turn(
        self,
        chat_key: str,
        user_text: str,
        send_progress: SendProgress | None = None,
    ) -> str:
        record = await self._repo.get(chat_key) or ChatRecord(chat_key=chat_key)
        dump = None
        if record.session_blob_key is not None:
            dump = await self._storage.get_bytes(record.session_blob_key)

        try:
            async with asyncio.timeout(self._settings.turn_deadline_secs):
                reply, new_dump = await self._execute_turn(record, dump, user_text, send_progress)
        except TimeoutError:
            # Discard this turn's sandbox changes (the old blob stays valid);
            # record the exchange so the model knows it happened.
            record.history.append({'role': 'user', 'content': prompts.user_turn_message(user_text)})
            record.history.append({'role': 'assistant', 'content': '```python\n# (turn timed out)\n```'})
            await self._save(record, None)
            return FALLBACK_TIMEOUT

        await self._save(record, new_dump)
        return reply

    async def _execute_turn(
        self,
        record: ChatRecord,
        dump: bytes | None,
        user_text: str,
        send_progress: SendProgress | None,
    ) -> tuple[str, bytes | None]:
        settings = self._settings

        async def llm_query(prompt: str) -> str:
            return await self._llm.complete([{'role': 'user', 'content': str(prompt)}])

        async def send_message(text: str) -> None:
            if send_progress is not None:
                await send_progress(str(text))

        externals = {'llm_query': llm_query, 'send_message': send_message}

        checkout_kwargs: dict[str, Any] = {
            'script_name': 'rlm.py',
            'limits': {
                'max_duration_secs': settings.monty_max_duration_secs,
                'max_memory': settings.monty_max_memory,
                'max_recursion_depth': settings.monty_max_recursion_depth,
            },
        }

        if dump is not None:
            async with self._pool.checkout(**checkout_kwargs) as session:
                try:
                    await session.load_session(dump)
                except Exception:
                    # A failed load poisons the checkout; fall through to a
                    # fresh session below with reset=True.
                    logger.exception('discarding unloadable session dump for %s', record.chat_key)
                else:
                    return await self._run_in_session(
                        session, record, user_text, externals, reset=False
                    )
        async with self._pool.checkout(**checkout_kwargs) as session:
            return await self._run_in_session(
                session, record, user_text, externals, reset=dump is not None
            )

    async def _run_in_session(
        self,
        session: Any,
        record: ChatRecord,
        user_text: str,
        externals: dict[str, Any],
        reset: bool,
    ) -> tuple[str, bytes | None]:
        settings = self._settings
        # Bind the new message symbolically and (re)arm the FINAL sentinel.
        await session.feed_run('FINAL = None', inputs={'incoming_message': user_text})

        messages: list[dict[str, Any]] = [{'role': 'system', 'content': prompts.SYSTEM_PROMPT}]
        messages.extend(record.history[-settings.history_max_turns * 2 :])
        if reset:
            messages.append({'role': 'user', 'content': prompts.ENVIRONMENT_RESET_NOTE})
        messages.append({'role': 'user', 'content': prompts.user_turn_message(user_text)})

        final: str | None = None
        for _ in range(settings.max_iterations):
            reply = await self._llm.complete(messages)
            code = prompts.extract_code(reply)
            messages.append({'role': 'assistant', 'content': f'```python\n{code}\n```'})

            out = CollectString(max_bytes=settings.stdout_max_bytes)
            try:
                await session.feed_run(code, external_lookup=externals, print_callback=out)
            except MontyCrashedError:
                logger.warning('monty worker crashed for %s; state lost', record.chat_key)
                record.history = messages[1:] + [{'role': 'user', 'content': FALLBACK_CRASHED}]
                return FALLBACK_CRASHED, b''
            except MontyError as e:
                error = e.display(format='traceback') if hasattr(e, 'display') else str(e)
                messages.append({
                    'role': 'user',
                    'content': prompts.ERROR_FEEDBACK.format(error=error[: settings.stdout_max_bytes]),
                })
                continue

            final = await session.feed_run('FINAL')
            if final is not None:
                break
            messages.append({
                'role': 'user',
                'content': prompts.STDOUT_FEEDBACK.format(
                    max_bytes=settings.stdout_max_bytes, stdout=out.output or '(empty)'
                ),
            })

        if final is None:
            final = FALLBACK_EXHAUSTED
        final = str(final)

        # Reset the sentinel so a restored session doesn't answer instantly.
        await session.feed_run('FINAL = None')
        new_dump = await session.dump()
        logger.info('chat %s: session dump %d bytes', record.chat_key, len(new_dump))

        record.history = messages[1:]  # everything but the system prompt
        return final, new_dump

    async def _save(self, record: ChatRecord, new_dump: bytes | None) -> None:
        settings = self._settings
        record.history = record.history[-settings.history_max_turns * 2 :]
        if new_dump == b'':
            # Sentinel from a crashed worker: drop the stale blob reference.
            if record.session_blob_key is not None:
                await self._storage.delete(record.session_blob_key)
            record.session_blob_key = None
        elif new_dump is not None:
            record.session_blob_key = f'sessions/{record.chat_key.replace(":", "/")}.bin'
            await self._storage.put_bytes(record.session_blob_key, new_dump)
        await self._repo.save(record)
