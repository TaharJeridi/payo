"""merchants, API keys, client idempotency, session expiry and reconciliation fields

Existing payments (created before merchants existed) are assigned to a merchant named
"Legacy (pre-0002)", created only if there is at least one payment.

Revision ID: 0002_merchants_idempotency
Revises: 0001_payment_engine
Create Date: 2026-11-06
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_merchants_idempotency"
down_revision: str | None = "0001_payment_engine"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LEGACY_MERCHANT_NAME = "Legacy (pre-0002)"


def upgrade() -> None:
    op.create_table(
        "merchants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_merchants")),
    )
    op.create_table(
        "merchant_api_keys",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("merchant_id", sa.Uuid(), nullable=False),
        sa.Column("prefix", sa.String(32), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("label", sa.String(120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["merchant_id"],
            ["merchants.id"],
            name=op.f("fk_merchant_api_keys_merchant_id_merchants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_merchant_api_keys")),
    )
    op.create_index("ix_merchant_api_keys_merchant_id", "merchant_api_keys", ["merchant_id"])
    op.create_index("ix_merchant_api_keys_prefix", "merchant_api_keys", ["prefix"], unique=True)

    op.add_column("payments", sa.Column("merchant_id", sa.Uuid(), nullable=True))
    op.add_column("payments", sa.Column("idempotency_key", sa.String(255), nullable=True))
    op.add_column("payments", sa.Column("request_fingerprint", sa.String(64), nullable=True))
    op.add_column("payments", sa.Column("checkout_url", sa.Text(), nullable=True))
    op.add_column("payments", sa.Column("checkout_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("payments", sa.Column("checkout_creation_error", sa.String(255), nullable=True))
    op.add_column("payments", sa.Column("last_reconciled_at", sa.DateTime(timezone=True), nullable=True))

    bind = op.get_bind()
    if bind.execute(sa.text("SELECT EXISTS (SELECT 1 FROM payments)")).scalar():
        legacy_id = uuid.uuid4()
        bind.execute(
            sa.text("INSERT INTO merchants (id, name) VALUES (:id, :name)"),
            {"id": legacy_id, "name": LEGACY_MERCHANT_NAME},
        )
        bind.execute(sa.text("UPDATE payments SET merchant_id = :id"), {"id": legacy_id})

    op.alter_column("payments", "merchant_id", nullable=False)
    op.create_foreign_key(
        op.f("fk_payments_merchant_id_merchants"),
        "payments",
        "merchants",
        ["merchant_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint("uq_payments_merchant_idempotency_key", "payments", ["merchant_id", "idempotency_key"])
    op.create_index("ix_payments_merchant_created", "payments", ["merchant_id", sa.text("created_at DESC")])
    op.create_index(
        "ix_payments_pending_expiry",
        "payments",
        ["checkout_expires_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index("ix_payments_pending_expiry", table_name="payments")
    op.drop_index("ix_payments_merchant_created", table_name="payments")
    op.drop_constraint("uq_payments_merchant_idempotency_key", "payments", type_="unique")
    op.drop_constraint(op.f("fk_payments_merchant_id_merchants"), "payments", type_="foreignkey")
    for column in (
        "last_reconciled_at",
        "checkout_creation_error",
        "checkout_expires_at",
        "checkout_url",
        "request_fingerprint",
        "idempotency_key",
        "merchant_id",
    ):
        op.drop_column("payments", column)
    op.drop_index("ix_merchant_api_keys_prefix", table_name="merchant_api_keys")
    op.drop_index("ix_merchant_api_keys_merchant_id", table_name="merchant_api_keys")
    op.drop_table("merchant_api_keys")
    op.drop_table("merchants")
