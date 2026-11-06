import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import InvalidQuantity, PaymentProviderError
from app.integrations.stripe_gateway import PaymentGateway
from app.payments.catalog import get_product
from app.payments.models import Payment
from app.payments.repository import PaymentRepository
from app.payments.state import PaymentStatus

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CheckoutResult:
    payment: Payment
    checkout_url: str


class CheckoutService:
    def __init__(self, session: Session, gateway: PaymentGateway, settings: Settings) -> None:
        self._session = session
        self._gateway = gateway
        self._settings = settings
        self._payments = PaymentRepository(session)

    def create_checkout(self, *, product_id: str, quantity: int, customer_email: str | None) -> CheckoutResult:
        product = get_product(product_id)
        if quantity > self._settings.checkout_max_quantity:
            raise InvalidQuantity(f"Quantity must be <= {self._settings.checkout_max_quantity}")

        payment = self._payments.add(
            Payment(
                product_id=product.id,
                quantity=quantity,
                unit_amount=product.unit_amount,
                amount=product.unit_amount * quantity,
                currency=product.currency,
                status=PaymentStatus.PENDING,
                customer_email=customer_email,
            )
        )
        self._session.commit()

        try:
            checkout = self._gateway.create_checkout_session(
                payment_id=payment.id,
                product=product,
                quantity=quantity,
                customer_email=customer_email,
            )
        except PaymentProviderError:
            payment.status = PaymentStatus.FAILED
            payment.failure_reason = "checkout_session_creation_failed"
            self._session.commit()
            raise

        if checkout.amount_total is not None and checkout.amount_total != payment.amount:
            self._reject_session(payment, checkout.id, "checkout_amount_mismatch")
            raise PaymentProviderError("Stripe session amount does not match backend amount")

        if not checkout.url:
            self._reject_session(payment, checkout.id, "checkout_url_missing")
            raise PaymentProviderError("Stripe did not return a Checkout URL")

        payment.stripe_checkout_session_id = checkout.id
        self._session.commit()
        logger.info("checkout_created payment_id=%s session_id=%s amount=%s", payment.id, checkout.id, payment.amount)
        return CheckoutResult(payment=payment, checkout_url=checkout.url)

    def _reject_session(self, payment: Payment, session_id: str, reason: str) -> None:
        """Mark the payment failed and close the Stripe session so nobody can still pay it."""
        payment.status = PaymentStatus.FAILED
        payment.failure_reason = reason
        payment.stripe_checkout_session_id = session_id
        self._session.commit()
        try:
            self._gateway.expire_checkout_session(session_id)
        except PaymentProviderError:
            logger.error("checkout_session_left_open payment_id=%s session_id=%s", payment.id, session_id)
