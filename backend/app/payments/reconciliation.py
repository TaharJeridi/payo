"""Reconciliation: bring non-final payments in line with Stripe when webhooks or API answers did not.

Work selection: every non-final payment carries ``next_reconcile_at``. A run claims the due rows
with ``FOR UPDATE SKIP LOCKED`` and moves them forward by a lease, so parallel reconcilers never
process the same payment.

Per payment:
- ``creating`` within its creation lease → skipped (a request is still talking to Stripe);
- session id known → ``retrieve``;
- session id unknown → list the sessions created since the payment existed and match
  ``metadata.payment_id``. Absence is concluded only after the stored ``expires_at`` (the value
  Stripe accepted or reported): before that a client retry may still create it; after it no
  session can be created anymore, so the payment is closed as ``failed``.

Stripe calls happen outside transactions; the result is applied under a row lock after checking
the payment is still non-final and unchanged, with the verification and state machine shared
with webhooks. Stripe errors never change the state: the payment is rescheduled with exponential
backoff. Open sessions are re-polled with the same backoff, never later than their real
``expires_at`` plus a grace period.
"""

import enum
import logging
import secrets
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.errors import PaymentProviderError
from app.integrations.stripe_gateway import CheckoutSessionData, PaymentGateway
from app.payments.ledger import EventSource, PaymentLedger, SchedulePolicy
from app.payments.models import Payment
from app.payments.repository import PaymentRepository
from app.payments.state import PaymentStatus, is_final
from app.payments.sync import SyncResult, apply_remote_state

logger = logging.getLogger(__name__)

EXPIRY_GRACE = timedelta(minutes=5)


class ReconcileOutcome(enum.StrEnum):
    OPEN = "open"
    PAID = "paid"
    FAILED = "failed"
    EXPIRED = "expired"
    SESSION_NOT_CREATED = "session_not_created"
    VERIFICATION_FAILED = "verification_failed"
    NO_CHANGE = "no_change"
    AWAITING_SESSION = "awaiting_session"
    DEFERRED_IN_FLIGHT = "deferred_in_flight"
    CHANGED_CONCURRENTLY = "changed_concurrently"
    SKIPPED = "skipped"
    ERROR = "error"


_BY_SYNC_RESULT = {
    SyncResult.OPEN: ReconcileOutcome.OPEN,
    SyncResult.PAID: ReconcileOutcome.PAID,
    SyncResult.FAILED: ReconcileOutcome.FAILED,
    SyncResult.EXPIRED: ReconcileOutcome.EXPIRED,
    SyncResult.VERIFICATION_FAILED: ReconcileOutcome.VERIFICATION_FAILED,
    SyncResult.NO_CHANGE: ReconcileOutcome.NO_CHANGE,
    SyncResult.STALE: ReconcileOutcome.NO_CHANGE,
}


@dataclass
class ReconcileReport:
    outcomes: dict[ReconcileOutcome, int] = field(default_factory=dict)

    def add(self, outcome: ReconcileOutcome) -> None:
        self.outcomes[outcome] = self.outcomes.get(outcome, 0) + 1

    @property
    def checked(self) -> int:
        return sum(self.outcomes.values())

    def summary(self) -> str:
        parts = ", ".join(f"{k.value}={v}" for k, v in sorted(self.outcomes.items()))
        return f"checked={self.checked} {parts}".strip()


@dataclass(frozen=True, slots=True)
class _Snapshot:
    status: PaymentStatus
    session_id: str | None
    created_at: datetime
    expires_at: datetime | None
    creation_attempts: int
    creation_attempt_at: datetime | None


