"""Snapshots: pause execution mid-flight, serialize it, resume it later.

`feed_start` runs code until it hits something the host must answer — an
external function call, an undefined name — and hands back a *snapshot*
instead of blocking. You can answer it with `resume(...)`, drive it
automatically with `resume_auto()`, or `dump()` the entire suspended
interpreter to bytes and restore it later (even in a different process).

This is the mechanism behind durable/resumable AI agents: an agent's code
can be parked at an LLM call, persisted to a database, and woken up days
later on another machine.

Run: uv run snippets/04_snapshots.py
"""

from pydantic_monty import (
    FunctionSnapshot,
    Monty,
    MontyComplete,
    NameLookupSnapshot,
)

CODE = """
total = 0
for city in ['tokyo', 'paris', 'bangkok']:
    total += get_population(city)
total
"""

POPULATIONS = {'tokyo': 37_000_000, 'paris': 11_000_000, 'bangkok': 17_000_000}


def main() -> None:
    with Monty() as pool:
        # --- manual stepping ----------------------------------------------
        with pool.checkout() as session:
            snapshot = session.feed_start(CODE)
            step = 0
            while not isinstance(snapshot, MontyComplete):
                if isinstance(snapshot, FunctionSnapshot):
                    city = snapshot.args[0]
                    print(f'step {step}: sandbox wants {snapshot.function_name}({city!r})')
                    snapshot = snapshot.resume({'return_value': POPULATIONS[city]})
                elif isinstance(snapshot, NameLookupSnapshot):
                    print(f'step {step}: sandbox wants name {snapshot.variable_name!r}')
                    snapshot = snapshot.resume(value=POPULATIONS)
                step += 1
            print('total population:', snapshot.output)

        # --- dump mid-execution, restore in a fresh session ---------------
        with pool.checkout() as session:
            snapshot = session.feed_start(CODE)
            assert isinstance(snapshot, FunctionSnapshot)
            # Suspended at the first get_population() call: freeze everything.
            frozen: bytes = snapshot.dump()
            print(f'\nsuspended at {snapshot.function_name}{snapshot.args}, '
                  f'dumped {len(frozen)} bytes')

        # Pretend time passed / the process restarted. A fresh session
        # restores the dump and re-announces the same pending call.
        with pool.checkout() as session:
            snapshot = session.load_snapshot(
                frozen,
                external_lookup={'get_population': POPULATIONS.get},
            )
            # resume_auto answers each suspension from external_lookup.
            while not isinstance(snapshot, MontyComplete):
                snapshot = snapshot.resume_auto()
            print('restored + finished, total:', snapshot.output)

        # --- session dumps (between feeds) ---------------------------------
        with pool.checkout() as session:
            session.feed_run('history = [1, 2, 3]')
            saved = session.dump()
        with pool.checkout() as session:
            session.load_session(saved)
            print('globals survived the move:', session.feed_run('history'))


if __name__ == '__main__':
    main()
