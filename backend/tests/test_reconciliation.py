import os
import subprocess
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select, update

from app.core.config import get_settings
from app.db.session import get_session_factory
from app.payments.ledger import EventSource, PaymentLedger
from app.payments.models import Payment
from app.payments.reconciliation import EXPIRY_GRACE, ReconcileOutcome, Reconciler
from app.payments.repository import PaymentRepository
from app.payments.state import PaymentStatus
from app.webhooks.service import Outcome, WebhookProcessor
from tests.conftest import build_event, post_checkout

SETTINGS = get_settings()
BASE = SETTINGS.reconcile_backoff_base_seconds


def _payment(db, payment_id) -> Payment:
    db.expire_all()
    return db.get(Payment, uuid.UUID(str(payment_id)))


def _reconciler(gateway, at: datetime) -> Reconciler:
    return Reconciler(get_session_factory(), gateway, SETTINGS, clock=lambda: at, jitter=lambda: 1.0)


def _due(payment: Payment, seconds: int = 1) -> datetime:
    return payment.next_reconcile_at + timedelta(seconds=seconds)


def _after_expiry(payment: Payment, seconds: int = 1) -> datetime:
    return payment.checkout_expires_at + timedelta(seconds=SETTINGS.reconcile_clock_skew_seconds + seconds)


def _unknown(client, gateway, behaviour: str) -> str:
    gateway.create_behaviours = [behaviour]
    response = post_checkout(client)
    assert response.status_code == 503
    return response.json()["error"]["details"]["payment_id"]


def test_payments_are_not_selected_before_their_next_check(create_checkout, gateway, db):
    payment = _payment(db, create_checkout()["payment_id"])

    report = _reconciler(gateway, payment.next_reconcile_at - timedelta(seconds=1)).run_once()

    assert report.checked == 0
    assert gateway.retrieve_calls == []


def test_lost_webhook_for_a_paid_session_is_recovered_before_expiry(create_checkout, gateway, db):
    checkout = create_checkout()
    remote = gateway.mark_paid(checkout["checkout_session_id"])
    payment = _payment(db, checkout["payment_id"])
    assert _due(payment) < payment.checkout_expires_at

    report = _reconciler(gateway, _due(payment)).run_once()

    assert report.outcomes == {ReconcileOutcome.PAID: 1}
    payment = _payment(db, checkout["payment_id"])
    assert payment.status is PaymentStatus.PAID
    assert payment.stripe_payment_intent_id == remote.payment_intent_id
    assert payment.next_reconcile_at is None
    assert PaymentRepository(db).history(payment.id)[-1].source == EventSource.RECONCILIATION


def test_expired_session_becomes_expired_not_failed(create_checkout, gateway, db):
    checkout = create_checkout()
    gateway.update_remote(checkout["checkout_session_id"], status="expired")
    payment = _payment(db, checkout["payment_id"])

    report = _reconciler(gateway, _due(payment)).run_once()

    assert report.outcomes == {ReconcileOutcome.EXPIRED: 1}
    payment = _payment(db, checkout["payment_id"])
    assert payment.status is PaymentStatus.EXPIRED
    assert payment.failure_reason == "checkout_session_expired"


def test_open_session_is_polled_with_backoff_capped_at_real_stripe_expiry(create_checkout, gateway, db):
    checkout = create_checkout()
    at = _due(_payment(db, checkout["payment_id"]))
    real_expiry = at - timedelta(minutes=4)
    gateway.update_remote(checkout["checkout_session_id"], expires_at=real_expiry)

    report = _reconciler(gateway, at).run_once()

    assert report.outcomes == {ReconcileOutcome.NO_CHANGE: 1}
    payment = _payment(db, checkout["payment_id"])
    assert payment.status is PaymentStatus.OPEN
    assert payment.checkout_expires_at == real_expiry
    assert real_expiry + EXPIRY_GRACE < at + timedelta(seconds=BASE * 2)
    assert payment.next_reconcile_at == real_expiry + EXPIRY_GRACE


def test_long_lived_session_is_not_assumed_to_expire_after_31_minutes(create_checkout, gateway, db):
    checkout = create_checkout()
    gateway.update_remote(checkout["checkout_session_id"], expires_at=datetime.now(UTC) + timedelta(hours=20))
    at = _due(_payment(db, checkout["payment_id"]))

    _reconciler(gateway, at + timedelta(minutes=31)).run_once()

    payment = _payment(db, checkout["payment_id"])
    assert payment.status is PaymentStatus.OPEN
    assert payment.next_reconcile_at == at + timedelta(minutes=31, seconds=BASE * 2)


