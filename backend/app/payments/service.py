"""Checkout creation with client-side idempotency and explicit handling of unknown outcomes.

Flow for ``POST /payments/checkout`` with ``Idempotency-Key: K``:

1. ``INSERT … ON CONFLICT (merchant_id, idempotency_key) DO NOTHING`` claims K atomically.
   Concurrent requests with the same K are serialized by the unique index: exactly one wins.
2. Winner: commits a ``creating`` row (with the session expiry decided now and a creation lease),
   then calls Stripe outside any transaction, with Stripe idempotency key ``checkout-<payment_id>``.
3. Loser / later retry: different fingerprint → 409 ``idempotency_key_reused``; creation still
   within its lease → 409 ``idempotency_operation_in_progress``; ``creation_unknown`` or an
   abandoned ``creating`` → the creation is retried with the *same* parameters, which makes Stripe
   return the session it may already have created; anything else → replay of the stored state.

Stripe errors (see ``StripeGateway``):
- definitive rejection on the first attempt → ``failed`` (nothing exists at Stripe);
- unknown outcome, or any error on a retry → ``creation_unknown``; the client gets 503 and the
  reconciler settles it from Stripe's data if nobody retries.
"""

import hashlib
import json
import logging
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import (
    IdempotencyKeyReused,
    InvalidIdempotencyKey,
    InvalidQuantity,
    OperationInProgress,
    PaymentOutcomeUnknown,
    PaymentProviderError,
    PaymentProviderNotConfigured,
    PaymentProviderUnavailable,
)
from app.integrations.stripe_gateway import CheckoutRequestData, CheckoutSessionData, PaymentGateway
from app.payments.catalog import get_product
from app.payments.ledger import EventSource, PaymentLedger, SchedulePolicy
from app.payments.models import Payment
from app.payments.repository import PaymentRepository
from app.payments.state import PaymentStatus

logger = logging.getLogger(__name__)

_IDEMPOTENCY_KEY_RE = re.compile(r"^[\x21-\x7e]{1,255}$")
RETRY_AFTER_SECONDS = 5
IN_PROGRESS_RETRY_AFTER_SECONDS = 2


def validate_idempotency_key(raw: str | None) -> str:
    if raw is None or not raw.strip():
        raise InvalidIdempotencyKey("The Idempotency-Key header is required (use a new UUID per purchase)")
    key = raw.strip()
    if not _IDEMPOTENCY_KEY_RE.match(key):
        raise InvalidIdempotencyKey("Idempotency-Key must be 1-255 printable ASCII characters without spaces")
    return key


