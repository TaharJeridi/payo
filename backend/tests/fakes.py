import threading
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from app.core.errors import PaymentProviderError, PaymentProviderUnavailable
from app.integrations.stripe_gateway import CheckoutRequestData, CheckoutSessionData
from app.payments.catalog import get_product

CreateBehaviour = Literal["reject", "unavailable", "lost_response"]


class FakeStripeGateway:
    """In-memory Stripe with the property that matters here: idempotent session creation.

    Like Stripe with ``idempotency_key=checkout-<payment_id>``, creating twice for the same
    payment returns the same session. ``create_behaviours`` scripts failures:
    - "reject": Stripe refuses the request, nothing is created (definitive);
    - "unavailable": the request never reaches Stripe (timeout before sending);
    - "lost_response": Stripe creates the session but the answer is lost (timeout after).
    """

    def __init__(self) -> None:
        self.sessions: dict[str, CheckoutSessionData] = {}
        self.created_at: dict[str, datetime] = {}
        self.session_by_payment: dict[uuid.UUID, str] = {}
        self.create_calls: list[CheckoutRequestData] = []
        self.retrieve_calls: list[str] = []
        self.find_calls: list[tuple[uuid.UUID, datetime, datetime]] = []
        self.expire_calls: list[str] = []
        self.create_behaviours: list[CreateBehaviour] = []
        self.amount_total_override: int | None = None
        self.url_override: str | None = None
        self.fail_create = False
        self.fail_expire = False
        self.fail_retrieve = False
        self.fail_find = False
        self.create_barrier: threading.Barrier | None = None
        self.create_started = threading.Event()
        self.create_hold: threading.Event | None = None
        self.enforce_min_expiry = False
        self.clock_offset = timedelta(0)
        self.retrieve_barrier: threading.Barrier | None = None
        self._lock = threading.Lock()

    # --- PaymentGateway protocol ---------------------------------------------------------

    def create_checkout_session(self, request: CheckoutRequestData) -> CheckoutSessionData:
        with self._lock:
            self.create_calls.append(request)
            behaviour = self.create_behaviours.pop(0) if self.create_behaviours else None
        if self.create_barrier is not None:
            self.create_barrier.wait(timeout=10)
        self.create_started.set()
        if self.create_hold is not None:
            self.create_hold.wait(timeout=10)
        if (
            self.enforce_min_expiry
            and request.payment_id not in self.session_by_payment
            and request.expires_at < datetime.now(UTC) + self.clock_offset + timedelta(minutes=30)
        ):
            raise PaymentProviderError("expires_at must be at least 30 minutes in the future")
        if self.fail_create or behaviour == "reject":
            raise PaymentProviderError("Stripe rejected the request to create a Checkout Session")
        if behaviour == "unavailable":
            raise PaymentProviderUnavailable("Stripe outcome unknown while trying to create a Checkout Session")
        with self._lock:
            session_id = self.session_by_payment.get(request.payment_id)
            if session_id is None:
                session_id = self._new_session(request)
        if behaviour == "lost_response":
            raise PaymentProviderUnavailable("Stripe outcome unknown while trying to create a Checkout Session")
        return self.sessions[session_id]

    def retrieve_checkout_session(self, session_id: str) -> CheckoutSessionData:
        with self._lock:
            self.retrieve_calls.append(session_id)
        if self.retrieve_barrier is not None:
            self.retrieve_barrier.wait(timeout=10)
        if self.fail_retrieve:
            raise PaymentProviderUnavailable("Stripe outcome unknown while trying to retrieve a Checkout Session")
        return self.sessions[session_id]

    def find_checkout_session(
        self, payment_id: uuid.UUID, created_from: datetime, created_to: datetime
    ) -> CheckoutSessionData | None:
        self.find_calls.append((payment_id, created_from, created_to))
        if self.fail_find:
            raise PaymentProviderUnavailable("Stripe outcome unknown while trying to list Checkout Sessions")
        for session_id, data in self.sessions.items():
            if data.metadata.get("payment_id") == str(payment_id) and (
                created_from <= self.created_at[session_id] <= created_to
            ):
                return data
        return None

    def expire_checkout_session(self, session_id: str) -> None:
        self.expire_calls.append(session_id)
        if self.fail_expire:
            raise PaymentProviderError("Unable to expire Stripe Checkout session")
        self.sessions[session_id] = replace(self.sessions[session_id], status="expired", url=None)

    # --- test helpers --------------------------------------------------------------------

    def _new_session(self, request: CheckoutRequestData) -> str:
        session_id = f"cs_test_{uuid.uuid4().hex}"
        amount = request.product.unit_amount * request.quantity
        self.sessions[session_id] = CheckoutSessionData(
            id=session_id,
            url=self.url_override
            if self.url_override is not None
            else f"https://checkout.stripe.com/c/pay/{session_id}",
            status="open",
            payment_status="unpaid",
            amount_total=self.amount_total_override if self.amount_total_override is not None else amount,
            currency=request.product.currency,
            livemode=False,
            client_reference_id=str(request.payment_id),
            payment_intent_id=None,
            customer_email=request.customer_email,
            expires_at=request.expires_at,
            metadata={
                "payment_id": str(request.payment_id),
                "merchant_id": str(request.merchant_id),
                "product_id": request.product.id,
            },
        )
        self.created_at[session_id] = datetime.now(UTC)
        self.session_by_payment[request.payment_id] = session_id
        return session_id

    def create_for(
        self, payment_id: uuid.UUID, *, product_id: str = "payflow-pro", quantity: int = 1
    ) -> CheckoutSessionData:
        """Create a session as Stripe would, outside the backend (e.g. `stripe trigger`)."""
        request = CheckoutRequestData(
            payment_id=payment_id,
            merchant_id=uuid.uuid4(),
            product=get_product(product_id),
            quantity=quantity,
            customer_email=None,
            expires_at=datetime.now(UTC) + timedelta(minutes=31),
        )
        with self._lock:
            return self.sessions[self._new_session(request)]

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