def test_lost_creation_response_is_resolved_by_searching_stripe(client, gateway, db):
    payment_id = _unknown(client, gateway, "lost_response")
    session_id = next(iter(gateway.sessions))
    payment = _payment(db, payment_id)

    report = _reconciler(gateway, _due(payment)).run_once()

    assert report.outcomes == {ReconcileOutcome.OPEN: 1}
    payment = _payment(db, payment_id)
    assert payment.status is PaymentStatus.OPEN
    assert payment.stripe_checkout_session_id == session_id
    assert payment.checkout_creation_error is None
    searched_id, created_from, _ = gateway.find_calls[0]
    assert searched_id == payment.id
    assert created_from == payment.created_at - timedelta(seconds=SETTINGS.reconcile_clock_skew_seconds)


def test_lost_creation_response_for_a_paid_session_becomes_paid(client, gateway, db):
    payment_id = _unknown(client, gateway, "lost_response")
    gateway.mark_paid(next(iter(gateway.sessions)))

    _reconciler(gateway, _due(_payment(db, payment_id))).run_once()

    assert _payment(db, payment_id).status is PaymentStatus.PAID


def test_creation_that_never_reached_stripe_is_closed_only_after_expiry(client, gateway, db):
    payment_id = _unknown(client, gateway, "unavailable")
    payment = _payment(db, payment_id)

    before = _reconciler(gateway, _due(payment)).reconcile_payment(payment.id)

    assert before is ReconcileOutcome.AWAITING_SESSION
    assert _payment(db, payment_id).status is PaymentStatus.CREATION_UNKNOWN
    assert _payment(db, payment_id).next_reconcile_at <= _after_expiry(payment, 0)

    after = _reconciler(gateway, _after_expiry(payment)).reconcile_payment(payment.id)

    assert after is ReconcileOutcome.SESSION_NOT_CREATED
    payment = _payment(db, payment_id)
    assert payment.status is PaymentStatus.FAILED
    assert payment.failure_reason == "checkout_session_not_created"
    assert gateway.sessions == {}


def test_stripe_outage_backs_off_exponentially_without_changing_state(create_checkout, gateway, db):
    checkout = create_checkout()
    gateway.mark_paid(checkout["checkout_session_id"])
    gateway.fail_retrieve = True
    at = _due(_payment(db, checkout["payment_id"]))

    delays = []
    for _ in range(4):
        assert _reconciler(gateway, at).run_once().outcomes == {ReconcileOutcome.ERROR: 1}
        payment = _payment(db, checkout["payment_id"])
        delays.append((payment.next_reconcile_at - at).total_seconds())
        at = payment.next_reconcile_at + timedelta(seconds=1)

    payment = _payment(db, checkout["payment_id"])
    assert payment.status is PaymentStatus.OPEN
    assert payment.reconcile_attempts == 4
    assert payment.last_reconcile_error == "payment_provider_unavailable"
    assert delays == [BASE * 2, BASE * 4, BASE * 8, BASE * 16]

    gateway.fail_retrieve = False
    assert _reconciler(gateway, at).run_once().outcomes == {ReconcileOutcome.PAID: 1}
    payment = _payment(db, checkout["payment_id"])
    assert payment.reconcile_attempts == 0
    assert payment.last_reconcile_error is None


def test_backoff_is_capped(gateway):
    assert _reconciler(gateway, datetime.now(UTC)).backoff(60) == timedelta(
        seconds=SETTINGS.reconcile_backoff_max_seconds
    )


def test_reconciliation_applies_the_same_verification_as_webhooks(create_checkout, gateway, db):
    amount = create_checkout()
    currency = create_checkout()
    live = create_checkout()
    for checkout, change in (
        (amount, {"amount_total": 1}),
        (currency, {"currency": "usd"}),
        (live, {"livemode": True}),
    ):
        gateway.mark_paid(checkout["checkout_session_id"])
        gateway.update_remote(checkout["checkout_session_id"], **change)
    at = _due(_payment(db, live["payment_id"]))

    report = _reconciler(gateway, at).run_once()

    assert report.outcomes == {ReconcileOutcome.VERIFICATION_FAILED: 3}
    for checkout in (amount, currency, live):
        assert _payment(db, checkout["payment_id"]).status is PaymentStatus.FAILED


def test_session_found_by_discovery_must_match_the_payment(client, gateway, db):
    payment_id = _unknown(client, gateway, "unavailable")
    forged = gateway.create_for(uuid.UUID(payment_id), quantity=3)
    gateway.mark_paid(forged.id)

    report = _reconciler(gateway, _due(_payment(db, payment_id))).run_once()

    assert report.outcomes == {ReconcileOutcome.VERIFICATION_FAILED: 1}
    assert _payment(db, payment_id).status is not PaymentStatus.PAID


