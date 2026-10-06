"""FastAPI application factory. Run: uvicorn minsky_api.main:app"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import FastAPI

from minsky_api.agent.memory import ConversationStore
from minsky_api.api import chat_router, console_router, demo_router
from minsky_api.config import Settings, get_settings
from minsky_api.identity.demo_sessions import DemoSessionStore
from minsky_api.observability import configure_tracing, shutdown_tracing
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


# Without a handler Python prints only WARNING and above, so the SDK's INFO retry lines
# ("Retrying request in X seconds (retry N of M)") were dropped. One handler, added once.
_PROVIDER_RETRY_HANDLER = logging.StreamHandler()
_PROVIDER_RETRY_HANDLER.setLevel(logging.INFO)
_PROVIDER_RETRY_HANDLER.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))


def _log_provider_retries() -> None:
    provider = logging.getLogger("openai")
    if provider.level == logging.NOTSET or provider.level > logging.INFO:
        provider.setLevel(logging.INFO)
    if _PROVIDER_RETRY_HANDLER not in provider.handlers:
        provider.addHandler(_PROVIDER_RETRY_HANDLER)


def create_app() -> FastAPI:
    settings = get_settings()
    configure_tracing()  # no-op unless OTEL_EXPORTER_OTLP_ENDPOINT is set (ADR 0007)
    _log_provider_retries()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        cases = _cases_backend(settings)
        app.state.cases = cases
        app.state.conversations = ConversationStore()
        if settings.demo_operator_enabled:
            app.state.demo_sessions = DemoSessionStore(
                ttl=timedelta(minutes=settings.demo_session_ttl_minutes),
                per_minute=settings.demo_sessions_per_minute,
                max_active=settings.demo_max_active_sessions,
            )
        try:
            yield
        finally:
            if isinstance(cases, SqlCasesBackend):
                cases.dispose()
            await dispose_engine()
            shutdown_tracing()

    app = FastAPI(title="minsky dispute intake", version="0.1.0", lifespan=lifespan)
    app.include_router(chat_router)
    app.include_router(console_router)
    app.include_router(demo_router)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        """Liveness: the process is up. Readiness checks (database, Bedrock) belong in /api/ready."""
        return {"status": "ok", "environment": settings.environment}

    return app


app = create_app()
