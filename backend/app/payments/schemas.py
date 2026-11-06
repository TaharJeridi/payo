import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.payments.state import PaymentStatus


class ProductRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str
    unit_amount: int
    currency: str


class CheckoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    product_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9\-]*$")
    quantity: int = Field(default=1, ge=1)
    customer_email: str | None = Field(default=None, max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class CheckoutResponse(BaseModel):
    payment_id: uuid.UUID
    checkout_session_id: str
    checkout_url: str
    amount: int
    currency: str
    status: PaymentStatus


class PaymentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_id: str
    quantity: int
    unit_amount: int
    amount: int
    currency: str
    status: PaymentStatus
    stripe_checkout_session_id: str | None
    stripe_payment_intent_id: str | None
    failure_reason: str | None
    paid_at: datetime | None
    created_at: datetime
    updated_at: datetime


class PaymentPage(BaseModel):
    items: list[PaymentRead]
    total: int
    limit: int
    offset: int