def test_settled_payments_are_never_selected(create_checkout, gateway, send_event, db):
    checkout = create_checkout()
    gateway.mark_paid(checkout["checkout_session_id"])
    send_event(build_event("checkout.session.completed", {"id": checkout["checkout_session_id"]}))

    report = _reconciler(gateway, datetime.now(UTC) + timedelta(days=2)).run_once()

    assert report.checked == 0


def test_payment_settled_by_webhook_during_reconciliation_is_not_touched(create_checkout, gateway, db):
    checkout = create_checkout()
    payment_id = uuid.UUID(checkout["payment_id"])
    gateway.update_remote(checkout["checkout_session_id"], status="expired")
    original = gateway.retrieve_checkout_session

    def retrieve_while_webhook_pays(session_id):
        remote = original(session_id)
        with get_session_factory()() as session:
            payment = PaymentRepository(session).get_for_update(payment_id)
            PaymentLedger(session, lambda: datetime.now(UTC)).transition(
                payment, PaymentStatus.PAID, source=EventSource.WEBHOOK
            )
            session.commit()
        return remote

    gateway.retrieve_checkout_session = retrieve_while_webhook_pays

    report = _reconciler(gateway, _due(_payment(db, payment_id))).run_once()

    assert report.outcomes == {ReconcileOutcome.SKIPPED: 1}
    assert _payment(db, payment_id).status is PaymentStatus.PAID


def test_client_retry_between_read_and_write_is_detected(client, gateway, db):
    payment_id = _unknown(client, gateway, "unavailable")
    original = gateway.find_checkout_session

    def find_while_client_retries(*args, **kwargs):
        result = original(*args, **kwargs)
        with get_session_factory()() as session:
            session.execute(
                update(Payment)
                .where(Payment.id == uuid.UUID(payment_id))
                .values(creation_attempts=Payment.creation_attempts + 1)
            )
            session.commit()
        return result

    gateway.find_checkout_session = find_while_client_retries
    payment = _payment(db, payment_id)

    outcome = _reconciler(gateway, _after_expiry(payment)).reconcile_payment(payment.id)

    assert outcome is ReconcileOutcome.CHANGED_CONCURRENTLY
    assert _payment(db, payment_id).status is PaymentStatus.CREATION_UNKNOWN


def test_creation_in_flight_is_deferred_until_its_lease_ends(merchant_a, gateway, db):
    now = datetime.now(UTC)
    with get_session_factory()() as session:
        payment = Payment(
            merchant_id=merchant_a.id,
            idempotency_key="in-flight",
            request_fingerprint="x",
            product_id="payflow-pro",
            quantity=1,
            unit_amount=2990,
            amount=2990,
            currency="eur",
            status=PaymentStatus.CREATING,
            checkout_expires_at=now + timedelta(hours=2),
            creation_attempts=1,
            creation_attempt_at=now,
            next_reconcile_at=now - timedelta(seconds=1),
        )
        session.add(payment)
        session.commit()
        payment_id = payment.id

    deferred = _reconciler(gateway, now).run_once()
    abandoned = _reconciler(gateway, now + timedelta(seconds=SETTINGS.checkout_creation_lease_seconds + 1)).run_once()

    assert deferred.outcomes == {ReconcileOutcome.DEFERRED_IN_FLIGHT: 1}
    assert abandoned.outcomes == {ReconcileOutcome.AWAITING_SESSION: 1}
    assert len(gateway.find_calls) == 1
    assert _payment(db, payment_id).status is PaymentStatus.CREATING


def test_parallel_reconcilers_never_process_the_same_payment(create_checkout, gateway, db):
    for _ in range(6):
        create_checkout()
    at = datetime.now(UTC) + timedelta(hours=1)
    start = threading.Barrier(2)

    def worker():
        start.wait(timeout=10)
        return _reconciler(gateway, at).run_once().checked

    with ThreadPoolExecutor(max_workers=2) as pool:
        checked = [f.result() for f in [pool.submit(worker) for _ in range(2)]]

    assert sum(checked) == 6
    assert len(gateway.retrieve_calls) == len(set(gateway.retrieve_calls)) == 6


