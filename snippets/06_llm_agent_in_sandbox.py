"""An agent loop *written in sandboxed Python* that calls a local LLM.

This is monty's headline use case, inverted from the usual "LLM writes
code" pattern: here the agent's control flow runs inside the sandbox, and
`call_llm` is an async *external function* — the only door to the outside
world. The sandbox cannot reach the network; the host makes the actual
call to Ollama (model: minimax-m3:cloud) and feeds the answer back in.

Because externals may be coroutines, we use AsyncMonty. Type stubs +
type_check=True mean the agent code is statically checked before it runs.

Requires: `ollama serve` running with the minimax-m3:cloud model available.
Run: uv run snippets/06_llm_agent_in_sandbox.py
"""

import asyncio

from openai import AsyncOpenAI

import pydantic_monty

OLLAMA_MODEL = 'minimax-m3:cloud'

client = AsyncOpenAI(base_url='http://localhost:11434/v1', api_key='ollama')


async def call_llm(messages: list[dict]) -> str:
    """Host-side external function: the sandbox calls this, we hit Ollama."""
    response = await client.chat.completions.create(
        model=OLLAMA_MODEL,
        messages=messages,  # type: ignore[arg-type] # noqa
    )
    return response.choices[0].message.content or ''


# The agent itself: plain Python, but it runs inside monty. It can loop,
# branch, and keep state — while the host stays in control of every LLM call.
AGENT_CODE = """
async def agent(question: str) -> str:
    messages: Messages = [
        {'role': 'system', 'content': 'Answer tersely. If your answer needs a '
         'follow-up check, end your reply with the single line NEED_CHECK.'},
        {'role': 'user', 'content': question},
    ]
    for turn in range(3):
        reply = await call_llm(messages)
        print(f'[turn {turn}] got {len(reply)} chars')
        if 'NEED_CHECK' not in reply:
            return reply
        messages.append({'role': 'assistant', 'content': reply})
        messages.append({'role': 'user', 'content': 'Double-check and give the final answer.'})
    return reply

await agent(question)
"""

# Stubs describing what the host will provide, so type_check=True can
# verify the agent code against them before anything executes.
TYPE_STUBS = """
from typing import Any

Messages = list[dict[str, Any]]

async def call_llm(messages: Messages) -> str: ...

question: str = ''
"""


async def main() -> None:
    async with pydantic_monty.AsyncMonty() as pool:
        async with pool.checkout(
            script_name='agent.py',
            type_check=True,
            type_check_stubs=TYPE_STUBS,
            limits={'max_duration_secs': 120.0, 'max_memory': 100 * 1024 * 1024},
        ) as session:
            answer = await session.feed_run(
                AGENT_CODE,
                inputs={'question': 'What is the capital of Thailand, and what river runs through it?'},
                external_lookup={'call_llm': call_llm},
            )
    print('\nagent answer:\n', answer)


if __name__ == '__main__':
    asyncio.run(main())
