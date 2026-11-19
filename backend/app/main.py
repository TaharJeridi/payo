import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.config import get_settings
from app.core.errors import DomainError
from app.core.logging import configure_logging
from app.dependencies import DbDep
from app.payments.router import router as payments_router
from app.rag.router import router as rag_router
from app.webhooks.router import router as webhooks_router

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    if settings.is_production and settings.demo_endpoints_enabled:
        logger.warning("DEMO_ENDPOINTS_ENABLED is ignored in production: demo endpoints stay disabled")

    app = FastAPI(title=settings.app_name, version="0.1.0")

    @app.exception_handler(DomainError)
    async def _domain_error_handler(_: Request, exc: DomainError) -> JSONResponse:
        error: dict[str, object] = {"code": exc.code, "message": exc.message}
        if exc.details:
            error["details"] = exc.details
        return JSONResponse(status_code=exc.status_code, content={"error": error}, headers=exc.headers or None)

    @app.get("/health", tags=["ops"])
    def health(db: DbDep) -> dict[str, str]:
        db.execute(text("SELECT 1"))
        return {"status": "ok", "stripe_mode": "test"}

    app.include_router(payments_router)
    app.include_router(webhooks_router)
    app.include_router(rag_router)
    return app


app = create_app()
