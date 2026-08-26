"""Resource limits, type checking, and the Monty error taxonomy.

Untrusted code can loop forever, eat memory, or recurse to death. Monty
enforces `ResourceLimits` *inside* the sandbox, and the pool's
`request_timeout` is a host-side backstop that kills a wedged worker
(raising MontyCrashedError) and transparently replaces it.

Run: uv run snippets/03_limits_and_errors.py
"""

from pydantic_monty import (
    Monty,
    MontyRuntimeError,
    MontySyntaxError,
    MontyTypingError,
    ResourceLimits,
)


def main() -> None:
    limits: ResourceLimits = {
        'max_duration_secs': 1.0,       # wall-clock budget for execution
        'max_memory': 50 * 1024 * 1024, # 50 MiB heap cap
        'max_recursion_depth': 100,     # stack depth cap
    }

    with Monty(request_timeout=10.0) as pool:
        # --- runtime limits ------------------------------------------------
        # Note: max_duration_secs is a budget for the whole session, so each
        # demo gets its own checkout.
        with pool.checkout(limits=limits) as session:
            try:
                session.feed_run('while True: pass')
            except MontyRuntimeError as e:
                print('infinite loop stopped:', e.display(format='type-msg'))

        with pool.checkout(limits=limits) as session:
            try:
                session.feed_run('def f(n): return f(n + 1)\nf(0)')
            except MontyRuntimeError as e:
                print('runaway recursion:', e.display(format='type-msg'))

        with pool.checkout(limits=limits) as session:
            try:
                session.feed_run("x = ['boom' * 1000] * 1_000_000\ny = [list(s) for s in x]")
            except MontyRuntimeError as e:
                print('memory bomb:', e.display(format='type-msg'))

        # --- syntax errors -------------------------------------------------
        with pool.checkout() as session:
            try:
                session.feed_run('def broken(:')
            except MontySyntaxError as e:
                print('\nsyntax error:', e.display(format='type-msg'))

            # Runtime errors carry a real traceback you can inspect frame by frame.
            try:
                session.feed_run('def inner():\n    return 1 / 0\ninner()')
            except MontyRuntimeError as e:
                print('\nfull traceback:')
                print(e.display(format='traceback'))
                frames = e.traceback()
                print('deepest frame:', frames[-1].dict())

        # --- static type checking (powered by ty) --------------------------
        # With type_check=True every snippet is type-checked *before* it runs;
        # each accepted snippet extends the typing context for the next.
        with pool.checkout(type_check=True, type_check_format='concise') as session:
            session.feed_run('def double(x: int) -> int:\n    return x * 2')
            try:
                session.feed_run("double('not an int')")
            except MontyTypingError as e:
                print('\nrejected before execution:')
                print(e.display())


if __name__ == '__main__':
    main()
