"""Typed, validated filters over a merchant's payments.

A ``PaymentFilters`` never contains a merchant: the tenant is a separate argument supplied
by the server from the authenticated API key. Amounts are integer minor units (cents).
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.payments.state import PaymentStatus


class AmountRange(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    minimum: int | None = Field(default=None, ge=0)
    maximum: int | None = Field(default=None, ge=0)
    minimum_inclusive: bool = True
    maximum_inclusive: bool = True

    @model_validator(mode="after")
    def _ordered(self) -> "AmountRange":
        if self.minimum is None and self.maximum is None:
            raise ValueError("An amount range needs a minimum or a maximum")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("minimum must not exceed maximum")
        return self

    @classmethod
    def exactly(cls, cents: int) -> "AmountRange":
        return cls(minimum=cents, maximum=cents)

    @property
    def is_exact(self) -> bool:
        return (
            self.minimum is not None
            and self.minimum == self.maximum
            and self.minimum_inclusive
            and self.maximum_inclusive
        )


class PaymentFilters(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    payment_ids: tuple[uuid.UUID, ...] = ()
    statuses: tuple[PaymentStatus, ...] = ()
    product_ids: tuple[str, ...] = ()
    quantity: int | None = Field(default=None, ge=1)
    amount: AmountRange | None = None
    currency: str | None = Field(default=None, pattern=r"^[a-z]{3}$")
    created_from: datetime | None = None
    created_to: datetime | None = None
    recovered: bool | None = None
    recovery_source: str | None = Field(default=None, pattern=r"^(api|webhook|reconciliation)$")
    paid_after_expiry: bool | None = None

    @model_validator(mode="after")
    def _dates(self) -> "PaymentFilters":
        if self.created_from and self.created_to and self.created_from >= self.created_to:
            raise ValueError("created_from must precede created_to")
        return self

    @property
    def is_empty(self) -> bool:
        return self == PaymentFilters()

    @property
    def identifies_payments(self) -> bool:
        return bool(self.payment_ids) or (self.amount is not None and self.amount.is_exact)
