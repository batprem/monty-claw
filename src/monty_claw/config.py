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

    # Image generation. 'cursor' runs a Cursor agent that writes and executes
    # a Pillow script per prompt; 'mock' is the hash-derived placeholder.
    # Without CURSOR_API_KEY, 'cursor' degrades to 'mock'.
    image_backend: Literal['cursor', 'mock'] = 'cursor'
    cursor_api_key: str = ''
    cursor_model: str = 'composer-2.5'
    # A generation is a whole agent turn, so this is tens of seconds. It has to
    # fit inside `turn_deadline_secs` or the turn dies before the fallback runs.
    cursor_image_timeout_secs: float = 40.0

    # Database (MongoDB) — structured chat state
    mongodb_uri: str = 'mongodb://localhost:27017'
    mongodb_db: str = 'monty_claw'

    # Blob storage — Monty session dumps
    storage_backend: Literal['gcs', 'local'] = 'local'
    gcs_bucket: str = ''
    local_storage_dir: Path = Path('.monty_claw_state')

    # Public origin of this deployment (e.g. https://monty-claw-xyz.run.app),
    # used to build shareable URLs for generated media. Left empty, those URLs
    # are site-relative, which only works from the web UI itself.
    public_base_url: str = ''

    # Web UI (disabled unless both username and password are set)
    web_username: str = ''
    web_password: str = ''
    # Cookie-signing secret; derived from credentials if left empty.
    web_secret_key: str = ''
    web_session_ttl_secs: int = 30 * 24 * 3600

    # Agent engine knobs
    # Model requests allowed in one turn (Pydantic AI usage limit).
    max_iterations: int = 8
    # Nested tool calls one `run_code` snippet may dispatch.
    max_tool_calls: int = 100
    # User turns of conversation kept as the assistant's memory.
    history_max_turns: int = 20
    monty_max_duration_secs: float = 30.0
    monty_max_memory: int = 200 * 1024 * 1024
    # Stay under Telegram's ~60s webhook patience; Cloud Run timeout is headroom.
    turn_deadline_secs: float = 50.0

    port: int = 8080


def get_settings() -> Settings:
    return Settings()
