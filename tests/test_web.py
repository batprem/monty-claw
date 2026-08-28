import httpx
import pytest

from monty_claw.app import create_app
from monty_claw.config import Settings

from .conftest import InMemoryChatRepo

# Unreachable-but-fast Mongo so app startup/config persistence fails quickly.
FAST_FAIL_MONGO = 'mongodb://localhost:9/?serverSelectionTimeoutMS=100&connectTimeoutMS=100'


class FakeConfigRepo:
    def __init__(self) -> None:
        self.saved: dict | None = None

    async def load(self) -> dict:
        return self.saved or {}

    async def save(self, values: dict) -> None:
        self.saved = values


class StubEngine:
    async def run_turn(self, chat_key, user_text, send_progress=None) -> str:
        if send_progress is not None:
            await send_progress('working…')
        return f'echo: {user_text}'


@pytest.fixture
async def client(tmp_path):
    settings = Settings(
        _env_file=None,  # isolate tests from the developer's .env
        telegram_bot_token='test-token',
        web_username='prem',
        web_password='hunter2',
        mongodb_uri=FAST_FAIL_MONGO,
        storage_backend='local',
        local_storage_dir=tmp_path / 'blobs',
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        app.state.repo = InMemoryChatRepo()
        app.state.config_repo = FakeConfigRepo()
        app.state.engine = StubEngine()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url='http://test') as c:
            c.app = app  # give tests access to app.state
            yield c


async def login(client) -> None:
    response = await client.post('/api/login', json={'username': 'prem', 'password': 'hunter2'})
    assert response.status_code == 200


async def test_index_served(client) -> None:
    response = await client.get('/')
    assert response.status_code == 200
    assert 'monty-claw' in response.text


async def test_login_rejects_bad_credentials(client) -> None:
    for username, password in [('prem', 'wrong'), ('other', 'hunter2'), ('', '')]:
        response = await client.post('/api/login', json={'username': username, 'password': password})
        assert response.status_code == 401


async def test_api_requires_auth(client) -> None:
    for method, path in [('GET', '/api/me'), ('GET', '/api/history'), ('GET', '/api/config')]:
        assert (await client.request(method, path)).status_code == 401
    assert (await client.post('/api/chat', json={'message': 'hi'})).status_code == 401
    assert (await client.put('/api/config', json={})).status_code == 401


async def test_login_and_chat_flow(client) -> None:
    await login(client)
    me = await client.get('/api/me')
    assert me.json() == {'username': 'prem'}

    response = await client.post('/api/chat', json={'message': 'hello'})
    assert response.status_code == 200
    assert response.json() == {'reply': 'echo: hello', 'progress': ['working…']}

    history = (await client.get('/api/history')).json()['transcript']
    assert [m['content'] for m in history] == ['hello', 'working…', 'echo: hello']
    assert history[1].get('progress') is True


async def test_chat_reset_wipes_state(client) -> None:
    await login(client)
    await client.post('/api/chat', json={'message': 'hello'})
    response = await client.post('/api/chat', json={'message': '/reset'})
    assert 'wiped' in response.json()['reply'].lower()
    assert (await client.get('/api/history')).json()['transcript'] == []


async def test_config_roundtrip(client) -> None:
    await login(client)
    config = (await client.get('/api/config')).json()
    assert config['editable']['llm_model'] == 'minimax-m3:cloud'
    assert config['editable']['llm_api_key'] == '••••••••'  # masked, never echoed

    response = await client.put(
        '/api/config', json={'llm_model': 'gpt-oss:120b-cloud', 'max_iterations': 5}
    )
    assert response.json() == {'ok': True, 'persisted': True}

    config = (await client.get('/api/config')).json()
    assert config['editable']['llm_model'] == 'gpt-oss:120b-cloud'
    assert config['editable']['max_iterations'] == 5


async def test_config_rejects_unknown_and_bad_values(client) -> None:
    await login(client)
    assert (await client.put('/api/config', json={'mongodb_uri': 'x'})).status_code == 422
    assert (await client.put('/api/config', json={'max_iterations': 'lots'})).status_code == 422


async def test_masked_api_key_is_kept(client) -> None:
    await login(client)
    response = await client.put('/api/config', json={'llm_api_key': '••••••••', 'llm_model': 'm2'})
    assert response.status_code == 200
    # The mask is treated as "keep the current key": it must be applied
    # nowhere and never persisted.
    assert client.app.state.settings.llm_api_key == 'ollama'
    assert 'llm_api_key' not in client.app.state.config_repo.saved
    assert client.app.state.config_repo.saved['llm_model'] == 'm2'


async def test_web_disabled_without_credentials(tmp_path) -> None:
    settings = Settings(
        _env_file=None,  # isolate tests from the developer's .env
        telegram_bot_token='t',
        mongodb_uri=FAST_FAIL_MONGO,
        storage_backend='local',
        local_storage_dir=tmp_path / 'blobs',
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url='http://test') as c:
            response = await c.post('/api/login', json={'username': 'a', 'password': 'b'})
            assert response.status_code == 503
            assert (await c.get('/api/me')).status_code == 503
