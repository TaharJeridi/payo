from sqlalchemy import select

from app.core.config import Settings
from app.core.rate_limit import SlidingWindowRateLimiter
from app.dependencies import get_checkout_rate_limiter, get_payment_gateway
from app.integrations.stripe_gateway import StripeGateway
from app.main import app
from app.payments.models import Payment
from app.payments.state import PaymentStatus
from tests.conftest import build_event, post_checkout


def test_products_are_served_from_backend_catalog(anon_client):
    response = anon_client.get("/payments/products")

    assert response.status_code == 200
    products = {p["id"]: p for p in response.json()}
    assert products["payflow-pro"]["unit_amount"] == 2990
    assert products["payflow-pro"]["currency"] == "eur"


def test_checkout_creates_open_payment_with_backend_amount(client, gateway, db, merchant_a):
    response = post_checkout(client, {"product_id": "payflow-pro", "quantity": 2}, key="order-42")

    assert response.status_code == 201
    body = response.json()
    assert body["amount"] == 5980
    assert body["currency"] == "eur"
    assert body["status"] == "open"
    assert body["checkout_url"].startswith("https://checkout.stripe.com/")
    assert body["checkout_expires_at"] is not None

    payment = db.get(Payment, body["payment_id"])
    assert payment.status is PaymentStatus.OPEN
    assert payment.merchant_id == merchant_a.id
    assert payment.idempotency_key == "order-42"
    assert payment.amount == 5980
    assert payment.unit_amount == 2990
    assert payment.stripe_checkout_session_id == body["checkout_session_id"]

    call = gateway.create_calls[0]
    assert call.product.unit_amount == 2990
    assert call.quantity == 2
    assert call.merchant_id == merchant_a.id
    assert str(call.payment_id) == body["payment_id"]
    assert call.expires_at == payment.checkout_expires_at


def test_checkout_rejects_client_supplied_amount_and_currency(client, gateway, db):
    response = post_checkout(client, {"product_id": "payflow-pro", "quantity": 1, "amount": 1, "currency": "usd"})

    assert response.status_code == 422
    assert gateway.create_calls == []
    assert db.execute(select(Payment)).first() is None


def test_checkout_unknown_product_returns_404(client, gateway):
    response = post_checkout(client, {"product_id": "free-money"})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "product_not_found"
    assert gateway.create_calls == []


def test_checkout_quantity_bounds(client):
    assert post_checkout(client, {"product_id": "payflow-pro", "quantity": 0}).status_code == 422
    assert post_checkout(client, {"product_id": "payflow-pro", "quantity": -3}).status_code == 422
    too_many = post_checkout(client, {"product_id": "payflow-pro", "quantity": 11})
    assert too_many.status_code == 422
    assert too_many.json()["error"]["code"] == "invalid_quantity"


def test_definitive_stripe_rejection_marks_payment_failed(client, gateway, db):
    gateway.create_behaviours = ["reject"]

    response = post_checkout(client, {"product_id": "payflow-starter"})

    assert response.status_code == 502
    payment = db.execute(select(Payment)).scalar_one()
    assert payment.status is PaymentStatus.FAILED
    assert payment.failure_reason == "checkout_session_creation_rejected"


def test_list_payments_with_filter_and_pagination(client, create_checkout, gateway, send_event):
    first = create_checkout("payflow-starter")
    create_checkout("payflow-pro")
    create_checkout("payflow-report")
    gateway.mark_paid(first["checkout_session_id"])
    send_event(build_event("checkout.session.completed", {"id": first["checkout_session_id"]}))

    all_payments = client.get("/payments").json()
    assert all_payments["total"] == 3
    assert len(all_payments["items"]) == 3

    paid = client.get("/payments", params={"status": "paid"}).json()
    assert paid["total"] == 1
    assert paid["items"][0]["id"] == first["payment_id"]

    page = client.get("/payments", params={"limit": 2, "offset": 2}).json()
    assert len(page["items"]) == 1
    assert "customer_email" not in page["items"][0]
    assert "idempotency_key" not in page["items"][0]


