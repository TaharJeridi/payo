import enum


class PaymentStatus(enum.StrEnum):
    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"


_ALLOWED: dict[PaymentStatus, frozenset[PaymentStatus]] = {
    PaymentStatus.PENDING: frozenset({PaymentStatus.PAID, PaymentStatus.FAILED}),
    PaymentStatus.FAILED: frozenset({PaymentStatus.PAID}),
    PaymentStatus.PAID: frozenset(),
}


def can_transition(current: PaymentStatus, target: PaymentStatus) -> bool:
    return target in _ALLOWED[current]
