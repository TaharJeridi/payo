"""Apply the state of a Stripe Checkout Session to a local payment.

Shared by checkout creation, the webhook processor and the reconciler, so every path verifies
the same things and obeys the same state machine. The caller must hold a row lock on ``payment``.
"""

import enum

from app.integrations.stripe_gateway import CheckoutSessionData
from app.payments.ledger import EventSource, PaymentLedger
from app.payments.models import Payment
from app.payments.state import PaymentStatus, can_transition

ASYNC_PAYMENT_FAILED_EVENT = "checkout.session.async_payment_failed"


class SyncResult(enum.StrEnum):
    OPEN = "open"
    PAID = "paid"
    FAILED = "failed"
    EXPIRED = "expired"
    NO_CHANGE = "no_change"
    STALE = "stale"
    VERIFICATION_FAILED = "verification_failed"


_RESULT_BY_STATUS = {
    PaymentStatus.OPEN: SyncResult.OPEN,
    PaymentStatus.PAID: SyncResult.PAID,
    PaymentStatus.FAILED: SyncResult.FAILED,
    PaymentStatus.EXPIRED: SyncResult.EXPIRED,
}


def verify_remote(payment: Payment, session_id: str, remote: CheckoutSessionData) -> str | None:
    """Return a mismatch reason if the Stripe session does not belong to this payment."""
    if remote.livemode:
        return "livemode_session"
    if remote.id != session_id:
        return "session_id_mismatch"
    if payment.stripe_checkout_session_id and payment.stripe_checkout_session_id != remote.id:
        return "session_id_mismatch"
    if remote.metadata.get("payment_id") != str(payment.id):
        return "metadata_mismatch"
    if remote.client_reference_id and remote.client_reference_id != str(payment.id):
        return "client_reference_mismatch"
    if remote.amount_total != payment.amount:
        return "amount_mismatch"
    if (remote.currency or "").lower() != payment.currency:
        return "currency_mismatch"
    return None


def decide(remote: CheckoutSessionData, event_type: str | None) -> tuple[PaymentStatus, str | None]:
    """Target status from Stripe's own view of the session (never from an event payload)."""
    if remote.payment_status == "paid":
        return PaymentStatus.PAID, None
    if remote.status == "expired":
        return PaymentStatus.EXPIRED, "checkout_session_expired"
    if event_type == ASYNC_PAYMENT_FAILED_EVENT and remote.status == "complete" and remote.payment_status == "unpaid":
        return PaymentStatus.FAILED, "async_payment_failed"
    return PaymentStatus.OPEN, None


def apply_remote_state(
    payment: Payment,
    session_id: str,
    remote: CheckoutSessionData,
    *,
    ledger: PaymentLedger,
    source: EventSource,
    event_type: str | None = None,
    event_id: str | None = None,
) -> tuple[SyncResult, str | None]:
    mismatch = verify_remote(payment, session_id, remote)
    if mismatch is not None:
        if can_transition(payment.status, PaymentStatus.FAILED):
            ledger.transition(
                payment,
                PaymentStatus.FAILED,
                source=source,
                reason=f"stripe_verification_failed:{mismatch}",
                stripe_event_id=event_id,
            )
        return SyncResult.VERIFICATION_FAILED, mismatch

    if payment.stripe_checkout_session_id is None:
        payment.stripe_checkout_session_id = remote.id
    if payment.checkout_url is None and remote.url and remote.status == "open":
        payment.checkout_url = remote.url
    if remote.expires_at is not None:
        payment.checkout_expires_at = remote.expires_at
    payment.checkout_creation_error = None
    if remote.payment_intent_id:
        payment.stripe_payment_intent_id = remote.payment_intent_id
    if remote.customer_email and not payment.customer_email:
        payment.customer_email = remote.customer_email

    target, reason = decide(remote, event_type)
    if payment.status is target:
        return SyncResult.NO_CHANGE, None
    if not can_transition(payment.status, target):
        return SyncResult.STALE, None
    ledger.transition(payment, target, source=source, reason=reason, stripe_event_id=event_id)
    return _RESULT_BY_STATUS[target], None