def test_get_single_payment(client, create_checkout):
    checkout = create_checkout()

    response = client.get(f"/payments/{checkout['payment_id']}")

    assert response.status_code == 200
    assert response.json()["id"] == checkout["payment_id"]


def test_amount_mismatch_from_stripe_expires_the_session(client, gateway, db):
    gateway.amount_total_override = 1

    response = post_checkout(client)

    assert response.status_code == 502
    payment = db.execute(select(Payment)).scalar_one()
    assert payment.status is PaymentStatus.FAILED
    assert payment.failure_reason == "checkout_amount_mismatch"
    assert payment.checkout_url is None
    assert gateway.expire_calls == [payment.stripe_checkout_session_id]
    assert gateway.sessions[payment.stripe_checkout_session_id].status == "expired"


def test_missing_checkout_url_expires_the_session(client, gateway, db):
    gateway.url_override = ""

    response = post_checkout(client)

    assert response.status_code == 502
    payment = db.execute(select(Payment)).scalar_one()
    assert payment.failure_reason == "checkout_url_missing"
    assert gateway.expire_calls == [payment.stripe_checkout_session_id]


def test_expire_failure_still_returns_502_and_marks_failed(client, gateway, db):
    gateway.amount_total_override = 1
    gateway.fail_expire = True

    response = post_checkout(client)

    assert response.status_code == 502
    assert db.execute(select(Payment)).scalar_one().status is PaymentStatus.FAILED


def test_missing_stripe_key_returns_503(client):
    app.dependency_overrides[get_payment_gateway] = lambda: StripeGateway(Settings(stripe_secret_key=""))

    response = post_checkout(client)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "payment_provider_not_configured"


def test_checkout_is_rate_limited_per_merchant(client, gateway, merchant_b):
    limiter = SlidingWindowRateLimiter(limit=2)
    app.dependency_overrides[get_checkout_rate_limiter] = lambda: limiter

    statuses = [post_checkout(client).status_code for _ in range(3)]
    other_merchant = post_checkout(client, headers=merchant_b.headers)

    assert statuses == [201, 201, 429]
    assert other_merchant.status_code == 201
    assert len(gateway.create_calls) == 3


def test_payment_detail_exposes_its_history(client, create_checkout):
    checkout = create_checkout()

    detail = client.get(f"/payments/{checkout['payment_id']}").json()

    assert [(h["from_status"], h["to_status"], h["source"]) for h in detail["history"]] == [
        (None, "creating", "api"),
        ("creating", "open", "api"),
    ]


def test_stats_are_scoped_to_the_merchant_and_sum_only_paid(client, gateway, send_event, anon_client, merchant_b):
    paid = [post_checkout(client, {"product_id": "payflow-pro", "quantity": q}).json() for q in (1, 2)]
    post_checkout(client, {"product_id": "payflow-starter"})
    for checkout in paid:
        gateway.mark_paid(checkout["checkout_session_id"])
        send_event(build_event("checkout.session.completed", {"id": checkout["checkout_session_id"]}))
    other = post_checkout(anon_client, {"product_id": "payflow-report"}, headers=merchant_b.headers).json()
    gateway.mark_paid(other["checkout_session_id"])
    send_event(build_event("checkout.session.completed", {"id": other["checkout_session_id"]}))

    stats = client.get("/payments/stats").json()

    assert stats["total"] == 3
    assert stats["by_status"]["paid"] == 2
    assert stats["by_status"]["open"] == 1
    assert stats["by_status"]["expired"] == 0
    assert set(stats["by_status"]) == {"creating", "open", "creation_unknown", "paid", "failed", "expired"}
    assert stats["paid_totals"] == [{"currency": "eur", "amount": 2990 * 3}]


def test_stats_require_authentication(anon_client):
    assert anon_client.get("/payments/stats").status_code == 401
