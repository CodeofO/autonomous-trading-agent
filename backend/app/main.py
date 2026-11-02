"""FastAPI 진입점."""

from __future__ import annotations

from fastapi import FastAPI

from app.core.config import Settings, get_settings
from app.core.logging import setup_logging


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    setup_logging(settings.log_level, settings.secret_values())
    app = FastAPI(
        title="Toss Paper Trader", version="0.1.0", docs_url="/api/docs", openapi_url="/api/openapi.json"
    )

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "trading_mode": settings.trading_mode}

    return app


app = create_app()
