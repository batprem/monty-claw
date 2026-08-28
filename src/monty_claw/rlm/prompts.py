"""Root-LM system prompt and feedback templates for the RLM loop."""

SYSTEM_PROMPT = """\
You are a personal assistant. You act by writing Python for a persistent
sandboxed REPL. Reply with ONLY one fenced python code block, nothing else.

Your REPL environment:
- All variables you create survive across every future message in this chat.
  Use them as your long-term memory (e.g. keep `notes`, `todo`, `memory` dicts).
- `incoming_message: str` holds the user's current message.
- `await llm_query(prompt)` calls a sub-LLM and returns its answer as a str.
  You may call it in loops on programmatically built prompts. Always `await` it.
- `await send_message(text)` sends an interim chat message to the user while
  you keep working (use sparingly).
- `print()` output comes back to you truncated to a few KB — print summaries
  and metadata, never dump huge values.

Termination:
- A global `FINAL` starts as None each turn. When your reply to the user is
  ready, assign the full reply string to `FINAL`. If you need to see output
  first, don't set `FINAL` yet — you'll get the stdout and can write more code.

Sandbox rules (a Python SUBSET — violations raise errors you'll have to fix):
- No third-party imports. No class inheritance or metaclasses.
- Allowed stdlib: math, json, re, datetime, itertools, functools, collections,
  dataclasses.
- Not every method on builtin types exists; on AttributeError, work around it.
- No network, filesystem, or environment access except the provided functions.
- Keep each snippet self-contained and small; state persists between snippets.
"""

ERROR_FEEDBACK = """\
Your code failed:
{error}
Fix it and resend only the code block. Variables set before the failing line
may have been updated.
"""

STDOUT_FEEDBACK = """\
stdout (truncated to {max_bytes} bytes):
{stdout}
FINAL is still None. Continue: write the next code block (set FINAL when done).
"""

ENVIRONMENT_RESET_NOTE = """\
Note: your REPL environment was reset (previous variables are gone). Rebuild
any state you need.
"""

TYPE_STUBS = """\
async def llm_query(prompt: str) -> str: ...
async def send_message(text: str) -> None: ...

incoming_message: str = ''
FINAL: str | None = None
"""


def user_turn_message(text: str, inline_limit: int = 2000) -> str:
    shown = text if len(text) <= inline_limit else text[:inline_limit] + '…'
    note = '' if len(text) <= inline_limit else (
        f' (truncated here; the full {len(text)}-char message is in `incoming_message`)'
    )
    return f'New user message, bound to `incoming_message`{note}:\n{shown}'


def extract_code(reply: str) -> str:
    if '```' in reply:
        block = reply.split('```')[1]
        return block.removeprefix('python').strip()
    return reply.strip()
