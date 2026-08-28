import httpx
import pytest

from monty_claw.app import create_app
from monty_claw.config import Settings


@pytest.fixture
async def client(tmp_path):
    settings = Settings(
        _env_file=None,  # isolate tests from the developer's .env
        telegram_bot_token='test-token',
        telegram_secret_token='s3cret',
        storage_backend='local',
        local_storage_dir=tmp_path / 'blobs',
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url='http://test') as c:
            yield c


async def test_healthz(client) -> None:
    response = await client.get('/healthz')
    assert response.status_code == 200
    assert response.json() == {'ok': True}


async def test_webhook_rejects_bad_secret(client) -> None:
    response = await client.post('/webhook/telegram', json={'update_id': 1})
    assert response.status_code == 403
    response = await client.post(
        '/webhook/telegram',
        json={'update_id': 1},
        headers={'X-Telegram-Bot-Api-Secret-Token': 'wrong'},
    )
    assert response.status_code == 403


async def test_webhook_ignores_non_text_update(client) -> None:
    response = await client.post(
        '/webhook/telegram',
        json={'update_id': 1, 'message': {'photo': [], 'chat': {'id': 5}}},
        headers={'X-Telegram-Bot-Api-Secret-Token': 's3cret'},
    )
    assert response.status_code == 200