def test_legacy_payment_without_stored_expiry_uses_stripe_expiry(create_checkout, gateway, db):
    checkout = create_checkout()
    db.execute(update(Payment).where(Payment.id == uuid.UUID(checkout["payment_id"])).values(checkout_expires_at=None))
    db.commit()
    real_expiry = datetime(2031, 5, 1, tzinfo=UTC)
    session_id = checkout["checkout_session_id"]
    gateway.sessions[session_id] = replace(gateway.sessions[session_id], expires_at=real_expiry)

    report = _reconciler(gateway, _due(_payment(db, checkout["payment_id"]))).run_once()

    assert report.outcomes == {ReconcileOutcome.NO_CHANGE: 1}
    assert _payment(db, checkout["payment_id"]).checkout_expires_at == real_expiry


def test_reconciler_and_webhook_racing_on_the_same_payment_apply_once(create_checkout, gateway, db):
    checkout = create_checkout()
    session_id = checkout["checkout_session_id"]
    gateway.mark_paid(session_id)
    payment = _payment(db, checkout["payment_id"])
    reconciler = _reconciler(gateway, _due(payment))
    processor = WebhookProcessor(get_session_factory(), gateway)
    gateway.retrieve_barrier = threading.Barrier(2)

    with ThreadPoolExecutor(max_workers=2) as pool:
        from_reconciler = pool.submit(reconciler.reconcile_payment, payment.id)
        from_webhook = pool.submit(processor.process, build_event("checkout.session.completed", {"id": session_id}))
        outcomes = {from_reconciler.result(), from_webhook.result()}

    assert outcomes in (
        {ReconcileOutcome.PAID, Outcome.NO_CHANGE},
        {ReconcileOutcome.SKIPPED, Outcome.PROCESSED_PAID},
    ), outcomes
    assert _payment(db, checkout["payment_id"]).status is PaymentStatus.PAID
    events = PaymentRepository(db).history(payment.id)
    assert [e.to_status for e in events].count(PaymentStatus.PAID) == 1


def test_history_is_a_gapless_chain_across_api_reconciliation_and_webhook(client, gateway, send_event, db):
    payment_id = _unknown(client, gateway, "lost_response")
    session_id = next(iter(gateway.sessions))
    _reconciler(gateway, _due(_payment(db, payment_id))).run_once()
    gateway.mark_paid(session_id)
    send_event(build_event("checkout.session.completed", {"id": session_id}))

    history = PaymentRepository(db).history(uuid.UUID(payment_id))

    assert [(e.from_status, e.to_status, e.source) for e in history] == [
        (None, PaymentStatus.CREATING, "api"),
        (PaymentStatus.CREATING, PaymentStatus.CREATION_UNKNOWN, "api"),
        (PaymentStatus.CREATION_UNKNOWN, PaymentStatus.OPEN, "reconciliation"),
        (PaymentStatus.OPEN, PaymentStatus.PAID, "webhook"),
    ]
    for previous, current in zip(history, history[1:], strict=False):
        assert current.from_status == previous.to_status
    assert history[-1].to_status == _payment(db, payment_id).status


def test_a_locked_payment_does_not_block_other_reconcilers(create_checkout, gateway, db):
    first = create_checkout()
    for _ in range(3):
        create_checkout()
    at = datetime.now(UTC) + timedelta(hours=1)
    blocker = get_session_factory()()
    blocker.execute(select(Payment).where(Payment.id == uuid.UUID(first["payment_id"])).with_for_update()).scalar_one()
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        checked = pool.submit(lambda: _reconciler(gateway, at).run_once().checked).result(timeout=5)
    finally:
        blocker.rollback()
        blocker.close()
        pool.shutdown(wait=True)

    assert checked == 3
    assert first["checkout_session_id"] not in gateway.retrieve_calls


def test_reconcile_cli_process_registers_every_model_before_writing(create_checkout, db):
    checkout = create_checkout()
    script = (
        "import sys, uuid\n"
        "from datetime import UTC, datetime, timedelta\n"
        "import app.reconcile\n"
        "from app.core.config import get_settings\n"
        "from app.db.session import get_session_factory\n"
        "from app.payments.reconciliation import Reconciler\n"
        "from tests.fakes import FakeStripeGateway\n"
        "gateway = FakeStripeGateway()\n"
        "gateway.fail_retrieve = True\n"
        "at = datetime.now(UTC) + timedelta(hours=1)\n"
        "report = Reconciler(get_session_factory(), gateway, get_settings(), clock=lambda: at).run_once()\n"
        "print(report.summary())\n"
    )
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}

    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", script], capture_output=True, text=True, env=env, timeout=60, check=False
    )

    assert result.returncode == 0, result.stderr[-2000:]
    assert "error=1" in result.stdout
    assert _payment(db, checkout["payment_id"]).reconcile_attempts == 1
