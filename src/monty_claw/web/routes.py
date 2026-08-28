"""Web UI API: login, chat, and runtime configuration."""

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel

from monty_claw.db.base import ChatRecord
from monty_claw.web import auth

logger = logging.getLogger(__name__)

router = APIRouter()

STATIC_DIR = Path(__file__).parent / 'static'
INDEX_HTML = STATIC_DIR / 'index.html'
TRANSCRIPT_MAX_ENTRIES = 200

# Settings fields editable from the configuration tab. Everything else is
# deploy-time only.
EDITABLE_FIELDS = (
    'llm_base_url',
    'llm_model',
    'llm_api_key',
    'max_iterations',
    'stdout_max_bytes',
    'history_max_turns',
    'monty_max_duration_secs',
    'turn_deadline_secs',
)
SECRET_FIELDS = {'llm_api_key'}
SECRET_MASK = '••••••••'


def require_user(request: Request) -> str:
    settings = request.app.state.settings
    if not settings.web_username or not settings.web_password:
        raise HTTPException(status_code=503, detail='web UI disabled: set WEB_USERNAME and WEB_PASSWORD')
    token = request.cookies.get(auth.COOKIE_NAME)
    username = auth.verify_token(token, auth.secret_for(settings)) if token else None
    if username is None or username != settings.web_username:
        raise HTTPException(status_code=401, detail='not signed in')
    return username


class LoginRequest(BaseModel):
    username: str
    password: str


class ChatRequest(BaseModel):
    message: str


@router.get('/', include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(INDEX_HTML, media_type='text/html')


@router.post('/api/login')
async def login(request: Request, response: Response, body: LoginRequest) -> dict:
    settings = request.app.state.settings
    if not settings.web_username or not settings.web_password:
        raise HTTPException(status_code=503, detail='web UI disabled: set WEB_USERNAME and WEB_PASSWORD')
    if not auth.check_credentials(settings, body.username, body.password):
        raise HTTPException(status_code=401, detail='invalid username or password')
    token = auth.make_token(body.username, auth.secret_for(settings), settings.web_session_ttl_secs)
    response.set_cookie(
        auth.COOKIE_NAME,
        token,
        max_age=settings.web_session_ttl_secs,
        httponly=True,
        samesite='lax',
        secure=request.url.scheme == 'https',
    )
    return {'username': body.username}


@router.post('/api/logout')
async def logout(response: Response) -> dict:
    response.delete_cookie(auth.COOKIE_NAME)
    return {'ok': True}


@router.get('/api/me')
async def me(username: str = Depends(require_user)) -> dict:
    return {'username': username}


@router.get('/api/history')
async def history(request: Request, username: str = Depends(require_user)) -> dict:
    record = await request.app.state.repo.get(f'web:{username}')
    return {'transcript': record.transcript if record else []}


@router.post('/api/chat')
async def chat(request: Request, body: ChatRequest, username: str = Depends(require_user)) -> dict:
    app = request.app
    chat_key = f'web:{username}'
    text = body.message.strip()
    if not text:
        raise HTTPException(status_code=422, detail='empty message')

    if text == '/reset':
        record = await app.state.repo.get(chat_key)
        if record and record.session_blob_key:
            await app.state.storage.delete(record.session_blob_key)
        await app.state.repo.delete(chat_key)
        return {'reply': 'Memory wiped. Fresh start.', 'progress': []}

    progress: list[str] = []

    async def send_progress(message: str) -> None:
        progress.append(message)

    reply = await app.state.engine.run_turn(chat_key, text, send_progress)

    record = await app.state.repo.get(chat_key) or ChatRecord(chat_key=chat_key)
    record.transcript.append({'role': 'user', 'content': text})
    for message in progress:
        record.transcript.append({'role': 'assistant', 'content': message, 'progress': True})
    record.transcript.append({'role': 'assistant', 'content': reply})
    record.transcript = record.transcript[-TRANSCRIPT_MAX_ENTRIES:]
    await app.state.repo.save(record)

    return {'reply': reply, 'progress': progress}


@router.get('/api/config')
async def get_config(request: Request, username: str = Depends(require_user)) -> dict:
    settings = request.app.state.settings
    values = {}
    for name in EDITABLE_FIELDS:
        value = getattr(settings, name)
        values[name] = (SECRET_MASK if value else '') if name in SECRET_FIELDS else value
    return {
        'editable': values,
        'readonly': {
            'llm_configured': bool(settings.llm_api_key),
            'storage_backend': settings.storage_backend,
            'mongodb_db': settings.mongodb_db,
        },
    }


@router.put('/api/config')
async def put_config(request: Request, body: dict, username: str = Depends(require_user)) -> dict:
    app = request.app
    settings = app.state.settings

    changes: dict = {}
    for name, value in body.items():
        if name not in EDITABLE_FIELDS:
            raise HTTPException(status_code=422, detail=f'unknown field: {name}')
        if name in SECRET_FIELDS and value in ('', SECRET_MASK):
            continue  # blank/masked secret means "keep the current one"
        current = getattr(settings, name)
        try:
            changes[name] = type(current)(value)
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail=f'bad value for {name}: {value!r}')

    apply_config(app, changes)
    stored = dict(app.state.config_values)
    stored.update(changes)
    app.state.config_values = stored
    try:
        await app.state.config_repo.save(stored)
    except Exception:
        logger.exception('failed to persist config overrides; they apply until restart only')
        return {'ok': True, 'persisted': False}
    return {'ok': True, 'persisted': True}


def apply_config(app, changes: dict) -> None:
    """Mutate live settings and rebuild the LLM client / engine."""
    from monty_claw.rlm.engine import RlmEngine
    from monty_claw.rlm.llm import OpenAiLlmClient

    if not changes:
        return
    settings = app.state.settings
    for name, value in changes.items():
        setattr(settings, name, value)
    app.state.engine = RlmEngine(
        pool=app.state.pool,
        llm=OpenAiLlmClient(settings.llm_base_url, settings.llm_api_key, settings.llm_model),
        repo=app.state.repo,
        storage=app.state.storage,
        settings=settings,
    )
