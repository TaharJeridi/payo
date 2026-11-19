"""Deterministic synthetic payments for evaluating the RAG assistant.

The rows belong to a dedicated merchant (plus a second "other" tenant used to test isolation)
and never touch Stripe: session ids are
``cs_test_seed_*`` and ``next_reconcile_at`` is NULL, so the reconciler never claims them.
Ids are uuid5 values derived from a reference name, which lets evaluation cases point to
"the payment of 89.70 EUR" by name. Seeding is insert-only and can be repeated.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.merchants.models import Merchant
from app.payments.catalog import get_product
from app.payments.models import Payment, PaymentEvent
from app.payments.state import PaymentStatus, can_transition

SEED_NAMESPACE = uuid.UUID("6f1c9a52-3d4e-4b8a-9f0e-2a7c5d1e8b34")
SEED_MERCHANT_ID = uuid.uuid5(SEED_NAMESPACE, "merchant")
SEED_MERCHANT_NAME = "PayFlow Eval (dati sintetici)"
OTHER_MERCHANT_ID = uuid.uuid5(SEED_NAMESPACE, "merchant:other")
OTHER_MERCHANT_NAME = "PayFlow Eval - altro tenant (dati sintetici)"
SEED_MERCHANTS = {"main": (SEED_MERCHANT_ID, SEED_MERCHANT_NAME), "other": (OTHER_MERCHANT_ID, OTHER_MERCHANT_NAME)}
SEED_TTL = timedelta(hours=2)

S = PaymentStatus


@dataclass(frozen=True, slots=True)
class Step:
    to_status: PaymentStatus
    source: str
    after: timedelta
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class SeedPayment:
    ref: str
    product_id: str
    quantity: int
    created_at: datetime
    steps: tuple[Step, ...]
    reconcile_attempts: int = 0
    last_reconcile_error: str | None = None
    has_session: bool = True
    tenant: str = "main"

    @property
    def id(self) -> uuid.UUID:
        return seed_payment_id(self.ref)

    @property
    def merchant_id(self) -> uuid.UUID:
        return SEED_MERCHANTS[self.tenant][0]

    @property
    def status(self) -> PaymentStatus:
        return self.steps[-1].to_status if self.steps else S.CREATING


def seed_payment_id(ref: str) -> uuid.UUID:
    return uuid.uuid5(SEED_NAMESPACE, f"payment:{ref}")


def _at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=UTC)


def _opened(after: timedelta = timedelta(seconds=1)) -> Step:
    return Step(S.OPEN, "api", after)


def _paid(minutes: int, source: str = "webhook") -> Step:
    return Step(S.PAID, source, timedelta(minutes=minutes))


SEED_PAYMENTS: tuple[SeedPayment, ...] = (
    SeedPayment("pro_paid_oct02", "payflow-pro", 1, _at(2, 9, 15), (_opened(), _paid(4))),
    SeedPayment("pro_paid_oct05", "payflow-pro", 1, _at(5, 14, 40), (_opened(), _paid(11))),
    SeedPayment("pro3_paid", "payflow-pro", 3, _at(7, 10, 5), (_opened(), _paid(6))),
    SeedPayment("report_paid", "payflow-report", 1, _at(8, 16, 30), (_opened(), _paid(3))),
    SeedPayment("report2_paid", "payflow-report", 2, _at(12, 11, 0), (_opened(), _paid(9))),
    SeedPayment("starter_paid_a", "payflow-starter", 1, _at(3, 8, 50), (_opened(), _paid(2))),
    SeedPayment("starter_paid_b", "payflow-starter", 1, _at(15, 19, 20), (_opened(), _paid(5))),
    SeedPayment("starter5_paid", "payflow-starter", 5, _at(18, 12, 10), (_opened(), _paid(7))),
    SeedPayment(
        "report_failed_rejected",
        "payflow-report",
        1,
        _at(9, 10, 0),
        (Step(S.FAILED, "api", timedelta(seconds=1), "checkout_session_creation_rejected"),),
        has_session=False,
    ),
    SeedPayment(
        "pro_failed_async",
        "payflow-pro",
        1,
        _at(14, 15, 45),
        (_opened(), Step(S.FAILED, "webhook", timedelta(hours=26), "async_payment_failed")),
    ),
    SeedPayment(
        "starter_failed_not_created",
        "payflow-starter",
        1,
        _at(20, 9, 30),
        (
            Step(S.CREATION_UNKNOWN, "api", timedelta(seconds=10), "stripe_outcome_unknown"),
            Step(S.FAILED, "reconciliation", timedelta(hours=2, minutes=7), "checkout_session_not_created"),
        ),
        has_session=False,
    ),
    SeedPayment(
        "pro_expired",
        "payflow-pro",
        1,
        _at(6, 17, 0),
        (_opened(), Step(S.EXPIRED, "webhook", SEED_TTL, "checkout_session_expired")),
    ),
    SeedPayment(
        "report_expired",
        "payflow-report",
        1,
        _at(16, 13, 25),
        (_opened(), Step(S.EXPIRED, "reconciliation", SEED_TTL + timedelta(minutes=5), "checkout_session_expired")),
    ),
    SeedPayment(
        "starter2_expired",
        "payflow-starter",
        2,
        _at(22, 18, 5),
        (_opened(), Step(S.EXPIRED, "webhook", SEED_TTL, "checkout_session_expired")),
    ),
    SeedPayment("pro_open", "payflow-pro", 1, _at(27, 10, 30), (_opened(),)),
    SeedPayment("report_open", "payflow-report", 1, _at(28, 9, 10), (_opened(),)),
    SeedPayment("starter_open", "payflow-starter", 1, _at(28, 15, 55), (_opened(),)),
    SeedPayment(
        "pro2_unknown",
        "payflow-pro",
        2,
        _at(29, 11, 20),
        (Step(S.CREATION_UNKNOWN, "api", timedelta(seconds=10), "stripe_outcome_unknown"),),
        reconcile_attempts=3,
        last_reconcile_error="payment_provider_unavailable",
        has_session=False,
    ),
    SeedPayment(
        "report_paid_recovered",
        "payflow-report",
        1,
        _at(10, 9, 0),
        (
            Step(S.CREATION_UNKNOWN, "api", timedelta(seconds=10), "stripe_outcome_unknown"),
            Step(S.OPEN, "reconciliation", timedelta(minutes=1)),
            _paid(8, "reconciliation"),
        ),
    ),
    SeedPayment(
        "pro_paid_after_expiry",
        "payflow-pro",
        1,
        _at(19, 20, 0),
        (
            _opened(),
            Step(S.EXPIRED, "reconciliation", SEED_TTL + timedelta(minutes=5), "checkout_session_expired"),
            _paid(3),
        ),
    ),
    SeedPayment("other_report3_paid", "payflow-report", 3, _at(11, 12, 0), (_opened(), _paid(4)), tenant="other"),
    SeedPayment("other_pro_open", "payflow-pro", 1, _at(21, 16, 45), (_opened(),), tenant="other"),
)


@dataclass(frozen=True, slots=True)
class SeedReport:
    merchant_created: bool
    inserted: int
    existing: int

    def summary(self) -> str:
        merchant = "created" if self.merchant_created else "existing"
        return f"merchant={merchant} inserted={self.inserted} existing={self.existing}"


def validate(payments: Sequence[SeedPayment] = SEED_PAYMENTS) -> None:
    refs = [p.ref for p in payments]
    if len(refs) != len(set(refs)):
        raise ValueError("Duplicate seed references")
    for payment in payments:
        if payment.tenant not in SEED_MERCHANTS:
            raise ValueError(f"{payment.ref}: unknown tenant {payment.tenant}")
        current = S.CREATING
        for step in payment.steps:
            if not can_transition(current, step.to_status):
                raise ValueError(f"{payment.ref}: illegal {current.value} -> {step.to_status.value}")
            current = step.to_status


def seed(session: Session, payments: Sequence[SeedPayment] = SEED_PAYMENTS) -> SeedReport:
    validate(payments)
    merchant_created = session.get(Merchant, SEED_MERCHANT_ID) is None
    for merchant_id, name in SEED_MERCHANTS.values():
        if session.get(Merchant, merchant_id) is None:
            session.add(Merchant(id=merchant_id, name=name))
    session.flush()

    wanted = {p.id: p for p in payments}
    existing = set(session.execute(select(Payment.id).where(Payment.id.in_(wanted))).scalars())
    for payment_id, spec in wanted.items():
        if payment_id not in existing:
            _insert(session, spec)
    session.flush()
    return SeedReport(merchant_created, inserted=len(wanted) - len(existing), existing=len(existing))


def _insert(session: Session, spec: SeedPayment) -> None:
    product = get_product(spec.product_id)
    moments = []
    moment = spec.created_at
    for step in spec.steps:
        moment += step.after
        moments.append(moment)
    session_id = f"cs_test_seed_{spec.ref}" if spec.has_session else None
    paid_at = next((m for s, m in zip(spec.steps, moments, strict=True) if s.to_status is S.PAID), None)
    failure = spec.steps[-1].reason if spec.status in (S.FAILED, S.EXPIRED) else None
    payment = Payment(
        id=spec.id,
        merchant_id=spec.merchant_id,
        product_id=product.id,
        quantity=spec.quantity,
        unit_amount=product.unit_amount,
        amount=product.unit_amount * spec.quantity,
        currency=product.currency,
        status=spec.status,
        idempotency_key=f"seed-{spec.ref}",
        stripe_checkout_session_id=session_id,
        checkout_expires_at=spec.created_at + SEED_TTL,
        stripe_payment_intent_id=f"pi_test_seed_{spec.ref}" if paid_at else None,
        failure_reason=failure,
        paid_at=paid_at,
        creation_attempts=1,
        creation_attempt_at=spec.created_at,
        reconcile_attempts=spec.reconcile_attempts,
        last_reconcile_error=spec.last_reconcile_error,
        next_reconcile_at=None,
        created_at=spec.created_at,
        updated_at=moments[-1] if moments else spec.created_at,
    )
    session.add(payment)
    session.flush()
    session.add(
        PaymentEvent(
            payment_id=spec.id, from_status=None, to_status=S.CREATING, source="api", created_at=spec.created_at
        )
    )
    previous = S.CREATING
    for step, at in zip(spec.steps, moments, strict=True):
        session.add(
            PaymentEvent(
                payment_id=spec.id,
                from_status=previous,
                to_status=step.to_status,
                source=step.source,
                reason=step.reason,
                stripe_event_id=f"evt_seed_{spec.ref}_{step.to_status.value}" if step.source == "webhook" else None,
                created_at=at,
            )
        )
        previous = step.to_status
