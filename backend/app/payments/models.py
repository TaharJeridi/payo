import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin
from app.payments.state import PaymentStatus

payment_status_enum = Enum(
    PaymentStatus,
    name="payment_status",
    values_callable=lambda e: [m.value for m in e],
)


class Payment(TimestampMixin, Base):
    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("char_length(currency) = 3", name="currency_iso"),
        Index("ix_payments_created_at", text("created_at DESC")),
        Index("ix_payments_merchant_created", "merchant_id", text("created_at DESC")),
        # Idempotency is scoped per merchant: two merchants may use the same key.
        UniqueConstraint("merchant_id", "idempotency_key", name="uq_payments_merchant_idempotency_key"),
        Index(
            "ix_payments_next_reconcile_at",
            "next_reconcile_at",
            postgresql_where=text("next_reconcile_at IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("merchants.id", ondelete="RESTRICT"), nullable=False
    )
    product_id: Mapped[str] = mapped_column(String(64), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_amount: Mapped[int] = mapped_column(Integer, nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[PaymentStatus] = mapped_column(
        payment_status_enum,
        nullable=False,
        default=PaymentStatus.CREATING,
        index=True,
    )
    # Client-supplied Idempotency-Key and the SHA-256 of the parameters it was first used with.
    # Nullable only for rows created before migration 0002.
    idempotency_key: Mapped[str | None] = mapped_column(String(255))
    request_fingerprint: Mapped[str | None] = mapped_column(String(64))

    stripe_checkout_session_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    checkout_url: Mapped[str | None] = mapped_column(Text)
    # Decided once, before the first Stripe call, and sent as `expires_at`; then overwritten
    # with the value Stripe reports for the session. Never recomputed on retries.
    checkout_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set when Stripe's answer to the creation request was lost (timeout, 5xx...).
    checkout_creation_error: Mapped[str | None] = mapped_column(String(255))

    stripe_payment_intent_id: Mapped[str | None] = mapped_column(String(255))
    customer_email: Mapped[str | None] = mapped_column(String(320))
    failure_reason: Mapped[str | None] = mapped_column(String(255))
    last_stripe_event_id: Mapped[str | None] = mapped_column(String(255))
    last_reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    creation_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    creation_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reconcile_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    next_reconcile_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_reconcile_error: Mapped[str | None] = mapped_column(String(255))


class PaymentEvent(Base):
    __tablename__ = "payment_events"
    __table_args__ = (Index("ix_payment_events_payment_id_id", "payment_id", "id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    payment_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("payments.id", ondelete="CASCADE"), nullable=False)
    from_status: Mapped[PaymentStatus | None] = mapped_column(payment_status_enum)
    to_status: Mapped[PaymentStatus] = mapped_column(payment_status_enum, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(255))
    stripe_event_id: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
