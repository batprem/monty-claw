# monty-claw

A serverless, OpenClaw-style personal AI assistant. The agent runtime is a
Recursive Language Model loop ([arXiv:2512.24601](docs/researchs/2512.24601-recursive-language-models.md)):
the root LLM writes Python that executes in a persistent
[pydantic-monty](https://github.com/pydantic/monty) sandbox per chat, and the
sandbox's variables are the assistant's long-term memory. Monty session dumps
make that memory durable across stateless invocations — MongoDB holds the
structured chat state, GCS (or a local directory) holds the dump blobs, and
the whole thing runs scale-to-zero on Cloud Run behind a Telegram webhook.

```
Telegram ──webhook──▶ FastAPI (Cloud Run)
                        │  load history (MongoDB) + session dump (GCS)
                        ▼
                  RLM engine: root LLM ⇄ Monty session
                        │  llm_query / send_message externals
                        ▼
                  persist history + dump ──▶ reply via Bot API
```

## Quick start

```bash
uv sync
uv run pytest                 # unit tests (no network needed)
uv run monty-claw repl        # terminal chat; needs an OpenAI-compatible LLM
```

The default LLM config targets local Ollama (`minimax-m3:cloud` at
`http://localhost:11434/v1`); override with `LLM_BASE_URL`, `LLM_MODEL`,
`LLM_API_KEY`.

## Commands

| Command | Purpose |
|---|---|
| `monty-claw repl` | Terminal chat against the full engine (local storage, no Telegram/Mongo) |
| `monty-claw poll` | Local dev against real Telegram via `getUpdates` (no public URL) |
| `monty-claw serve` | FastAPI webhook server — the Cloud Run entrypoint |
| `monty-claw set-webhook --url …` | Point the Telegram webhook at a deployment |

In chat, `/reset` wipes the assistant's memory for that chat.

## Web UI

`monty-claw serve` also hosts a web frontend at `/` with a chat tab and a
configuration tab (LLM endpoint/model/key and engine knobs, persisted to
MongoDB and applied live). It's protected by username/password login
(signed-cookie sessions, so they survive scale-to-zero) and disabled unless
both `WEB_USERNAME` and `WEB_PASSWORD` are set.

## Layout

- `src/monty_claw/rlm/` — the RLM engine, prompts, LLM client
- `src/monty_claw/db/` — MongoDB chat records (history, dedupe, blob pointer)
- `src/monty_claw/storage/` — blob storage for session dumps (GCS / local)
- `src/monty_claw/channels/` — channel adapters (Telegram; Line/Slack later)
- `src/monty_claw/web/` — login-protected web frontend (chat + configuration)
- `snippets/` — standalone pydantic-monty examples
- `docs/deploy.md` — Cloud Run deployment guide
