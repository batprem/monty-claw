from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelResponse, TextPart, ToolCallPart

from monty_claw.config import Settings
from monty_claw.db.base import ChatRecord
from monty_claw.rlm.engine import FALLBACK_EXHAUSTED, RlmEngine, trim_history
from monty_claw.storage.local import LocalStorage

from .conftest import InMemoryChatRepo, ScriptedModel


def run_code(code: str) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart('run_code', {'code': code})])


def say(text: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(text)])


def make_engine(settings: Settings, repo: InMemoryChatRepo, model: ScriptedModel) -> RlmEngine:
    return RlmEngine(
        repo=repo,
        storage=LocalStorage(settings.local_storage_dir),
        settings=settings,
        model=model,
    )


async def test_single_shot_reply(settings, repo) -> None:
    engine = make_engine(settings, repo, ScriptedModel([say('hi there')]))
    assert await engine.run_turn('t:1', 'hello') == 'hi there'
    assert repo.records['t:1'].session_blob_key == 'sessions/t/1.json'


async def test_code_mode_computes_the_answer(settings, repo) -> None:
    model = ScriptedModel([run_code('x = 21\nx * 2'), say('42')])
    engine = make_engine(settings, repo, model)
    assert await engine.run_turn('t:1', 'double 21') == '42'
    # The sandbox really ran the snippet and handed the value back.
    assert '42' in str(model.requests[1][-1].parts)


async def test_tools_are_callable_from_the_sandbox(settings, repo) -> None:
    sent: list[str] = []

    async def send_progress(text: str) -> None:
        sent.append(text)

    model = ScriptedModel(
        [
            run_code(
                'await send_message(text="working on it")\n'
                'answer = await llm_query(prompt="sub question")\n'
                'answer'
            ),
            say('done'),
        ],
        # The sub-agent shares this model; its run consumes the third response.
        sub_replies=['SUB-ANSWER'],
    )
    engine = make_engine(settings, repo, model)
    assert await engine.run_turn('t:1', 'go', send_progress) == 'done'
    assert sent == ['working on it']
    assert 'SUB-ANSWER' in str(model.requests[1][-1].parts)


async def test_sandbox_error_is_retried(settings, repo) -> None:
    model = ScriptedModel([run_code('undefined_name'), run_code('"ok"'), say('recovered')])
    engine = make_engine(settings, repo, model)
    assert await engine.run_turn('t:1', 'go') == 'recovered'
    # The model saw the sandbox error before its second attempt.
    assert 'undefined_name' in str(model.requests[1][-1].parts)


async def test_history_persists_across_turns(settings, repo) -> None:
    engine = make_engine(settings, repo, ScriptedModel([say('noted')]))
    assert await engine.run_turn('t:1', 'remember tangerine') == 'noted'

    model = ScriptedModel([say('you said tangerine')])
    engine2 = make_engine(settings, repo, model)
    assert await engine2.run_turn('t:1', 'what did I say?') == 'you said tangerine'
    # The second turn was primed with the first turn's messages.
    assert 'tangerine' in str(model.requests[0][0].parts)


async def test_request_limit(settings, repo) -> None:
    # Never produces a final text part, so every step is another request.
    model = ScriptedModel([run_code('1')] * 20)
    engine = make_engine(settings, repo, model)
    assert await engine.run_turn('t:1', 'loop forever') == FALLBACK_EXHAUSTED
    assert len(model.requests) == settings.max_iterations
    # A blown-up turn leaves the stored history untouched.
    assert 't:1' not in repo.records


async def test_corrupt_history_recovery(settings, repo) -> None:
    storage = LocalStorage(settings.local_storage_dir)
    await storage.put_bytes('sessions/t/1.bin', b'not a real history')
    repo.records['t:1'] = ChatRecord(chat_key='t:1', session_blob_key='sessions/t/1.bin')

    model = ScriptedModel([say('recovered')])
    engine = make_engine(settings, repo, model)
    assert await engine.run_turn('t:1', 'hi') == 'recovered'
    assert repo.records['t:1'].session_blob_key == 'sessions/t/1.json'
    assert await storage.get_bytes('sessions/t/1.bin') is None  # stale blob cleaned up


async def test_history_is_trimmed_at_user_boundaries(settings, repo) -> None:
    engine = make_engine(settings, repo, ScriptedModel([run_code('1'), say('ok')]))
    for i in range(settings.history_max_turns + 3):
        assert await engine.run_turn('t:1', f'message {i}') == 'ok'

    storage = LocalStorage(settings.local_storage_dir)
    raw = await storage.get_bytes('sessions/t/1.json')
    messages = ModelMessagesTypeAdapter.validate_json(raw)
    prompts = [p.content for m in messages for p in m.parts if getattr(p, 'part_kind', '') == 'user-prompt']
    assert len(prompts) == settings.history_max_turns
    assert prompts[-1] == f'message {settings.history_max_turns + 2}'
    # Trimming cut at a user turn, so no tool return is left orphaned.
    assert trim_history(messages, settings.history_max_turns) == messages


async def test_soul_names_the_agent(settings, repo) -> None:
    model = ScriptedModel([say("I'm MontyClaw.")])
    engine = make_engine(settings, repo, model)
    assert await engine.run_turn('t:1', 'who are you?') == "I'm MontyClaw."
    # The identity travels with every request, not just the first turn.
    assert 'MontyClaw' in (model.requests[0][0].instructions or '')
