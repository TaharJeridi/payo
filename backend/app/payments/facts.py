"""Facts derived deterministically from ``payment_events``.

They are not stored: the event history is the source of truth and these rules are applied
both in Python (documents, evidence) and in SQL (filters), with the same definitions:

- recovered: the creation outcome was lost (``creation_unknown``) and later became known
  (``creation_unknown -> open`` or ``-> paid``). ``recovery_source`` is the source of that
  transition (``reconciliation`` when the reconciler adopted the Stripe session).
  A client retry first moves ``creation_unknown -> creating``; the payment counts as
  recovered once the session is confirmed, with ``recovery_source`` "api".
- paid_after_expiry: the payment reached ``paid`` from ``expired``, i.e. Stripe confirmed
  it after the Checkout Session had been reported expired.
- confirmed_by: the source of the transition to ``paid``.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import ColumnElement, and_, exists, func, select

from app.payments.models import Payment, PaymentEvent
from app.payments.state import PaymentStatus

RECOVERY_TARGETS = (PaymentStatus.OPEN, PaymentStatus.PAID)


@dataclass(frozen=True, slots=True)
class PaymentFacts:
    recovered: bool
    recovery_source: str | None
    paid_after_expiry: bool
    confirmed_by: str | None


def derive_facts(events: Sequence[PaymentEvent]) -> PaymentFacts:
    lost = False
    recovery_source: str | None = None
    paid_after_expiry = False
    confirmed_by: str | None = None
    for event in events:
        if event.to_status is PaymentStatus.CREATION_UNKNOWN:
            lost = True
        elif lost and recovery_source is None and event.to_status in RECOVERY_TARGETS:
            recovery_source = event.source
        if event.to_status is PaymentStatus.PAID:
            confirmed_by = event.source
            paid_after_expiry = event.from_status is PaymentStatus.EXPIRED
    return PaymentFacts(
        recovered=recovery_source is not None,
        recovery_source=recovery_source,
        paid_after_expiry=paid_after_expiry,
        confirmed_by=confirmed_by,
    )


def recovered_clause(source: str | None = None) -> ColumnElement[bool]:
    events = PaymentEvent.__table__
    lost, later, first = events.alias("lost"), events.alias("later"), events.alias("recovery")
    first_lost = (
        select(func.min(lost.c.id))
        .where(lost.c.payment_id == Payment.id, lost.c.to_status == PaymentStatus.CREATION_UNKNOWN)
        .scalar_subquery()
    )
    first_recovery = (
        select(func.min(later.c.id))
        .where(later.c.payment_id == Payment.id, later.c.to_status.in_(RECOVERY_TARGETS), later.c.id > first_lost)
        .scalar_subquery()
    )
    condition = and_(first.c.payment_id == Payment.id, first.c.id == first_recovery)
    if source is not None:
        condition = and_(condition, first.c.source == source)
    return exists(select(first.c.id).where(condition))


def paid_after_expiry_clause() -> ColumnElement[bool]:
    return exists(
        select(PaymentEvent.id).where(
            PaymentEvent.payment_id == Payment.id,
            PaymentEvent.from_status == PaymentStatus.EXPIRED,
            PaymentEvent.to_status == PaymentStatus.PAID,
        )
    )
