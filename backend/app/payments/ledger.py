"""The only place where ``Payment.status`` changes.

Every transition is validated against the state machine and appended to ``payment_events``,
so the history is a gapless chain: each row's ``from_status`` is the previous ``to_status``.
"""

import enum
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.payments.models import Payment, PaymentEvent
from app.payments.state import PaymentStatus, can_transition, is_final

logger = logging.getLogger(__name__)


class EventSource(enum.StrEnum):
    API = "api"
    WEBHOOK = "webhook"
    RECONCILIATION = "reconciliation"


class IllegalTransition(Exception):
    def __init__(self, current: PaymentStatus, target: PaymentStatus) -> None:
        super().__init__(f"Illegal payment transition {current.value} -> {target.value}")
        self.current = current
        self.target = target


@dataclass(frozen=True, slots=True)
class SchedulePolicy:
    creation_lease: timedelta = timedelta(minutes=2)
    unknown_first_check: timedelta = timedelta(minutes=1)
    open_first_check: timedelta = timedelta(minutes=15)


class PaymentLedger:
    def __init__(self, session: Session, clock: Callable[[], datetime], policy: SchedulePolicy | None = None) -> None:
        self._session = session
        self._clock = clock
        self._policy = policy or SchedulePolicy()

    def record_creation(self, payment: Payment, source: EventSource) -> None:
        self._session.add(
            PaymentEvent(payment_id=payment.id, from_status=None, to_status=payment.status, source=source.value)
        )

    def transition(
        self,
        payment: Payment,
        target: PaymentStatus,
        *,
        source: EventSource,
        reason: str | None = None,
        stripe_event_id: str | None = None,
    ) -> None:
        current = payment.status
        if not can_transition(current, target):
            raise IllegalTransition(current, target)
        now = self._clock()
        self._session.add(
            PaymentEvent(
                payment_id=payment.id,
                from_status=current,
                to_status=target,
                source=source.value,
                reason=reason,
                stripe_event_id=stripe_event_id,
            )
        )
        payment.status = target
        if stripe_event_id:
            payment.last_stripe_event_id = stripe_event_id
        payment.reconcile_attempts = 0
        payment.last_reconcile_error = None
        payment.next_reconcile_at = self._next_check(target, now)
        if target is PaymentStatus.PAID:
            payment.paid_at = now
            payment.failure_reason = None
        elif target in (PaymentStatus.FAILED, PaymentStatus.EXPIRED, PaymentStatus.CREATION_UNKNOWN):
            payment.failure_reason = reason
        if target in (PaymentStatus.FAILED, PaymentStatus.EXPIRED):
            payment.checkout_url = None
        if current in (PaymentStatus.FAILED, PaymentStatus.EXPIRED) and target is PaymentStatus.PAID:
            logger.warning("late_verified_payment payment_id=%s from=%s", payment.id, current.value)

    def _next_check(self, status: PaymentStatus, now: datetime) -> datetime | None:
        if is_final(status):
            return None
        if status is PaymentStatus.CREATING:
            return now + self._policy.creation_lease
        if status is PaymentStatus.CREATION_UNKNOWN:
            return now + self._policy.unknown_first_check
        return now + self._policy.open_first_check
