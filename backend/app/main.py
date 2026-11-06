from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.config import get_settings
from app.core.errors import DomainError
from app.core.logging import configure_logging
from app.dependencies import DbDep
from app.payments.router import router as payments_router
from app.webhooks.router import router as webhooks_router


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(title=settings.app_name, version="0.1.0")

    @app.exception_handler(DomainError)
    async def _domain_error_handler(_: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    @app.get("/health", tags=["ops"])
    def health(db: DbDep) -> dict[str, str]:
        db.execute(text("SELECT 1"))
        return {"status": "ok", "stripe_mode": "test"}

    app.include_router(payments_router)
    app.include_router(webhooks_router)
    return app


app = create_app()
