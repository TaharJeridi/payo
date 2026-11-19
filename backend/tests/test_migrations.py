import os
import uuid
from collections.abc import Iterator

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from alembic import command
from app.db import models  # noqa: F401
from app.db.base import Base
from app.db.session import get_engine
from tests.conftest import alembic_config


def test_0002_assigns_existing_payments_to_a_legacy_merchant():
    """Simulates the real database that already holds payments from before merchants existed."""
    cfg = alembic_config()
    get_engine().dispose()
    command.downgrade(cfg, "0001_payment_engine")
    try:
        payment_id = uuid.uuid4()
        with get_engine().begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO payments (id, product_id, quantity, unit_amount, amount, currency, status) "
                    "VALUES (:id, 'payflow-pro', 1, 2990, 2990, 'eur', 'paid')"
                ),
                {"id": payment_id},
            )

        command.upgrade(cfg, "head")

        with get_engine().connect() as conn:
            row = conn.execute(
                text(
                    "SELECT m.name, p.idempotency_key FROM payments p JOIN merchants m ON m.id = p.merchant_id "
                    "WHERE p.id = :id"
                ),
                {"id": payment_id},
            ).one()
            nullable = conn.execute(
                text(
                    "SELECT is_nullable FROM information_schema.columns "
                    "WHERE table_name = 'payments' AND column_name = 'merchant_id'"
                )
            ).scalar_one()
        assert row.name == "Legacy (pre-0002)"
        assert row.idempotency_key is None
        assert nullable == "NO"
    finally:
        get_engine().dispose()
        command.upgrade(cfg, "head")


def test_0002_on_an_empty_database_creates_no_legacy_merchant():
    cfg = alembic_config()
    get_engine().dispose()
    command.downgrade(cfg, "0001_payment_engine")
    command.upgrade(cfg, "head")
    with get_engine().connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM merchants")).scalar_one() == 0


@pytest.fixture
def clean_database_url() -> Iterator[str]:
    base_url = make_url(os.environ["DATABASE_URL"])
    name = f"payflow_migrate_{uuid.uuid4().hex[:8]}"
    admin = create_engine(base_url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        yield base_url.set(database=name).render_as_string(hide_password=False)
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def _config_for(url: str):
    cfg = alembic_config()
    cfg.attributes["database_url"] = url
    return cfg


def test_clean_database_migrates_to_head_matching_models_and_back(clean_database_url):
    cfg = _config_for(clean_database_url)
    engine = create_engine(clean_database_url)
    try:
        command.upgrade(cfg, "head")
        with engine.connect() as conn:
            context = MigrationContext.configure(conn, opts={"compare_type": True})
            assert compare_metadata(context, Base.metadata) == []
            assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == ("0004_rag_sources")
        command.downgrade(cfg, "base")
        with engine.connect() as conn:
            tables = conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")).scalars().all()
            assert tables == ["alembic_version"]
            assert conn.execute(text("SELECT count(*) FROM pg_type WHERE typname = 'payment_status'")).scalar_one() == 0
        command.upgrade(cfg, "head")
    finally:
        engine.dispose()


def test_0003_splits_pending_by_what_is_known_and_backfills_history(clean_database_url):
    cfg = _config_for(clean_database_url)
    engine = create_engine(clean_database_url)
    rows = {
        "pending_with_session": ("pending", "cs_test_a", None),
        "pending_without_session": ("pending", None, None),
        "expired": ("failed", "cs_test_b", "checkout_session_expired"),
        "async_failure": ("failed", "cs_test_c", "async_payment_failed"),
        "paid": ("paid", "cs_test_d", None),
    }
    ids = {key: uuid.uuid4() for key in rows}
    try:
        command.upgrade(cfg, "0002_merchants_idempotency")
        with engine.begin() as conn:
            merchant_id = uuid.uuid4()
            conn.execute(text("INSERT INTO merchants (id, name) VALUES (:id, 'm')"), {"id": merchant_id})
            for key, (status, session_id, reason) in rows.items():
                conn.execute(
                    text(
                        "INSERT INTO payments (id, merchant_id, product_id, quantity, unit_amount, amount, currency,"
                        " status, stripe_checkout_session_id, failure_reason)"
                        " VALUES (:id, :m, 'payflow-pro', 1, 2990, 2990, 'eur', :status, :sid, :reason)"
                    ),
                    {"id": ids[key], "m": merchant_id, "status": status, "sid": session_id, "reason": reason},
                )

        command.upgrade(cfg, "head")
        with engine.connect() as conn:
            result = {
                row.id: row
                for row in conn.execute(text("SELECT id, status::text AS status, next_reconcile_at FROM payments"))
            }
            assert result[ids["pending_with_session"]].status == "open"
            assert result[ids["pending_without_session"]].status == "creation_unknown"
            assert result[ids["expired"]].status == "expired"
            assert result[ids["async_failure"]].status == "failed"
            assert result[ids["paid"]].status == "paid"
            assert result[ids["pending_without_session"]].next_reconcile_at is not None
            assert result[ids["paid"]].next_reconcile_at is None
            assert conn.execute(text("SELECT count(*) FROM payment_events")).scalar_one() == len(rows)

        command.downgrade(cfg, "0002_merchants_idempotency")
        with engine.connect() as conn:
            statuses = dict(conn.execute(text("SELECT id, status::text FROM payments")).all())
            assert statuses[ids["pending_without_session"]] == "pending"
            assert statuses[ids["expired"]] == "failed"
            assert statuses[ids["paid"]] == "paid"
    finally:
        engine.dispose()
