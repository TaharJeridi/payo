import os
import socket
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import Settings
from app.integrations.stripe_gateway import CheckoutRequestData, StripeGateway
from app.payments.catalog import get_product
from app.payments.models import Payment
from app.payments.state import PaymentStatus
from app.payments.sync import verify_remote

pytestmark = pytest.mark.stripe_testmode

KEY_ENV = "STRIPE_INTEGRATION_SECRET_KEY"


def _blocked_reason() -> str | None:
    key = os.environ.get(KEY_ENV, "")
    if not key:
        return f"BLOCKED: {KEY_ENV} not set"
    if not key.startswith(("sk_test_", "rk_test_")):
        return "BLOCKED: only sk_test_/rk_test_ keys are allowed"
    try:
        socket.create_connection(("api.stripe.com", 443), timeout=5).close()
    except OSError as exc:
        return f"BLOCKED: api.stripe.com unreachable ({type(exc).__name__})"
    return None


@pytest.fixture(scope="module")
def stripe_gateway() -> StripeGateway:
    reason = _blocked_reason()
    if reason:
        pytest.skip(reason)
    return StripeGateway(Settings(stripe_secret_key=os.environ[KEY_ENV], stripe_api_timeout_seconds=20))


def test_real_checkout_session_lifecycle_in_test_mode(stripe_gateway):
    payment_id = uuid.uuid4()
    created_from = datetime.now(UTC) - timedelta(minutes=5)
    request = CheckoutRequestData(
        payment_id=payment_id,
        merchant_id=uuid.uuid4(),
        product=get_product("payflow-pro"),
        quantity=1,
        customer_email=None,
        expires_at=(datetime.now(UTC) + timedelta(minutes=45)).replace(microsecond=0),
    )
    created = stripe_gateway.create_checkout_session(request)
    try:
        assert created.id.startswith("cs_test_")
        assert created.livemode is False
        assert created.status == "open"
        assert created.amount_total == 2990
        assert created.expires_at == request.expires_at

        assert stripe_gateway.create_checkout_session(request).id == created.id

        local = Payment(id=payment_id, amount=2990, currency="eur", status=PaymentStatus.CREATING)
        remote = stripe_gateway.retrieve_checkout_session(created.id)
        assert verify_remote(local, created.id, remote) is None

        found = stripe_gateway.find_checkout_session(payment_id, created_from, datetime.now(UTC) + timedelta(minutes=5))
        assert found is not None and found.id == created.id
    finally:
        stripe_gateway.expire_checkout_session(created.id)

    assert stripe_gateway.retrieve_checkout_session(created.id).status == "expired"
