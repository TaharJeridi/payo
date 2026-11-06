import hashlib
import hmac
import json
import os
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://payflow:payflow@localhost:5432/payflow_test"
)
os.environ["STRIPE_SECRET_KEY"] = "sk_test_dummy_key_for_automated_tests"
os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_test_dummy_secret_for_automated_tests"
os.environ["DEMO_ENDPOINTS_ENABLED"] = "true"
os.environ["CHECKOUT_RATE_LIMIT_PER_MINUTE"] = "0"

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from alembic import command
from app.db.session import get_engine, get_session_factory
from app.dependencies import get_payment_gateway
from app.main import app
from tests.fakes import FakeStripeGateway

BACKEND_DIR = Path(__file__).resolve().parents[1]
WEBHOOK_SECRET = os.environ["STRIPE_WEBHOOK_SECRET"]


def _alembic_config() -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.attributes["database_url"] = os.environ["DATABASE_URL"]
    cfg.attributes["configure_logger"] = False
    return cfg


@pytest.fixture(scope="session", autouse=True)
def _migrated_database() -> Iterator[None]:
    cfg = _alembic_config()
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    yield
    get_engine().dispose()


@pytest.fixture(autouse=True)
def _clean_tables() -> Iterator[None]:
    with get_engine().begin() as conn:
        conn.execute(text("TRUNCATE payments, stripe_events"))
    yield


@pytest.fixture
def gateway() -> FakeStripeGateway:
    return FakeStripeGateway()


@pytest.fixture
def client(gateway: FakeStripeGateway) -> Iterator[TestClient]:
    app.dependency_overrides[get_payment_gateway] = lambda: gateway
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def db() -> Iterator[Session]:
    with get_session_factory()() as session:
        yield session


def sign_payload(payload: bytes, secret: str = WEBHOOK_SECRET, timestamp: int | None = None) -> str:
    ts = int(time.time()) if timestamp is None else timestamp
    signed = f"{ts}.".encode() + payload
    signature = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return f"t={ts},v1={signature}"


def build_event(
    event_type: str,
    session: dict[str, Any],
    *,
    event_id: str | None = None,
    created: int | None = None,
    livemode: bool = False,
) -> dict[str, Any]:
    return {
        "id": event_id or f"evt_test_{uuid.uuid4().hex}",
        "object": "event",
        "type": event_type,
        "created": created or int(time.time()),
        "livemode": livemode,
        "api_version": "2024-11-20.acacia",
        "data": {"object": {"object": "checkout.session", **session}},
    }


@pytest.fixture
def send_event(client: TestClient) -> Callable[..., Any]:
    def _send(event: dict[str, Any]) -> Any:
        payload = json.dumps(event, separators=(",", ":")).encode()
        return client.post(
            "/webhooks/stripe",
            content=payload,
            headers={"Stripe-Signature": sign_payload(payload), "Content-Type": "application/json"},
        )

    return _send


@pytest.fixture
def create_checkout(client: TestClient) -> Callable[..., dict[str, Any]]:
    def _create(product_id: str = "payflow-pro", quantity: int = 1) -> dict[str, Any]:
        response = client.post("/payments/checkout", json={"product_id": product_id, "quantity": quantity})
        assert response.status_code == 201, response.text
        return response.json()

    return _create
