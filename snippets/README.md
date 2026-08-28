# pydantic/monty examples

[Monty](https://github.com/pydantic/monty) is a minimal, secure Python interpreter
written in Rust, built to run LLM-generated (or otherwise untrusted) Python with
microsecond startup — no containers. Code executes in subprocess workers with no
network, filesystem, or environment access; the only capabilities it has are the
ones the host explicitly grants (external functions, mounts, OS handlers).

These examples were written and tested against `pydantic-monty` 0.0.21
(installed in this project's venv). Run any of them with:

```bash
uv run snippets/<file>.py
```

| File | Shows |
|---|---|
| `01_basics.py` | Worker pool, sessions, persistent REPL state, `inputs`, value conversion |
| `02_external_functions.py` | Host functions via `external_lookup`, exception propagation both ways, `print_callback` |
| `03_limits_and_errors.py` | `ResourceLimits` (time/memory/recursion), error taxonomy, tracebacks, static type checking via `ty` |
| `04_snapshots.py` | `feed_start`/`resume` stepping, `dump()`ing a *suspended* interpreter to bytes and restoring it — the basis for durable agents |
| `05_filesystem_mounts.py` | `MountDir` in `overlay` / `read-only` / `read-write` modes, write budgets |
| `06_llm_agent_in_sandbox.py` | An agent loop running *inside* the sandbox, calling a local LLM through an async external function (`AsyncMonty` + type-checked stubs) |
| `07_code_interpreter.py` | The classic tool: local LLM writes Python, monty executes it under hard limits, errors fed back for self-repair |
| `08_rlm_loop.py` | A minimal Recursive Language Model loop (arXiv:2512.24601): the long prompt lives as a REPL variable, the root LM writes code each turn, sub-LM calls happen inside that code via `await llm_query(...)`, and the answer exits symbolically through a `FINAL` variable |

Examples 06, 07, and 08 need a local Ollama server with the `minimax-m3:cloud`
model (`ollama run minimax-m3:cloud`), reached via the OpenAI SDK at
`http://localhost:11434/v1`.

## Things worth knowing (learned the hard way)

- The snippet's **trailing expression** is the return value — but it must be at
  module level, not inside a `try`/`if` block.
- `ResourceLimits['max_duration_secs']` is a budget for the **whole session**,
  not per feed; check out a fresh session if you want a fresh clock.
- Monty implements a Python **subset**: no class inheritance/metaclasses, only
  parts of the stdlib (`json`, `math`, `re`, `datetime`, `itertools`,
  `functools`, `collections`, `dataclasses`, `pathlib`, ...), and not every
  method on supported types exists (e.g. `date.isoformat()` works,
  `date.toordinal()` doesn't).
- A crashed or wedged worker raises `MontyCrashedError`; the pool replaces the
  worker and the host process is never at risk.
