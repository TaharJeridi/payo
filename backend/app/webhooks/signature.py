import json
from typing import Any

import stripe

from app.core.errors import InvalidWebhook, PayloadTooLarge, WebhookNotConfigured

MAX_WEBHOOK_PAYLOAD_BYTES = 512 * 1024


def verify_and_parse_event(payload: bytes, signature_header: str | None, secret: str, tolerance: int) -> dict[str, Any]:
    if not secret:
        raise WebhookNotConfigured("STRIPE_WEBHOOK_SECRET is not configured")
    if len(payload) > MAX_WEBHOOK_PAYLOAD_BYTES:
        raise PayloadTooLarge("Webhook payload too large")
    if not signature_header:
        raise InvalidWebhook("Missing Stripe-Signature header")
    try:
        stripe.WebhookSignature.verify_header(  # type: ignore[no-untyped-call]
            payload.decode("utf-8"), signature_header, secret, tolerance
        )
    except (stripe.SignatureVerificationError, UnicodeDecodeError):
        raise InvalidWebhook("Invalid Stripe signature") from None
    try:
        event = json.loads(payload)
    except ValueError:
        raise InvalidWebhook("Malformed event payload") from None
    if not isinstance(event, dict) or event.get("object") != "event":
        raise InvalidWebhook("Payload is not a Stripe event")
    return event
