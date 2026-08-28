# Stage 1: build the venv (includes maturin build of the pyo3 extension).
FROM ghcr.io/astral-sh/uv:python3.13-bookworm AS builder

RUN apt-get update && apt-get install -y --no-install-recommends curl build-essential \
    && rm -rf /var/lib/apt/lists/* \
    && curl -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal
ENV PATH="/root/.cargo/bin:${PATH}"

WORKDIR /app
COPY pyproject.toml uv.lock Cargo.toml Cargo.lock ./
COPY src ./src
COPY README.md ./
RUN uv sync --frozen --no-dev --no-editable

# Stage 2: slim runtime.
FROM python:3.13-slim

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:${PATH}"

# Cloud Run injects PORT; config.py reads it.
EXPOSE 8080
CMD ["monty-claw", "serve"]
