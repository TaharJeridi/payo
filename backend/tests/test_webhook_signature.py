import json
import time

from app.core.config import Settings, get_settings
from app.main import app
from tests.conftest import build_event, sign_payload


def _raw(event) -> bytes:
    return json.dumps(event, separators=(",", ":")).encode()


def _event():
    return build_event("checkout.session.completed", {"id": "cs_test_unknown"})


def test_missing_signature_header_is_rejected(client):
    response = client.post("/webhooks/stripe", content=_raw(_event()))

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_webhook"


def test_signature_with_wrong_secret_is_rejected(client):
    payload = _raw(_event())

    response = client.post(
        "/webhooks/stripe",
        content=payload,
        headers={"Stripe-Signature": sign_payload(payload, secret="whsec_attacker")},
    )

    assert response.status_code == 400


def test_tampered_body_is_rejected(client):
    payload = _raw(_event())
    header = sign_payload(payload)
    tampered = payload.replace(b"checkout.session.completed", b"checkout.session.expired")

    response = client.post("/webhooks/stripe", content=tampered, headers={"Stripe-Signature": header})

    assert response.status_code == 400


def test_signature_is_bound_to_raw_bytes_not_parsed_json(client):
    payload = _raw(_event())
    header = sign_payload(payload)
    reserialized = json.dumps(json.loads(payload), indent=2).encode()

    response = client.post("/webhooks/stripe", content=reserialized, headers={"Stripe-Signature": header})

    assert response.status_code == 400


def test_replayed_old_signature_is_rejected(client):
    payload = _raw(_event())
    header = sign_payload(payload, timestamp=int(time.time()) - 3600)

    response = client.post("/webhooks/stripe", content=payload, headers={"Stripe-Signature": header})

    assert response.status_code == 400


def test_valid_signature_is_accepted(client, gateway):
    payload = _raw(build_event("customer.created", {"id": "cs_test_irrelevant"}))

    response = client.post("/webhooks/stripe", content=payload, headers={"Stripe-Signature": sign_payload(payload)})

    assert response.status_code == 200
    assert response.json()["outcome"] == "ignored_event_type"
    assert gateway.retrieve_calls == []


def test_non_event_payload_is_rejected(client):
    payload = b'{"object":"charge","id":"ch_1"}'

    response = client.post("/webhooks/stripe", content=payload, headers={"Stripe-Signature": sign_payload(payload)})

    assert response.status_code == 400


def test_missing_webhook_secret_returns_503(client):
    app.dependency_overrides[get_settings] = lambda: Settings(stripe_webhook_secret="")
    try:
        payload = _raw(_event())
        response = client.post("/webhooks/stripe", content=payload, headers={"Stripe-Signature": sign_payload(payload)})
        assert response.status_code == 503
    finally:
        app.dependency_overrides.pop(get_settings)
