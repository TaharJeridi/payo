import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta

from sqlalchemy import ColumnElement, func, select, update
from sqlalchemy.orm import Session

from app.payments.facts import paid_after_expiry_clause, recovered_clause
from app.payments.filters import PaymentFilters
from app.payments.models import Payment, PaymentEvent
from app.payments.state import NON_FINAL_STATUSES, PaymentStatus


class PaymentRepository:
    """Data access for payments.

    Every read exposed to API clients takes a ``merchant_id``: tenant scoping is applied
    here, in one place, and never derived from request parameters.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    # --- merchant-scoped (API) ---------------------------------------------------------

    def get_for_merchant(self, merchant_id: uuid.UUID, payment_id: uuid.UUID) -> Payment | None:
        return self._session.execute(
            select(Payment).where(Payment.id == payment_id, Payment.merchant_id == merchant_id)
        ).scalar_one_or_none()

    def get_by_idempotency_key(
        self, merchant_id: uuid.UUID, idempotency_key: str, *, for_update: bool = False
    ) -> Payment | None:
        query = select(Payment).where(Payment.merchant_id == merchant_id, Payment.idempotency_key == idempotency_key)
        if for_update:
            query = query.with_for_update()
        return self._session.execute(query).scalar_one_or_none()

    def stats_for_merchant(self, merchant_id: uuid.UUID) -> tuple[dict[PaymentStatus, int], dict[str, int]]:
        rows = self._session.execute(
            select(Payment.status, Payment.currency, func.count(), func.coalesce(func.sum(Payment.amount), 0))
            .where(Payment.merchant_id == merchant_id)
            .group_by(Payment.status, Payment.currency)
        ).all()
        counts: dict[PaymentStatus, int] = dict.fromkeys(PaymentStatus, 0)
        paid: dict[str, int] = {}
        for status, currency, count, amount in rows:
            counts[status] += int(count)
            if status is PaymentStatus.PAID:
                paid[currency] = paid.get(currency, 0) + int(amount)
        return counts, paid

    def list_for_merchant(
        self,
        merchant_id: uuid.UUID,
        *,
        limit: int,
        offset: int,
        status: PaymentStatus | None = None,
    ) -> tuple[Sequence[Payment], int]:
        conditions = [Payment.merchant_id == merchant_id]
        if status is not None:
            conditions.append(Payment.status == status)
        items = (
            self._session.execute(
                select(Payment)
                .where(*conditions)
                .order_by(Payment.created_at.desc(), Payment.id)
                .limit(limit)
                .offset(offset)
            )
            .scalars()
            .all()
        )
        total = self._session.execute(select(func.count()).select_from(Payment).where(*conditions)).scalar_one()
        return items, total

    def find(self, merchant_id: uuid.UUID, filters: PaymentFilters, *, limit: int) -> tuple[Sequence[Payment], int]:
        conditions = self._conditions(merchant_id, filters)
        items = (
            self._session.execute(
                select(Payment).where(*conditions).order_by(Payment.created_at.desc(), Payment.id).limit(limit)
            )
            .scalars()
            .all()
        )
        total = self._session.execute(select(func.count()).select_from(Payment).where(*conditions)).scalar_one()
        return items, total

    def count_by_status(self, merchant_id: uuid.UUID, filters: PaymentFilters) -> dict[PaymentStatus, int]:
        rows = self._session.execute(
            select(Payment.status, func.count()).where(*self._conditions(merchant_id, filters)).group_by(Payment.status)
        ).all()
        counts: dict[PaymentStatus, int] = dict.fromkeys(PaymentStatus, 0)
        for status, count in rows:
            counts[status] = int(count)
        return counts

    def amount_totals(self, merchant_id: uuid.UUID, filters: PaymentFilters) -> dict[str, tuple[int, int]]:
        rows = self._session.execute(
            select(Payment.currency, func.count(), func.coalesce(func.sum(Payment.amount), 0))
            .where(*self._conditions(merchant_id, filters))
            .group_by(Payment.currency)
            .order_by(Payment.currency)
        ).all()
        return {currency: (int(count), int(total)) for currency, count, total in rows}

    def product_totals(self, merchant_id: uuid.UUID, filters: PaymentFilters) -> list[tuple[str, str, int, int, int]]:
        rows = self._session.execute(
            select(
                Payment.product_id,
                Payment.currency,
                func.count(),
                func.coalesce(func.sum(Payment.quantity), 0),
                func.coalesce(func.sum(Payment.amount), 0),
            )
            .where(*self._conditions(merchant_id, filters))
            .group_by(Payment.product_id, Payment.currency)
            .order_by(func.sum(Payment.quantity).desc(), Payment.product_id)
        ).all()
        return [(product, currency, int(n), int(units), int(total)) for product, currency, n, units, total in rows]

    def histories(self, payment_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, list[PaymentEvent]]:
        result: dict[uuid.UUID, list[PaymentEvent]] = {pid: [] for pid in payment_ids}
        if not payment_ids:
            return result
        events = self._session.execute(
            select(PaymentEvent).where(PaymentEvent.payment_id.in_(payment_ids)).order_by(PaymentEvent.id)
        ).scalars()
        for event in events:
            result[event.payment_id].append(event)
        return result

    @staticmethod
    def _conditions(merchant_id: uuid.UUID, filters: PaymentFilters) -> list[ColumnElement[bool]]:
        conditions: list[ColumnElement[bool]] = [Payment.merchant_id == merchant_id]
        if filters.payment_ids:
            conditions.append(Payment.id.in_(filters.payment_ids))
        if filters.statuses:
            conditions.append(Payment.status.in_(filters.statuses))
        if filters.product_ids:
            conditions.append(Payment.product_id.in_(filters.product_ids))
        if filters.quantity is not None:
            conditions.append(Payment.quantity == filters.quantity)
        if filters.currency is not None:
            conditions.append(Payment.currency == filters.currency)
        if (amount := filters.amount) is not None:
            if amount.minimum is not None:
                conditions.append(
                    Payment.amount >= amount.minimum if amount.minimum_inclusive else Payment.amount > amount.minimum
                )
            if amount.maximum is not None:
                conditions.append(
                    Payment.amount <= amount.maximum if amount.maximum_inclusive else Payment.amount < amount.maximum
                )
        if filters.created_from is not None:
            conditions.append(Payment.created_at >= filters.created_from)
        if filters.created_to is not None:
            conditions.append(Payment.created_at < filters.created_to)
        if filters.recovered is not None or filters.recovery_source is not None:
            clause = recovered_clause(filters.recovery_source)
            conditions.append(clause if filters.recovered is not False else ~clause)
        if filters.paid_after_expiry is not None:
            clause = paid_after_expiry_clause()
            conditions.append(clause if filters.paid_after_expiry else ~clause)
        return conditions

    # --- internal (webhooks, reconciliation): identity comes from Stripe ids ----------

    def get_for_update(self, payment_id: uuid.UUID) -> Payment | None:
        return self._session.execute(
            select(Payment).where(Payment.id == payment_id).with_for_update().execution_options(populate_existing=True)
        ).scalar_one_or_none()

    def get_by_session_for_update(self, session_id: str) -> Payment | None:
        return self._session.execute(
            select(Payment)
            .where(Payment.stripe_checkout_session_id == session_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()

    def history(self, payment_id: uuid.UUID) -> Sequence[PaymentEvent]:
        return (
            self._session.execute(
                select(PaymentEvent).where(PaymentEvent.payment_id == payment_id).order_by(PaymentEvent.id)
            )
            .scalars()
            .all()
        )

    def claim_due_for_reconciliation(self, now: datetime, limit: int, lease: timedelta) -> Sequence[uuid.UUID]:
        """Non-final payments whose next check is due, locked with SKIP LOCKED and leased.

        Concurrent reconcilers never receive the same row: a claimed row is skipped by the
        others while locked, and its ``next_reconcile_at`` moves past ``now`` before commit.
        """
        ids = (
            self._session.execute(
                select(Payment.id)
                .where(Payment.status.in_(NON_FINAL_STATUSES), Payment.next_reconcile_at <= now)
                .order_by(Payment.next_reconcile_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
            .scalars()
            .all()
        )
        if ids:
            self._session.execute(update(Payment).where(Payment.id.in_(ids)).values(next_reconcile_at=now + lease))
        return ids
