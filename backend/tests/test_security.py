import itertools
import logging
import os
import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.db.session import get_session_factory
from app.integrations.stripe_gateway import CheckoutSessionData
from app.payments.models import Payment
from app.payments.reconciliation import Reconciler
from app.payments.state import PaymentStatus
from app.payments.sync import decide
from tests.conftest import build_event, post_checkout

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKIP_DIRS = {".venv", ".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "node_modules"}
SKIP_FILES = {".env"}
SECRET_PATTERNS = [
    re.compile(r"sk_live_[0-9A-Za-z]{10,}"),
    re.compile(r"rk_live_[0-9A-Za-z]{10,}"),
    re.compile(r"sk_test_[0-9A-Za-z]{20,}"),
    re.compile(r"rk_test_[0-9A-Za-z]{20,}"),
    re.compile(r"whsec_[0-9A-Za-z]{20,}"),
    re.compile(r"pf_[0-9a-f]{12}_[0-9A-Za-z_\-]{30,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]


def _repository_files():
    for path in PROJECT_ROOT.rglob("*"):
        relative = path.relative_to(PROJECT_ROOT)
        if path.is_file() and not SKIP_DIRS.intersection(relative.parts) and path.name not in SKIP_FILES:
            yield path


def _require_repository() -> None:
    if not (PROJECT_ROOT / ".gitignore").is_file():
        pytest.skip("repository checks run only from a source checkout, not inside the container image")


def test_no_secrets_in_repository_files():
    _require_repository()
    offenders = []
    for path in _repository_files():
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        offenders += [f"{path.relative_to(PROJECT_ROOT)}: {p.pattern}" for p in SECRET_PATTERNS if p.search(content)]
    assert offenders == []


def test_env_file_is_git_ignored_and_example_has_placeholders_only():
    _require_repository()
    ignored = (PROJECT_ROOT / ".gitignore").read_text().splitlines()
    assert ".env" in ignored
    assert "!.env.example" in ignored
    example = (PROJECT_ROOT / ".env.example").read_text()
    assert not any(p.search(example) for p in SECRET_PATTERNS)


def test_logs_never_contain_customer_data_secrets_or_payable_urls(client, gateway, send_event, merchant_a, caplog):
    caplog.set_level(logging.DEBUG)
    email = "mario.rossi.private@example.com"
    body = {"product_id": "payflow-pro", "customer_email": email}

    ok = post_checkout(client, body).json()
    gateway.create_behaviours = ["lost_response"]
    post_checkout(client, body)
    gateway.create_behaviours = ["reject"]
    post_checkout(client, body)
    gateway.mark_paid(ok["checkout_session_id"])
    send_event(build_event("checkout.session.completed", {"id": ok["checkout_session_id"]}))
    gateway.fail_find = True
    Reconciler(
        get_session_factory(), gateway, get_settings(), clock=lambda: datetime.now(UTC) + timedelta(hours=1)
    ).run_once()

    text = caplog.text
    assert "checkout_created" in text
    assert email not in text
    assert ok["checkout_url"] not in text
    assert os.environ["STRIPE_SECRET_KEY"] not in text
    assert os.environ["STRIPE_WEBHOOK_SECRET"] not in text
    assert merchant_a.api_key not in text


@pytest.mark.parametrize(
    ("status", "payment_status", "event_type"),
    list(
        itertools.product(
            ["open", "complete", "expired", None],
            ["paid", "unpaid", "no_payment_required"],
            [
                None,
                "checkout.session.completed",
                "checkout.session.async_payment_succeeded",
                "checkout.session.async_payment_failed",
                "checkout.session.expired",
            ],
        )
    ),
)
def test_stripe_data_can_never_produce_creation_states_or_unpaid_paid(status, payment_status, event_type):
    remote = CheckoutSessionData(
        id="cs_test_x",
        url=None,
        status=status,
        payment_status=payment_status,
        amount_total=1,
        currency="eur",
        livemode=False,
        client_reference_id=None,
        payment_intent_id=None,
        customer_email=None,
    )
    target, _ = decide(remote, event_type)
    assert target not in (PaymentStatus.CREATING, PaymentStatus.CREATION_UNKNOWN)
    assert (target is PaymentStatus.PAID) == (payment_status == "paid")


def test_webhook_cannot_reopen_fail_or_expire_a_paid_payment(create_checkout, gateway, send_event, db):
    checkout = create_checkout()
    session_id = checkout["checkout_session_id"]
    gateway.mark_paid(session_id)
    send_event(build_event("checkout.session.completed", {"id": session_id}))

    gateway.update_remote(session_id, status="complete", payment_status="unpaid")
    async_failed = send_event(build_event("checkout.session.async_payment_failed", {"id": session_id}))
    gateway.update_remote(session_id, status="expired")
    expired = send_event(build_event("checkout.session.expired", {"id": session_id}))
    gateway.update_remote(session_id, status="open")
    reopened = send_event(build_event("checkout.session.completed", {"id": session_id}))

    assert [r.json()["outcome"] for r in (async_failed, expired, reopened)] == ["stale_event"] * 3
    db.expire_all()
    assert db.get(Payment, uuid.UUID(checkout["payment_id"])).status is PaymentStatus.PAID


def test_webhook_for_one_session_cannot_apply_another_sessions_data(create_checkout, gateway, send_event, db):
    victim = create_checkout()
    attacker = create_checkout()
    gateway.mark_paid(attacker["checkout_session_id"])
    gateway.sessions[victim["checkout_session_id"]] = gateway.sessions[attacker["checkout_session_id"]]

    response = send_event(build_event("checkout.session.completed", {"id": victim["checkout_session_id"]}))

    assert response.json()["outcome"] == "verification_failed"
    db.expire_all()
    assert db.get(Payment, uuid.UUID(victim["payment_id"])).status is not PaymentStatus.PAID


def test_unsigned_or_forged_webhooks_change_nothing(anon_client, create_checkout, gateway, db):
    checkout = create_checkout()
    gateway.mark_paid(checkout["checkout_session_id"])
    payload = b'{"id":"evt_x","object":"event","type":"checkout.session.completed","created":1,"livemode":false}'

    unsigned = anon_client.post("/webhooks/stripe", content=payload)
    forged = anon_client.post("/webhooks/stripe", content=payload, headers={"Stripe-Signature": "t=1,v1=deadbeef"})

    assert unsigned.status_code == forged.status_code == 400
    db.expire_all()
    assert db.get(Payment, uuid.UUID(checkout["payment_id"])).status is PaymentStatus.OPEN
