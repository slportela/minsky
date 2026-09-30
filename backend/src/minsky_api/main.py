"""FastAPI application factory. Run: uvicorn minsky_api.main:app"""

from fastapi import FastAPI

from minsky_api.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="minsky dispute intake", version="0.1.0")

    @app.get("/api/health")
    def health() -> dict[str, str]:
        """Liveness: the process is up. Readiness checks (database, Bedrock) belong in /api/ready."""
        return {"status": "ok", "environment": settings.environment}

    return app


app = create_app()
