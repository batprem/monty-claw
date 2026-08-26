"""External functions: let sandboxed code call back into the host, safely.

The sandbox has no network, filesystem, or environment access. The *only*
doors out are the ones you open via `external_lookup`: names the snippet
leaves undefined are resolved lazily against host values, and callable
entries become host functions the sandbox can invoke.

Run: uv run snippets/02_external_functions.py
"""

from pydantic_monty import CollectString, Monty, MontyRuntimeError

FAKE_DB = {
    'ada': {'role': 'engineer', 'karma': 1815},
    'grace': {'role': 'admiral', 'karma': 1906},
}


def get_user(username: str) -> dict:
    """Host function: the sandbox can call this but can't see FAKE_DB itself."""
    try:
        return FAKE_DB[username]
    except KeyError:
        # Exceptions raised here propagate into the sandbox as real exceptions.
        raise LookupError(f'no such user: {username}')


def main() -> None:
    code = """
report = {}
for name in usernames:
    user = get_user(name)
    report[name] = user['karma']
print(f'checked {len(report)} users')
total_karma = sum(report.values())
{'report': report, 'total': total_karma}
"""

    with Monty() as pool:
        with pool.checkout(script_name='karma.py') as session:
            # Capture the sandbox's print() output instead of letting it hit
            # the host stdout.
            printed = CollectString()
            result = session.feed_run(
                code,
                # eager values, bound as globals up front
                inputs={'usernames': ['ada', 'grace']},
                # lazy host functions/values, resolved on demand
                external_lookup={'get_user': get_user},
                print_callback=printed,
            )
            print('result:', result)
            print('sandbox printed:', printed.output.strip())

            # Host exceptions surface inside the sandbox and, if unhandled
            # there, come back to us as MontyRuntimeError with a traceback.
            try:
                session.feed_run(
                    "get_user('nobody')",
                    external_lookup={'get_user': get_user},
                )
            except MontyRuntimeError as e:
                print('\nsandbox error, as expected:')
                print(e.display(format='type-msg'))  # LookupError: no such user: nobody

            # The sandbox can also *handle* host exceptions like normal ones.
            result = session.feed_run(
                'try:\n'
                "    msg = get_user('nobody')\n"
                'except LookupError as e:\n'
                "    msg = f'recovered: {e}'\n"
                'msg',
                external_lookup={'get_user': get_user},
            )
            print('\nhandled inside sandbox:', result)


if __name__ == '__main__':
    main()
