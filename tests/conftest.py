from pathlib import Path

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from monty_claw.config import Settings
from monty_claw.db.base import ChatRecord


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
        self.records: dict[str, ChatRecord] = {}

    async def get(self, chat_key: str) -> ChatRecord | None:
        return self.records.get(chat_key)

    async def save(self, record: ChatRecord) -> None:
        self.records[record.chat_key] = record

    async def delete(self, chat_key: str) -> None:
        self.records.pop(chat_key, None)


def _fresh(response: ModelResponse) -> ModelResponse:
    """Copy a scripted response so each replay gets its own tool-call ids."""
    parts = [
        ToolCallPart(p.tool_name, p.args) if isinstance(p, ToolCallPart) else TextPart(p.content)
        for p in response.parts
    ]
    return ModelResponse(parts=parts)


class ScriptedModel(FunctionModel):
    """Replays scripted model responses, cycling when the script runs out.

    The `llm_query` sub-agent shares this model; its runs are told apart by
    having no tools and answered from `sub_replies`.
    """

    def __init__(self, responses: list[ModelResponse], sub_replies: list[str] | None = None) -> None:
        self.responses = list(responses)
        self.sub_replies = list(sub_replies or [])
        self.requests: list[list[ModelMessage]] = []
        self._next_index = 0
        super().__init__(self._respond)

    def _respond(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if not info.function_tools:  # the sub-model behind `llm_query`
            return ModelResponse(parts=[TextPart(self.sub_replies.pop(0))])
        self.requests.append(messages)
        response = self.responses[self._next_index % len(self.responses)]
        self._next_index += 1
        return _fresh(response)


@pytest.fixture
def repo() -> InMemoryChatRepo:
    return InMemoryChatRepo()
