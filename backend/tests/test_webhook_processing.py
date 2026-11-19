import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import func, select

from app.db.session import get_session_factory
from app.payments.models import Payment
from app.payments.state import PaymentStatus
from app.webhooks.models import StripeEvent
from app.webhooks.service import Outcome, WebhookProcessor
from tests.conftest import build_event, post_checkout

COMPLETED = "checkout.session.completed"
ASYNC_OK = "checkout.session.async_payment_succeeded"
ASYNC_KO = "checkout.session.async_payment_failed"
EXPIRED = "checkout.session.expired"


def _payment(db, payment_id) -> Payment:
    db.expire_all()
    return db.get(Payment, uuid.UUID(payment_id))


def _event_count(db) -> int:
    return db.execute(select(func.count()).select_from(StripeEvent)).scalar_one()


def test_completed_and_verified_marks_paid(create_checkout, gateway, send_event, db):
    checkout = create_checkout()
    remote = gateway.mark_paid(checkout["checkout_session_id"])

    response = send_event(build_event(COMPLETED, {"id": checkout["checkout_session_id"]}))

    assert response.status_code == 200
    assert response.json()["outcome"] == "processed_paid"
    payment = _payment(db, checkout["payment_id"])
    assert payment.status is PaymentStatus.PAID
    assert payment.paid_at is not None
    assert payment.stripe_payment_intent_id == remote.payment_intent_id


def test_event_payload_is_not_trusted_without_stripe_confirmation(create_checkout, send_event, db):
    checkout = create_checkout()
    forged = build_event(
        COMPLETED,
        {"id": checkout["checkout_session_id"], "payment_status": "paid", "amount_total": 2990, "status": "complete"},
    )

    response = send_event(forged)

    assert response.json()["outcome"] == "no_change"
    assert _payment(db, checkout["payment_id"]).status is PaymentStatus.OPEN


def test_duplicate_event_is_processed_once(create_checkout, gateway, send_event, db):
    checkout = create_checkout()
    gateway.mark_paid(checkout["checkout_session_id"])
    event = build_event(COMPLETED, {"id": checkout["checkout_session_id"]})

    first = send_event(event)
    second = send_event(event)

    assert first.json()["outcome"] == "processed_paid"
    assert second.status_code == 200
    assert second.json()["outcome"] == "duplicate"
    assert len(gateway.retrieve_calls) == 1
    assert _event_count(db) == 1


def test_async_payment_flow_open_then_paid(create_checkout, gateway, send_event, db):
    checkout = create_checkout()
    session_id = checkout["checkout_session_id"]
    gateway.update_remote(session_id, status="complete", payment_status="unpaid")

    assert send_event(build_event(COMPLETED, {"id": session_id})).json()["outcome"] == "no_change"
    assert _payment(db, checkout["payment_id"]).status is PaymentStatus.OPEN

    gateway.mark_paid(session_id)
    assert send_event(build_event(ASYNC_OK, {"id": session_id})).json()["outcome"] == "processed_paid"
    assert _payment(db, checkout["payment_id"]).status is PaymentStatus.PAID


def test_async_payment_failure_marks_failed(create_checkout, gateway, send_event, db):
    checkout = create_checkout()
    gateway.update_remote(checkout["checkout_session_id"], status="complete", payment_status="unpaid")

    response = send_event(build_event(ASYNC_KO, {"id": checkout["checkout_session_id"]}))

    assert response.json()["outcome"] == "processed_failed"
    payment = _payment(db, checkout["payment_id"])
    assert payment.status is PaymentStatus.FAILED
    assert payment.failure_reason == "async_payment_failed"


def test_expired_session_is_marked_expired(create_checkout, gateway, send_event, db):
    checkout = create_checkout()
    gateway.update_remote(checkout["checkout_session_id"], status="expired")

    response = send_event(build_event(EXPIRED, {"id": checkout["checkout_session_id"]}))

    assert response.json()["outcome"] == "processed_expired"
    assert _payment(db, checkout["payment_id"]).status is PaymentStatus.EXPIRED
    assert _payment(db, checkout["payment_id"]).failure_reason == "checkout_session_expired"


def test_out_of_order_failure_after_paid_does_not_downgrade(create_checkout, gateway, send_event, db):
    checkout = create_checkout()
    session_id = checkout["checkout_session_id"]
    gateway.mark_paid(session_id)
    now = int(time.time())
    send_event(build_event(COMPLETED, {"id": session_id}, created=now))

    gateway.update_remote(session_id, status="expired", payment_status="unpaid")
    response = send_event(build_event(EXPIRED, {"id": session_id}, created=now - 60))

    assert response.json()["outcome"] == "stale_event"
    assert _payment(db, checkout["payment_id"]).status is PaymentStatus.PAID


def test_out_of_order_async_success_before_completed(create_checkout, gateway, send_event, db):
    checkout = create_checkout()
    session_id = checkout["checkout_session_id"]
    gateway.mark_paid(session_id)
    now = int(time.time())

    assert send_event(build_event(ASYNC_OK, {"id": session_id}, created=now)).json()["outcome"] == "processed_paid"
    assert send_event(build_event(COMPLETED, {"id": session_id}, created=now - 30)).json()["outcome"] == "no_change"
    assert _payment(db, checkout["payment_id"]).status is PaymentStatus.PAID


