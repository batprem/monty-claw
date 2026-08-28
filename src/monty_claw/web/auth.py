"""Stateless signed-cookie sessions for the web UI.

Tokens are HMAC-signed and self-contained, so they survive Cloud Run
scale-to-zero without any server-side session store.
"""

import base64
import hashlib
import hmac
import time

from monty_claw.config import Settings

COOKIE_NAME = 'mc_session'


def secret_for(settings: Settings) -> str:
    if settings.web_secret_key:
        return settings.web_secret_key
    seed = f'{settings.web_username}:{settings.web_password}:{settings.telegram_bot_token}'
    return hashlib.sha256(seed.encode()).hexdigest()


def _sign(payload: str, secret: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def make_token(username: str, secret: str, ttl_secs: int) -> str:
    payload = f'{username}:{int(time.time()) + ttl_secs}'
    return base64.urlsafe_b64encode(f'{payload}:{_sign(payload, secret)}'.encode()).decode()


def verify_token(token: str, secret: str) -> str | None:
    try:
        username, expiry, signature = base64.urlsafe_b64decode(token.encode()).decode().rsplit(':', 2)
        expires_at = int(expiry)
    except (ValueError, UnicodeDecodeError):
        return None
    if not hmac.compare_digest(signature, _sign(f'{username}:{expiry}', secret)):
        return None
    if expires_at < time.time():
        return None
    return username


def check_credentials(settings: Settings, username: str, password: str) -> bool:
    if not settings.web_username or not settings.web_password:
        return False
    return hmac.compare_digest(username, settings.web_username) and hmac.compare_digest(
        password, settings.web_password
    )
