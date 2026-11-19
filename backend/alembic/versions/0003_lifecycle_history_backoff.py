"""explicit checkout lifecycle states, payment history, reconciliation scheduling with backoff

pending is split by what is actually known:
- pending with a Stripe session id    -> open
- pending without a Stripe session id -> creation_unknown (the reconciler resolves it)
failed because the session expired    -> expired

Revision ID: 0003_lifecycle_history_backoff
Revises: 0002_merchants_idempotency
Create Date: 2026-11-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003_lifecycle_history_backoff"
down_revision: str | None = "0002_merchants_idempotency"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW_VALUES = ("creating", "open", "creation_unknown", "paid", "failed", "expired")
OLD_VALUES = ("pending", "paid", "failed")


def _enum_sql(name: str, values: Sequence[str]) -> str:
    return f"CREATE TYPE {name} AS ENUM ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    op.drop_index("ix_payments_pending_expiry", table_name="payments")
    op.drop_index("ix_payments_status", table_name="payments")
    op.execute(_enum_sql("payment_status_v3", NEW_VALUES))
    op.execute(
        """
        ALTER TABLE payments ALTER COLUMN status TYPE payment_status_v3 USING (
            CASE
                WHEN status = 'pending' AND stripe_checkout_session_id IS NOT NULL THEN 'open'
                WHEN status = 'pending' THEN 'creation_unknown'
                WHEN status = 'failed' AND failure_reason = 'checkout_session_expired' THEN 'expired'
                ELSE status::text
            END
        )::payment_status_v3
        """
    )
    op.execute("DROP TYPE payment_status")
    op.execute("ALTER TYPE payment_status_v3 RENAME TO payment_status")
    op.create_index("ix_payments_status", "payments", ["status"])

    op.add_column("payments", sa.Column("creation_attempts", sa.Integer(), server_default="0", nullable=False))
    op.add_column("payments", sa.Column("creation_attempt_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("payments", sa.Column("reconcile_attempts", sa.Integer(), server_default="0", nullable=False))
    op.add_column("payments", sa.Column("next_reconcile_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("payments", sa.Column("last_reconcile_error", sa.String(255), nullable=True))
    op.execute(
        """
        UPDATE payments SET
            creation_attempts = 1,
            creation_attempt_at = created_at,
            next_reconcile_at = CASE WHEN status IN ('open', 'creation_unknown') THEN now() END
        """
    )
    op.create_index(
        "ix_payments_next_reconcile_at",
        "payments",
        ["next_reconcile_at"],
        postgresql_where=sa.text("next_reconcile_at IS NOT NULL"),
    )

    status = postgresql.ENUM(*NEW_VALUES, name="payment_status", create_type=False)
    op.create_table(
        "payment_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("payment_id", sa.Uuid(), nullable=False),
        sa.Column("from_status", status, nullable=True),
        sa.Column("to_status", status, nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("reason", sa.String(255), nullable=True),
        sa.Column("stripe_event_id", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["payment_id"], ["payments.id"], name=op.f("fk_payment_events_payment_id_payments"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payment_events")),
    )
    op.create_index("ix_payment_events_payment_id_id", "payment_events", ["payment_id", "id"])
    op.execute(
        """
        INSERT INTO payment_events (payment_id, from_status, to_status, source, reason)
        SELECT id, NULL, status, 'migration', 'backfilled by 0003'
        FROM payments
        """
    )


def downgrade() -> None:
    op.drop_index("ix_payment_events_payment_id_id", table_name="payment_events")
    op.drop_table("payment_events")
    op.drop_index("ix_payments_next_reconcile_at", table_name="payments")
    for column in (
        "last_reconcile_error",
        "next_reconcile_at",
        "reconcile_attempts",
        "creation_attempt_at",
        "creation_attempts",
    ):
        op.drop_column("payments", column)

    op.drop_index("ix_payments_status", table_name="payments")
    op.execute(_enum_sql("payment_status_v2", OLD_VALUES))
    op.execute(
        """
        ALTER TABLE payments ALTER COLUMN status TYPE payment_status_v2 USING (
            CASE
                WHEN status IN ('creating', 'open', 'creation_unknown') THEN 'pending'
                WHEN status = 'expired' THEN 'failed'
                ELSE status::text
            END
        )::payment_status_v2
        """
    )
    op.execute("DROP TYPE payment_status")
    op.execute("ALTER TYPE payment_status_v2 RENAME TO payment_status")
    op.create_index("ix_payments_status", "payments", ["status"])
    op.create_index(
        "ix_payments_pending_expiry",
        "payments",
        ["checkout_expires_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )
