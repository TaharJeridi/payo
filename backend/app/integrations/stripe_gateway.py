import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol, cast

import stripe
from stripe.checkout import SessionService

from app.core.config import Settings
from app.core.errors import PaymentProviderError, PaymentProviderNotConfigured, PaymentProviderUnavailable
from app.payments.catalog import Product

logger = logging.getLogger(__name__)

# Errors after which we cannot know whether Stripe executed the request:
# - APIConnectionError: timeout / connection reset (the request may have arrived);
# - APIError: Stripe-side 5xx;
# - RateLimitError: 429, retryable;
# - IdempotencyError: e.g. the same key is still being processed by a concurrent request.
# stripe-python already retried these (max_network_retries) with the same idempotency key.
_UNCERTAIN_ERRORS: tuple[type[stripe.StripeError], ...] = (
    stripe.APIConnectionError,
    stripe.APIError,
    stripe.RateLimitError,
    stripe.IdempotencyError,
)


def _to_datetime(value: Any) -> datetime | None:
    return datetime.fromtimestamp(value, tz=UTC) if isinstance(value, int) else None


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
    expires_at: datetime | None = None
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
            expires_at=_to_datetime(obj.get("expires_at")),
            metadata=dict(obj.get("metadata") or {}),
        )


@dataclass(frozen=True, slots=True)
class CheckoutRequestData:
    """Everything sent to Stripe to create a session.

    It is rebuilt identically from the stored payment on every retry: Stripe only replays
    an idempotent request if the parameters are exactly the same.
    """

    payment_id: uuid.UUID
    merchant_id: uuid.UUID
    product: Product
    quantity: int
    customer_email: str | None
    expires_at: datetime


class PaymentGateway(Protocol):
    def create_checkout_session(self, request: CheckoutRequestData) -> CheckoutSessionData: ...

    def retrieve_checkout_session(self, session_id: str) -> CheckoutSessionData: ...

    def find_checkout_session(
        self, payment_id: uuid.UUID, created_from: datetime, created_to: datetime
    ) -> CheckoutSessionData | None: ...

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

    @staticmethod
    def _wrap(exc: stripe.StripeError, action: str) -> PaymentProviderError:
        status = exc.http_status
        if isinstance(exc, _UNCERTAIN_ERRORS) or (status is not None and (status in (409, 429) or status >= 500)):
            return PaymentProviderUnavailable(f"Stripe outcome unknown while trying to {action}")
        return PaymentProviderError(f"Stripe rejected the request to {action}")

    def create_checkout_session(self, request: CheckoutRequestData) -> CheckoutSessionData:
        product = request.product
        params: dict[str, Any] = {
            "mode": "payment",
            "line_items": [
                {
                    "quantity": request.quantity,
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
            "client_reference_id": str(request.payment_id),
            "metadata": {
                "payment_id": str(request.payment_id),
                "merchant_id": str(request.merchant_id),
                "product_id": product.id,
            },
            "payment_intent_data": {"metadata": {"payment_id": str(request.payment_id)}},
            "success_url": self._settings.checkout_success_url,
            "cancel_url": self._settings.checkout_cancel_url,
            "expires_at": int(request.expires_at.timestamp()),
        }
        if request.customer_email:
            params["customer_email"] = request.customer_email
        try:
            session = self._client.checkout.sessions.create(
                params=cast(SessionService.CreateParams, params),
                # Stable per payment: a retry of the same payment returns the same session.
                options={"idempotency_key": f"checkout-{request.payment_id}"},
            )
        except stripe.StripeError as exc:
            logger.warning(
                "stripe_checkout_create_failed payment_id=%s error=%s", request.payment_id, type(exc).__name__
            )
            raise self._wrap(exc, "create a Checkout Session") from exc
        data = CheckoutSessionData.from_stripe(session)
        if data.livemode:
            raise PaymentProviderError("Refusing live-mode Stripe session")
        return data

    def retrieve_checkout_session(self, session_id: str) -> CheckoutSessionData:
        try:
            session = self._client.checkout.sessions.retrieve(session_id)
        except stripe.StripeError as exc:
            logger.warning("stripe_checkout_retrieve_failed session_id=%s error=%s", session_id, type(exc).__name__)
            raise self._wrap(exc, "retrieve a Checkout Session") from exc
        return CheckoutSessionData.from_stripe(session)

    def find_checkout_session(
        self, payment_id: uuid.UUID, created_from: datetime, created_to: datetime
    ) -> CheckoutSessionData | None:
        """Look for a session created for ``payment_id`` when our copy of its id was lost.

        Checkout Sessions cannot be filtered by metadata, so we list the ones created in the
        time window in which ours could have been created and match ``metadata.payment_id``.
        Read-only: reconciliation never creates sessions.
        """
        params: dict[str, Any] = {
            "created": {"gte": int(created_from.timestamp()), "lte": int(created_to.timestamp())},
            "limit": 100,
        }
        try:
            page = self._client.checkout.sessions.list(params=cast(SessionService.ListParams, params))
            for session in page.auto_paging_iter():
                data = CheckoutSessionData.from_stripe(session)
                if data.metadata.get("payment_id") == str(payment_id):
                    return data
        except stripe.StripeError as exc:
            logger.warning("stripe_checkout_list_failed payment_id=%s error=%s", payment_id, type(exc).__name__)
            raise self._wrap(exc, "list Checkout Sessions") from exc
        return None

    def expire_checkout_session(self, session_id: str) -> None:
        try:
            self._client.checkout.sessions.expire(session_id)
        except stripe.StripeError as exc:
            logger.warning("stripe_checkout_expire_failed session_id=%s error=%s", session_id, type(exc).__name__)
            raise self._wrap(exc, "expire a Checkout Session") from exc
