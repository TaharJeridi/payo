from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.core.rate_limit import SlidingWindowRateLimiter
from app.dependencies import get_checkout_rate_limiter, get_payment_gateway
from app.integrations.stripe_gateway import StripeGateway
from app.main import app
from app.payments.models import Payment
from app.payments.state import PaymentStatus
from tests.conftest import build_event


def test_products_are_served_from_backend_catalog(client):
    response = client.get("/payments/products")

    assert response.status_code == 200
    products = {p["id"]: p for p in response.json()}
    assert products["payflow-pro"]["unit_amount"] == 2990
    assert products["payflow-pro"]["currency"] == "eur"


def test_checkout_creates_pending_payment_with_backend_amount(client, gateway, db):
    response = client.post("/payments/checkout", json={"product_id": "payflow-pro", "quantity": 2})

    assert response.status_code == 201
    body = response.json()
    assert body["amount"] == 5980
    assert body["currency"] == "eur"
    assert body["status"] == "pending"
    assert body["checkout_url"].startswith("https://checkout.stripe.com/")

    payment = db.get(Payment, body["payment_id"])
    assert payment.status is PaymentStatus.PENDING
    assert payment.amount == 5980
    assert payment.unit_amount == 2990
    assert payment.stripe_checkout_session_id == body["checkout_session_id"]

    call = gateway.create_calls[0]
    assert call["product"].unit_amount == 2990
    assert call["quantity"] == 2
    assert str(call["payment_id"]) == body["payment_id"]


def test_checkout_rejects_client_supplied_amount_and_currency(client, gateway, db):
    response = client.post(
        "/payments/checkout",
        json={"product_id": "payflow-pro", "quantity": 1, "amount": 1, "currency": "usd"},
    )

    assert response.status_code == 422
    assert gateway.create_calls == []
    assert db.execute(select(Payment)).first() is None


def test_checkout_unknown_product_returns_404(client, gateway):
    response = client.post("/payments/checkout", json={"product_id": "free-money"})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "product_not_found"
    assert gateway.create_calls == []


def test_checkout_quantity_bounds(client):
    assert client.post("/payments/checkout", json={"product_id": "payflow-pro", "quantity": 0}).status_code == 422
    assert client.post("/payments/checkout", json={"product_id": "payflow-pro", "quantity": -3}).status_code == 422
    too_many = client.post("/payments/checkout", json={"product_id": "payflow-pro", "quantity": 11})
    assert too_many.status_code == 422
    assert too_many.json()["error"]["code"] == "invalid_quantity"


def test_checkout_provider_failure_marks_payment_failed(client, gateway, db):
    gateway.fail_create = True

    response = client.post("/payments/checkout", json={"product_id": "payflow-starter"})

    assert response.status_code == 502
    payment = db.execute(select(Payment)).scalar_one()
    assert payment.status is PaymentStatus.FAILED
    assert payment.failure_reason == "checkout_session_creation_failed"


def test_list_payments_with_filter_and_pagination(client, create_checkout, gateway, send_event):
    first = create_checkout("payflow-starter")
    create_checkout("payflow-pro")
    create_checkout("payflow-report")
    gateway.mark_paid(first["checkout_session_id"])
    send_event(
        build_event("checkout.session.completed", {"id": first["checkout_session_id"], "payment_status": "paid"})
    )

    all_payments = client.get("/payments").json()
    assert all_payments["total"] == 3
    assert len(all_payments["items"]) == 3

    paid = client.get("/payments", params={"status": "paid"}).json()
    assert paid["total"] == 1
    assert paid["items"][0]["id"] == first["payment_id"]

    page = client.get("/payments", params={"limit": 2, "offset": 2}).json()
    assert len(page["items"]) == 1
    assert "customer_email" not in page["items"][0]


def test_list_payments_hidden_when_demo_disabled(client):
    app.dependency_overrides[get_settings] = lambda: Settings(demo_endpoints_enabled=False)
    try:
        assert client.get("/payments").status_code == 404
    finally:
        app.dependency_overrides.pop(get_settings)


def test_amount_mismatch_from_stripe_expires_the_session(client, gateway, db):
    gateway.amount_total_override = 1

    response = client.post("/payments/checkout", json={"product_id": "payflow-pro"})

    assert response.status_code == 502
    payment = db.execute(select(Payment)).scalar_one()
    assert payment.status is PaymentStatus.FAILED
    assert payment.failure_reason == "checkout_amount_mismatch"
    assert gateway.expire_calls == [payment.stripe_checkout_session_id]
    assert gateway.sessions[payment.stripe_checkout_session_id].status == "expired"


def test_missing_checkout_url_expires_the_session(client, gateway, db):
    gateway.url_override = ""

    response = client.post("/payments/checkout", json={"product_id": "payflow-pro"})

    assert response.status_code == 502
    payment = db.execute(select(Payment)).scalar_one()
    assert payment.failure_reason == "checkout_url_missing"
    assert gateway.expire_calls == [payment.stripe_checkout_session_id]


def test_expire_failure_still_returns_502_and_marks_failed(client, gateway, db):
    gateway.amount_total_override = 1
    gateway.fail_expire = True

    response = client.post("/payments/checkout", json={"product_id": "payflow-pro"})

    assert response.status_code == 502
    assert db.execute(select(Payment)).scalar_one().status is PaymentStatus.FAILED


def test_missing_stripe_key_returns_503(client):
    app.dependency_overrides[get_payment_gateway] = lambda: StripeGateway(Settings(stripe_secret_key=""))

    response = client.post("/payments/checkout", json={"product_id": "payflow-pro"})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "payment_provider_not_configured"


def test_checkout_is_rate_limited_per_client(client, gateway):
    limiter = SlidingWindowRateLimiter(limit=2)
    app.dependency_overrides[get_checkout_rate_limiter] = lambda: limiter

    statuses = [client.post("/payments/checkout", json={"product_id": "payflow-pro"}).status_code for _ in range(3)]

    assert statuses == [201, 201, 429]
    assert len(gateway.create_calls) == 2


def test_demo_listing_is_disabled_by_default():
    assert Settings.model_fields["demo_endpoints_enabled"].default is False
