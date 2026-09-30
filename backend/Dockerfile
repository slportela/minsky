# Build from the repo root (the uv workspace lock lives there): docker build -f backend/Dockerfile .
FROM python:3.12-slim AS base
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1
WORKDIR /app

FROM base AS build
COPY pyproject.toml uv.lock ./
COPY backend/pyproject.toml backend/pyproject.toml
RUN uv sync --frozen --no-dev --package minsky-api --no-install-workspace
COPY backend backend
COPY prompts prompts
RUN uv sync --frozen --no-dev --package minsky-api

FROM base AS runtime
RUN useradd --create-home --uid 10001 app
COPY --from=build --chown=app /app /app
USER app
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')"
CMD ["uvicorn", "minsky_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
