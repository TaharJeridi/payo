from functools import lru_cache
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings
from app.core.errors import RateLimited, Unauthorized
from app.core.rate_limit import SlidingWindowRateLimiter
from app.db.session import get_db, get_session_factory
from app.integrations.stripe_gateway import PaymentGateway, StripeGateway
from app.merchants.models import Merchant
from app.merchants.service import MerchantService
from app.webhooks.service import WebhookProcessor

SettingsDep = Annotated[Settings, Depends(get_settings)]
DbDep = Annotated[Session, Depends(get_db)]

_bearer = HTTPBearer(auto_error=False, description="Merchant API key: `Authorization: Bearer pf_…`")


def get_current_merchant(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    db: DbDep,
) -> Merchant:
    """Resolve the merchant from the API key. The tenant is *only* ever taken from here."""
    challenge = {"WWW-Authenticate": "Bearer"}
    if credentials is None:
        raise Unauthorized("Missing API key", headers=challenge)
    merchant = MerchantService(db).authenticate(credentials.credentials)
    if merchant is None:
        raise Unauthorized("Invalid or revoked API key", headers=challenge)
    return merchant


CurrentMerchant = Annotated[Merchant, Depends(get_current_merchant)]


@lru_cache
def _stripe_gateway() -> StripeGateway:
    return StripeGateway(get_settings())


def get_payment_gateway() -> PaymentGateway:
    return _stripe_gateway()


@lru_cache
def get_checkout_rate_limiter() -> SlidingWindowRateLimiter:
    return SlidingWindowRateLimiter(get_settings().checkout_rate_limit_per_minute)


def enforce_checkout_rate_limit(
    merchant: CurrentMerchant,
    limiter: Annotated[SlidingWindowRateLimiter, Depends(get_checkout_rate_limiter)],
) -> None:
    # Keyed on the authenticated merchant, not the IP: one merchant cannot exhaust another's quota.
    if not limiter.hit(f"merchant:{merchant.id}"):
        raise RateLimited("Too many checkout requests, retry in a minute", headers={"Retry-After": "60"})


@lru_cache
def get_rag_rate_limiter() -> SlidingWindowRateLimiter:
    return SlidingWindowRateLimiter(get_settings().rag_rate_limit_per_minute)


def enforce_rag_rate_limit(
    merchant: CurrentMerchant,
    limiter: Annotated[SlidingWindowRateLimiter, Depends(get_rag_rate_limiter)],
) -> None:
    if not limiter.hit(f"rag:{merchant.id}"):
        raise RateLimited("Too many questions, retry in a minute", headers={"Retry-After": "60"})


def require_demo_endpoints(settings: SettingsDep) -> None:
    if not settings.demo_endpoints_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)


def get_webhook_processor(
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    gateway: Annotated[PaymentGateway, Depends(get_payment_gateway)],
) -> WebhookProcessor:
    return WebhookProcessor(session_factory=session_factory, gateway=gateway)


GatewayDep = Annotated[PaymentGateway, Depends(get_payment_gateway)]
WebhookProcessorDep = Annotated[WebhookProcessor, Depends(get_webhook_processor)]
