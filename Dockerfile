FROM python:3.14.2-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.9.21 /uv /uvx /bin/
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
COPY configs ./configs
RUN uv sync --frozen --no-dev
CMD ["urbanflow", "serve"]
