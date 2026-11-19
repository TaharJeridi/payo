import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.errors import IdempotencyKeyReused, OperationInProgress
from app.db.session import get_session_factory
from app.payments.models import Payment
from app.payments.service import CheckoutService, request_fingerprint
from app.payments.state import PaymentStatus
from tests.conftest import build_event, post_checkout


def _count(db) -> int:
    db.expire_all()
    return db.execute(select(func.count()).select_from(Payment)).scalar_one()


# --- protocol -------------------------------------------------------------------------------


def test_same_key_same_params_returns_the_same_operation(client, gateway, db):
    first = post_checkout(client, key="order-1")
    second = post_checkout(client, key="order-1")

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.headers["Idempotent-Replayed"] == "true"
    assert second.json() == first.json()
    assert len(gateway.create_calls) == 1  # the replay does not even call Stripe
    assert _count(db) == 1


def test_same_key_different_params_is_rejected_with_409(client, gateway, db):
    first = post_checkout(client, {"product_id": "payflow-pro"}, key="order-1")
    conflict = post_checkout(client, {"product_id": "payflow-pro", "quantity": 2}, key="order-1")

    assert conflict.status_code == 409
    error = conflict.json()["error"]
    assert error["code"] == "idempotency_key_reused"
    assert error["details"]["payment_id"] == first.json()["payment_id"]
    assert len(gateway.create_calls) == 1
    assert _count(db) == 1


def test_different_keys_with_identical_params_are_distinct_purchases(client, db):
    a = post_checkout(client, key="order-1")
    b = post_checkout(client, key="order-2")

    assert a.status_code == b.status_code == 201
    assert a.json()["payment_id"] != b.json()["payment_id"]
    assert a.json()["checkout_session_id"] != b.json()["checkout_session_id"]
    assert _count(db) == 2


@pytest.mark.parametrize("key", [None, "", "   ", "has space", "x" * 256])
def test_missing_or_malformed_key_is_rejected(client, gateway, key):
    headers = {} if key is None else {"Idempotency-Key": key}
    response = client.post("/payments/checkout", json={"product_id": "payflow-pro"}, headers=headers)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_idempotency_key"
    assert gateway.create_calls == []


def test_keys_are_scoped_per_merchant(client, merchant_b, db):
    a = post_checkout(client, key="shared-key")
    b = post_checkout(client, key="shared-key", headers=merchant_b.headers)

    assert a.status_code == b.status_code == 201
    assert a.json()["payment_id"] != b.json()["payment_id"]
    assert _count(db) == 2


def test_replay_after_payment_returns_current_state_without_url(client, gateway, send_event):
    first = post_checkout(client, key="order-1").json()
    gateway.mark_paid(first["checkout_session_id"])
    send_event(build_event("checkout.session.completed", {"id": first["checkout_session_id"]}))

    replay = post_checkout(client, key="order-1")

    assert replay.status_code == 200
    assert replay.json()["status"] == "paid"
    assert replay.json()["checkout_url"] is None


def test_fingerprint_is_canonical():
    assert request_fingerprint("p", 1, "A@B.it") == request_fingerprint("p", 1, "a@b.it")
    assert request_fingerprint("p", 1, None) != request_fingerprint("p", 2, None)


# --- unknown outcomes ------------------------------------------------------------------------


def test_lost_stripe_response_is_not_a_failure_and_retry_recovers_the_same_session(client, gateway, db):
    gateway.create_behaviours = ["lost_response"]

    lost = post_checkout(client, key="order-1")

    assert lost.status_code == 503
    assert lost.headers["Retry-After"] == "5"
    error = lost.json()["error"]
    assert error["code"] == "payment_outcome_unknown"
    payment = db.get(Payment, uuid.UUID(error["details"]["payment_id"]))
    assert payment.status is PaymentStatus.CREATION_UNKNOWN  # NOT failed
    assert error["details"]["status"] == "creation_unknown"
    assert payment.checkout_creation_error == "stripe_outcome_unknown"
    assert payment.stripe_checkout_session_id is None
    assert len(gateway.sessions) == 1  # ...but Stripe did create the session

    retry = post_checkout(client, key="order-1")

    assert retry.status_code == 200
    body = retry.json()
    assert body["payment_id"] == str(payment.id)
    assert body["checkout_session_id"] == next(iter(gateway.sessions))
    assert body["checkout_url"].startswith("https://checkout.stripe.com/")
    assert len(gateway.sessions) == 1  # no duplicate session at Stripe
    # Identical parameters on both attempts, as Stripe's idempotency requires.
    assert gateway.create_calls[0] == gateway.create_calls[1]
    db.refresh(payment)
    assert payment.checkout_creation_error is None


