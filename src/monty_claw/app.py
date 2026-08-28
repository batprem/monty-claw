"""FastAPI app: Telegram webhook endpoint + health check.

The whole RLM turn runs inside the webhook request. That is deliberate:
with Cloud Run scale-to-zero, CPU is only guaranteed during a request, so
respond-then-background is unsafe. Telegram waits on webhooks on the order
of a minute and retries unacknowledged updates; combined with update_id
dedupe (idempotency) and turn_deadline_secs < 60, in-request processing is
correct. If turns outgrow the deadline, move to Cloud Tasks or
--no-cpu-throttling (documented, not built).
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Response
from pydantic_monty import AsyncMonty
from pymongo import AsyncMongoClient

from monty_claw.channels.telegram import TelegramChannel
from monty_claw.config import Settings, get_settings
from monty_claw.db.mongo import MongoChatRepo, MongoConfigRepo
from monty_claw.rlm.engine import RlmEngine
from monty_claw.rlm.llm import OpenAiLlmClient
from monty_claw.storage import get_storage
from monty_claw.web import apply_config
from monty_claw.web import router as web_router

logger = logging.getLogger(__name__)


async def handle_inbound(app: FastAPI, payload: dict) -> None:
    """Shared by the webhook route and the long-polling CLI."""
    telegram: TelegramChannel = app.state.telegram
    engine: RlmEngine = app.state.engine
    repo: MongoChatRepo = app.state.repo

    inbound = telegram.parse_update(payload)
    if inbound is None:
        return

    chat_key = f'{inbound.channel}:{inbound.chat_id}'
    if inbound.update_id is not None:
        record = await repo.get(chat_key)
        if record and record.last_update_id is not None and inbound.update_id <= record.last_update_id:
            logger.info('skipping already-processed update %s for %s', inbound.update_id, chat_key)
            return

    await telegram.send_typing(inbound.chat_id)

    async def send_progress(text: str) -> None:
        await telegram.send_text(inbound.chat_id, text)

    if inbound.text.strip() == '/reset':
        record = await repo.get(chat_key)
        if record and record.session_blob_key:
            await app.state.storage.delete(record.session_blob_key)
        await repo.delete(chat_key)
        await telegram.send_text(inbound.chat_id, 'Memory wiped. Fresh start.')
        return

    reply = await engine.run_turn(chat_key, inbound.text, send_progress)

    # Persist the dedupe marker on the freshly saved record.
    if inbound.update_id is not None:
        record = await repo.get(chat_key)
        if record:
            record.last_update_id = inbound.update_id
            await repo.save(record)

    await telegram.send_text(inbound.chat_id, reply)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        mongo = AsyncMongoClient(settings.mongodb_uri)
        async with AsyncMonty() as pool:
            app.state.settings = settings
            app.state.pool = pool
            app.state.storage = get_storage(settings)
            app.state.repo = MongoChatRepo(mongo, settings.mongodb_db)
            app.state.config_repo = MongoConfigRepo(mongo, settings.mongodb_db)
            app.state.telegram = TelegramChannel(settings.telegram_bot_token)
            app.state.engine = RlmEngine(
                pool=pool,
                llm=OpenAiLlmClient(settings.llm_base_url, settings.llm_api_key, settings.llm_model),
                repo=app.state.repo,
                storage=app.state.storage,
                settings=settings,
            )
            # Re-apply config overrides saved from the web UI. Best-effort:
            # an unreachable Mongo shouldn't stop startup (webhook requests
            # will surface the real error).
            app.state.config_values = {}
            if settings.web_username and settings.web_password:
                try:
                    app.state.config_values = await asyncio.wait_for(
                        app.state.config_repo.load(), timeout=5.0
                    )
                    apply_config(app, app.state.config_values)
                except Exception:
                    logger.warning('could not load config overrides from MongoDB', exc_info=True)
            try:
                yield
            finally:
                await app.state.telegram.aclose()
                await mongo.close()

    app = FastAPI(lifespan=lifespan)
    app.include_router(web_router)

    @app.get('/healthz')
    async def healthz() -> dict:
        return {'ok': True}

    @app.post('/webhook/telegram')
    async def telegram_webhook(request: Request) -> Response:
        expected = settings.telegram_secret_token
        if expected and request.headers.get('X-Telegram-Bot-Api-Secret-Token') != expected:
            raise HTTPException(status_code=403)
        payload = await request.json()
        try:
            await handle_inbound(app, payload)
        except Exception:
            # Return 200 anyway: a 5xx would make Telegram retry the same
            # failing update for hours. The error is logged; dedupe was not
            # recorded, so a bot restart can retry naturally via getUpdates.
            logger.exception('failed to handle update')
        return Response(status_code=200)

    return app
