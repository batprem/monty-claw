import pytest
from pydantic_monty import AsyncMonty

from monty_claw.config import Settings
from monty_claw.rlm.engine import FALLBACK_EXHAUSTED, RlmEngine
from monty_claw.storage.local import LocalStorage

from .conftest import FakeLlm, InMemoryChatRepo


@pytest.fixture
async def pool():
    async with AsyncMonty() as p:
        yield p


def make_engine(pool, settings: Settings, repo: InMemoryChatRepo, llm: FakeLlm) -> RlmEngine:
    return RlmEngine(
        pool=pool,
        llm=llm,
        repo=repo,
        storage=LocalStorage(settings.local_storage_dir),
        settings=settings,
    )


async def test_single_shot_final(pool, settings, repo) -> None:
    llm = FakeLlm(['```python\nFINAL = "hi there"\n```'])
    engine = make_engine(pool, settings, repo, llm)
    reply = await engine.run_turn('t:1', 'hello')
    assert reply == 'hi there'
    record = repo.records['t:1']
    assert record.session_blob_key == 'sessions/t/1.bin'


async def test_error_self_repair(pool, settings, repo) -> None:
    llm = FakeLlm([
        '```python\nFINAL = undefined_name\n```',
        '```python\nFINAL = "fixed"\n```',
    ])
    engine = make_engine(pool, settings, repo, llm)
    reply = await engine.run_turn('t:1', 'go')
    assert reply == 'fixed'
    # The second LLM call must have seen the error feedback.
    assert any('Your code failed' in m['content'] for m in llm.calls[1])


async def test_state_persists_across_turns(pool, settings, repo) -> None:
    llm1 = FakeLlm(['```python\nsecret = "tangerine"\nFINAL = "noted"\n```'])
    engine = make_engine(pool, settings, repo, llm1)
    assert await engine.run_turn('t:1', 'remember tangerine') == 'noted'

    llm2 = FakeLlm(['```python\nFINAL = "you said " + secret\n```'])
    engine2 = make_engine(pool, settings, repo, llm2)
    assert await engine2.run_turn('t:1', 'what did I say?') == 'you said tangerine'


async def test_llm_query_from_sandbox(pool, settings, repo) -> None:
    llm = FakeLlm([
        '```python\nanswer = await llm_query("sub question")\nFINAL = "sub said: " + answer\n```',
        'SUB-ANSWER',
    ])

    # llm_query routes through the same client; scripted FakeLlm pops in order,
    # so the sub-call gets 'SUB-ANSWER'.
    engine = make_engine(pool, settings, repo, llm)
    reply = await engine.run_turn('t:1', 'ask a sub-llm')
    assert reply == 'sub said: SUB-ANSWER'


async def test_iteration_cap(pool, settings, repo) -> None:
    llm = FakeLlm(['```python\nprint("thinking...")\n```'])  # never sets FINAL
    engine = make_engine(pool, settings, repo, llm)
    reply = await engine.run_turn('t:1', 'loop forever')
    assert reply == FALLBACK_EXHAUSTED
    assert len(llm.calls) == settings.max_iterations


async def test_multi_iteration_with_stdout_feedback(pool, settings, repo) -> None:
    llm = FakeLlm([
        '```python\nx = 21\nprint("x is", x)\n```',
        '```python\nFINAL = str(x * 2)\n```',
    ])
    engine = make_engine(pool, settings, repo, llm)
    reply = await engine.run_turn('t:1', 'compute')
    assert reply == '42'
    assert any('x is 21' in m['content'] for m in llm.calls[1])


async def test_corrupt_dump_recovery(pool, settings, repo) -> None:
    storage = LocalStorage(settings.local_storage_dir)
    await storage.put_bytes('sessions/t/1.bin', b'not a real dump')
    from monty_claw.db.base import ChatRecord

    repo.records['t:1'] = ChatRecord(chat_key='t:1', session_blob_key='sessions/t/1.bin')

    llm = FakeLlm(['```python\nFINAL = "recovered"\n```'])
    engine = make_engine(pool, settings, repo, llm)
    assert await engine.run_turn('t:1', 'hi') == 'recovered'
    # Model was told the environment reset.
    assert any('reset' in m['content'] for m in llm.calls[0])


async def test_send_progress_external(pool, settings, repo) -> None:
    sent: list[str] = []

    async def send_progress(text: str) -> None:
        sent.append(text)

    llm = FakeLlm(['```python\nawait send_message("working on it")\nFINAL = "done"\n```'])
    engine = make_engine(pool, settings, repo, llm)
    assert await engine.run_turn('t:1', 'go', send_progress) == 'done'
    assert sent == ['working on it']