def test_failed_locally_can_be_recovered_by_verified_stripe_payment(client, gateway, send_event, db):
    gateway.create_behaviours = ["reject"]
    post_checkout(client)
    payment = db.execute(select(Payment)).scalar_one()
    assert payment.status is PaymentStatus.FAILED

    remote = gateway.create_for(payment.id)
    gateway.mark_paid(remote.id)

    response = send_event(build_event(COMPLETED, {"id": remote.id}))

    assert response.json()["outcome"] == "processed_paid"
    recovered = _payment(db, str(payment.id))
    assert recovered.status is PaymentStatus.PAID
    assert recovered.stripe_checkout_session_id == remote.id
    assert recovered.failure_reason is None


def test_amount_mismatch_never_marks_paid(create_checkout, gateway, send_event, db):
    checkout = create_checkout()
    gateway.mark_paid(checkout["checkout_session_id"])
    gateway.update_remote(checkout["checkout_session_id"], amount_total=1)

    response = send_event(build_event(COMPLETED, {"id": checkout["checkout_session_id"]}))

    assert response.json()["outcome"] == "verification_failed"
    payment = _payment(db, checkout["payment_id"])
    assert payment.status is PaymentStatus.FAILED
    assert payment.failure_reason == "stripe_verification_failed:amount_mismatch"


def test_currency_mismatch_never_marks_paid(create_checkout, gateway, send_event, db):
    checkout = create_checkout()
    gateway.mark_paid(checkout["checkout_session_id"])
    gateway.update_remote(checkout["checkout_session_id"], currency="usd")

    assert (
        send_event(build_event(COMPLETED, {"id": checkout["checkout_session_id"]})).json()["outcome"]
        == "verification_failed"
    )
    assert _payment(db, checkout["payment_id"]).status is not PaymentStatus.PAID


def test_metadata_pointing_to_other_payment_is_rejected(create_checkout, gateway, send_event, db):
    victim = create_checkout("payflow-pro")
    attacker = create_checkout("payflow-pro")
    gateway.mark_paid(attacker["checkout_session_id"])
    gateway.update_remote(
        attacker["checkout_session_id"],
        metadata={"payment_id": victim["payment_id"]},
        client_reference_id=victim["payment_id"],
    )

    response = send_event(build_event(COMPLETED, {"id": attacker["checkout_session_id"]}))

    assert response.json()["outcome"] == "verification_failed"
    assert _payment(db, victim["payment_id"]).status is not PaymentStatus.PAID


def test_livemode_event_is_ignored(create_checkout, gateway, send_event, db):
    checkout = create_checkout()
    gateway.mark_paid(checkout["checkout_session_id"])

    response = send_event(build_event(COMPLETED, {"id": checkout["checkout_session_id"]}, livemode=True))

    assert response.json()["outcome"] == "ignored_livemode"
    assert gateway.retrieve_calls == []
    assert _payment(db, checkout["payment_id"]).status is PaymentStatus.OPEN


def test_livemode_remote_session_is_rejected(create_checkout, gateway, send_event, db):
    checkout = create_checkout()
    gateway.mark_paid(checkout["checkout_session_id"])
    gateway.update_remote(checkout["checkout_session_id"], livemode=True)

    assert (
        send_event(build_event(COMPLETED, {"id": checkout["checkout_session_id"]})).json()["outcome"]
        == "verification_failed"
    )
    assert _payment(db, checkout["payment_id"]).status is not PaymentStatus.PAID


def test_unknown_payment_is_acknowledged(gateway, send_event):
    remote = gateway.create_for(uuid.uuid4())
    gateway.mark_paid(remote.id)

    response = send_event(build_event(COMPLETED, {"id": remote.id}))

    assert response.status_code == 200
    assert response.json()["outcome"] == "payment_not_found"


def test_stripe_outage_is_not_recorded_so_stripe_retries(create_checkout, gateway, send_event, db):
    checkout = create_checkout()
    gateway.mark_paid(checkout["checkout_session_id"])
    event = build_event(COMPLETED, {"id": checkout["checkout_session_id"]})

    gateway.fail_retrieve = True
    failed = send_event(event)
    assert failed.status_code == 503
    assert _event_count(db) == 0

    gateway.fail_retrieve = False
    retried = send_event(event)
    assert retried.json()["outcome"] == "processed_paid"
    assert _event_count(db) == 1


def test_concurrent_delivery_of_same_event_is_processed_once(create_checkout, gateway, db):
    checkout = create_checkout()
    gateway.mark_paid(checkout["checkout_session_id"])
    workers = 8
    gateway.retrieve_barrier = threading.Barrier(workers)
    processor = WebhookProcessor(get_session_factory(), gateway)
    event = build_event(COMPLETED, {"id": checkout["checkout_session_id"]})

    with ThreadPoolExecutor(max_workers=workers) as pool:
        outcomes = list(pool.map(lambda _: processor.process(event), range(workers)))

    assert len(gateway.retrieve_calls) == workers
    assert outcomes.count(Outcome.PROCESSED_PAID) == 1
    assert outcomes.count(Outcome.DUPLICATE) == workers - 1
    assert _event_count(db) == 1
    assert _payment(db, checkout["payment_id"]).status is PaymentStatus.PAID


def test_concurrent_distinct_events_for_same_payment_serialize(create_checkout, gateway, db):
    checkout = create_checkout()
    session_id = checkout["checkout_session_id"]
    gateway.mark_paid(session_id)
    gateway.retrieve_barrier = threading.Barrier(2)
    processor = WebhookProcessor(get_session_factory(), gateway)
    events = [build_event(COMPLETED, {"id": session_id}), build_event(ASYNC_OK, {"id": session_id})]

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(o.value for o in pool.map(processor.process, events))

    assert outcomes == ["no_change", "processed_paid"]
    assert _event_count(db) == 2