class Reconciler:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        gateway: PaymentGateway,
        settings: Settings,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        jitter: Callable[[], float] = lambda: secrets.SystemRandom().uniform(0.8, 1.2),
    ) -> None:
        self._session_factory = session_factory
        self._gateway = gateway
        self._settings = settings
        self._clock = clock
        self._jitter = jitter
        self._skew = timedelta(seconds=settings.reconcile_clock_skew_seconds)
        self._lease = timedelta(seconds=settings.checkout_creation_lease_seconds)
        self._policy = SchedulePolicy(
            creation_lease=self._lease,
            open_first_check=timedelta(seconds=settings.reconcile_open_first_check_seconds),
        )

    def run_once(self) -> ReconcileReport:
        due = self._claim()
        report = ReconcileReport()
        for payment_id in due:
            try:
                outcome = self.reconcile_payment(payment_id)
            except Exception:
                logger.exception("reconciliation_unexpected_error payment_id=%s", payment_id)
                self._record_error(payment_id, "unexpected_error")
                outcome = ReconcileOutcome.ERROR
            report.add(outcome)
        if due:
            logger.info("reconciliation_run %s", report.summary())
        return report

    def backoff(self, attempts: int) -> timedelta:
        cap = self._settings.reconcile_backoff_max_seconds
        seconds = min(self._settings.reconcile_backoff_base_seconds * 2 ** min(attempts, 20), cap) * self._jitter()
        return timedelta(seconds=min(seconds, cap))

    def reconcile_payment(self, payment_id: uuid.UUID) -> ReconcileOutcome:
        snapshot = self._snapshot(payment_id)
        if snapshot is None:
            return ReconcileOutcome.SKIPPED
        now = self._clock()
        if (
            snapshot.status is PaymentStatus.CREATING
            and snapshot.creation_attempt_at is not None
            and snapshot.creation_attempt_at > now - self._lease
        ):
            self._reschedule(payment_id, snapshot.creation_attempt_at + self._lease)
            return ReconcileOutcome.DEFERRED_IN_FLIGHT
        try:
            remote = self._fetch_remote(payment_id, snapshot, now)
        except PaymentProviderError as exc:
            self._record_error(payment_id, exc.code)
            return ReconcileOutcome.ERROR
        return self._apply(payment_id, snapshot, remote, now)

    def _claim(self) -> list[uuid.UUID]:
        now = self._clock()
        lease = timedelta(seconds=self._settings.reconcile_claim_lease_seconds)
        with self._session_factory() as session, session.begin():
            return list(
                PaymentRepository(session).claim_due_for_reconciliation(now, self._settings.reconcile_batch_size, lease)
            )

    def _snapshot(self, payment_id: uuid.UUID) -> _Snapshot | None:
        with self._session_factory() as session:
            payment = session.get(Payment, payment_id)
            if payment is None or is_final(payment.status):
                return None
            return _Snapshot(
                status=payment.status,
                session_id=payment.stripe_checkout_session_id,
                created_at=payment.created_at,
                expires_at=payment.checkout_expires_at,
                creation_attempts=payment.creation_attempts,
                creation_attempt_at=payment.creation_attempt_at,
            )

    def _fetch_remote(self, payment_id: uuid.UUID, snap: _Snapshot, now: datetime) -> CheckoutSessionData | None:
        if snap.session_id is not None:
            return self._gateway.retrieve_checkout_session(snap.session_id)
        upper = min(now, snap.expires_at) if snap.expires_at is not None else now
        return self._gateway.find_checkout_session(
            payment_id,
            created_from=snap.created_at - self._skew,
            created_to=upper + self._skew,
        )

    def _apply(
        self, payment_id: uuid.UUID, snap: _Snapshot, remote: CheckoutSessionData | None, now: datetime
    ) -> ReconcileOutcome:
        with self._session_factory() as session, session.begin():
            payment = PaymentRepository(session).get_for_update(payment_id)
            if payment is None or is_final(payment.status):
                return ReconcileOutcome.SKIPPED
            if (
                payment.creation_attempts != snap.creation_attempts
                or payment.stripe_checkout_session_id != snap.session_id
            ):
                payment.next_reconcile_at = now + timedelta(seconds=self._settings.reconcile_backoff_base_seconds)
                return ReconcileOutcome.CHANGED_CONCURRENTLY
            payment.last_reconciled_at = now
            ledger = PaymentLedger(session, self._clock, self._policy)

            if remote is None:
                return self._missing_session(payment, ledger, now)

            result, mismatch = apply_remote_state(
                payment, remote.id, remote, ledger=ledger, source=EventSource.RECONCILIATION
            )
            if result is SyncResult.VERIFICATION_FAILED:
                logger.error("reconciliation_verification_failed payment_id=%s reason=%s", payment_id, mismatch)
            elif result not in (SyncResult.NO_CHANGE, SyncResult.STALE):
                logger.info("reconciliation_applied payment_id=%s status=%s", payment_id, payment.status.value)
            if not is_final(payment.status):
                if result in (SyncResult.NO_CHANGE, SyncResult.STALE):
                    payment.reconcile_attempts += 1
                payment.next_reconcile_at = self._next_open_check(payment, remote, now)
            return _BY_SYNC_RESULT[result]

    def _missing_session(self, payment: Payment, ledger: PaymentLedger, now: datetime) -> ReconcileOutcome:
        expires_at = payment.checkout_expires_at
        if expires_at is not None and expires_at + self._skew <= now:
            payment.checkout_creation_error = None
            ledger.transition(
                payment,
                PaymentStatus.FAILED,
                source=EventSource.RECONCILIATION,
                reason="checkout_session_not_created",
            )
            logger.info("reconciliation_no_session payment_id=%s", payment.id)
            return ReconcileOutcome.SESSION_NOT_CREATED
        payment.reconcile_attempts += 1
        polled = now + self.backoff(payment.reconcile_attempts)
        payment.next_reconcile_at = min(polled, expires_at + self._skew) if expires_at is not None else polled
        return ReconcileOutcome.AWAITING_SESSION

    def _next_open_check(self, payment: Payment, remote: CheckoutSessionData, now: datetime) -> datetime:
        polled = now + self.backoff(payment.reconcile_attempts)
        if remote.status == "open" and remote.expires_at is not None:
            after_expiry = remote.expires_at + EXPIRY_GRACE
            if after_expiry > now:
                return min(polled, after_expiry)
        return polled

    def _reschedule(self, payment_id: uuid.UUID, when: datetime) -> None:
        with self._session_factory() as session, session.begin():
            session.execute(update(Payment).where(Payment.id == payment_id).values(next_reconcile_at=when))

    def _record_error(self, payment_id: uuid.UUID, error: str) -> None:
        with self._session_factory() as session, session.begin():
            payment = PaymentRepository(session).get_for_update(payment_id)
            if payment is None or is_final(payment.status):
                return
            payment.reconcile_attempts += 1
            payment.last_reconcile_error = error[:255]
            payment.last_reconciled_at = self._clock()
            payment.next_reconcile_at = self._clock() + self.backoff(payment.reconcile_attempts)
            logger.warning(
                "reconciliation_stripe_error payment_id=%s attempts=%s error=%s next=%s",
                payment_id,
                payment.reconcile_attempts,
                error,
                payment.next_reconcile_at.isoformat(),
            )
