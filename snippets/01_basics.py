"""Basics: run untrusted Python snippets in a Monty sandbox.

Monty (https://github.com/pydantic/monty) is a minimal, secure Python
interpreter written in Rust, designed to run LLM-generated code with
microsecond startup and no container overhead. Code always executes in a
subprocess worker pool, so even a crashing sandbox never harms the host.

Run: uv run snippets/01_basics.py
"""

from pydantic_monty import Monty


def main() -> None:
    # `Monty()` owns a pool of subprocess workers; `checkout()` gives you a
    # REPL-like session on one dedicated worker.
    with Monty() as pool:
        with pool.checkout() as session:
            # The trailing expression of a snippet is converted back to a
            # real Python object and returned.
            result = session.feed_run('1 + 1')
            print('1 + 1 =', result)  # 2

            # Session state (globals, functions) persists across feeds.
            session.feed_run('x = 21')
            print('x * 2 =', session.feed_run('x * 2'))  # 42

            # A useful subset of the stdlib is available inside the sandbox.
            result = session.feed_run(
                'import json\n'
                'from datetime import date\n'
                "json.dumps({'today': date(2026, 8, 26).isoformat()})"
            )
            print('json result:', result)

            # `inputs` eagerly binds host values as globals before the code runs.
            result = session.feed_run(
                'sorted(scores, reverse=True)[:top_n]',
                inputs={'scores': [3, 141, 59, 26, 535], 'top_n': 3},
            )
            print('top scores:', result)  # [535, 141, 59]

            # Rich values round-trip: dicts, lists, sets, tuples, dataclasses...
            result = session.feed_run(
                'from dataclasses import dataclass\n'
                '@dataclass\n'
                'class Point:\n'
                '    x: int\n'
                '    y: int\n'
                'Point(3, 4)'
            )
            print('dataclass comes back as:', result)


if __name__ == '__main__':
    main()
