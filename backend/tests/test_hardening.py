import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, update

from app.core.config import get_settings
from app.core.errors import OperationInProgress, PaymentOutcomeUnknown
from app.db.session import get_session_factory
from app.payments.models import Payment
from app.payments.repository import PaymentRepository
from app.payments.service import CheckoutService
from app.payments.state import PaymentStatus
from tests.conftest import build_event, post_checkout


def _count(db) -> int:
    db.expire_all()
    return db.execute(select(func.count()).select_from(Payment)).scalar_one()


def _payment(db, payment_id) -> Payment:
    db.expire_all()
    return db.get(Payment, uuid.UUID(str(payment_id)))


def _chain(db, payment_id) -> list[tuple[str | None, str]]:
    db.expire_all()
    return [
        (e.from_status.value if e.from_status else None, e.to_status.value)
        for e in PaymentRepository(db).history(uuid.UUID(str(payment_id)))
    ]


def _service_checkout(merchant_id, gateway, key, *, clock=None, settings=None):
    with get_session_factory()() as session:
        service = CheckoutService(session, gateway, settings or get_settings(), clock or (lambda: datetime.now(UTC)))
        return service.create_checkout(
            merchant_id=merchant_id, idempotency_key=key, product_id="payflow-pro", quantity=1, customer_email=None
        )


def test_each_lifecycle_state_is_observable(client, gateway, send_event, db, merchant_a):
    gateway.create_hold = threading.Event()
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(_service_checkout, merchant_a.id, gateway, "state-creating")
        assert gateway.create_started.wait(timeout=5)
        creating = db.execute(select(Payment).where(Payment.idempotency_key == "state-creating")).scalar_one()
        assert creating.status is PaymentStatus.CREATING
        gateway.create_hold.set()
        assert pending.result(timeout=10).payment.status is PaymentStatus.OPEN
    gateway.create_hold = None

    gateway.create_behaviours = ["lost_response"]
    unknown_id = post_checkout(client).json()["error"]["details"]["payment_id"]
    assert _payment(db, unknown_id).status is PaymentStatus.CREATION_UNKNOWN

    paid = post_checkout(client).json()
    gateway.mark_paid(paid["checkout_session_id"])
    send_event(build_event("checkout.session.completed", {"id": paid["checkout_session_id"]}))
    assert _payment(db, paid["payment_id"]).status is PaymentStatus.PAID

    gateway.create_behaviours = ["reject"]
    post_checkout(client)
    failed = db.execute(
        select(Payment).where(Payment.failure_reason == "checkout_session_creation_rejected")
    ).scalar_one()
    assert failed.status is PaymentStatus.FAILED

    expired = post_checkout(client).json()
    gateway.update_remote(expired["checkout_session_id"], status="expired")
    send_event(build_event("checkout.session.expired", {"id": expired["checkout_session_id"]}))
    assert _payment(db, expired["payment_id"]).status is PaymentStatus.EXPIRED


def test_concurrent_request_with_same_key_gets_409_in_progress_and_one_stripe_call(client, gateway, merchant_a, db):
    gateway.create_hold = threading.Event()
    with ThreadPoolExecutor(max_workers=1) as pool:
        leader = pool.submit(_service_checkout, merchant_a.id, gateway, "same-op")
        assert gateway.create_started.wait(timeout=5)

        follower = post_checkout(client, key="same-op")

        assert follower.status_code == 409
        assert follower.json()["error"]["code"] == "idempotency_operation_in_progress"
        assert follower.headers["Retry-After"] == "2"
        gateway.create_hold.set()
        original = leader.result(timeout=10)

    replay = post_checkout(client, key="same-op")
    assert replay.status_code == 200
    assert replay.json()["checkout_session_id"] == original.payment.stripe_checkout_session_id
    assert len(gateway.create_calls) == 1
    assert len(gateway.sessions) == 1
    assert _count(db) == 1


