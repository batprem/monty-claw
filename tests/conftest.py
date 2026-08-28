from pathlib import Path

import pytest

from monty_claw.config import Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,  # isolate tests from the developer's .env
        storage_backend='local',
        local_storage_dir=tmp_path / 'blobs',
        max_iterations=4,
        turn_deadline_secs=30.0,
        monty_max_duration_secs=10.0,
        history_max_turns=5,
    )


class InMemoryChatRepo:
    def __init__(self) -> None:
        self.records: dict[str, object] = {}

    async def get(self, chat_key: str):
        return self.records.get(chat_key)

    async def save(self, record) -> None:
        self.records[record.chat_key] = record

    async def delete(self, chat_key: str) -> None:
        self.records.pop(chat_key, None)


class FakeLlm:
    """Returns scripted replies in order; repeats the last one when exhausted."""

    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.calls: list[list[dict]] = []

    async def complete(self, messages: list[dict]) -> str:
        self.calls.append(messages)
        if len(self.replies) > 1:
            return self.replies.pop(0)
        return self.replies[0]


@pytest.fixture
def repo() -> InMemoryChatRepo:
    return InMemoryChatRepo()
