import re
import uuid

import pytest
from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.db.session import get_session_factory
from app.main import app
from app.merchants import cli
from app.merchants.keys import generate_api_key, hash_key, parse_prefix
from app.merchants.models import MerchantApiKey
from app.merchants.service import MerchantService
from app.payments.models import Payment
from tests.conftest import build_event, post_checkout

PROTECTED = [
    ("get", "/payments"),
    ("get", f"/payments/{uuid.uuid4()}"),
    ("post", "/payments/checkout"),
]


# --- API keys ------------------------------------------------------------------------------


def test_generated_keys_are_random_and_parseable():
    a, b = generate_api_key(), generate_api_key()

    assert a.plaintext != b.plaintext
    assert re.fullmatch(r"pf_[0-9a-f]{12}_[A-Za-z0-9_-]{43}", a.plaintext)
    assert parse_prefix(a.plaintext) == a.prefix
    assert a.key_hash == hash_key(a.plaintext)
    assert parse_prefix("pf_short_key") is None


def test_only_the_hash_is_stored(merchant_a, db):
    row = db.get(MerchantApiKey, merchant_a.key_id)

    assert row.key_hash == hash_key(merchant_a.api_key)
    stored = " ".join(str(v) for v in (row.prefix, row.key_hash, row.label))
    assert merchant_a.api_key not in stored
    assert merchant_a.api_key.split("_", 2)[2] not in stored  # the secret part never hits the DB


# --- authentication ------------------------------------------------------------------------


@pytest.mark.parametrize(("method", "path"), PROTECTED)
def test_payment_data_requires_an_api_key(anon_client, method, path):
    response = anon_client.request(method, path, json={"product_id": "payflow-pro"})

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == "unauthorized"


@pytest.mark.parametrize(
    "value",
    [
        "Bearer not-a-key",
        "Bearer pf_000000000000_" + "A" * 43,  # well-formed, unknown
        "Basic dXNlcjpwYXNz",
    ],
)
def test_invalid_credentials_are_rejected(anon_client, merchant_a, value):
    assert anon_client.get("/payments", headers={"Authorization": value}).status_code == 401


def test_tampered_secret_with_valid_prefix_is_rejected(anon_client, merchant_a):
    tampered = merchant_a.api_key[:-1] + ("A" if merchant_a.api_key[-1] != "A" else "B")

    assert anon_client.get("/payments", headers={"Authorization": f"Bearer {tampered}"}).status_code == 401


def test_revoked_key_stops_working_and_other_keys_keep_working(anon_client, merchant_a):
    with get_session_factory()() as session, session.begin():
        second = MerchantService(session).issue_key(merchant_a.id, label="second")
        assert MerchantService(session).revoke_key(merchant_a.key_id) is True

    assert anon_client.get("/payments", headers=merchant_a.headers).status_code == 401
    ok = anon_client.get("/payments", headers={"Authorization": f"Bearer {second.plaintext}"})
    assert ok.status_code == 200


def test_public_endpoints_need_no_key(anon_client):
    assert anon_client.get("/health").status_code == 200
    assert anon_client.get("/payments/products").status_code == 200


# --- tenant isolation ----------------------------------------------------------------------


def test_merchant_b_cannot_see_merchant_a_payments(client, merchant_a, merchant_b, gateway, send_event):
    paid = post_checkout(client).json()
    gateway.mark_paid(paid["checkout_session_id"])
    send_event(build_event("checkout.session.completed", {"id": paid["checkout_session_id"]}))
    post_checkout(client)
    b_payment = post_checkout(client, headers=merchant_b.headers).json()

    a_list = client.get("/payments").json()
    b_list = client.get("/payments", headers=merchant_b.headers).json()
    b_paid_filter = client.get("/payments", params={"status": "paid"}, headers=merchant_b.headers).json()

    assert a_list["total"] == 2
    assert b_list["total"] == 1
    assert [p["id"] for p in b_list["items"]] == [b_payment["payment_id"]]
    assert b_paid_filter["total"] == 0


