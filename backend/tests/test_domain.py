import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.errors import ProductNotFound
from app.payments.catalog import CATALOG, get_product
from app.payments.state import PaymentStatus, can_transition


@pytest.mark.parametrize(
    ("current", "target", "allowed"),
    [
        (PaymentStatus.PENDING, PaymentStatus.PAID, True),
        (PaymentStatus.PENDING, PaymentStatus.FAILED, True),
        (PaymentStatus.FAILED, PaymentStatus.PAID, True),
        (PaymentStatus.PAID, PaymentStatus.FAILED, False),
        (PaymentStatus.PAID, PaymentStatus.PENDING, False),
        (PaymentStatus.FAILED, PaymentStatus.PENDING, False),
        (PaymentStatus.PENDING, PaymentStatus.PENDING, False),
    ],
)
def test_state_machine(current, target, allowed):
    assert can_transition(current, target) is allowed


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
