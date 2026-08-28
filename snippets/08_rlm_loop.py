"""A minimal Recursive Language Model (RLM) loop, with monty as environment E.

RLMs (arXiv:2512.24601) process prompts far beyond the context window by
treating the prompt as *data in an external environment*: the long prompt
lives as a variable in a persistent Python REPL, the root LM iteratively
*writes code* that peeks into and slices it, and that code can call a
sub-LM (`llm_query`) on programmatically constructed excerpts — recursion
inside the code. The loop ends when the code sets a `FINAL` variable, so
the answer is assembled symbolically, never verbalized through the window.

The paper's reference implementation runs unrestricted CPython; here monty
plays the environment E, so the same loop is sandboxed, resource-limited,
and (via dump()/load_snapshot, see 04_snapshots.py) resumable.

Requires: `ollama serve` running with the minimax-m3:cloud model available.
Run: uv run snippets/08_rlm_loop.py
"""

import asyncio

from openai import AsyncOpenAI

from pydantic_monty import AsyncMonty, CollectString, MontyError

OLLAMA_MODEL = 'minimax-m3:cloud'

client = AsyncOpenAI(base_url='http://localhost:11434/v1', api_key='ollama')

# --- a "huge" prompt the root LM must never read directly -------------------
# Facts are scattered across chapters; only code + sub-LM calls can find them.
FINDINGS = {
    1: 'Bob found a silver flask beside the frozen river.',
    3: 'Alice uncovered a ring belonging to the fallen King Herod.',
    5: 'Alice found a cracked astrolabe in the monastery ruins.',
    7: 'Karn dug up a bronze compass from the ash fields.',
}
FILLER = 'The wind carried ash over the hills for days on end. ' * 20

HUGE_PROMPT = 'Task: list every item found, and by whom.\n\n' + '\n\n'.join(
    f'**Chapter {n}**\n{FILLER}{FINDINGS.get(n, "Nothing of note happened.")}\n{FILLER}'
    for n in range(1, 9)
)

SYSTEM_PROMPT = """\
You drive a Python REPL that holds a huge document in the variable `prompt`
(a str). You cannot read it directly — it is far larger than your window.
Reply with ONLY a Python code block each turn. Sandbox rules:
- Python subset: no third-party imports, stdlib subset only (re, json, math...).
- Inspect with print() — stdout comes back to you truncated to 4 KB.
- llm_query(text) -> str is an ASYNC sub-LM call: always `await llm_query(...)`.
- When you have the answer, assign it to a variable named FINAL (a str).
Follow this plan exactly:
Turn 1: print(prompt[:200]) to read the task.
Turn 2: delegate every chapter to the sub-LM and collect the answers:
    chapters = prompt.split('**Chapter')[1:]
    findings = []
    for c in chapters:
        findings.append(await llm_query('List any item found and by whom, or say NONE: ' + c))
    print(findings)
Turn 3: combine the non-NONE findings into one string and assign it to FINAL.
Do not explore chapter by chapter with print() — chapters are filler except
for at most one finding sentence each; llm_query reads them for you.
"""


def extract_code(reply: str) -> str:
    if '```' in reply:
        block = reply.split('```')[1]
        return block.removeprefix('python').strip()
    return reply.strip()


async def root_llm(messages: list[dict]) -> str:
    """The root LM: writes the next REPL turn."""
    response = await client.chat.completions.create(model=OLLAMA_MODEL, messages=messages)  # type: ignore[arg-type]
    return response.choices[0].message.content or ''


async def llm_query(text: str) -> str:
    """Sub-LM (depth=1): called *from inside* sandboxed code, via the host."""
    response = await client.chat.completions.create(
        model=OLLAMA_MODEL,
        messages=[
            {'role': 'system', 'content': 'Answer tersely from the given excerpt only.'},
            {'role': 'user', 'content': text},
        ],
    )
    return response.choices[0].message.content or ''


async def main() -> None:
    async with AsyncMonty() as pool:
        async with pool.checkout(script_name='rlm.py') as env:  # persistent E
            # 1. Load the huge prompt as a sandbox variable — the root LM
            #    only ever learns its length.
            await env.feed_run('pass', inputs={'prompt': HUGE_PROMPT})
            messages = [
                {'role': 'system', 'content': SYSTEM_PROMPT},
                {'role': 'user', 'content': f'len(prompt) = {len(HUGE_PROMPT)}. Begin.'},
            ]

            for iteration in range(1, 9):
                code = extract_code(await root_llm(messages))
                print(f'\n--- iteration {iteration}: root LM wrote ---\n{code}\n')
                messages.append({'role': 'assistant', 'content': code})

                # 2. Run the model's code inside monty; sub-LM calls happen
                #    through llm_query, stdout is captured and capped.
                out = CollectString(max_bytes=4096)
                try:
                    await env.feed_run(
                        code,
                        external_lookup={'llm_query': llm_query},
                        print_callback=out,
                    )
                except MontyError as e:
                    error = e.display(format='type-msg')
                    print('sandbox error:', error)
                    messages.append({'role': 'user', 'content': f'Error: {error}'})
                    continue
                messages.append({'role': 'user', 'content': f'stdout:\n{out.output}'})

                # 3. Symbolic exit: the answer leaves through the REPL, not
                #    the model's mouth. (`dir()` isn't in monty's subset, so
                #    probe FINAL with a NameError guard.)
                final = await env.feed_run(
                    'try:\n    _final = FINAL\nexcept NameError:\n    _final = None\n_final'
                )
                if final is not None:
                    print('\n=> FINAL answer from the environment:\n', final)
                    return

    print('\nno FINAL after 8 iterations')


if __name__ == '__main__':
    asyncio.run(main())
