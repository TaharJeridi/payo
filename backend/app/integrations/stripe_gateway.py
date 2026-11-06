import logging
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, cast

import stripe
from stripe.checkout import SessionService

from app.core.config import Settings
from app.core.errors import PaymentProviderError, PaymentProviderNotConfigured
from app.payments.catalog import Product

logger = logging.getLogger(__name__)

CHECKOUT_SESSION_TTL_SECONDS = 30 * 60


@dataclass(frozen=True, slots=True)
class CheckoutSessionData:
    id: str
    url: str | None
    status: str | None
    payment_status: str
    amount_total: int | None
    currency: str | None
    livemode: bool
    client_reference_id: str | None
    payment_intent_id: str | None
    customer_email: str | None
    metadata: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def from_stripe(cls, obj: Mapping[str, Any]) -> "CheckoutSessionData":
        payment_intent = obj.get("payment_intent")
        if isinstance(payment_intent, Mapping):
            payment_intent = payment_intent.get("id")
        details = obj.get("customer_details") or {}
        return cls(
            id=obj["id"],
            url=obj.get("url"),
            status=obj.get("status"),
            payment_status=obj.get("payment_status") or "unpaid",
            amount_total=obj.get("amount_total"),
            currency=obj.get("currency"),
            livemode=bool(obj.get("livemode")),
            client_reference_id=obj.get("client_reference_id"),
            payment_intent_id=payment_intent,
            customer_email=details.get("email") or obj.get("customer_email"),
            metadata=dict(obj.get("metadata") or {}),
        )


class PaymentGateway(Protocol):
    def create_checkout_session(
        self,
        *,
        payment_id: uuid.UUID,
        product: Product,
        quantity: int,
        customer_email: str | None,
    ) -> CheckoutSessionData: ...

    def retrieve_checkout_session(self, session_id: str) -> CheckoutSessionData: ...

    def expire_checkout_session(self, session_id: str) -> None: ...


class StripeGateway:
    def __init__(self, settings: Settings) -> None:
        api_key = settings.stripe_secret_key.get_secret_value()
        if not api_key:
            raise PaymentProviderNotConfigured("STRIPE_SECRET_KEY is not configured")
        self._settings = settings
        self._client = stripe.StripeClient(
            api_key,
            max_network_retries=2,
            http_client=stripe.new_default_http_client(timeout=settings.stripe_api_timeout_seconds),
        )

    def create_checkout_session(
        self,
        *,
        payment_id: uuid.UUID,
        product: Product,
        quantity: int,
        customer_email: str | None,
    ) -> CheckoutSessionData:
        params: dict[str, Any] = {
            "mode": "payment",
            "line_items": [
                {
                    "quantity": quantity,
                    "price_data": {
                        "currency": product.currency,
                        "unit_amount": product.unit_amount,
                        "product_data": {
                            "name": product.name,
                            "description": product.description,
                            "metadata": {"product_id": product.id},
                        },
                    },
                }
            ],
            "client_reference_id": str(payment_id),
            "metadata": {"payment_id": str(payment_id), "product_id": product.id},
            "payment_intent_data": {"metadata": {"payment_id": str(payment_id)}},
            "success_url": self._settings.checkout_success_url,
            "cancel_url": self._settings.checkout_cancel_url,
            "expires_at": int(time.time()) + CHECKOUT_SESSION_TTL_SECONDS + 60,
        }
        if customer_email:
            params["customer_email"] = customer_email
        try:
            session = self._client.checkout.sessions.create(
                params=cast(SessionService.CreateParams, params),
                options={"idempotency_key": f"checkout-{payment_id}"},
            )
        except stripe.StripeError as exc:
            logger.warning(
                "stripe_checkout_create_failed payment_id=%s error=%s",
                payment_id,
                exc.user_message or type(exc).__name__,
            )
            raise PaymentProviderError("Unable to create Stripe Checkout session") from exc
        data = CheckoutSessionData.from_stripe(session)
        if data.livemode:
            raise PaymentProviderError("Refusing live-mode Stripe session")
        return data

    def retrieve_checkout_session(self, session_id: str) -> CheckoutSessionData:
        try:
            session = self._client.checkout.sessions.retrieve(session_id)
        except stripe.StripeError as exc:
            logger.warning("stripe_checkout_retrieve_failed session_id=%s error=%s", session_id, type(exc).__name__)
            raise PaymentProviderError("Unable to retrieve Stripe Checkout session") from exc
        return CheckoutSessionData.from_stripe(session)

    def expire_checkout_session(self, session_id: str) -> None:
        try:
            self._client.checkout.sessions.expire(session_id)
        except stripe.StripeError as exc:
            logger.warning("stripe_checkout_expire_failed session_id=%s error=%s", session_id, type(exc).__name__)
            raise PaymentProviderError("Unable to expire Stripe Checkout session") from exc
