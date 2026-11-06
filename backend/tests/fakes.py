import threading
import uuid
from dataclasses import replace
from typing import Any

from app.core.errors import PaymentProviderError
from app.integrations.stripe_gateway import CheckoutSessionData
from app.payments.catalog import Product


class FakeStripeGateway:
    def __init__(self) -> None:
        self.sessions: dict[str, CheckoutSessionData] = {}
        self.create_calls: list[dict[str, Any]] = []
        self.retrieve_calls: list[str] = []
        self.expire_calls: list[str] = []
        self.amount_total_override: int | None = None
        self.url_override: str | None = None
        self.fail_expire = False
        self.fail_create = False
        self.fail_retrieve = False
        self.retrieve_barrier: threading.Barrier | None = None
        self._lock = threading.Lock()

    def create_checkout_session(
        self, *, payment_id: uuid.UUID, product: Product, quantity: int, customer_email: str | None
    ) -> CheckoutSessionData:
        self.create_calls.append(
            {"payment_id": payment_id, "product": product, "quantity": quantity, "customer_email": customer_email}
        )
        if self.fail_create:
            raise PaymentProviderError("Unable to create Stripe Checkout session")
        session_id = f"cs_test_{uuid.uuid4().hex}"
        data = CheckoutSessionData(
            id=session_id,
            url=self.url_override
            if self.url_override is not None
            else f"https://checkout.stripe.com/c/pay/{session_id}",
            status="open",
            payment_status="unpaid",
            amount_total=self.amount_total_override
            if self.amount_total_override is not None
            else product.unit_amount * quantity,
            currency=product.currency,
            livemode=False,
            client_reference_id=str(payment_id),
            payment_intent_id=None,
            customer_email=customer_email,
            metadata={"payment_id": str(payment_id), "product_id": product.id},
        )
        self.sessions[session_id] = data
        return data

    def retrieve_checkout_session(self, session_id: str) -> CheckoutSessionData:
        with self._lock:
            self.retrieve_calls.append(session_id)
        if self.retrieve_barrier is not None:
            self.retrieve_barrier.wait(timeout=10)
        if self.fail_retrieve:
            raise PaymentProviderError("Unable to retrieve Stripe Checkout session")
        return self.sessions[session_id]

    def expire_checkout_session(self, session_id: str) -> None:
        self.expire_calls.append(session_id)
        if self.fail_expire:
            raise PaymentProviderError("Unable to expire Stripe Checkout session")
        self.sessions[session_id] = replace(self.sessions[session_id], status="expired")

    def update_remote(self, session_id: str, **changes: Any) -> CheckoutSessionData:
        self.sessions[session_id] = replace(self.sessions[session_id], **changes)
        return self.sessions[session_id]

    def mark_paid(self, session_id: str) -> CheckoutSessionData:
        return self.update_remote(
            session_id,
            status="complete",
            payment_status="paid",
            payment_intent_id=f"pi_test_{uuid.uuid4().hex[:24]}",
        )