def test_timeout_after_stripe_created_the_session_yields_one_session_and_a_coherent_history(client, gateway, db):
    gateway.create_behaviours = ["lost_response"]

    lost = post_checkout(client, key="timeout-op")
    retry = post_checkout(client, key="timeout-op")

    payment_id = lost.json()["error"]["details"]["payment_id"]
    assert lost.status_code == 503
    assert retry.status_code == 200
    assert retry.json()["payment_id"] == payment_id
    assert retry.json()["status"] == "open"
    assert len(gateway.sessions) == 1
    assert _chain(db, payment_id) == [
        (None, "creating"),
        ("creating", "creation_unknown"),
        ("creation_unknown", "creating"),
        ("creating", "open"),
    ]


def test_network_error_is_never_recorded_as_a_failed_payment(client, gateway, db):
    for behaviour in ("unavailable", "lost_response"):
        gateway.create_behaviours = [behaviour]
        assert post_checkout(client).status_code == 503
    db.expire_all()
    statuses = set(db.execute(select(Payment.status)).scalars())
    assert statuses == {PaymentStatus.CREATION_UNKNOWN}


def test_retry_minutes_later_still_creates_the_session_with_default_ttl(merchant_a, gateway, db):
    gateway.enforce_min_expiry = True
    gateway.create_behaviours = ["unavailable"]
    with pytest.raises(PaymentOutcomeUnknown):
        _service_checkout(merchant_a.id, gateway, "late-retry")

    gateway.clock_offset = timedelta(minutes=2)
    two_minutes_later = datetime.now(UTC) + gateway.clock_offset
    result = _service_checkout(merchant_a.id, gateway, "late-retry", clock=lambda: two_minutes_later)

    assert result.payment.status is PaymentStatus.OPEN
    assert len(gateway.sessions) == 1


def test_retry_rejected_by_stripe_stays_unknown_instead_of_failing(merchant_a, gateway, db):
    settings = get_settings().model_copy(update={"checkout_session_ttl_seconds": 31 * 60})
    gateway.enforce_min_expiry = True
    gateway.create_behaviours = ["unavailable"]
    with pytest.raises(PaymentOutcomeUnknown):
        _service_checkout(merchant_a.id, gateway, "short-ttl", settings=settings)

    gateway.clock_offset = timedelta(minutes=2)
    two_minutes_later = datetime.now(UTC) + gateway.clock_offset
    with pytest.raises(PaymentOutcomeUnknown) as exc_info:
        _service_checkout(merchant_a.id, gateway, "short-ttl", clock=lambda: two_minutes_later, settings=settings)

    assert exc_info.value.details["status"] == "creation_unknown"
    payment = db.execute(select(Payment)).scalar_one()
    assert payment.status is PaymentStatus.CREATION_UNKNOWN


def test_retry_after_the_session_window_closed_does_not_call_stripe(client, gateway, db):
    gateway.create_behaviours = ["unavailable"]
    payment_id = post_checkout(client, key="closed-window").json()["error"]["details"]["payment_id"]
    db.execute(
        update(Payment)
        .where(Payment.id == uuid.UUID(payment_id))
        .values(checkout_expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    db.commit()
    calls = len(gateway.create_calls)

    retry = post_checkout(client, key="closed-window")

    assert retry.status_code == 503
    assert retry.json()["error"]["code"] == "payment_outcome_unknown"
    assert len(gateway.create_calls) == calls


def test_abandoned_creation_can_be_taken_over_after_its_lease(merchant_a, gateway, db):
    gateway.create_hold = threading.Event()
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(_service_checkout, merchant_a.id, gateway, "abandoned")
        assert gateway.create_started.wait(timeout=5)
        with pytest.raises(OperationInProgress):
            _service_checkout(merchant_a.id, gateway, "abandoned")
        lease = timedelta(seconds=get_settings().checkout_creation_lease_seconds + 1)
        later = datetime.now(UTC) + lease
        gateway.create_hold.set()
    db.execute(update(Payment).values(status=PaymentStatus.CREATING, stripe_checkout_session_id=None))
    db.commit()

    result = _service_checkout(merchant_a.id, gateway, "abandoned", clock=lambda: later)

    assert result.payment.status is PaymentStatus.OPEN
    assert result.payment.creation_attempts == 2
    assert len(gateway.sessions) == 1
