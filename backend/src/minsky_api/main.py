"""FastAPI application factory. Run: uvicorn minsky_api.main:app"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from minsky_api.agent.memory import ConversationStore
from minsky_api.api import chat_router
from minsky_api.config import get_settings
from minsky_api.store.cases_memory import InMemoryCasesBackend
from minsky_api.store.db import dispose_engine


def create_app() -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.cases = InMemoryCasesBackend()
        app.state.conversations = ConversationStore()
        yield
        await dispose_engine()

    app = FastAPI(title="minsky dispute intake", version="0.1.0", lifespan=lifespan)
    app.include_router(chat_router)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        """Liveness: the process is up. Readiness checks (database, Bedrock) belong in /api/ready."""
        return {"status": "ok", "environment": settings.environment}

    return app


app = create_app()