def request_fingerprint(product_id: str, quantity: int, customer_email: str | None) -> str:
    """SHA-256 of the canonical request: same purchase ⇔ same fingerprint."""
    canonical = json.dumps(
        {
            "product_id": product_id,
            "quantity": quantity,
            "customer_email": customer_email.lower() if customer_email else None,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class CheckoutResult:
    payment: Payment
    checkout_url: str | None
    replayed: bool


class CheckoutService:
    def __init__(
        self,
        session: Session,
        gateway: PaymentGateway,
        settings: Settings,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._session = session
        self._gateway = gateway
        self._settings = settings
        self._clock = clock
        self._payments = PaymentRepository(session)
        self._lease = timedelta(seconds=settings.checkout_creation_lease_seconds)
        self._ledger = PaymentLedger(
            session,
            clock,
            SchedulePolicy(
                creation_lease=self._lease,
                open_first_check=timedelta(seconds=settings.reconcile_open_first_check_seconds),
            ),
        )

    def create_checkout(
        self,
        *,
        merchant_id: uuid.UUID,
        idempotency_key: str,
        product_id: str,
        quantity: int,
        customer_email: str | None,
    ) -> CheckoutResult:
        product = get_product(product_id)
        if quantity > self._settings.checkout_max_quantity:
            raise InvalidQuantity(f"Quantity must be <= {self._settings.checkout_max_quantity}")
        fingerprint = request_fingerprint(product.id, quantity, customer_email)

        payment_id = self._claim(
            merchant_id=merchant_id,
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
            product_id=product.id,
            unit_amount=product.unit_amount,
            currency=product.currency,
            quantity=quantity,
            customer_email=customer_email,
        )
        if payment_id is None:
            return self._replay(merchant_id, idempotency_key, fingerprint)
        return self._open_session(payment_id, first_attempt=True)

    def _claim(
        self,
        *,
        merchant_id: uuid.UUID,
        idempotency_key: str,
        fingerprint: str,
        product_id: str,
        unit_amount: int,
        currency: str,
        quantity: int,
        customer_email: str | None,
    ) -> uuid.UUID | None:
        now = self._clock()
        expires_at = now + timedelta(seconds=self._settings.checkout_session_ttl_seconds)
        stmt = (
            insert(Payment)
            .values(
                id=uuid.uuid4(),
                merchant_id=merchant_id,
                idempotency_key=idempotency_key,
                request_fingerprint=fingerprint,
                product_id=product_id,
                quantity=quantity,
                unit_amount=unit_amount,
                amount=unit_amount * quantity,
                currency=currency,
                status=PaymentStatus.CREATING,
                customer_email=customer_email,
                checkout_expires_at=expires_at.replace(microsecond=0),
                creation_attempts=1,
                creation_attempt_at=now,
                next_reconcile_at=now + self._lease,
            )
            .on_conflict_do_nothing(constraint="uq_payments_merchant_idempotency_key")
            .returning(Payment.id)
        )
        payment_id = self._session.execute(stmt).scalar_one_or_none()
        if payment_id is not None:
            payment = self._session.get(Payment, payment_id)
            if payment is not None:
                self._ledger.record_creation(payment, EventSource.API)
        self._session.commit()
        return payment_id

    def _replay(self, merchant_id: uuid.UUID, idempotency_key: str, fingerprint: str) -> CheckoutResult:
        payment = self._payments.get_by_idempotency_key(merchant_id, idempotency_key)
        if payment is None:  # pragma: no cover - the conflicting row cannot disappear (no deletes)
            raise RuntimeError("Idempotency key conflict without a stored payment")
        if payment.request_fingerprint != fingerprint:
            raise IdempotencyKeyReused(
                "This Idempotency-Key was already used with different parameters",
                details={"payment_id": str(payment.id)},
            )
        if payment.status in (PaymentStatus.CREATING, PaymentStatus.CREATION_UNKNOWN) and self._acquire_retry(
            payment.id
        ):
            return self._open_session(payment.id, first_attempt=False)
        self._session.expire_all()
        payment = self._payments.get_by_idempotency_key(merchant_id, idempotency_key) or payment
        logger.info("checkout_replayed payment_id=%s status=%s", payment.id, payment.status.value)
        return CheckoutResult(payment=payment, checkout_url=self._payable_url(payment), replayed=True)

    def _acquire_retry(self, payment_id: uuid.UUID) -> bool:
        now = self._clock()
        payment = self._payments.get_for_update(payment_id)
        if payment is None or payment.status not in (PaymentStatus.CREATING, PaymentStatus.CREATION_UNKNOWN):
            self._session.commit()
            return False
        if (
            payment.status is PaymentStatus.CREATING
            and payment.creation_attempt_at is not None
            and payment.creation_attempt_at > now - self._lease
        ):
            self._session.commit()
            raise OperationInProgress(
                "A request with this Idempotency-Key is still being processed. Retry shortly.",
                details={"payment_id": str(payment_id), "status": payment.status.value},
                headers={"Retry-After": str(IN_PROGRESS_RETRY_AFTER_SECONDS)},
            )
        if payment.checkout_expires_at is None or payment.checkout_expires_at <= now:
            self._session.commit()
            raise self._unknown(payment_id, "The Checkout Session window has closed; reconciliation will settle it.")
        if payment.status is PaymentStatus.CREATION_UNKNOWN:
            self._ledger.transition(payment, PaymentStatus.CREATING, source=EventSource.API, reason="client_retry")
        payment.creation_attempts += 1
        payment.creation_attempt_at = now
        payment.next_reconcile_at = now + self._lease
        self._session.commit()
        return True

    def _open_session(self, payment_id: uuid.UUID, *, first_attempt: bool) -> CheckoutResult:
        payment = self._session.get(Payment, payment_id, populate_existing=True)
        if payment is None or payment.checkout_expires_at is None:  # pragma: no cover - invariant
            raise RuntimeError(f"Payment {payment_id} cannot open a Checkout Session")
        request = CheckoutRequestData(
            payment_id=payment.id,
            merchant_id=payment.merchant_id,
            product=get_product(payment.product_id),
            quantity=payment.quantity,
            customer_email=payment.customer_email,
            expires_at=payment.checkout_expires_at,
        )
        self._session.commit()
        try:
            checkout = self._gateway.create_checkout_session(request)
        except PaymentProviderUnavailable:
            self._mark_unknown(payment_id, "stripe_outcome_unknown")
            raise self._unknown(
                payment_id, "Stripe did not confirm the Checkout Session. Retry with the same Idempotency-Key."
            ) from None
        except PaymentProviderNotConfigured:
            self._mark_unknown(payment_id, "payment_provider_not_configured")
            raise
        except PaymentProviderError:
            if first_attempt:
                self._mark_failed(payment_id, "checkout_session_creation_rejected")
                raise
            self._mark_unknown(payment_id, "stripe_rejected_retry")
            raise self._unknown(
                payment_id, "Stripe rejected the retry; the original attempt may still exist. Reconciliation decides."
            ) from None
        return self._store_session(payment_id, checkout, replayed=not first_attempt)

    def _store_session(self, payment_id: uuid.UUID, checkout: CheckoutSessionData, *, replayed: bool) -> CheckoutResult:
        payment = self._payments.get_for_update(payment_id)
        if payment is None:  # pragma: no cover - invariant
            raise RuntimeError(f"Payment {payment_id} disappeared")
        if payment.stripe_checkout_session_id not in (None, checkout.id):
            self._session.rollback()
            logger.error(
                "checkout_session_conflict payment_id=%s stored=%s received=%s",
                payment_id,
                payment.stripe_checkout_session_id,
                checkout.id,
            )
            raise PaymentProviderError("Stripe returned a different Checkout Session for this payment")

        reject_reason = None
        if checkout.amount_total is not None and checkout.amount_total != payment.amount:
            reject_reason = "checkout_amount_mismatch"
        elif not checkout.url:
            reject_reason = "checkout_url_missing"
        if reject_reason is not None:
            self._reject_session(payment, checkout.id, reject_reason)
            raise PaymentProviderError("Stripe returned an unusable Checkout Session")

        payment.stripe_checkout_session_id = checkout.id
        payment.checkout_creation_error = None
        if checkout.expires_at is not None:
            payment.checkout_expires_at = checkout.expires_at
        if payment.status in (PaymentStatus.CREATING, PaymentStatus.CREATION_UNKNOWN):
            payment.checkout_url = checkout.url
            self._ledger.transition(payment, PaymentStatus.OPEN, source=EventSource.API)
        self._session.commit()
        logger.info(
            "checkout_created payment_id=%s session_id=%s amount=%s replayed=%s status=%s",
            payment.id,
            checkout.id,
            payment.amount,
            replayed,
            payment.status.value,
        )
        return CheckoutResult(payment=payment, checkout_url=self._payable_url(payment), replayed=replayed)

    @staticmethod
    def _payable_url(payment: Payment) -> str | None:
        return payment.checkout_url if payment.status is PaymentStatus.OPEN else None

    @staticmethod
    def _unknown(payment_id: uuid.UUID, message: str) -> PaymentOutcomeUnknown:
        return PaymentOutcomeUnknown(
            message,
            details={"payment_id": str(payment_id), "status": PaymentStatus.CREATION_UNKNOWN.value},
            headers={"Retry-After": str(RETRY_AFTER_SECONDS)},
        )

    def _mark_unknown(self, payment_id: uuid.UUID, error: str) -> None:
        payment = self._payments.get_for_update(payment_id)
        if payment is not None and payment.status is PaymentStatus.CREATING:
            payment.checkout_creation_error = error
            self._ledger.transition(payment, PaymentStatus.CREATION_UNKNOWN, source=EventSource.API, reason=error)
        self._session.commit()
        logger.warning("checkout_outcome_unknown payment_id=%s error=%s", payment_id, error)

    def _mark_failed(self, payment_id: uuid.UUID, reason: str) -> None:
        payment = self._payments.get_for_update(payment_id)
        if (
            payment is not None
            and payment.status is PaymentStatus.CREATING
            and payment.stripe_checkout_session_id is None
        ):
            self._ledger.transition(payment, PaymentStatus.FAILED, source=EventSource.API, reason=reason)
        self._session.commit()

    def _reject_session(self, payment: Payment, session_id: str, reason: str) -> None:
        """Mark the payment failed and close the Stripe session so nobody can still pay it."""
        payment.stripe_checkout_session_id = session_id
        if payment.status in (PaymentStatus.CREATING, PaymentStatus.CREATION_UNKNOWN):
            self._ledger.transition(payment, PaymentStatus.FAILED, source=EventSource.API, reason=reason)
        self._session.commit()
        try:
            self._gateway.expire_checkout_session(session_id)
        except PaymentProviderError:
            logger.error("checkout_session_left_open payment_id=%s session_id=%s", payment.id, session_id)
