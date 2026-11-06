"""payment engine: payments and stripe_events

Revision ID: 0001_payment_engine
Revises:
Create Date: 2026-11-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001_payment_engine"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

payment_status = postgresql.ENUM("pending", "paid", "failed", name="payment_status", create_type=False)


def upgrade() -> None:
    payment_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "payments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.String(64), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_amount", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", payment_status, nullable=False),
        sa.Column("stripe_checkout_session_id", sa.String(255), nullable=True),
        sa.Column("stripe_payment_intent_id", sa.String(255), nullable=True),
        sa.Column("customer_email", sa.String(320), nullable=True),
        sa.Column("failure_reason", sa.String(255), nullable=True),
        sa.Column("last_stripe_event_id", sa.String(255), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("amount > 0", name=op.f("ck_payments_amount_positive")),
        sa.CheckConstraint("quantity > 0", name=op.f("ck_payments_quantity_positive")),
        sa.CheckConstraint("char_length(currency) = 3", name=op.f("ck_payments_currency_iso")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payments")),
        sa.UniqueConstraint("stripe_checkout_session_id", name=op.f("uq_payments_stripe_checkout_session_id")),
    )
    op.create_index(op.f("ix_payments_status"), "payments", ["status"])
    op.create_index("ix_payments_created_at", "payments", [sa.text("created_at DESC")])

    op.create_table(
        "stripe_events",
        sa.Column("id", sa.String(255), nullable=False),
        sa.Column("type", sa.String(128), nullable=False),
        sa.Column("stripe_created", sa.BigInteger(), nullable=False),
        sa.Column("outcome", sa.String(64), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stripe_events")),
    )


def downgrade() -> None:
    op.drop_table("stripe_events")
    op.drop_index("ix_payments_created_at", table_name="payments")
    op.drop_index(op.f("ix_payments_status"), table_name="payments")
    op.drop_table("payments")
    payment_status.drop(op.get_bind(), checkfirst=True)
