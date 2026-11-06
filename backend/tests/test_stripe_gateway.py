import time
import uuid
from types import SimpleNamespace

import pytest
import stripe

from app.core.config import Settings
from app.core.errors import PaymentProviderError, PaymentProviderNotConfigured
from app.integrations.stripe_gateway import CheckoutSessionData, StripeGateway
from app.payments.catalog import get_product


class _Sessions:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def expire(self, session_id):
        self.calls.append(("expire", session_id))
        if self.error:
            raise self.error

    def create(self, params, options):
        self.calls.append((params, options))
        if self.error:
            raise self.error
        return self.response


def _gateway(sessions: _Sessions) -> StripeGateway:
    gateway = StripeGateway(Settings(stripe_secret_key="sk_test_unit"))
    gateway._client = SimpleNamespace(checkout=SimpleNamespace(sessions=sessions))
    return gateway


def _session_payload(payment_id, **overrides):
    payload = {
        "id": "cs_test_123",
        "url": "https://checkout.stripe.com/c/pay/cs_test_123",
        "status": "open",
        "payment_status": "unpaid",
        "amount_total": 5980,
        "currency": "eur",
        "livemode": False,
        "client_reference_id": str(payment_id),
        "payment_intent": None,
        "metadata": {"payment_id": str(payment_id)},
    }
    payload.update(overrides)
    return payload


def test_checkout_params_come_from_catalog_and_are_idempotent():
    payment_id = uuid.uuid4()
    sessions = _Sessions(response=_session_payload(payment_id))

    _gateway(sessions).create_checkout_session(
        payment_id=payment_id, product=get_product("payflow-pro"), quantity=2, customer_email="a@b.it"
    )

    params, options = sessions.calls[0]
    line = params["line_items"][0]
    assert params["mode"] == "payment"
    assert line["quantity"] == 2
    assert line["price_data"]["unit_amount"] == 2990
    assert line["price_data"]["currency"] == "eur"
    assert params["metadata"]["payment_id"] == str(payment_id)
    assert params["client_reference_id"] == str(payment_id)
    assert params["customer_email"] == "a@b.it"
    assert 30 * 60 < params["expires_at"] - time.time() <= 24 * 3600
    assert options == {"idempotency_key": f"checkout-{payment_id}"}


def test_stripe_errors_are_wrapped():
    sessions = _Sessions(error=stripe.APIConnectionError("boom"))

    with pytest.raises(PaymentProviderError):
        _gateway(sessions).create_checkout_session(
            payment_id=uuid.uuid4(), product=get_product("payflow-pro"), quantity=1, customer_email=None
        )


def test_live_session_is_refused():
    payment_id = uuid.uuid4()
    sessions = _Sessions(response=_session_payload(payment_id, livemode=True))

    with pytest.raises(PaymentProviderError):
        _gateway(sessions).create_checkout_session(
            payment_id=payment_id, product=get_product("payflow-pro"), quantity=2, customer_email=None
        )


def test_session_mapping_handles_expanded_payment_intent_and_customer_details():
    data = CheckoutSessionData.from_stripe(
        _session_payload(
            uuid.uuid4(),
            payment_intent={"id": "pi_test_1", "object": "payment_intent"},
            customer_details={"email": "buyer@example.com"},
        )
    )

    assert data.payment_intent_id == "pi_test_1"
    assert data.customer_email == "buyer@example.com"


def test_gateway_requires_configured_key():
    with pytest.raises(PaymentProviderNotConfigured) as exc_info:
        StripeGateway(Settings(stripe_secret_key=""))
    assert exc_info.value.status_code == 503


def test_expire_calls_stripe_and_wraps_errors():
    sessions = _Sessions()
    _gateway(sessions).expire_checkout_session("cs_test_123")
    assert sessions.calls == [("expire", "cs_test_123")]

    failing = _Sessions(error=stripe.APIConnectionError("down"))
    with pytest.raises(PaymentProviderError):
        _gateway(failing).expire_checkout_session("cs_test_123")
