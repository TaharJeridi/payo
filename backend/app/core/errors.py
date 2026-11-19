from collections.abc import Mapping
from typing import Any


class DomainError(Exception):
    status_code = 400
    code = "domain_error"

    def __init__(
        self,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = dict(details or {})
        self.headers = dict(headers or {})


class ProductNotFound(DomainError):
    status_code = 404
    code = "product_not_found"


class PaymentNotFound(DomainError):
    status_code = 404
    code = "payment_not_found"


class InvalidQuantity(DomainError):
    status_code = 422
    code = "invalid_quantity"


class Unauthorized(DomainError):
    status_code = 401
    code = "unauthorized"


class InvalidIdempotencyKey(DomainError):
    status_code = 400
    code = "invalid_idempotency_key"


class IdempotencyKeyReused(DomainError):
    """Same Idempotency-Key, different request parameters."""

    status_code = 409
    code = "idempotency_key_reused"


class OperationInProgress(DomainError):
    """Same Idempotency-Key while the original request has not settled yet."""

    status_code = 409
    code = "idempotency_operation_in_progress"


class PaymentProviderError(DomainError):
    """Stripe answered and definitively rejected the request (nothing was created)."""

    status_code = 502
    code = "payment_provider_error"


class PaymentProviderUnavailable(PaymentProviderError):
    """The outcome at Stripe is unknown (timeout, connection error, 5xx, 429).

    The request may or may not have been executed by Stripe: never treat it as a failure.
    """

    status_code = 503
    code = "payment_provider_unavailable"


class PaymentProviderNotConfigured(PaymentProviderError):
    status_code = 503
    code = "payment_provider_not_configured"


class PaymentOutcomeUnknown(DomainError):
    """Returned to the client when the checkout creation outcome is unknown."""

    status_code = 503
    code = "payment_outcome_unknown"


class RateLimited(DomainError):
    status_code = 429
    code = "rate_limited"


class InvalidWebhook(DomainError):
    status_code = 400
    code = "invalid_webhook"


class WebhookNotConfigured(DomainError):
    status_code = 503
    code = "webhook_not_configured"


class PayloadTooLarge(DomainError):
    status_code = 413
    code = "payload_too_large"


class RagUnavailable(DomainError):
    """Ollama or Qdrant could not be reached or answered with an error."""

    status_code = 503
    code = "rag_unavailable"


class InvalidQuestion(DomainError):
    status_code = 422
    code = "invalid_question"
