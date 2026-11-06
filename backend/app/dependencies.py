from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings
from app.core.errors import RateLimited
from app.core.rate_limit import SlidingWindowRateLimiter
from app.db.session import get_db, get_session_factory
from app.integrations.stripe_gateway import PaymentGateway, StripeGateway
from app.webhooks.service import WebhookProcessor


@lru_cache
def _stripe_gateway() -> StripeGateway:
    return StripeGateway(get_settings())


def get_payment_gateway() -> PaymentGateway:
    return _stripe_gateway()


@lru_cache
def get_checkout_rate_limiter() -> SlidingWindowRateLimiter:
    return SlidingWindowRateLimiter(get_settings().checkout_rate_limit_per_minute)


def enforce_checkout_rate_limit(
    request: Request,
    limiter: Annotated[SlidingWindowRateLimiter, Depends(get_checkout_rate_limiter)],
) -> None:
    # Keyed on the direct peer address. Behind a reverse proxy, run uvicorn with
    # --proxy-headers / --forwarded-allow-ips so this is the real client IP.
    client_ip = request.client.host if request.client else "unknown"
    if not limiter.hit(client_ip):
        raise RateLimited("Too many checkout requests, retry in a minute")


def get_webhook_processor(
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    gateway: Annotated[PaymentGateway, Depends(get_payment_gateway)],
) -> WebhookProcessor:
    return WebhookProcessor(session_factory=session_factory, gateway=gateway)


SettingsDep = Annotated[Settings, Depends(get_settings)]
DbDep = Annotated[Session, Depends(get_db)]
GatewayDep = Annotated[PaymentGateway, Depends(get_payment_gateway)]
WebhookProcessorDep = Annotated[WebhookProcessor, Depends(get_webhook_processor)]
