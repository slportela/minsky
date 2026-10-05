"""FastAPI application factory. Run: uvicorn minsky_api.main:app"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from minsky_api.agent.memory import ConversationStore
from minsky_api.api import chat_router, console_router
from minsky_api.config import Settings, get_settings
from minsky_api.observability import configure_tracing
from minsky_api.store.cases import CasesBackend
from minsky_api.store.cases_memory import InMemoryCasesBackend
from minsky_api.store.cases_sql import SqlCasesBackend
from minsky_api.store.db import dispose_engine


def _cases_backend(settings: Settings) -> CasesBackend:
    if settings.cases_backend == "postgres":
        backend = SqlCasesBackend.from_url(
            settings.database_url,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_timeout=settings.db_pool_timeout,
        )
        backend.ensure_schema()  # fail at startup, not on the first customer write
        return backend
    return InMemoryCasesBackend()


def create_app() -> FastAPI:
    settings = get_settings()
    configure_tracing()  # no-op unless OTEL_EXPORTER_OTLP_ENDPOINT is set (ADR 0007)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        cases = _cases_backend(settings)
        app.state.cases = cases
        app.state.conversations = ConversationStore()
        yield
        if isinstance(cases, SqlCasesBackend):
            cases.dispose()
        await dispose_engine()

    app = FastAPI(title="minsky dispute intake", version="0.1.0", lifespan=lifespan)
    app.include_router(chat_router)
    app.include_router(console_router)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        """Liveness: the process is up. Readiness checks (database, Bedrock) belong in /api/ready."""
        return {"status": "ok", "environment": settings.environment}

    return app


app = create_app()
