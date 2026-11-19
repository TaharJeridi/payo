import hashlib
import hmac
import json
import os
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://payflow:payflow@localhost:5432/payflow_test"
)
os.environ["ENVIRONMENT"] = "test"
os.environ["STRIPE_SECRET_KEY"] = "sk_test_dummy_key_for_automated_tests"
os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_test_dummy_secret_for_automated_tests"
os.environ["DEMO_ENDPOINTS_ENABLED"] = "true"
os.environ["CHECKOUT_RATE_LIMIT_PER_MINUTE"] = "0"
os.environ["RAG_RATE_LIMIT_PER_MINUTE"] = "0"

import httpx
import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from alembic import command
from app.db.session import get_engine, get_session_factory
from app.dependencies import get_payment_gateway
from app.main import app
from app.merchants.service import MerchantService
from tests.fakes import FakeStripeGateway

BACKEND_DIR = Path(__file__).resolve().parents[1]
WEBHOOK_SECRET = os.environ["STRIPE_WEBHOOK_SECRET"]


def alembic_config() -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.attributes["database_url"] = os.environ["DATABASE_URL"]
    cfg.attributes["configure_logger"] = False
    return cfg


@pytest.fixture(scope="session", autouse=True)
def _migrated_database() -> Iterator[None]:
    cfg = alembic_config()
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    yield
    get_engine().dispose()


@pytest.fixture(autouse=True)
def _clean_tables() -> Iterator[None]:
    with get_engine().begin() as conn:
        conn.execute(text("TRUNCATE payments, stripe_events, merchant_api_keys, merchants, rag_sources CASCADE"))
    yield


@dataclass(frozen=True)
class MerchantCreds:
    id: uuid.UUID
    key_id: uuid.UUID
    api_key: str

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}


def make_merchant(name: str) -> MerchantCreds:
    with get_session_factory()() as session, session.begin():
        service = MerchantService(session)
        merchant = service.create_merchant(name)
        issued = service.issue_key(merchant.id, label="tests")
        return MerchantCreds(id=merchant.id, key_id=issued.key_id, api_key=issued.plaintext)


@pytest.fixture
def merchant_a() -> MerchantCreds:
    return make_merchant("Merchant A")


@pytest.fixture
def merchant_b() -> MerchantCreds:
    return make_merchant("Merchant B")


@pytest.fixture
def gateway() -> FakeStripeGateway:
    return FakeStripeGateway()


@pytest.fixture
def anon_client(gateway: FakeStripeGateway) -> Iterator[TestClient]:
    app.dependency_overrides[get_payment_gateway] = lambda: gateway
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def client(anon_client: TestClient, merchant_a: MerchantCreds) -> TestClient:
    """Authenticated as merchant A by default; pass explicit headers to act as someone else."""
    anon_client.headers.update(merchant_a.headers)
    return anon_client


@pytest.fixture
def db() -> Iterator[Session]:
    with get_session_factory()() as session:
        yield session


def post_checkout(
    client: TestClient,
    body: dict[str, Any] | None = None,
    *,
    key: str | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    request_headers = {"Idempotency-Key": key or str(uuid.uuid4()), **(headers or {})}
    return client.post("/payments/checkout", json=body or {"product_id": "payflow-pro"}, headers=request_headers)


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
def send_event(anon_client: TestClient) -> Callable[..., Any]:
    def _send(event: dict[str, Any]) -> Any:
        payload = json.dumps(event, separators=(",", ":")).encode()
        return anon_client.post(
            "/webhooks/stripe",
            content=payload,
            headers={"Stripe-Signature": sign_payload(payload), "Content-Type": "application/json"},
        )

    return _send


@pytest.fixture
def create_checkout(client: TestClient) -> Callable[..., dict[str, Any]]:
    def _create(product_id: str = "payflow-pro", quantity: int = 1) -> dict[str, Any]:
        response = post_checkout(client, {"product_id": product_id, "quantity": quantity})
        assert response.status_code == 201, response.text
        return response.json()

    return _create
