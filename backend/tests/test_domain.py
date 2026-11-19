import itertools

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.errors import ProductNotFound
from app.payments.catalog import CATALOG, get_product
from app.payments.state import FINAL_STATUSES, can_transition
from app.payments.state import PaymentStatus as S

SPEC_ALLOWED = {
    (S.CREATING, S.OPEN),
    (S.CREATING, S.CREATION_UNKNOWN),
    (S.CREATING, S.PAID),
    (S.CREATING, S.FAILED),
    (S.CREATING, S.EXPIRED),
    (S.CREATION_UNKNOWN, S.CREATING),
    (S.CREATION_UNKNOWN, S.OPEN),
    (S.CREATION_UNKNOWN, S.PAID),
    (S.CREATION_UNKNOWN, S.FAILED),
    (S.CREATION_UNKNOWN, S.EXPIRED),
    (S.OPEN, S.PAID),
    (S.OPEN, S.FAILED),
    (S.OPEN, S.EXPIRED),
    (S.FAILED, S.PAID),
    (S.EXPIRED, S.PAID),
}


@pytest.mark.parametrize(("current", "target"), list(itertools.product(S, S)), ids=str)
def test_transition_matrix_matches_spec(current, target):
    assert can_transition(current, target) is ((current, target) in SPEC_ALLOWED)


def test_paid_is_terminal_and_open_never_goes_back():
    assert not any(can_transition(S.PAID, t) for t in S)
    assert not can_transition(S.OPEN, S.CREATING)
    assert not can_transition(S.OPEN, S.CREATION_UNKNOWN)


def test_six_distinct_lifecycle_states():
    assert {s.value for s in S} == {"creating", "open", "creation_unknown", "paid", "failed", "expired"}
    assert {S.PAID, S.FAILED, S.EXPIRED} == FINAL_STATUSES


def test_catalog_is_immutable():
    with pytest.raises(TypeError):
        CATALOG["hack"] = None


def test_unknown_product_raises():
    with pytest.raises(ProductNotFound):
        get_product("nope")


@pytest.mark.parametrize("key", ["sk_live_abc123", "pk_test_abc", "random"])
def test_settings_reject_non_test_secret_keys(key):
    with pytest.raises(ValidationError):
        Settings(stripe_secret_key=key)


def test_settings_accept_test_keys():
    assert Settings(stripe_secret_key="sk_test_abc").stripe_secret_key.get_secret_value() == "sk_test_abc"


def test_settings_reject_malformed_webhook_secret():
    with pytest.raises(ValidationError):
        Settings(stripe_webhook_secret="not-a-whsec")


def test_secrets_are_not_leaked_in_repr():
    settings = Settings(stripe_secret_key="sk_test_supersecret")
    assert "supersecret" not in repr(settings)
