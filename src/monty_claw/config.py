"""Environment-driven configuration for monty-claw."""

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    # Telegram
    telegram_bot_token: str = ''
    telegram_secret_token: str = ''

    # LLM (any OpenAI-compatible endpoint; defaults target local Ollama)
    llm_base_url: str = 'http://localhost:11434/v1'
    llm_model: str = 'minimax-m3:cloud'
    llm_api_key: str = 'ollama'

    # Database (MongoDB) — structured chat state
    mongodb_uri: str = 'mongodb://localhost:27017'
    mongodb_db: str = 'monty_claw'

    # Blob storage — Monty session dumps
    storage_backend: Literal['gcs', 'local'] = 'local'
    gcs_bucket: str = ''
    local_storage_dir: Path = Path('.monty_claw_state')

    # Web UI (disabled unless both username and password are set)
    web_username: str = ''
    web_password: str = ''
    # Cookie-signing secret; derived from credentials if left empty.
    web_secret_key: str = ''
    web_session_ttl_secs: int = 30 * 24 * 3600

    # RLM engine knobs
    max_iterations: int = 8
    stdout_max_bytes: int = 4096
    history_max_turns: int = 20
    monty_max_duration_secs: float = 30.0
    monty_max_memory: int = 200 * 1024 * 1024
    monty_max_recursion_depth: int = 200
    # Stay under Telegram's ~60s webhook patience; Cloud Run timeout is headroom.
    turn_deadline_secs: float = 50.0

    port: int = 8080


def get_settings() -> Settings:
    return Settings()
