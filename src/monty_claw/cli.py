"""CLI entrypoints: serve (Cloud Run), poll (local dev), set-webhook, repl."""

import argparse
import asyncio
import logging
import sys

from monty_claw.config import get_settings

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)


def cmd_serve() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run('monty_claw.app:create_app', factory=True, host='0.0.0.0', port=settings.port)


async def _poll() -> None:
    """Local dev: no public URL needed. Deletes any webhook, then getUpdates."""
    from monty_claw.app import create_app, handle_inbound

    settings = get_settings()
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        telegram = app.state.telegram
        await telegram.delete_webhook()
        logger.info('long-polling started (webhook deleted)')
        offset: int | None = None
        while True:
            try:
                updates = await telegram.get_updates(offset)
            except Exception:
                logger.exception('getUpdates failed; retrying in 3s')
                await asyncio.sleep(3)
                continue
            for update in updates:
                offset = update['update_id'] + 1
                try:
                    await handle_inbound(app, update)
                except Exception:
                    logger.exception('failed to handle update %s', update.get('update_id'))


def cmd_poll() -> None:
    asyncio.run(_poll())


async def _set_webhook(url: str) -> None:
    from monty_claw.channels.telegram import TelegramChannel

    settings = get_settings()
    telegram = TelegramChannel(settings.telegram_bot_token)
    try:
        result = await telegram.set_webhook(url, settings.telegram_secret_token)
        print(result)
    finally:
        await telegram.aclose()


async def _repl() -> None:
    """Terminal chat against the full engine with local storage — M1 smoke test."""
    from monty_claw.db.base import ChatRecord, ChatRepo
    from monty_claw.rlm.engine import RlmEngine
    from monty_claw.storage import LocalStorage

    settings = get_settings()

    class FileChatRepo:
        """Tiny JSON-on-disk ChatRepo so the repl needs no MongoDB."""

        def __init__(self) -> None:
            import json

            self._json = json
            self._store = LocalStorage(settings.local_storage_dir)

        async def get(self, chat_key: str) -> ChatRecord | None:
            raw = await self._store.get_bytes(f'repl/{chat_key}.json')
            if raw is None:
                return None
            data = self._json.loads(raw)
            return ChatRecord(chat_key=chat_key, **data)

        async def save(self, record: ChatRecord) -> None:
            data = {
                'session_blob_key': record.session_blob_key,
                'last_update_id': record.last_update_id,
                'transcript': record.transcript,
            }
            await self._store.put_bytes(f'repl/{record.chat_key}.json', self._json.dumps(data).encode())

        async def delete(self, chat_key: str) -> None:
            await self._store.delete(f'repl/{chat_key}.json')

    repo: ChatRepo = FileChatRepo()
    storage = LocalStorage(settings.local_storage_dir)

    async def send_progress(text: str) -> None:
        print(f'[progress] {text}')

    engine = RlmEngine(repo=repo, storage=storage, settings=settings)
    print(f'monty-claw repl — model {settings.llm_model} at {settings.llm_base_url}')
    print('state dir:', settings.local_storage_dir, '(type /reset to wipe, ctrl-d to exit)')
    chat_key = 'repl:default'
    while True:
        try:
            text = input('\nyou> ').strip()
        except EOFError:
            print()
            return
        if not text:
            continue
        if text == '/reset':
            record = await repo.get(chat_key)
            if record and record.session_blob_key:
                await storage.delete(record.session_blob_key)
            await repo.delete(chat_key)
            print('memory wiped')
            continue
        reply = await engine.run_turn(chat_key, text, send_progress)
        print(f'\nbot> {reply}')


def main() -> None:
    parser = argparse.ArgumentParser(prog='monty-claw')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('serve', help='run the FastAPI webhook server (Cloud Run entrypoint)')
    sub.add_parser('poll', help='local dev: long-poll Telegram getUpdates')
    p_hook = sub.add_parser('set-webhook', help='point the Telegram webhook at a URL')
    p_hook.add_argument('--url', required=True)
    sub.add_parser('repl', help='terminal chat against the engine (no Telegram/Mongo)')

    args = parser.parse_args()
    if args.command == 'serve':
        cmd_serve()
    elif args.command == 'poll':
        cmd_poll()
    elif args.command == 'set-webhook':
        asyncio.run(_set_webhook(args.url))
    elif args.command == 'repl':
        try:
            asyncio.run(_repl())
        except KeyboardInterrupt:
            sys.exit(0)
