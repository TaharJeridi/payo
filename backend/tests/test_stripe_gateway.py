import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
import stripe

from app.core.config import Settings
from app.core.errors import PaymentProviderError, PaymentProviderNotConfigured, PaymentProviderUnavailable
from app.integrations.stripe_gateway import CheckoutRequestData, CheckoutSessionData, StripeGateway
from app.payments.catalog import get_product

EXPIRES_AT = datetime(2030, 1, 1, 12, 0, tzinfo=UTC)


class _Page:
    def __init__(self, items):
        self.items = items

    def auto_paging_iter(self):
        return iter(self.items)


class _Sessions:
    def __init__(self, response=None, error=None, listed=None):
        self.response = response
        self.error = error
        self.listed = listed or []
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

    def list(self, params):
        self.calls.append(("list", params))
        if self.error:
            raise self.error
        return _Page(self.listed)


def _gateway(sessions: _Sessions) -> StripeGateway:
    gateway = StripeGateway(Settings(stripe_secret_key="sk_test_unit"))
    gateway._client = SimpleNamespace(checkout=SimpleNamespace(sessions=sessions))
    return gateway


def _request(payment_id=None, quantity=1, customer_email=None) -> CheckoutRequestData:
    return CheckoutRequestData(
        payment_id=payment_id or uuid.uuid4(),
        merchant_id=uuid.UUID(int=7),
        product=get_product("payflow-pro"),
        quantity=quantity,
        customer_email=customer_email,
        expires_at=EXPIRES_AT,
    )


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
        "expires_at": int(EXPIRES_AT.timestamp()),
        "metadata": {"payment_id": str(payment_id)},
    }
    payload.update(overrides)
    return payload


def test_checkout_params_come_from_catalog_and_are_idempotent():
    request = _request(quantity=2, customer_email="a@b.it")
    sessions = _Sessions(response=_session_payload(request.payment_id))

    data = _gateway(sessions).create_checkout_session(request)

    params, options = sessions.calls[0]
    line = params["line_items"][0]
    assert params["mode"] == "payment"
    assert line["quantity"] == 2
    assert line["price_data"]["unit_amount"] == 2990
    assert line["price_data"]["currency"] == "eur"
    assert params["metadata"]["payment_id"] == str(request.payment_id)
    assert params["metadata"]["merchant_id"] == str(request.merchant_id)
    assert params["client_reference_id"] == str(request.payment_id)
    assert params["customer_email"] == "a@b.it"
    # The expiry comes from the stored payment, not from "now": retries send identical params.
    assert params["expires_at"] == int(EXPIRES_AT.timestamp())
    assert options == {"idempotency_key": f"checkout-{request.payment_id}"}
    assert data.expires_at == EXPIRES_AT


@pytest.mark.parametrize(
    "error",
    [
        stripe.APIConnectionError("timeout"),
        stripe.APIError("stripe 500"),
        stripe.RateLimitError("429"),
        stripe.IdempotencyError("key in use"),
    ],
)
def test_errors_with_unknown_outcome_are_reported_as_unavailable(error):
    with pytest.raises(PaymentProviderUnavailable):
        _gateway(_Sessions(error=error)).create_checkout_session(_request())


@pytest.mark.parametrize(
    "error",
    [
        stripe.InvalidRequestError("bad param", param="expires_at"),
        stripe.AuthenticationError("bad key"),
        stripe.PermissionError("no access"),
    ],
)
def test_definitive_rejections_are_not_reported_as_unavailable(error):
    with pytest.raises(PaymentProviderError) as exc_info:
        _gateway(_Sessions(error=error)).create_checkout_session(_request())
    assert not isinstance(exc_info.value, PaymentProviderUnavailable)


def test_live_session_is_refused():
    request = _request()
    sessions = _Sessions(response=_session_payload(request.payment_id, livemode=True))

    with pytest.raises(PaymentProviderError):
        _gateway(sessions).create_checkout_session(request)


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
    assert data.expires_at == EXPIRES_AT


def test_find_checkout_session_matches_metadata_within_time_window():
    payment_id = uuid.uuid4()
    other = _session_payload(uuid.uuid4(), id="cs_test_other")
    mine = _session_payload(payment_id, id="cs_test_mine")
    sessions = _Sessions(listed=[other, mine])
    start = datetime(2030, 1, 1, 11, 0, tzinfo=UTC)

    found = _gateway(sessions).find_checkout_session(payment_id, start, start + timedelta(hours=1))

    assert found is not None and found.id == "cs_test_mine"
    _, params = sessions.calls[0]
    assert params["created"] == {"gte": int(start.timestamp()), "lte": int(start.timestamp()) + 3600}


def test_find_checkout_session_returns_none_and_wraps_errors():
    assert _gateway(_Sessions(listed=[])).find_checkout_session(uuid.uuid4(), EXPIRES_AT, EXPIRES_AT) is None
    with pytest.raises(PaymentProviderUnavailable):
        _gateway(_Sessions(error=stripe.APIConnectionError("down"))).find_checkout_session(
            uuid.uuid4(), EXPIRES_AT, EXPIRES_AT
        )


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


@pytest.mark.parametrize("status", [409, 429, 500, 503])
def test_ambiguous_http_statuses_are_never_definitive(status):
    error = stripe.InvalidRequestError("conflict", param=None, http_status=status)

    with pytest.raises(PaymentProviderUnavailable):
        _gateway(_Sessions(error=error)).create_checkout_session(_request())
