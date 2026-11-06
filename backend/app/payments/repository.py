import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.payments.models import Payment
from app.payments.state import PaymentStatus


class PaymentRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, payment: Payment) -> Payment:
        self._session.add(payment)
        self._session.flush()
        return payment

    def get_for_update(self, payment_id: uuid.UUID) -> Payment | None:
        return self._session.execute(
            select(Payment).where(Payment.id == payment_id).with_for_update()
        ).scalar_one_or_none()

    def get_by_session_for_update(self, session_id: str) -> Payment | None:
        return self._session.execute(
            select(Payment).where(Payment.stripe_checkout_session_id == session_id).with_for_update()
        ).scalar_one_or_none()

    def list(self, *, limit: int, offset: int, status: PaymentStatus | None = None) -> tuple[Sequence[Payment], int]:
        query = select(Payment)
        count_query = select(func.count()).select_from(Payment)
        if status is not None:
            query = query.where(Payment.status == status)
            count_query = count_query.where(Payment.status == status)
        items = (
            self._session.execute(query.order_by(Payment.created_at.desc(), Payment.id).limit(limit).offset(offset))
            .scalars()
            .all()
        )
        total = self._session.execute(count_query).scalar_one()
        return items, total
