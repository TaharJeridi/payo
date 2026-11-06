class DomainError(Exception):
    status_code = 400
    code = "domain_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ProductNotFound(DomainError):
    status_code = 404
    code = "product_not_found"


class InvalidQuantity(DomainError):
    status_code = 422
    code = "invalid_quantity"


class PaymentProviderError(DomainError):
    status_code = 502
    code = "payment_provider_error"


class PaymentProviderNotConfigured(PaymentProviderError):
    status_code = 503
    code = "payment_provider_not_configured"


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
