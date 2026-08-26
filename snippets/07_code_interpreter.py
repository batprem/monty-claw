"""A code-interpreter tool: the LLM writes Python, monty runs it safely.

The classic pattern — give a local LLM (Ollama, minimax-m3:cloud) a
"run_python" capability without giving it your machine. Generated code
executes in a monty sandbox with hard resource limits and zero ambient
authority (no network, no filesystem, no env). Errors are fed back to the
model so it can fix its own code and retry.

Requires: `ollama serve` running with the minimax-m3:cloud model available.
Run: uv run snippets/07_code_interpreter.py
"""

from openai import OpenAI

from pydantic_monty import CollectString, Monty, MontyError

OLLAMA_MODEL = 'minimax-m3:cloud'

client = OpenAI(base_url='http://localhost:11434/v1', api_key='ollama')

SYSTEM_PROMPT = """\
You write Python to answer the user's question. Reply with ONLY a Python
code block. Rules for the sandbox your code runs in:
- Python subset: no classes with inheritance, no third-party imports.
- Allowed stdlib: math, json, re, datetime, itertools, functools, collections, dataclasses.
- End the snippet with a single trailing expression: the final answer.
- You may print() intermediate work; the trailing expression is the result.
"""


def extract_code(reply: str) -> str:
    if '```' in reply:
        block = reply.split('```')[1]
        return block.removeprefix('python').strip()
    return reply.strip()


def solve(question: str, max_attempts: int = 3) -> None:
    print(f'Q: {question}')
    messages = [
        {'role': 'system', 'content': SYSTEM_PROMPT},
        {'role': 'user', 'content': question},
    ]

    with Monty() as pool:
        for attempt in range(1, max_attempts + 1):
            response = client.chat.completions.create(model=OLLAMA_MODEL, messages=messages)  # type: ignore[arg-type]
            reply = response.choices[0].message.content or ''
            code = extract_code(reply)
            print(f'\n--- attempt {attempt}: generated code ---\n{code}\n')

            # Fresh session per attempt: no state leaks between tries.
            with pool.checkout(
                script_name='generated.py',
                limits={
                    'max_duration_secs': 5.0,
                    'max_memory': 100 * 1024 * 1024,
                    'max_recursion_depth': 200,
                },
            ) as session:
                printed = CollectString()
                try:
                    result = session.feed_run(code, print_callback=printed)
                except MontyError as e:
                    # Syntax, typing, or runtime failure — show the model its
                    # own traceback and let it try again.
                    error = e.display(format='traceback') if hasattr(e, 'display') else str(e)
                    print(f'sandbox rejected it:\n{error}')
                    messages.append({'role': 'assistant', 'content': reply})
                    messages.append({
                        'role': 'user',
                        'content': f'Your code failed:\n{error}\nFix it and resend only the code block.',
                    })
                    continue

            if printed.output:
                print('printed during run:', printed.output.strip())
            print(f'\n=> answer: {result!r}')
            return

    print('\ngave up after', max_attempts, 'attempts')


if __name__ == '__main__':
    solve('What are the first 5 prime numbers p where p, p+2 is a twin prime pair? '
          'Return them as a list of [p, p+2] pairs.')
