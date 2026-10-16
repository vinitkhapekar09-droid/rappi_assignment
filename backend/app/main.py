"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.agent.config import AgentConfig

from app.api.errors import register_error_handlers
from app.api.routes import router as api_router
from app.config import get_settings
from app.mock.router import router as mock_router


def create_app() -> FastAPI:
    settings = get_settings()
    AgentConfig.from_env()  # Invalid opt-in provider configuration fails at startup.
    app = FastAPI(
        title="AI Purchasing Agent API",
        version="0.1.0",
        description="Deterministic purchasing workflow with buyer approval and validation.",
    )
    register_error_handlers(app)
    app.add_middleware(
        CORSMiddleware, allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "PUT", "OPTIONS"],
        allow_headers=["Content-Type", "Idempotency-Key"],
    )
    app.include_router(api_router)
    app.include_router(mock_router)

    @app.get("/", include_in_schema=False)
    def root() -> dict[str, str]:
        return {"service": "ai-purchasing-agent", "docs": "/docs"}

    _ = settings
    return app


app = create_app()