def test_merchant_b_gets_404_for_merchant_a_payment(client, merchant_b):
    a_payment = post_checkout(client).json()["payment_id"]

    as_b = client.get(f"/payments/{a_payment}", headers=merchant_b.headers)
    missing = client.get(f"/payments/{uuid.uuid4()}", headers=merchant_b.headers)

    # Same status and body as a payment that does not exist: no existence oracle.
    assert as_b.status_code == missing.status_code == 404
    assert as_b.json() == missing.json()


def test_merchant_b_cannot_replay_merchant_a_idempotency_key(client, merchant_b, db):
    a = post_checkout(client, key="order-1").json()
    b = post_checkout(client, key="order-1", headers=merchant_b.headers)

    assert b.status_code == 201  # a brand new purchase for B, nothing of A's is returned
    assert b.json()["payment_id"] != a["payment_id"]
    assert b.json()["checkout_url"] != a["checkout_url"]


def test_payments_are_attributed_to_the_authenticated_merchant(client, merchant_a, merchant_b, gateway, db):
    a = post_checkout(client).json()
    b = post_checkout(client, headers=merchant_b.headers).json()

    owners = dict(db.execute(select(Payment.id, Payment.merchant_id)).all())
    assert owners[uuid.UUID(a["payment_id"])] == merchant_a.id
    assert owners[uuid.UUID(b["payment_id"])] == merchant_b.id
    assert gateway.create_calls[1].merchant_id == merchant_b.id


# --- demo endpoints ------------------------------------------------------------------------


def test_demo_pages_exist_outside_production(anon_client):
    assert anon_client.get("/payments/demo/success").status_code == 200
    assert anon_client.get("/payments/demo/cancel").status_code == 200


@pytest.mark.parametrize("environment", ["production", "PRODUCTION", "prod"])
def test_demo_pages_are_disabled_in_production_even_with_the_flag(anon_client, environment):
    app.dependency_overrides[get_settings] = lambda: Settings(environment=environment, demo_endpoints_enabled=True)
    try:
        assert anon_client.get("/payments/demo/success").status_code == 404
        assert anon_client.get("/payments/demo/cancel").status_code == 404
    finally:
        app.dependency_overrides.pop(get_settings)


def test_demo_flag_semantics():
    assert Settings.model_fields["demo_endpoints_enabled"].default is False
    assert Settings(demo_endpoints_enabled=True, environment="development").demo_endpoints_active is True
    assert Settings(demo_endpoints_enabled=True, environment="production").demo_endpoints_active is False
    assert Settings(demo_endpoints_enabled=False, environment="development").demo_endpoints_active is False


# --- CLI -----------------------------------------------------------------------------------


def test_cli_create_issue_list_revoke(capsys, anon_client):
    assert cli.main(["create", "Shop Srl"]) == 0
    created = dict(line.split("=", 1) for line in capsys.readouterr().out.split())
    merchant_id, key_id, api_key = created["merchant_id"], created["key_id"], created["api_key"]
    assert anon_client.get("/payments", headers={"Authorization": f"Bearer {api_key}"}).status_code == 200

    assert cli.main(["issue-key", merchant_id, "--label", "ci"]) == 0
    capsys.readouterr()
    assert cli.main(["list"]) == 0
    listing = capsys.readouterr().out
    assert "Shop Srl" in listing and listing.count("active") == 2
    assert api_key not in listing  # secrets are never listed

    assert cli.main(["revoke-key", key_id]) == 0
    assert "revoked" in capsys.readouterr().out
    assert anon_client.get("/payments", headers={"Authorization": f"Bearer {api_key}"}).status_code == 401
    assert cli.main(["revoke-key", key_id]) == 0
    assert "already revoked" in capsys.readouterr().out


def test_cli_reports_unknown_ids(capsys):
    assert cli.main(["issue-key", str(uuid.uuid4())]) == 1
    assert "not found" in capsys.readouterr().err
