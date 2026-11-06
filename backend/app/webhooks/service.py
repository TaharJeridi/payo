import enum
import logging
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import InvalidWebhook
from app.integrations.stripe_gateway import CheckoutSessionData, PaymentGateway
from app.payments.models import Payment
from app.payments.repository import PaymentRepository
from app.payments.state import PaymentStatus, can_transition
from app.webhooks.models import StripeEvent

logger = logging.getLogger(__name__)


class EventType(enum.StrEnum):
    CHECKOUT_COMPLETED = "checkout.session.completed"
    ASYNC_SUCCEEDED = "checkout.session.async_payment_succeeded"
    ASYNC_FAILED = "checkout.session.async_payment_failed"
    CHECKOUT_EXPIRED = "checkout.session.expired"


HANDLED_EVENT_TYPES = frozenset(e.value for e in EventType)


class Outcome(enum.StrEnum):
    PROCESSED_PAID = "processed_paid"
    PROCESSED_FAILED = "processed_failed"
    NO_CHANGE = "no_change"
    STALE_EVENT = "stale_event"
    DUPLICATE = "duplicate"
    IGNORED_EVENT_TYPE = "ignored_event_type"
    IGNORED_LIVEMODE = "ignored_livemode"
    PAYMENT_NOT_FOUND = "payment_not_found"
    VERIFICATION_FAILED = "verification_failed"


class WebhookProcessor:
    def __init__(self, session_factory: sessionmaker[Session], gateway: PaymentGateway) -> None:
        self._session_factory = session_factory
        self._gateway = gateway

    def process(self, event: Mapping[str, Any]) -> Outcome:
        event_id, event_type, created = self._envelope(event)

        if self._already_processed(event_id):
            return Outcome.DUPLICATE
        if event.get("livemode"):
            logger.warning("webhook_livemode_rejected event_id=%s", event_id)
            return self._record_only(event_id, event_type, created, Outcome.IGNORED_LIVEMODE)
        if event_type not in HANDLED_EVENT_TYPES:
            return self._record_only(event_id, event_type, created, Outcome.IGNORED_EVENT_TYPE)

        session_id = self._checkout_session_id(event)
        remote = self._gateway.retrieve_checkout_session(session_id)

        with self._session_factory() as session, session.begin():
            if not self._claim(session, event_id, event_type, created, outcome="processing"):
                return Outcome.DUPLICATE
            outcome = self._apply(session, event_id, event_type, session_id, remote)
            session.execute(update(StripeEvent).where(StripeEvent.id == event_id).values(outcome=outcome.value))
        logger.info(
            "webhook_processed event_id=%s type=%s session_id=%s outcome=%s",
            event_id,
            event_type,
            session_id,
            outcome.value,
        )
        return outcome

    @staticmethod
    def _envelope(event: Mapping[str, Any]) -> tuple[str, str, int]:
        event_id = event.get("id")
        event_type = event.get("type")
        created = event.get("created")
        if not isinstance(event_id, str) or not event_id.startswith("evt_"):
            raise InvalidWebhook("Invalid event id")
        if not isinstance(event_type, str) or not event_type:
            raise InvalidWebhook("Invalid event type")
        if not isinstance(created, int):
            raise InvalidWebhook("Invalid event timestamp")
        return event_id, event_type, created

    @staticmethod
    def _checkout_session_id(event: Mapping[str, Any]) -> str:
        obj = (event.get("data") or {}).get("object") or {}
        session_id = obj.get("id")
        if (
            obj.get("object") != "checkout.session"
            or not isinstance(session_id, str)
            or not session_id.startswith("cs_")
        ):
            raise InvalidWebhook("Event does not reference a Checkout Session")
        return session_id

    def _already_processed(self, event_id: str) -> bool:
        with self._session_factory() as session:
            return session.get(StripeEvent, event_id) is not None

    def _record_only(self, event_id: str, event_type: str, created: int, outcome: Outcome) -> Outcome:
        with self._session_factory() as session, session.begin():
            if not self._claim(session, event_id, event_type, created, outcome=outcome.value):
                return Outcome.DUPLICATE
        return outcome

    @staticmethod
    def _claim(session: Session, event_id: str, event_type: str, created: int, outcome: str) -> bool:
        stmt = (
            insert(StripeEvent)
            .values(id=event_id, type=event_type, stripe_created=created, outcome=outcome)
            .on_conflict_do_nothing(index_elements=[StripeEvent.id])
            .returning(StripeEvent.id)
        )
        return session.execute(stmt).scalar_one_or_none() is not None

    def _apply(
        self,
        session: Session,
        event_id: str,
        event_type: str,
        session_id: str,
        remote: CheckoutSessionData,
    ) -> Outcome:
        payment = self._locate_payment(session, session_id, remote)
        if payment is None:
            logger.warning("webhook_payment_not_found event_id=%s session_id=%s", event_id, session_id)
            return Outcome.PAYMENT_NOT_FOUND

        mismatch = self._verify(payment, session_id, remote)
        if mismatch is not None:
            logger.error(
                "webhook_verification_failed payment_id=%s session_id=%s reason=%s", payment.id, session_id, mismatch
            )
            if can_transition(payment.status, PaymentStatus.FAILED):
                payment.status = PaymentStatus.FAILED
                payment.failure_reason = f"stripe_verification_failed:{mismatch}"
                payment.last_stripe_event_id = event_id
            return Outcome.VERIFICATION_FAILED

        if payment.stripe_checkout_session_id is None:
            payment.stripe_checkout_session_id = remote.id
        if remote.payment_intent_id:
            payment.stripe_payment_intent_id = remote.payment_intent_id
        if remote.customer_email and not payment.customer_email:
            payment.customer_email = remote.customer_email

        target, reason = self._decide(event_type, remote)
        if target is None:
            return Outcome.NO_CHANGE
        if payment.status == target:
            return Outcome.NO_CHANGE
        if not can_transition(payment.status, target):
            logger.info(
                "webhook_stale_event payment_id=%s current=%s target=%s", payment.id, payment.status.value, target.value
            )
            return Outcome.STALE_EVENT

        payment.status = target
        payment.last_stripe_event_id = event_id
        if target is PaymentStatus.PAID:
            payment.paid_at = datetime.now(UTC)
            payment.failure_reason = None
            return Outcome.PROCESSED_PAID
        payment.failure_reason = reason
        return Outcome.PROCESSED_FAILED

    @staticmethod
    def _locate_payment(session: Session, session_id: str, remote: CheckoutSessionData) -> Payment | None:
        repo = PaymentRepository(session)
        reference = remote.metadata.get("payment_id") or remote.client_reference_id
        if reference:
            try:
                payment = repo.get_for_update(uuid.UUID(reference))
            except ValueError:
                payment = None
            if payment is not None:
                return payment
        return repo.get_by_session_for_update(session_id)

    @staticmethod
    def _verify(payment: Payment, session_id: str, remote: CheckoutSessionData) -> str | None:
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

    @staticmethod
    def _decide(event_type: str, remote: CheckoutSessionData) -> tuple[PaymentStatus | None, str | None]:
        if remote.payment_status == "paid":
            return PaymentStatus.PAID, None
        if remote.status == "expired":
            return PaymentStatus.FAILED, "checkout_session_expired"
        if event_type == EventType.ASYNC_FAILED.value and remote.payment_status == "unpaid":
            return PaymentStatus.FAILED, "async_payment_failed"
        return None, None