def test_request_that_never_reached_stripe_is_created_on_retry(client, gateway, db):
    gateway.create_behaviours = ["unavailable"]

    assert post_checkout(client, key="order-1").status_code == 503
    assert gateway.sessions == {}
    retry = post_checkout(client, key="order-1")

    assert retry.status_code == 200
    assert retry.json()["checkout_session_id"] in gateway.sessions
    assert _count(db) == 1


def test_definitive_rejection_on_a_retry_does_not_close_an_uncertain_payment(client, gateway, db):
    gateway.create_behaviours = ["lost_response", "reject"]

    assert post_checkout(client, key="order-1").status_code == 503
    retry = post_checkout(client, key="order-1")

    assert retry.status_code == 503
    assert retry.json()["error"]["code"] == "payment_outcome_unknown"
    payment = db.execute(select(Payment)).scalar_one()
    assert payment.status is PaymentStatus.CREATION_UNKNOWN
    assert payment.checkout_creation_error == "stripe_rejected_retry"


# --- concurrency on real PostgreSQL ----------------------------------------------------------


def _run_concurrently(merchant_id, calls):
    """Run CheckoutService.create_checkout calls at the same instant, one DB session each."""
    settings = get_settings()
    start = threading.Barrier(len(calls))
    factory = get_session_factory()

    def run(args):
        gateway, key, params = args
        start.wait(timeout=10)
        with factory() as session:
            try:
                result = CheckoutService(session, gateway, settings).create_checkout(
                    merchant_id=merchant_id, idempotency_key=key, **params
                )
                return ("ok", str(result.payment.id), result.payment.stripe_checkout_session_id, result.replayed)
            except IdempotencyKeyReused:
                return ("conflict", None, None, None)
            except OperationInProgress:
                return ("in_progress", None, None, None)

    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        return list(pool.map(run, calls))


PRO = {"product_id": "payflow-pro", "quantity": 1, "customer_email": None}


def test_concurrent_requests_with_the_same_key_create_exactly_one_payment(merchant_a, gateway, db):
    results = _run_concurrently(merchant_a.id, [(gateway, "same-key", PRO)] * 10)

    ok = [r for r in results if r[0] == "ok"]
    assert {r[0] for r in results} <= {"ok", "in_progress"}
    assert len({r[1] for r in ok}) == 1
    assert len({r[2] for r in ok}) == 1
    assert [r[3] for r in ok].count(False) == 1
    assert len(gateway.sessions) == 1
    assert len(gateway.create_calls) == 1
    assert _count(db) == 1


def test_concurrent_requests_with_different_keys_create_distinct_payments(merchant_a, gateway, db):
    results = _run_concurrently(merchant_a.id, [(gateway, f"key-{i}", PRO) for i in range(10)])

    assert {r[0] for r in results} == {"ok"}
    assert len({r[1] for r in results}) == 10
    assert _count(db) == 10


def test_concurrent_same_key_with_different_params_has_one_winner(merchant_a, gateway, db):
    other = {"product_id": "payflow-pro", "quantity": 3, "customer_email": None}
    calls = [(gateway, "same-key", PRO)] * 5 + [(gateway, "same-key", other)] * 5

    results = _run_concurrently(merchant_a.id, calls)

    assert _count(db) == 1
    winner_quantity = db.execute(select(Payment.quantity)).scalar_one()
    assert sum(1 for r in results if r[0] in ("ok", "in_progress")) == 5
    assert sum(1 for r in results if r[0] == "ok") >= 1
    assert sum(1 for r in results if r[0] == "conflict") == 5
    assert winner_quantity in (1, 3)
