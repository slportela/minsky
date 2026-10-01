"""FastAPI application factory. Run: uvicorn minsky_api.main:app"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from minsky_api.config import get_settings
from minsky_api.store.db import dispose_engine


def create_app() -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        await dispose_engine()

    app = FastAPI(title="minsky dispute intake", version="0.1.0", lifespan=lifespan)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        """Liveness: the process is up. Readiness checks (database, Bedrock) belong in /api/ready."""
        return {"status": "ok", "environment": settings.environment}

    return app


app = create_app()
